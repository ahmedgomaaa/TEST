"""install_helper.py: the steps HelperSetup.exe runs.  Each test uses a copy of the app in a temp folder."""

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class InstallHelperTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        shutil.copytree(os.path.join(APP_DIR, "helper"), os.path.join(self.dir, "helper"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        for name in ("install_helper.py", "config.example.json"):
            shutil.copy(os.path.join(APP_DIR, name), self.dir)
        self.config = os.path.join(self.dir, "config.json")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def run_helper(self, *args):
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        return subprocess.run([sys.executable, "-E", "-s", os.path.join(self.dir, "install_helper.py")] + list(args),
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, timeout=120)

    def settings_file(self, **values):
        path = os.path.join(self.dir, "settings.txt")
        keys = ("user_name", "api_key", "send_to", "smtp_username", "smtp_app_password")
        with io.open(path, "w", encoding="utf-16") as f:  # what NSIS writes: UTF-16-LE with a BOM
            f.write("".join("%s=%s\r\n" % (k, values.get(k, "")) for k in keys))
        return path

    def load(self):
        with io.open(self.config, "r", encoding="utf-8-sig") as f:
            return json.load(f)

    def test_new_config_from_installer_page(self):
        name = "محمد"
        out = self.run_helper("write-config", self.settings_file(
            user_name=name, api_key="sk-ant-key-123456", send_to="ahmed@example.com",
            smtp_app_password="abcd efgh ijkl mnop"))
        self.assertEqual(out.returncode, 0, out.stdout)
        cfg = self.load()
        self.assertEqual(cfg["user_name"], name)
        self.assertEqual(cfg["anthropic"]["api_key"], "sk-ant-key-123456")
        self.assertEqual(cfg["email_reports"]["send_to"], "ahmed@example.com")
        self.assertEqual(cfg["email_reports"]["smtp_username"], "ahmed@example.com")  # sends from his own Gmail
        self.assertEqual(cfg["email_reports"]["smtp_app_password"], "abcdefghijklmnop")  # spaces removed
        self.assertFalse(cfg["arabic_word_order_fix"])
        with open(self.config, "rb") as f:
            raw = f.read()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"), "Notepad-friendly BOM")
        self.assertIn(b"\r\n", raw)
        self.assertNotIn(b"sk-ant-key-123456", out.stdout, "secrets are not printed to the installer")

    def test_empty_boxes_keep_old_settings(self):
        self.run_helper("write-config", self.settings_file(user_name="Dad", api_key="sk-ant-old-key-1",
                                                            send_to="a@example.com"))
        out = self.run_helper("write-config", self.settings_file(api_key="sk-ant-new-key-2"))
        self.assertEqual(out.returncode, 0, out.stdout)
        cfg = self.load()
        self.assertEqual(cfg["user_name"], "Dad")
        self.assertEqual(cfg["anthropic"]["api_key"], "sk-ant-new-key-2")
        self.assertEqual(cfg["email_reports"]["send_to"], "a@example.com")

    def test_broken_config_is_kept_aside_and_replaced(self):
        with open(self.config, "w") as f:
            f.write("{ this is not json")
        out = self.run_helper("write-config", self.settings_file(api_key="sk-ant-key-123456"))
        self.assertEqual(out.returncode, 0, out.stdout)
        self.assertTrue(os.path.exists(self.config + ".broken"))
        self.assertEqual(self.load()["anthropic"]["api_key"], "sk-ant-key-123456")

    def test_test_command_reports_in_arabic(self):
        self.run_helper("write-config", self.settings_file())
        result = os.path.join(self.dir, "result.txt")
        out = self.run_helper("test", result)
        self.assertEqual(out.returncode, 1, out.stdout)  # AI_FAILED: no key
        with io.open(result, "r", encoding="utf-16-le") as f:
            lines = [line for line in f.read().splitlines() if line]
        self.assertEqual(len(lines), 2)
        self.assertIn("Claude API", lines[0])
        self.assertTrue(all("؀" <= ch <= "ۿ" for ch in lines[1] if ch.isalpha()), lines[1])

    def test_email_errors_are_explained(self):
        import smtplib
        import socket
        from unittest import mock
        sys.path.insert(0, APP_DIR)
        import install_helper
        from helper import config, report
        cfg, _ = config.load(os.path.join(self.dir, "missing.json"))
        cfg["email_reports"].update(send_to="a@example.com", smtp_username="a@example.com",
                                    smtp_app_password="abcdefghijklmnop")
        cases = [(socket.timeout("timed out"), "mail_offline"),
                 (ConnectionRefusedError(10061, "refused"), "mail_offline"),
                 (smtplib.SMTPAuthenticationError(535, b"Username and Password not accepted"), "mail_auth"),
                 (smtplib.SMTPException("something else"), None)]
        for error, key in cases:
            with mock.patch.object(report.Reporter, "_smtp_send", side_effect=error):
                ok, message = install_helper.test_email(cfg)
            self.assertFalse(ok)
            if key:
                self.assertEqual(message, install_helper.AR[key], error)
            else:
                self.assertIn("something else", message)

    def test_unknown_command(self):
        self.assertEqual(self.run_helper("nonsense").returncode, 2)


if __name__ == "__main__":
    unittest.main()
