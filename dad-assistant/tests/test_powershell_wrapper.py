"""Runs the real PowerShell wrapper used by the app against a PowerShell executable.

On Windows 7 this is powershell.exe 2.0.  Elsewhere set PWSH=/path/to/pwsh to check the wrapper's
behaviour (output capture, errors, syntax errors, Unicode, exit codes) with PowerShell 7; that does not
prove PowerShell 2.0 compatibility, which is covered by only using 2.0-era syntax in the wrapper.
"""

import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from helper import paths, shell  # noqa: E402

PWSH = os.environ.get("PWSH")
ARABIC = "\u0635\u0648\u0631 \u0627\u0644\u0631\u062d\u0644\u0629"


def real_powershell_available():
    if PWSH:
        return os.path.exists(PWSH)
    if sys.platform != "win32":
        return False
    # Wine ships an empty powershell.exe stub; only test with a working one.
    result = shell.run_powershell("Write-Output ('ps' + 'ok')", timeout=60)
    return "psok" in result.output


@unittest.skipUnless(real_powershell_available(), "needs a real PowerShell (set PWSH=... outside Windows)")
class PowerShellWrapperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="Dad's ps ")  # apostrophe tests the wrapper's quoting
        self.patches = [mock.patch.object(paths, "TEMP_DIR", self.tmp)]
        if PWSH:
            self.patches.append(mock.patch.object(shell, "_powershell_exe", lambda: PWSH))
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_ps(self, code, timeout=60):
        return shell.run_powershell(code, timeout=timeout)

    def test_output_is_captured(self):
        r = self.run_ps("Write-Output 'hello'\n1..3 | ForEach-Object { $_ * 2 }")
        self.assertEqual(r.output.split(), ["hello", "2", "4", "6"])
        self.assertEqual(os.listdir(self.tmp), [])  # temp files cleaned up

    def test_non_terminating_error_keeps_going(self):
        r = self.run_ps("Get-Item -LiteralPath 'no-such-file.txt'\nWrite-Output 'after'")
        self.assertIn("after", r.output)
        self.assertIn("no-such-file.txt", r.output)

    def test_terminating_error_is_reported(self):
        r = self.run_ps("Write-Output 'before'\nthrow 'boom'")
        self.assertIn("before", r.output)
        self.assertIn("ERROR", r.output)
        self.assertIn("boom", r.output)

    def test_syntax_error_is_reported_not_crashed(self):
        r = self.run_ps("Write-Output (")
        self.assertIn("ERROR", r.output)

    def test_unicode_output(self):
        r = self.run_ps("Write-Output '%s'" % ARABIC)
        self.assertEqual(r.output.strip(), ARABIC)

    def test_external_exit_code(self):
        cmd = "cmd /c exit 3" if sys.platform == "win32" else "sh -c 'exit 3'"
        r = self.run_ps(cmd)
        self.assertEqual(r.exit_code, 3)

    def test_read_host_does_not_hang(self):
        r = self.run_ps("$x = Read-Host 'Name'\nWrite-Output \"got [$x]\"", timeout=30)
        self.assertFalse(r.timed_out)


if __name__ == "__main__":
    unittest.main()
