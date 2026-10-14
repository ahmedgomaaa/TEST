"""Running PowerShell 2.0 and cmd.exe commands safely on Windows 7.

Windows 7 problems this module handles:
- Script execution policy is "Restricted" by default, so .ps1 files will not
  run.  We pass a tiny wrapper with -EncodedCommand (not subject to the policy)
  and run the real code with [ScriptBlock]::Create, which also catches syntax
  errors instead of crashing.
- Console output uses the OEM code page (cp437, cp720 for Arabic, ...), which
  garbles non-English file names.  The PowerShell wrapper writes its output to a
  UTF-8 file with .NET; cmd runs with /u so built-in commands print UTF-16.
- Commands that wait for keyboard input would hang forever: stdin is empty,
  PowerShell runs -NonInteractive, and every command has a time limit after
  which the whole process tree is killed with taskkill /T.
- No console window flashes up (CREATE_NO_WINDOW).
- Output goes to temporary files, not pipes, so large output can never deadlock.
"""

import base64
import logging
import os
import re
import subprocess
import time
import uuid

from . import paths, winapi

log = logging.getLogger("assistant.shell")

MAX_OUTPUT_CHARS = 8000
MAX_COMMAND_CHARS = 6000

# Things the assistant may never do through a command.  Deleting is blocked so
# that everything goes through the Recycle Bin tool instead.
_BLOCKED = [
    (r"\bformat(\.com)?\s+[a-z]:", "formatting drives"),
    (r"\bdiskpart\b", "changing disk partitions"),
    (r"\b(bcdedit|bootrec|bootsect)\b", "changing how Windows starts"),
    (r"\bcipher(\.exe)?\s+/w", "wiping disk space"),
    (r"\b(vssadmin|wbadmin)\b", "changing backups or restore points"),
    (r"\breg(\.exe)?\s+(delete|add|import|load|unload|restore)\b", "changing the registry"),
    (r"\b(remove-itemproperty|set-itemproperty|new-itemproperty)\b", "changing the registry"),
    (r"\bset-executionpolicy\b", "changing security settings"),
    (r"\bnetsh\b.*\b(firewall|advfirewall)\b", "changing the firewall"),
    (r"\b(net\s+user|net\s+localgroup)\b", "changing user accounts"),
    (r"\bsc(\.exe)?\s+(delete|config|create)\b", "changing system services"),
    (r"\b(takeown|icacls|cacls)\b", "changing file permissions"),
    (r"\b(del|erase)\b\s", "deleting files (use the Recycle Bin instead)"),
    (r"\b(rd|rmdir)\b\s", "deleting folders (use the Recycle Bin instead)"),
    (r"\b(remove-item|ri)\b", "deleting files (use the Recycle Bin instead)"),
    (r"(^|[;|&(\s])(rm|rmdir|del|erase)\s+-", "deleting files (use the Recycle Bin instead)"),
    (r"\bclear-content\b", "erasing file contents"),
    (r"\b(bitsadmin|certutil)\b", "downloading files"),
    (r"\bnet\.webclient\b|\bdownloadfile\b|\bdownloadstring\b|\bstart-bitstransfer\b", "downloading files"),
    (r"\b(msiexec|wusa)\b", "installing or removing software"),
    (r"\b(schtasks|at)\s+/(create|delete|change)\b", "changing scheduled tasks"),
    (r"\bwmic\b.*\b(delete|call\s+uninstall)\b", "removing software"),
    (r"\bstop-computer\b|\brestart-computer\b|\bshutdown(\.exe)?\s", "turning off the computer"),
]
_BLOCKED = [(re.compile(p, re.IGNORECASE), why) for p, why in _BLOCKED]

# Words that suggest a command changes something (so we ask first).
_CHANGING = re.compile(
    r"\b(move|mv|ren|rename|copy|cp|xcopy|robocopy|md|mkdir|new-item|ni|move-item|mi|rename-item|rni|"
    r"copy-item|cpi|set-content|add-content|out-file|set-item|stop-process|kill|taskkill|attrib|"
    r"start-process|saps|invoke-item|ii|compact|expand|tar|set-date)\b|>",
    re.IGNORECASE,
)


def blocked_reason(command):
    for pattern, why in _BLOCKED:
        if pattern.search(command):
            return why
    return None


def looks_like_change(command):
    return bool(_CHANGING.search(command))


