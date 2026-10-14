"""End-to-end tests over real sockets: fake Claude API (HTTP and TLS), fake Gmail SMTP.

Run on Linux and inside the 32-bit Windows Python (Wine / Windows 7).
"""

import base64
import email
import email.policy
import os
import shutil
import struct
import sys
import tempfile
import threading
import unittest
import zlib
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fakes  # noqa: E402
from helper import agent, config, llm, logs, net, paths, report, tools  # noqa: E402

PROXY_VARS = ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy", "ALL_PROXY", "all_proxy")


def tiny_png():
    raw = b"\x00\xff\x00\x00"  # one red pixel
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw[:4])) + chunk(b"IEND", b""))


class E2EBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="assistant-e2e-")
        self.home = os.path.join(self.tmp, "home")
        for sub in ("Desktop", "Documents", "Pictures"):
            os.makedirs(os.path.join(self.home, sub))
        env = {"USERPROFILE": self.home, "HOME": self.home, "NO_PROXY": "*", "no_proxy": "*"}
        for name in PROXY_VARS:
            env[name] = ""
        self.env = mock.patch.dict(os.environ, env)
        self.env.start()
        # Trust the TEST-ONLY CA in addition to the real bundle, just for this test.
        bundle = os.path.join(self.tmp, "bundle.pem")
        with open(bundle, "wb") as out:
            for name in (paths.CA_BUNDLE, fakes.TEST_CA):
                with open(name, "rb") as f:
                    out.write(f.read() + b"\n")
        self.patches = [mock.patch.object(paths, "CA_BUNDLE", bundle), mock.patch.object(net, "RETRY_DELAYS", (0, 0)),
                        mock.patch.object(paths, "OUTBOX_DIR", os.path.join(self.tmp, "outbox"))]
        for p in self.patches:
            p.start()
        os.makedirs(paths.OUTBOX_DIR)
        net._context = None
        self.servers = []
        self.reports = []
        self.emitted = []

    def tearDown(self):
        for s in self.servers:
            s.close()
        for p in self.patches:
            p.stop()
        net._context = None
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def cfg(self, api_url, **extra):
        cfg = config._merge(config.DEFAULTS, {"anthropic": {"api_key": "sk-ant-e2e-secret-key", "base_url": api_url}})
        cfg.update(extra)
        config._sanitize(cfg, [])
        return cfg

    def api(self, responses, tls=False):
        server = fakes.FakeAPI(responses, tls=tls)
        self.servers.append(server)
        return server

    def make_agent(self, cfg, answers=None):
        answers = list(answers or [])
        self.questions = []

        def confirm(q, d):
            self.questions.append(q)
            return answers.pop(0) if answers else True
        self.notified = []
        runner = tools.ToolRunner(cfg, confirm, lambda k, v: self.notified.append((k, v)),
                                  undo_path=os.path.join(self.tmp, "undo.json"))
        reporter = mock.Mock()
        reporter.report_error.side_effect = lambda *a, **k: self.reports.append(a)
        return agent.Agent(cfg, llm.make_llm(cfg), runner, lambda k, v: self.emitted.append((k, v)), reporter)


