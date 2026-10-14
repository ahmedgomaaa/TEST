"""Tests that run on any OS with Python 3.8+:  python -m unittest discover -s tests"""

import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from helper import strings as S  # noqa: E402
from helper import agent, config, llm, logs, net, paths, report, shell, textutil, tools  # noqa: E402


def make_cfg(**overrides):
    cfg = config._merge(config.DEFAULTS, {"anthropic": {"api_key": "sk-ant-test-1234567890"}})
    config._sanitize(cfg, [])
    cfg.update(overrides)
    return cfg


class HomeDirTest(unittest.TestCase):
    """Runs each test with a fake user profile folder."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="assistant-test-")
        self.home = os.path.join(self.tmp, "home")
        for sub in ("Desktop", "Documents", "Pictures", "Music", "Videos", "Downloads"):
            os.makedirs(os.path.join(self.home, sub))
        self.env = mock.patch.dict(os.environ, {"USERPROFILE": self.home, "HOME": self.home})
        self.env.start()
        self.answers = []
        self.questions = []
        self.notified = []
        self.runner = tools.ToolRunner(make_cfg(), self._confirm, lambda k, v: self.notified.append((k, v)),
                                       undo_path=os.path.join(self.tmp, "undo.json"))

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _confirm(self, question, details):
        self.questions.append((question, details))
        return self.answers.pop(0) if self.answers else True

    def write(self, rel, text="hello"):
        path = os.path.join(self.home, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path


class ConfigTests(unittest.TestCase):
    def test_bom_and_defaults(self):
        tmp = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp, "config.json")
            with open(path, "w", encoding="utf-8-sig") as f:
                json.dump({"provider": "anthropic", "anthropic": {"api_key": "sk-ant-abcdef123"}, "font_size": 99}, f)
            cfg, problems = config.load(path)
            self.assertEqual(problems, [])
            self.assertEqual(cfg["anthropic"]["model"], "claude-opus-5-5")
            self.assertEqual(cfg["font_size"], 30)
            self.assertIn("sk-ant-abcdef123", config.secrets(cfg))
        finally:
            shutil.rmtree(tmp)

    def test_broken_json_never_raises(self):
        tmp = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp, "config.json")
            with open(path, "w") as f:
                f.write("{ not json")
            cfg, problems = config.load(path)
            self.assertTrue(problems)
            self.assertEqual(cfg["provider"], "anthropic")
        finally:
            shutil.rmtree(tmp)

    def test_example_config_is_valid(self):
        cfg, problems = config.load(paths.CONFIG_EXAMPLE_PATH)
        self.assertEqual([p for p in problems if "JSON" in p], [])


class TextTests(unittest.TestCase):
    def test_emoji_removed_for_tk(self):
        self.assertEqual(textutil.for_tk("Done \U0001F600!"), "Done!")

    def test_rtl(self):
        self.assertTrue(textutil.is_rtl("مرحبا بك"))
        self.assertFalse(textutil.is_rtl("Hello"))

    def test_rtl_layout_reverses_words_per_line(self):
        with mock.patch.object(textutil, "REORDER", True):
            lines = textutil.rtl_visual_lines("one two three four five", 9, len)
            self.assertEqual(lines, ["two one", "three", "five four"])
            self.assertEqual(textutil.rtl_visual_lines("", 100, len), [""])

    def test_rtl_layout_wraps_without_reversing_when_off(self):
        with mock.patch.object(textutil, "REORDER", False):
            self.assertEqual(textutil.rtl_visual_lines("one two three four five", 9, len),
                             ["one two", "three", "four five"])

    def test_rtl_label(self):
        arabic = S.SEND + " " + S.STOP
        with mock.patch.object(textutil, "REORDER", True):
            self.assertEqual(textutil.rtl(arabic), S.STOP + " " + S.SEND)
            self.assertEqual(textutil.rtl("A+"), "A+")  # Latin text is left alone
            self.assertEqual(textutil.rtl(arabic + "\n" + arabic), (S.STOP + " " + S.SEND + "\n") * 1 + S.STOP + " " + S.SEND)
        with mock.patch.object(textutil, "REORDER", False):
            self.assertEqual(textutil.rtl(arabic), arabic)

    def test_strings_are_safe_for_tk(self):
        """Tk 8.6.9 crashes on characters above U+FFFF (emoji), so Dad's texts must not contain any."""
        for name in dir(S):
            value = getattr(S, name)
            values = value.values() if isinstance(value, dict) else [value]
            for v in values:
                if isinstance(v, str):
                    self.assertEqual(textutil.for_tk(v), v, name)

    def test_markdown(self):
        self.assertEqual(textutil.strip_markdown("## Title\n**bold** and `code`"), "Title\nbold and code")

    def test_redact(self):
        logs.set_secrets(["supersecretvalue"])
        self.assertNotIn("supersecretvalue", logs.redact("key=supersecretvalue"))
        self.assertNotIn("sk-ant-api03-XYZXYZXYZ", logs.redact("sk-ant-api03-XYZXYZXYZ"))


