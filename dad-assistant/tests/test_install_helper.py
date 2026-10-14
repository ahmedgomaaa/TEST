"""install_helper.py: the steps HelperSetup.exe runs.  Each test uses a copy of the app in a temp folder."""

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

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

    def test_deepseek_key_is_detected(self):
        out = self.run_helper("write-config", self.settings_file(api_key="sk-0123456789abcdef0123456789abcdef"))
        self.assertEqual(out.returncode, 0, out.stdout)
        cfg = self.load()
        self.assertEqual(cfg["provider"], "openai_compatible")
        self.assertEqual(cfg["openai_compatible"]["api_key"], "sk-0123456789abcdef0123456789abcdef")
        self.assertEqual(cfg["openai_compatible"]["base_url"], "https://api.deepseek.com/v1")
        self.assertIn(b"DeepSeek", out.stdout)
        # Switching back to Claude on a reinstall.
        self.run_helper("write-config", self.settings_file(api_key="sk-ant-key-123456"))
        cfg = self.load()
        self.assertEqual(cfg["provider"], "anthropic")
        self.assertEqual(cfg["anthropic"]["api_key"], "sk-ant-key-123456")

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
        self.assertIn("DeepSeek", lines[0])
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

    def ps_install(self, version, release, net_code=0, wusa_code=3010, fail_download=None):
        sys.path.insert(0, APP_DIR)
        import install_helper as ih
        runs, result = [], os.path.join(self.dir, "ps.txt")
        msu_zip = os.path.join(self.dir, "wmf.zip")
        import zipfile
        with zipfile.ZipFile(msu_zip, "w") as z:
            z.writestr("Win7-KB3191566-x86.msu", b"msu")
            z.writestr("Install-WMF5.1.ps1", b"ps1")

        def reg(path, name):
            return version if name == "PowerShellVersion" else release

        def download(name, url, sha256, folder):
            if fail_download and fail_download in name:
                raise ValueError("offline")
            return msu_zip if name.endswith(".zip") else os.path.join(self.dir, name)

        def run(args, what):
            runs.append(what)
            return net_code if ".NET" in what else wusa_code
        with mock.patch.object(ih, "_reg_value", reg), mock.patch.object(ih, "download_checked", download), \
                mock.patch.object(ih, "_run", run), mock.patch.object(ih, "_system_exe", lambda n: n), \
                mock.patch.object(ih, "install_log", lambda t: None), mock.patch.object(ih, "say", lambda t: None):
            code = ih.cmd_install_powershell([result, self.dir])
        with io.open(result, encoding="utf-16-le") as f:
            return code, runs, f.read()

    def test_powershell_already_there(self):
        code, runs, text = self.ps_install("5.1.14409.1005", 528040)
        self.assertEqual((code, runs), (0, []))
        self.assertIn("5.1.14409.1005", text)

    def test_powershell_install_with_and_without_dotnet(self):
        code, runs, text = self.ps_install("2.0", 0)
        self.assertEqual(code, 10)  # restart needed
        self.assertEqual(runs, [".NET Framework 4.8", "WMF 5.1 (KB3191566)"])
        code, runs, _text = self.ps_install("2.0", 528040)
        self.assertEqual(runs, ["WMF 5.1 (KB3191566)"], ".NET 4.8 is not installed twice")

    def test_powershell_failures_are_explained(self):
        code, runs, text = self.ps_install("2.0", 0, fail_download=".exe")
        self.assertEqual((code, runs), (1, []))
        self.assertIn(".NET 4.8", text)
        code, runs, text = self.ps_install("2.0", 528040, wusa_code=0x80240017)
        self.assertEqual(code, 1)
        self.assertIn("Service Pack 1", text)
        code, runs, text = self.ps_install("2.0", 0, net_code=1603)
        self.assertEqual((code, runs), (1, [".NET Framework 4.8"]))
        self.assertIn("1603", text)

    def test_download_is_checked(self):
        import hashlib
        sys.path.insert(0, APP_DIR)
        import install_helper as ih
        payload = b"x" * 1000

        class Resp(io.BytesIO):
            headers = {"Content-Length": str(len(payload))}

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        class Opener(object):
            def open(self, req, timeout=None):
                return Resp(payload)
        with mock.patch("urllib.request.build_opener", lambda *a: Opener()), \
                mock.patch.object(ih, "say", lambda t: None):
            good = ih.download_checked("f.bin", "https://x/f.bin", hashlib.sha256(payload).hexdigest(), self.dir)
            self.assertTrue(os.path.isfile(good))
            with self.assertRaises(ValueError):
                ih.download_checked("g.bin", "https://x/g.bin", "0" * 64, self.dir)
            self.assertFalse(os.path.exists(os.path.join(self.dir, "g.bin")))

    def test_unknown_command(self):
        self.assertEqual(self.run_helper("nonsense").returncode, 2)


if __name__ == "__main__":
    unittest.main()