class ConversationTests(E2EBase):
    def test_full_tool_conversation_over_tls(self):
        desk = os.path.join(self.home, "Desktop")
        for name in ("IMG_1.jpg", "IMG_2.jpg"):
            open(os.path.join(desk, name), "w").close()
        server = self.api([
            fakes.tool_reply("move_items", {"explanation": "Put photos in Photos",
                                            "moves": [{"from": os.path.join(desk, "IMG_1.jpg"), "to": os.path.join(desk, "Photos")},
                                                      {"from": os.path.join(desk, "IMG_2.jpg"), "to": os.path.join(desk, "Photos")}]}),
            fakes.text_reply("Done. Your photos are in the Photos folder."),
        ], tls=True)
        a = self.make_agent(self.cfg(server.url))
        a.handle("tidy my desktop", threading.Event())
        self.assertEqual(self.emitted[-1], ("text", "Done. Your photos are in the Photos folder."))
        self.assertEqual(sorted(os.listdir(os.path.join(desk, "Photos"))), ["IMG_1.jpg", "IMG_2.jpg"])
        first, second = server.requests
        self.assertEqual(first["path"], "/v1/messages")
        self.assertEqual(first["headers"].get("x-api-key"), "sk-ant-e2e-secret-key")
        self.assertEqual(first["headers"].get("anthropic-version"), "2023-06-01")
        self.assertEqual(first["headers"].get("anthropic-beta"), "server-side-fallback-2026-07-01")
        body = first["body"]
        self.assertEqual(body["model"], "claude-opus-5-5")
        self.assertEqual(body["output_config"], {"effort": "medium"})
        self.assertEqual(body["fallbacks"], "default")
        self.assertEqual(sorted(t["name"] for t in body["tools"]), sorted(tools.TOOL_NAMES))
        msgs = second["body"]["messages"]
        self.assertEqual([m["role"] for m in msgs], ["user", "assistant", "user"])
        self.assertEqual(msgs[1]["content"][0], {"type": "thinking", "thinking": "", "signature": "sig"})
        self.assertEqual(msgs[2]["content"][0]["tool_use_id"], "toolu_1")
        self.assertFalse(msgs[2]["content"][0]["is_error"])

    def test_stop_while_waiting_for_the_ai(self):
        import time

        def slow(body):
            time.sleep(6)
            return fakes.text_reply("too late")
        server = self.api([slow])
        a = self.make_agent(self.cfg(server.url))
        cancel = threading.Event()
        threading.Timer(0.5, cancel.set).start()
        start = time.time()
        a.handle("hi", cancel)
        self.assertLess(time.time() - start, 3)
        self.assertEqual(self.emitted[-1], ("notice", "Stopped."))
        self.assertNotIn(("text", "too late"), self.emitted)
        time.sleep(6)  # the abandoned request finishes in the background...
        self.assertNotIn(("text", "too late"), self.emitted)  # ...and its answer is thrown away

    def test_rate_limit_is_retried(self):
        server = self.api([(429, {"error": {"type": "rate_limit_error"}}), fakes.text_reply("Hello")])
        a = self.make_agent(self.cfg(server.url))
        a.handle("hi", threading.Event())
        self.assertEqual(self.emitted[-1], ("text", "Hello"))
        self.assertEqual(len(server.requests), 2)

    def test_bad_key_reports_to_ahmed(self):
        server = self.api([(401, {"error": {"type": "authentication_error", "message": "invalid x-api-key"}})])
        a = self.make_agent(self.cfg(server.url))
        a.handle("hi", threading.Event())
        self.assertEqual(self.emitted[-1][0], "error")
        self.assertIn("account settings", self.emitted[-1][1])
        self.assertTrue(self.reports)

    def test_out_of_credit(self):
        server = self.api([(400, {"error": {"type": "invalid_request_error",
                                            "message": "Your credit balance is too low to access the API."}})])
        a = self.make_agent(self.cfg(server.url))
        a.handle("hi", threading.Event())
        self.assertIn("run out of credit", self.emitted[-1][1])
        self.assertEqual(len(server.requests), 1)

    def test_server_unreachable(self):
        server = self.api([])
        url = server.url
        server.close()
        self.servers.remove(server)
        a = self.make_agent(self.cfg(url))
        a.handle("hi", threading.Event())
        self.assertIn("internet", self.emitted[-1][1])

    def test_untrusted_certificate_is_refused(self):
        server = self.api([fakes.text_reply("should never be seen")], tls=True)
        with mock.patch.object(paths, "CA_BUNDLE", os.path.join(self.tmp, "missing.pem")):
            net._context = None
            a = self.make_agent(self.cfg(server.url))
            a.handle("hi", threading.Event())
        self.assertEqual(self.emitted[-1][0], "error")
        self.assertIn("date and time", self.emitted[-1][1])
        self.assertEqual(server.requests, [])

    def test_openai_compatible_provider(self):
        server = self.api([
            (200, {"choices": [{"finish_reason": "tool_calls", "message": {"content": None, "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "list_folder",
                                                              "arguments": '{"path": "%s"}' % os.path.join(self.home, "Desktop").replace("\\", "\\\\")}}]}}]}),
            (200, {"choices": [{"finish_reason": "stop", "message": {"content": "Your desktop is empty."}}]}),
        ])
        cfg = self.cfg("unused")
        cfg["provider"] = "openai_compatible"
        cfg["openai_compatible"].update({"api_key": "dk-test", "base_url": server.url + "/v1", "model": "deepseek-chat"})
        a = self.make_agent(cfg)
        a.handle("what's on my desktop", threading.Event())
        self.assertEqual(self.emitted[-1], ("text", "Your desktop is empty."))
        self.assertEqual(server.requests[0]["path"], "/v1/chat/completions")
        self.assertEqual(server.requests[1]["body"]["messages"][-1]["role"], "tool")

    def test_generate_image(self):
        server = self.api([(200, {"data": [{"b64_json": base64.b64encode(tiny_png()).decode()}]})])
        cfg = self.cfg("unused")
        cfg["images"].update({"api_key": "sk-img", "base_url": server.url + "/v1"})
        a = self.make_agent(cfg)
        out, err = a.runner.run("generate_image", {"prompt": "a red dot", "file_name": "red dot"}, threading.Event())
        self.assertFalse(err, out)
        self.assertEqual(self.notified[0][0], "image")
        path = self.notified[0][1]
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        folder = os.path.dirname(path)
        self.assertEqual(os.path.basename(folder), "Assistant Pictures")
        with open(path, "rb") as f:
            self.assertEqual(f.read(), tiny_png())
        saved = [n for n in os.listdir(folder) if n == os.path.basename(path)]
        self.assertEqual(len(saved), 1)
        self.assertTrue(saved[0].startswith("red dot") and saved[0].endswith(".png"))
        self.assertEqual(self.notified[0][0], "image")