class ShellTests(unittest.TestCase):
    def test_blocked(self):
        for cmd in ["del /q C:\\x.txt", "Remove-Item -Recurse foo", "rd /s /q x", "format c:",
                    "reg delete HKCU\\x", "(New-Object Net.WebClient).DownloadFile('a','b')", "msiexec /i x.msi",
                    "Set-ExecutionPolicy Unrestricted", "shutdown /s /t 0", "rm -rf /"]:
            self.assertIsNotNone(shell.blocked_reason(cmd), cmd)
        for cmd in ["dir C:\\Users", "Get-WmiObject Win32_LogicalDisk | Select-Object DeviceID, FreeSpace",
                    "Get-ChildItem -LiteralPath 'C:\\a' | Sort-Object Length",
                    "Get-ItemProperty HKLM:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*"]:
            self.assertIsNone(shell.blocked_reason(cmd), cmd)

    def test_change_detection(self):
        self.assertTrue(shell.looks_like_change("Move-Item a b"))
        self.assertTrue(shell.looks_like_change("echo hi > file.txt"))
        self.assertFalse(shell.looks_like_change("Get-ChildItem C:\\"))

    def test_decode(self):
        self.assertEqual(shell._decode_console("Folder مرحبا".encode("utf-16-le")), "Folder مرحبا")
        self.assertEqual(shell._decode_console(b"plain"), "plain")
        arabic_only = "\u0635\u0648\u0631.jpg\r\n"
        self.assertEqual(shell._decode_console(arabic_only.encode("utf-16-le")), arabic_only)

    def test_shorten(self):
        out = shell.shorten("x" * 20000, 1000)
        self.assertLess(len(out), 1100)
        self.assertIn("cut", out)

    @unittest.skipIf(sys.platform == "win32", "uses /bin/sh")
    def test_timeout_and_cancel(self):
        r = shell.run("powershell", "echo hi", timeout=10)
        self.assertEqual(r.output.strip(), "hi")
        start = time.time()
        r = shell.run("powershell", "sleep 30", timeout=1)
        self.assertTrue(r.timed_out)
        self.assertLess(time.time() - start, 15)
        cancel = threading.Event()
        threading.Timer(0.5, cancel.set).start()
        r = shell.run("powershell", "sleep 30", timeout=60, cancel=cancel)
        self.assertTrue(r.cancelled)

    def test_powershell_wrapper_is_v2_safe(self):
        wrapper = shell._wrapper("C:\\a'b\\code.txt", "C:\\out.txt")
        self.assertIn("'C:\\a''b\\code.txt'", wrapper)
        for newer in ("ConvertTo-Json", "-Raw", "$PSItem", "[pscustomobject]", "Invoke-WebRequest"):
            self.assertNotIn(newer, wrapper)


