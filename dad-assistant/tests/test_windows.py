"""Windows-only tests (run with the 32-bit Python 3.8 on Windows 7, or under Wine)."""

import ctypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP_DIR)

from helper import shell, winapi  # noqa: E402

ARABIC = "\u0635\u0648\u0631 \u0627\u0644\u0631\u062d\u0644\u0629"  # "trip photos"


@unittest.skipUnless(sys.platform == "win32", "Windows only")
class WindowsApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="helper win ")  # space in the path on purpose

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_windows_version_and_bitness(self):
        v = sys.getwindowsversion()
        print("\n  Windows %d.%d build %d %s, %d-bit Python %s" % (
            v.major, v.minor, v.build, v.service_pack, ctypes.sizeof(ctypes.c_void_p) * 8, sys.version.split()[0]))
        self.assertGreaterEqual((v.major, v.minor), (6, 1))

    def test_shfileop_struct_layout(self):
        size = ctypes.sizeof(winapi._SHFILEOPSTRUCTW)
        # shellapi.h: packed to 1 byte on 32-bit (30 bytes); 8-byte aligned on 64-bit (56 bytes).
        self.assertEqual(size, 30 if ctypes.sizeof(ctypes.c_void_p) == 4 else 56)

    def test_recycle_files_and_folders_with_arabic_names(self):
        f = os.path.join(self.tmp, ARABIC + ".txt")
        with open(f, "w", encoding="utf-8") as fh:
            fh.write("x")
        folder = os.path.join(self.tmp, ARABIC + " folder")
        os.makedirs(os.path.join(folder, "sub"))
        open(os.path.join(folder, "sub", "a.jpg"), "w").close()
        self.assertIsNone(winapi.recycle(f))
        self.assertIsNone(winapi.recycle(folder))
        self.assertFalse(os.path.exists(f))
        self.assertFalse(os.path.exists(folder))
        self.assertEqual(winapi.recycle(os.path.join(self.tmp, "missing.txt")), "it does not exist")

    def test_known_folders_and_drives(self):
        folders = winapi.known_folders()
        for name in ("Desktop", "Documents", "Pictures", "Home"):
            self.assertTrue(os.path.isabs(folders[name]), folders)
        self.assertTrue(os.path.isdir(folders["Desktop"]), folders["Desktop"])
        kinds = dict(winapi.drives())
        self.assertEqual(kinds.get("C:\\"), "fixed")
        self.assertEqual(winapi.drive_kind(self.tmp), "fixed")
        self.assertEqual(winapi.drive_kind("\\\\server\\share\\x"), "network")
        self.assertTrue(any(d.lower().endswith("windows") for d in winapi.system_dirs()))

    def test_single_instance(self):
        name = "HelperTest%d" % os.getpid()
        self.assertTrue(winapi.single_instance(name))
        self.assertIsNone(winapi.single_instance(name))

    def test_oem_encoding(self):
        import codecs
        codecs.lookup(winapi.oem_encoding())


@unittest.skipUnless(sys.platform == "win32", "Windows only")
class CmdTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="helper cmd ")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_unicode_file_names_survive(self):
        open(os.path.join(self.tmp, ARABIC + ".jpg"), "w").close()
        r = shell.run("cmd", 'dir /b "%s"' % self.tmp, timeout=30)
        self.assertEqual(r.exit_code, 0, r.output)
        self.assertIn(ARABIC + ".jpg", r.output)

    def test_quotes_spaces_and_exit_code(self):
        r = shell.run("cmd", 'if exist "%s" (echo found it) else (echo missing)' % self.tmp, timeout=30)
        self.assertIn("found it", r.output)
        r = shell.run("cmd", "exit /b 3", timeout=30)
        self.assertEqual(r.exit_code, 3)

    def test_large_output_does_not_deadlock(self):
        start = time.time()
        r = shell.run("cmd", "for /L %i in (1,1,20000) do @echo line number %i", timeout=120)
        self.assertFalse(r.timed_out)
        self.assertIn("line number 20000", r.output)
        self.assertIn("characters cut", r.describe())
        print("\n  20000 lines in %.1fs" % (time.time() - start))

    def test_hanging_command_is_killed(self):
        start = time.time()
        r = shell.run("cmd", "ping -n 120 127.0.0.1", timeout=3)
        self.assertTrue(r.timed_out)
        self.assertLess(time.time() - start, 30)

    def test_waiting_for_input_does_not_hang(self):
        start = time.time()
        r = shell.run("cmd", "set /p answer=Type something: & echo got [%answer%]", timeout=20)
        self.assertFalse(r.timed_out, "command waited for keyboard input")
        self.assertLess(time.time() - start, 20)

    def test_stop_button(self):
        cancel = threading.Event()
        threading.Timer(1.0, cancel.set).start()
        start = time.time()
        r = shell.run("cmd", "ping -n 120 127.0.0.1", timeout=120, cancel=cancel)
        self.assertTrue(r.cancelled)
        self.assertLess(time.time() - start, 30)

    def test_no_temp_files_left_behind(self):
        from helper import paths
        before = set(os.listdir(paths.TEMP_DIR))
        shell.run("cmd", "echo hi", timeout=30)
        shell.run("powershell", "Get-Date", timeout=60)
        self.assertEqual(set(os.listdir(paths.TEMP_DIR)) - before, set())


