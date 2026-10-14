"""Tests for "Start Assistant.pyw" (the watchdog launcher), with real Windows crashes.

Windows only.  Each test copies the launcher into a temporary folder next to a stand-in
`helper` package whose __main__ crashes in a controlled way.
"""

import ctypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fakes  # noqa: E402

sys.path.insert(0, APP_DIR)
from helper import strings  # noqa: E402

CRASH_ONCE = '''
import ctypes, os, sys
from . import paths, winapi
winapi.disable_crash_dialogs()  # like the real app
here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
open(paths.RUNNING_FLAG, "w").close()  # like the real app
marker = os.path.join(here, "crashed-once")
if not os.path.exists(marker):
    open(marker, "w").close()
    ctypes.string_at(0)  # access violation: a real native crash
open(os.path.join(here, "second-run-ok"), "w").close()
os.remove(paths.RUNNING_FLAG)  # clean exit
sys.exit(0)
'''

ALWAYS_CRASH = '''
import os, sys
here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(here, "runs.txt"), "a") as f:
    f.write("run\\n")
raise RuntimeError("simulated start-up failure")
'''


@unittest.skipUnless(sys.platform == "win32", "Windows only")
class LauncherTests(unittest.TestCase):
    def make_install(self, main_source):
        root = tempfile.mkdtemp(prefix="launcher test ")
        self.addCleanup(shutil.rmtree, root, True)
        shutil.copy(os.path.join(APP_DIR, "Start Assistant.pyw"), root)
        shutil.copytree(os.path.join(APP_DIR, "helper"), os.path.join(root, "helper"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        with open(os.path.join(root, "helper", "__main__.py"), "w") as f:
            f.write(main_source)
        return root

    def launch(self, root):
        pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        return subprocess.Popen([pythonw, os.path.join(root, "Start Assistant.pyw")], cwd=root)

    def test_restarts_after_native_crash(self):
        root = self.make_install(CRASH_ONCE)
        proc = self.launch(root)
        proc.wait(120)
        self.assertTrue(os.path.exists(os.path.join(root, "crashed-once")))
        self.assertTrue(os.path.exists(os.path.join(root, "second-run-ok")), "launcher did not restart the app")
        with open(os.path.join(root, "data", "logs", "assistant.log"), encoding="utf-8") as f:
            log = f.read()
        self.assertIn("restarting", log)

    def test_gives_up_after_three_crashes_and_emails(self):
        root = self.make_install(ALWAYS_CRASH)
        smtp = fakes.FakeSMTP()
        self.addCleanup(smtp.close)
        with open(os.path.join(APP_DIR, "config.example.json"), encoding="utf-8") as f:
            cfg = json.load(f)
        cfg["anthropic"]["api_key"] = "sk-ant-test"
        cfg["email_reports"].update({"send_to": "ahmed@example.com", "smtp_username": "from@example.com",
                                     "smtp_app_password": "abcdefghijklmnop", "smtp_host": "localhost",
                                     "smtp_port": smtp.port})
        with open(os.path.join(root, "config.json"), "w", encoding="utf-8-sig") as f:
            json.dump(cfg, f)
        # The test CA must be trusted by this copy only.
        with open(os.path.join(root, "helper", "cacert.pem"), "ab") as out, open(fakes.TEST_CA, "rb") as ca:
            out.write(b"\n" + ca.read())
        proc = self.launch(root)
        # It should show a friendly message box; close it the way a user would.
        user32 = ctypes.windll.user32
        deadline = time.time() + 120
        hwnd = None
        while time.time() < deadline and proc.poll() is None:
            hwnd = user32.FindWindowW(None, strings.DEFAULT_ASSISTANT_NAME)
            if hwnd:
                break
            time.sleep(0.5)
        self.assertTrue(hwnd, "no message box was shown")
        user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
        proc.wait(60)
        with open(os.path.join(root, "runs.txt")) as f:
            self.assertEqual(len(f.read().split()), 3)
        subjects = [m["data"] for m in smtp.messages]
        self.assertTrue(any(b"cannot start" in s for s in subjects), subjects)

    def test_second_launch_says_already_open(self):
        root = self.make_install("import time\ntime.sleep(40)\n")
        first = self.launch(root)
        self.addCleanup(first.kill)
        time.sleep(4)
        second = self.launch(root)
        user32 = ctypes.windll.user32
        deadline = time.time() + 60
        hwnd = None
        while time.time() < deadline and second.poll() is None:
            hwnd = user32.FindWindowW(None, strings.DEFAULT_ASSISTANT_NAME)
            if hwnd:
                break
            time.sleep(0.5)
        self.assertTrue(hwnd, "second launch did not say it is already open")
        user32.PostMessageW(hwnd, 0x0010, 0, 0)
        second.wait(30)
        self.assertIsNone(first.poll())  # the first one is still running


if __name__ == "__main__":
    unittest.main()