class NetTests(unittest.TestCase):
    def test_retries_then_succeeds(self):
        calls = []

        def flaky():
            calls.append(1)
            if len(calls) < 3:
                raise net.HttpError(529, "overloaded")
            return b"ok"
        self.assertEqual(net.with_retries(flaky, delays=(0, 0, 0)), b"ok")
        self.assertEqual(len(calls), 3)

    def test_no_retry_on_400(self):
        calls = []

        def bad():
            calls.append(1)
            raise net.HttpError(400, "bad")
        with self.assertRaises(net.HttpError):
            net.with_retries(bad, delays=(0, 0))
        self.assertEqual(len(calls), 1)

    def test_cancel(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(net.Cancelled):
            net.with_retries(lambda: b"x", cancel=cancel)

    def test_clock_hint(self):
        self.assertTrue(net.NetworkError("SSLError: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed").looks_like_clock_problem)

    def test_ssl_context_loads_bundle(self):
        self.assertTrue(os.path.exists(paths.CA_BUNDLE))
        self.assertGreater(net.ssl_context().cert_store_stats()["x509_ca"], 100)


class LLMTests(unittest.TestCase):
    def test_openai_conversion_roundtrip(self):
        messages = [
            {"role": "user", "content": [{"type": "text", "text": "hi"}]},
            {"role": "assistant", "content": [{"type": "thinking", "thinking": ""}, {"type": "text", "text": "ok"},
                                              {"type": "tool_use", "id": "t1", "name": "list_folder", "input": {"path": "C:\\"}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "files"}]},
        ]
        out = llm.to_openai_messages("sys", messages)
        self.assertEqual([m["role"] for m in out], ["system", "user", "assistant", "tool"])
        self.assertEqual(json.loads(out[2]["tool_calls"][0]["function"]["arguments"]), {"path": "C:\\"})
        resp = llm.from_openai_response({"choices": [{"finish_reason": "tool_calls", "message": {
            "content": None, "tool_calls": [{"id": "c", "function": {"name": "find_files", "arguments": "{bad"}}]}}]})
        self.assertEqual(resp.stop_reason, "tool_use")
        self.assertIn("_invalid_arguments", resp.tool_uses()[0]["input"])

    def test_anthropic_drops_rejected_optional_features(self):
        client = llm.AnthropicLLM({"api_key": "k", "model": "m", "effort": "medium", "use_fallbacks": True})
        sent = []

        def fake_post(url, payload, headers, **kw):
            sent.append((payload, headers))
            if "fallbacks" in payload:
                raise net.HttpError(400, '{"error": {"message": "fallbacks: unsupported"}}')
            return {"content": [{"type": "text", "text": "hello"}], "stop_reason": "end_turn"}
        with mock.patch.object(net, "post_json", fake_post):
            resp = client.create("sys", [], [{"role": "user", "content": "hi"}])
        self.assertEqual(resp.text(), "hello")
        self.assertEqual(len(sent), 2)
        self.assertNotIn("anthropic-beta", sent[1][1])
        self.assertEqual(sent[0][0]["system"][0]["cache_control"], {"type": "ephemeral"})
        self.assertNotIn("tools", sent[0][0])


class ToolTests(HomeDirTest):
    def test_list_and_find(self):
        self.write("Desktop/photo1.jpg")
        self.write("Desktop/notes.txt", "call the doctor on Monday")
        self.write("Documents/letter.txt", "Dear bank")
        out, err = self.runner.run("list_folder", {"path": os.path.join(self.home, "Desktop")})
        self.assertFalse(err)
        self.assertIn("photo1.jpg", out)
        out, err = self.runner.run("find_files", {"extensions": ["jpg"]})
        self.assertIn("photo1.jpg", out)
        self.assertNotIn("notes.txt", out)
        out, err = self.runner.run("find_files", {"text_contains": "doctor"})
        self.assertIn("notes.txt", out)
        out, err = self.runner.run("find_files", {"name_contains": "*.txt", "folder": os.path.join(self.home, "Documents")})
        self.assertIn("letter.txt", out)

    def test_read_docx(self):
        path = os.path.join(self.home, "Documents", "a.docx")
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("word/document.xml", "<w:document><w:p><w:r><w:t>Hello &amp; welcome</w:t></w:r></w:p></w:document>")
        out, err = self.runner.run("read_file", {"path": path})
        self.assertFalse(err)
        self.assertIn("Hello & welcome", out)

    def test_move_conflict_and_undo(self):
        a = self.write("Desktop/a.jpg", "A")
        self.write("Desktop/Photos/a.jpg", "OLD")
        b = self.write("Desktop/b.jpg", "B")
        dest = os.path.join(self.home, "Desktop", "Photos")
        out, err = self.runner.run("move_items", {"moves": [{"from": a, "to": dest}, {"from": b, "to": dest}],
                                                  "explanation": "Put photos in Photos"})
        self.assertFalse(err, out)
        self.assertEqual(self.questions[0][0], "Put photos in Photos")
        self.assertTrue(os.path.exists(os.path.join(dest, "a (2).jpg")))
        with open(os.path.join(dest, "a.jpg")) as f:
            self.assertEqual(f.read(), "OLD")  # never overwritten
        out, err = self.runner.run("undo_last_change", {})
        self.assertFalse(err, out)
        self.assertTrue(os.path.exists(a) and os.path.exists(b))

    def test_move_to_new_folder_and_undo_removes_it(self):
        a = self.write("Desktop/x.txt")
        new = os.path.join(self.home, "Desktop", "New Folder", "Sub", "y.txt")
        self.runner.run("move_items", {"moves": [{"from": a, "to": new}], "explanation": "x"})
        self.assertTrue(os.path.exists(new))
        self.runner.run("undo_last_change", {})
        self.assertTrue(os.path.exists(a))
        self.assertFalse(os.path.exists(os.path.join(self.home, "Desktop", "New Folder")))

    def test_move_into_new_folder_vs_rename(self):
        a = self.write("Desktop/one.jpg")
        b = self.write("Desktop/two.txt")
        photos = os.path.join(self.home, "Desktop", "Photos")
        self.runner.run("move_items", {"moves": [{"from": a, "to": photos}], "explanation": "x"})
        self.assertTrue(os.path.isfile(os.path.join(photos, "one.jpg")))  # folder created, not a file named Photos
        self.runner.run("move_items", {"moves": [{"from": b, "to": os.path.join(self.home, "Desktop", "renamed.txt")}],
                                       "explanation": "x"})
        self.assertTrue(os.path.isfile(os.path.join(self.home, "Desktop", "renamed.txt")))

    def test_user_says_no(self):
        a = self.write("Desktop/a.txt")
        self.answers = [False]
        out, err = self.runner.run("move_items", {"moves": [{"from": a, "to": os.path.join(self.home, "Documents")}],
                                                  "explanation": "x"})
        self.assertIn("said NO", out)
        self.assertTrue(os.path.exists(a))

    def test_protected_paths(self):
        out, err = self.runner.run("move_items", {"moves": [{"from": os.path.join(self.home, "Desktop"),
                                                             "to": os.path.join(self.home, "Documents")}],
                                                  "explanation": "x"})
        self.assertTrue(err)
        self.assertIn("main folders", out)
        out, err = self.runner.run("recycle_items", {"paths": [os.path.join(paths.APP_DIR, "SETUP.bat")],
                                                     "explanation": "x"})
        self.assertTrue(err)
        self.assertTrue(os.path.exists(os.path.join(paths.APP_DIR, "SETUP.bat")))
        self.assertEqual(self.questions, [])  # refused before even asking

    def test_recycle(self):
        a = self.write("Desktop/old.txt")
        out, err = self.runner.run("recycle_items", {"paths": [a], "explanation": "Delete old.txt"})
        self.assertFalse(err, out)
        self.assertFalse(os.path.exists(a))

    def test_duplicates(self):
        self.write("Pictures/a.jpg", "same content")
        self.write("Pictures/sub/b.jpg", "same content")
        self.write("Pictures/c.jpg", "different")
        out, err = self.runner.run("find_duplicate_files", {"folder": os.path.join(self.home, "Pictures")})
        self.assertIn("1 groups", out)
        self.assertIn("b.jpg", out)
        self.assertNotIn("c.jpg", out)

    def test_command_blocked_and_bad_args(self):
        out, err = self.runner.run("run_command", {"shell": "cmd", "command": "del /s /q C:\\", "changes_things": True,
                                                   "explanation": "x"})
        self.assertTrue(err)
        self.assertIn("Blocked", out)
        out, err = self.runner.run("move_items", {"_invalid_arguments": "{"})
        self.assertTrue(err)
        out, err = self.runner.run("no_such_tool", {})
        self.assertTrue(err)

    def test_image_not_configured(self):
        out, err = self.runner.run("generate_image", {"prompt": "a cat"})
        self.assertTrue(err)
        self.assertIn("not set up", out)


class FakeClient(object):
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, system, tool_defs, messages, cancel=None):
        self.calls.append(json.loads(json.dumps(messages)))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def text_resp(text):
    return llm.LLMResponse([{"type": "text", "text": text}], "end_turn")