@unittest.skipUnless(sys.platform == "win32", "Windows only")
class PowerShellTests(unittest.TestCase):
    def test_powershell_missing_or_broken_never_crashes_or_hangs(self):
        start = time.time()
        r = shell.run("powershell", "Get-ChildItem C:\\ | Select-Object -First 3 Name", timeout=60)
        self.assertLess(time.time() - start, 70)
        self.assertIsInstance(r.describe(), str)
        print("\n  powershell.exe = %s\n  output: %r" % (shell._powershell_exe(), r.output[:200]))


@unittest.skipUnless(sys.platform == "win32", "Windows only")
class PythonwTests(unittest.TestCase):
    """The app runs under pythonw.exe, where sys.stdout/stderr are None and there is no console."""

    def test_app_internals_work_under_pythonw(self):
        pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        self.assertTrue(os.path.exists(pythonw))
        tmp = tempfile.mkdtemp(prefix="helper pyw ")
        try:
            result_file = os.path.join(tmp, "result.json")
            script = os.path.join(tmp, "probe.pyw")
            with open(script, "w", encoding="utf-8") as f:
                f.write("\n".join([
                    "import json, sys, logging",
                    "sys.path.insert(0, %r)" % APP_DIR,
                    "out = {'stdout_is_none': sys.stdout is None}",
                    "from helper import logs, shell",
                    "logs.setup()",
                    "print('this must not crash under pythonw')",
                    "sys.stderr.write('neither must this')",
                    "r = shell.run('cmd', 'echo hello from cmd', timeout=30)",
                    "out['cmd'] = r.output.strip()",
                    "out['exit'] = r.exit_code",
                    "json.dump(out, open(%r, 'w'))" % result_file,
                ]))
            code = subprocess.call([pythonw, script], timeout=120)
            self.assertEqual(code, 0)
            with open(result_file) as f:
                out = json.load(f)
            self.assertTrue(out["stdout_is_none"])
            self.assertEqual(out["cmd"], "hello from cmd")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_app_in_folder_with_arabic_name_and_spaces(self):
        tmp = tempfile.mkdtemp()
        try:
            target = os.path.join(tmp, ARABIC + " helper app")
            shutil.copytree(os.path.join(APP_DIR, "helper"), os.path.join(target, "helper"))
            probe = ("import sys; sys.path.insert(0, %r)\n"
                     "from helper import paths, shell, logs, tools, config\n"
                     "logs.setup(); import logging; logging.getLogger('t').info('hello')\n"
                     "assert paths.DATA_DIR.startswith(%r), paths.DATA_DIR\n"
                     "r = shell.run('cmd', 'echo ok', timeout=30); assert r.output.strip() == 'ok', r.output\n"
                     "r = shell.run('powershell', 'Write-Output ok', timeout=60)\n"
                     "print('PROBE-OK')\n") % (target, target)
            out = subprocess.run([sys.executable, "-c", probe], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, timeout=180)
            text = out.stdout.decode("utf-8", "replace")
            self.assertIn("PROBE-OK", text, text)
            self.assertTrue(os.path.exists(os.path.join(target, "data", "logs", "assistant.log")))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