def _decode_console(data):
    """Decode console bytes: UTF-16 from `cmd /u`, otherwise the OEM code page."""
    if not data:
        return ""
    if data.startswith(b"\xff\xfe"):
        return data[2:].decode("utf-16-le", "replace")
    if len(data) >= 4 and data[1::2].count(0) > len(data) // 4:
        return data[: len(data) // 2 * 2].decode("utf-16-le", "replace")
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", "replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode(winapi.oem_encoding(), "replace")


def _read(path, limit=400 * 1024):
    try:
        with open(path, "rb") as f:
            return f.read(limit)
    except OSError:
        return b""


def _remove(*files):
    for name in files:
        try:
            os.remove(name)
        except OSError:
            pass


def shorten(text, limit=MAX_OUTPUT_CHARS):
    text = (text or "").replace("\r\n", "\n").strip()
    if len(text) <= limit:
        return text
    head = text[: limit * 2 // 3]
    tail = text[-limit // 3:]
    return "%s\n\n[... %d characters cut ...]\n\n%s" % (head, len(text) - len(head) - len(tail), tail)


def _powershell_exe():
    root = os.environ.get("SystemRoot", r"C:\Windows")
    for sub in ("System32", "Sysnative", "SysWOW64"):
        exe = os.path.join(root, sub, "WindowsPowerShell", "v1.0", "powershell.exe")
        if os.path.exists(exe):
            return exe
    return "powershell.exe"


def _ps_quote(text):
    return "'" + text.replace("'", "''") + "'"


def _wrapper(code_path, out_path):
    # Only syntax that exists in PowerShell 2.0.
    return "\n".join([
        "$ErrorActionPreference = 'Continue'",
        "$ProgressPreference = 'SilentlyContinue'",
        "$__out = " + _ps_quote(out_path),
        "$__text = ''",
        "try {",
        "  $__code = [System.IO.File]::ReadAllText(" + _ps_quote(code_path) + ", [System.Text.Encoding]::UTF8)",
        "  $__sb = [ScriptBlock]::Create($__code)",
        "  $__text = (& $__sb 2>&1 | Out-String -Width 220)",
        "} catch {",
        "  $__text = $__text + \"`nERROR: \" + ($_ | Out-String)",
        "}",
        "[System.IO.File]::WriteAllText($__out, [string]$__text, (New-Object System.Text.UTF8Encoding $false))",
        "if ($LASTEXITCODE) { exit $LASTEXITCODE }",
    ])


class CommandResult(object):
    def __init__(self, exit_code, output, timed_out=False, cancelled=False):
        self.exit_code = exit_code
        self.output = output
        self.timed_out = timed_out
        self.cancelled = cancelled

    def describe(self):
        parts = []
        if self.cancelled:
            parts.append("The command was stopped because the user pressed Stop.")
        elif self.timed_out:
            parts.append("The command took too long and was stopped.")
        if self.exit_code not in (0, None) and not (self.cancelled or self.timed_out):
            parts.append("Exit code: %s" % self.exit_code)
        parts.append("Output:\n" + (shorten(self.output) or "(no output)"))
        return "\n".join(parts)


def _wait(proc, timeout, cancel):
    deadline = time.time() + timeout
    while True:
        if proc.poll() is not None:
            return "done"
        if cancel is not None and cancel.is_set():
            return "cancelled"
        if time.time() > deadline:
            return "timeout"
        time.sleep(0.2)


def _run(args, timeout, cancel, cwd=None):
    """Run a process with output to temp files.  Returns (exit_code, stdout+stderr bytes, state)."""
    paths.ensure_dirs()
    token = uuid.uuid4().hex
    out_file = os.path.join(paths.TEMP_DIR, token + ".stdout")
    err_file = os.path.join(paths.TEMP_DIR, token + ".stderr")
    try:
        with open(out_file, "wb") as out, open(err_file, "wb") as err:
            proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                    cwd=cwd or winapi.home_dir(), **winapi.subprocess_flags())
            state = _wait(proc, timeout, cancel)
            if state != "done":
                winapi.kill_tree(proc.pid)
                try:
                    proc.kill()
                except OSError:
                    pass
                try:
                    proc.wait(10)
                except subprocess.TimeoutExpired:
                    pass
        return proc.returncode, _read(out_file) + _read(err_file), state
    finally:
        _remove(out_file, err_file)


def run_powershell(code, timeout=120, cancel=None):
    paths.ensure_dirs()
    token = uuid.uuid4().hex
    code_path = os.path.join(paths.TEMP_DIR, token + ".code.txt")
    out_path = os.path.join(paths.TEMP_DIR, token + ".out.txt")
    try:
        with open(code_path, "w", encoding="utf-8-sig") as f:
            f.write(code)
        encoded = base64.b64encode(_wrapper(code_path, out_path).encode("utf-16-le")).decode("ascii")
        args = [_powershell_exe(), "-NoLogo", "-NoProfile", "-NonInteractive",
                "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded]
        exit_code, console, state = _run(args, timeout, cancel)
        output = _read(out_path).decode("utf-8", "replace")
        extra = _decode_console(console).strip()
        if extra:
            output = (output + "\n" + extra).strip()
        return CommandResult(exit_code, output, state == "timeout", state == "cancelled")
    finally:
        _remove(code_path, out_path)


def run_cmd(command, timeout=120, cancel=None):
    # /d skips AutoRun, /u makes built-in commands write UTF-16, /s /c keeps quotes intact.
    comspec = os.environ.get("ComSpec") or "cmd.exe"
    args = '"%s" /d /u /s /c "%s"' % (comspec, command)
    exit_code, console, state = _run(args, timeout, cancel)
    return CommandResult(exit_code, _decode_console(console), state == "timeout", state == "cancelled")


def run(shell, command, timeout=120, cancel=None):
    if not winapi.IS_WINDOWS:
        # Development fallback so the rest of the app can be exercised on Linux.
        exit_code, console, state = _run(["/bin/sh", "-c", command], timeout, cancel)
        return CommandResult(exit_code, console.decode("utf-8", "replace"), state == "timeout", state == "cancelled")
    if shell == "cmd":
        return run_cmd(command, timeout, cancel)
    return run_powershell(command, timeout, cancel)