class AgentTests(HomeDirTest):
    def make_agent(self, responses):
        self.emitted = []
        self.client = FakeClient(responses)
        return agent.Agent(make_cfg(), self.client, self.runner, lambda k, v: self.emitted.append((k, v)))

    def test_tool_loop(self):
        self.write("Desktop/a.txt")
        a = self.make_agent([
            llm.LLMResponse([{"type": "thinking", "thinking": "", "signature": "s"},
                             {"type": "tool_use", "id": "t1", "name": "list_folder",
                              "input": {"path": os.path.join(self.home, "Desktop")}},
                             {"type": "tool_use", "id": "t2", "name": "bogus", "input": {}}], "tool_use"),
            text_resp("You have one file."),
        ])
        a.handle("what's on my desktop?", threading.Event())
        self.assertEqual(self.emitted[-1], ("text", "You have one file."))
        results = a.messages[2]["content"]
        self.assertEqual([r["tool_use_id"] for r in results], ["t1", "t2"])
        self.assertTrue(results[1]["is_error"])
        self.assertEqual(a.messages[1]["content"][0]["type"], "thinking")  # stored unchanged

    def test_network_error_is_friendly(self):
        a = self.make_agent([net.NetworkError("URLError: offline")])
        a.handle("hi", threading.Event())
        self.assertEqual(self.emitted[-1][0], "error")
        self.assertIn(S.NO_INTERNET, self.emitted[-1][1])

    def test_400_starts_fresh_once(self):
        a = self.make_agent([text_resp("first")])
        a.handle("one", threading.Event())
        self.client.responses = [net.HttpError(400, "invalid history"), text_resp("ok")]
        a.handle("two", threading.Event())
        self.assertEqual(self.emitted[-1], ("text", "ok"))
        self.assertEqual(len(self.client.calls[-1]), 1)

    def test_cancel_closes_tool_calls(self):
        cancel = threading.Event()

        class CancellingRunner(object):
            folders = self.runner.folders

            def run(self, name, args, c):
                cancel.set()
                return "done", False
        self.emitted = []
        client = FakeClient([llm.LLMResponse([
            {"type": "tool_use", "id": "a", "name": "list_folder", "input": {}},
            {"type": "tool_use", "id": "b", "name": "list_folder", "input": {}}], "tool_use")])
        a = agent.Agent(make_cfg(), client, CancellingRunner(), lambda k, v: self.emitted.append((k, v)))
        a.handle("go", cancel)
        self.assertEqual(self.emitted[-1], ("notice", S.STOPPED))
        ids = [r["tool_use_id"] for r in a.messages[-1]["content"]]
        self.assertEqual(ids, ["a", "b"])
        self.assertIn("Stop", a.messages[-1]["content"][1]["content"])

    def test_system_prompt_mentions_ps2(self):
        a = self.make_agent([])
        self.assertIn("PowerShell is version 2.0", a.system)
        self.assertIn(self.home, a.system)


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.cfg = make_cfg()
        self.cfg["email_reports"].update({"send_to": "to@example.com", "smtp_username": "from@example.com",
                                          "smtp_app_password": "abcd efgh ijkl mnop"})
        logs.set_secrets(config.secrets(self.cfg))
        self.outbox = tempfile.mkdtemp()
        self.patch = mock.patch.object(paths, "OUTBOX_DIR", self.outbox)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        shutil.rmtree(self.outbox, ignore_errors=True)

    def test_redacted_and_queued_when_offline(self):
        r = report.Reporter(self.cfg)
        sent = []

        def fail(msg):
            raise OSError("offline")
        with mock.patch.object(r, "_smtp_send", fail):
            self.assertFalse(r.send_now("Test", "the key is sk-ant-test-1234567890"))
        self.assertEqual(len(os.listdir(self.outbox)), 1)
        with mock.patch.object(r, "_smtp_send", lambda msg: sent.append(msg)):
            r.flush_outbox()
        self.assertEqual(len(sent), 1)
        self.assertEqual(os.listdir(self.outbox), [])
        body = sent[0].get_body(("plain",)).get_content()
        self.assertNotIn("sk-ant-test-1234567890", body)
        self.assertIn("[REDACTED]", body)

    def test_error_reports_are_throttled(self):
        r = report.Reporter(self.cfg)
        calls = []
        with mock.patch.object(r, "send_async", lambda *a, **k: calls.append(a)):
            r.report_error("one")
            r.report_error("two")
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