class EmailTests(E2EBase):
    def reporter(self, smtp, password="abcd efgh ijkl mnop"):
        cfg = self.cfg("unused")
        cfg["email_reports"].update({"send_to": "ahmed@example.com", "smtp_username": "from@example.com",
                                     "smtp_app_password": password, "smtp_host": "localhost", "smtp_port": smtp.port})
        logs.set_secrets(config.secrets(cfg))
        return report.Reporter(cfg)

    def test_report_is_delivered_over_tls(self):
        smtp = fakes.FakeSMTP()
        self.servers.append(smtp)
        logging_probe = "\u0631\u0633\u0627\u0644\u0629.txt"
        with mock.patch.object(logs, "tail", lambda: "log line about " + logging_probe):
            ok = self.reporter(smtp).send_now("Dad asked for help", "Note: the key sk-ant-e2e-secret-key leaked?")
        self.assertTrue(ok)
        self.assertEqual(smtp.logins, [("from@example.com", "abcdefghijklmnop")])  # spaces removed
        msg = email.message_from_bytes(smtp.messages[0]["data"], policy=email.policy.default)
        self.assertEqual(msg["To"], "ahmed@example.com")
        self.assertIn("Dad asked for help", msg["Subject"])
        self.assertNotIn(b"sk-ant-e2e-secret-key", smtp.messages[0]["data"])
        names = [part.get_filename() for part in msg.iter_attachments()]
        self.assertEqual(names, ["assistant-log.txt"])
        attachment = next(msg.iter_attachments())
        self.assertEqual(attachment.get_content_charset(), "utf-8")
        self.assertIn(logging_probe, attachment.get_content())

    def test_wrong_password_goes_to_outbox_then_sends_later(self):
        smtp = fakes.FakeSMTP()
        self.servers.append(smtp)
        r = self.reporter(smtp, password="wrong password")
        self.assertFalse(r.send_now("Problem", "details"))
        self.assertEqual(len(os.listdir(paths.OUTBOX_DIR)), 1)
        r.settings["smtp_app_password"] = "abcdefghijklmnop"
        r.flush_outbox()
        self.assertEqual(os.listdir(paths.OUTBOX_DIR), [])
        self.assertEqual(len(smtp.messages), 1)


if __name__ == "__main__":
    unittest.main()
