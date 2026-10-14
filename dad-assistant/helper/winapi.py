"""Small Windows helpers via ctypes (no pywin32 needed).

Every function has a harmless fallback on other systems so the rest of the app
can be tested on Linux.
"""

import codecs
import logging
import os
import shutil
import subprocess
import sys
import time

log = logging.getLogger("assistant.winapi")

IS_WINDOWS = sys.platform == "win32"
CREATE_NO_WINDOW = 0x08000000

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    _shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _user32 = ctypes.WinDLL("user32", use_last_error=True)

    class _SHFILEOPSTRUCTW(ctypes.Structure):
        # shellapi.h packs this struct to 1 byte on 32-bit Windows.
        _pack_ = 1 if ctypes.sizeof(ctypes.c_void_p) == 4 else 8
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("wFunc", wintypes.UINT),
            ("pFrom", ctypes.c_void_p),
            ("pTo", ctypes.c_void_p),
            ("fFlags", ctypes.c_ushort),
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle", wintypes.LPCWSTR),
        ]


def subprocess_flags():
    """Keyword arguments that stop a console window from flashing up."""
    if not IS_WINDOWS:
        return {}
    info = subprocess.STARTUPINFO()
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    info.wShowWindow = 0  # SW_HIDE
    return {"creationflags": CREATE_NO_WINDOW, "startupinfo": info}


# --------------------------------------------------------------------------- folders

_CSIDL = {
    "Desktop": 0x0010,
    "Documents": 0x0005,
    "Pictures": 0x0027,
    "Music": 0x000D,
    "Videos": 0x000E,
}


def _shell_folder(csidl):
    buf = ctypes.create_unicode_buffer(1024)
    if _shell32.SHGetFolderPathW(None, csidl, None, 0, buf) == 0 and buf.value:
        return buf.value
    return None


def home_dir():
    return os.environ.get("USERPROFILE") or os.path.expanduser("~")


def known_folders():
    home = home_dir()
    result = {}
    for name, csidl in _CSIDL.items():
        path = None
        if IS_WINDOWS:
            try:
                path = _shell_folder(csidl)
            except Exception as e:
                log.warning("SHGetFolderPathW(%s) failed: %s", name, e)
        result[name] = path or os.path.join(home, name)
    result["Downloads"] = os.path.join(home, "Downloads")
    result["Home"] = home
    return result


def system_dirs():
    """Folders the assistant must never change."""
    names = ["SystemRoot", "windir", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432",
             "ProgramData", "CommonProgramFiles", "CommonProgramFiles(x86)"]
    dirs = []
    for name in names:
        value = os.environ.get(name)
        if value:
            dirs.append(value)
    if IS_WINDOWS:
        drive = os.environ.get("SystemDrive", "C:") + "\\"
        dirs.extend([
            os.path.join(drive, "Windows"),
            os.path.join(drive, "Program Files"),
            os.path.join(drive, "Program Files (x86)"),
            os.path.join(drive, "ProgramData"),
            os.path.join(drive, "Boot"),
            os.path.join(drive, "Recovery"),
            os.path.join(drive, "System Volume Information"),
            os.path.join(drive, "$Recycle.Bin"),
        ])
        for name in ("APPDATA", "LOCALAPPDATA"):
            if os.environ.get(name):
                dirs.append(os.environ[name])
        dirs.append(os.path.join(home_dir(), "AppData"))
    else:
        dirs.extend(["/bin", "/boot", "/etc", "/lib", "/proc", "/sbin", "/sys", "/usr", "/var"])
    return dirs


def drives():
    """List of (root, kind) for local drives: kind is fixed/removable/network/cdrom."""
    if not IS_WINDOWS:
        return [("/", "fixed")]
    kinds = {2: "removable", 3: "fixed", 4: "network", 5: "cdrom", 6: "ramdisk"}
    result = []
    try:
        mask = _kernel32.GetLogicalDrives()
        for i in range(26):
            if mask & (1 << i):
                root = "%s:\\" % chr(65 + i)
                kind = kinds.get(_kernel32.GetDriveTypeW(root), "unknown")
                result.append((root, kind))
    except Exception as e:
        log.warning("Could not list drives: %s", e)
    return result


def drive_kind(path):
    if not IS_WINDOWS:
        return "fixed"
    root = os.path.splitdrive(os.path.abspath(path))[0]
    if not root or root.startswith("\\\\"):
        return "network"
    try:
        kind = _kernel32.GetDriveTypeW(root + "\\")
    except Exception:
        return "unknown"
    return {2: "removable", 3: "fixed", 4: "network", 5: "cdrom", 6: "ramdisk"}.get(kind, "unknown")


# --------------------------------------------------------------------------- recycle bin

def recycle(path):
    """Send one file or folder to the Recycle Bin.  Returns None or an error text."""
    path = os.path.abspath(path)
    if not os.path.lexists(path):
        return "it does not exist"
    if not IS_WINDOWS:
        return _fake_recycle(path)
    FO_DELETE = 3
    flags = 0x0040 | 0x0010 | 0x0004 | 0x0400  # ALLOWUNDO | NOCONFIRMATION | SILENT | NOERRORUI
    text = path + "\0\0"
    buf = ctypes.create_unicode_buffer(len(text) + 1)
    for i, ch in enumerate(text):
        buf[i] = ch
    op = _SHFILEOPSTRUCTW()
    op.hwnd = None
    op.wFunc = FO_DELETE
    op.pFrom = ctypes.cast(buf, ctypes.c_void_p)
    op.pTo = None
    op.fFlags = flags
    result = _shell32.SHFileOperationW(ctypes.byref(op))
    if result != 0:
        return "Windows error code %s" % result
    if op.fAnyOperationsAborted:
        return "the operation was cancelled"
    if os.path.lexists(path):
        return "it is still there (maybe it is open in another program)"
    return None


def _fake_recycle(path):
    from . import paths
    trash = os.path.join(paths.DATA_DIR, "trash")
    os.makedirs(trash, exist_ok=True)
    try:
        shutil.move(path, os.path.join(trash, "%d-%s" % (int(time.time() * 1000), os.path.basename(path))))
    except OSError as e:
        return str(e)
    return None


# --------------------------------------------------------------------------- misc

def oem_encoding():
    """The console code page that cmd.exe and most console programs write in."""
    if IS_WINDOWS:
        try:
            name = "cp%d" % _kernel32.GetOEMCP()
            codecs.lookup(name)
            return name
        except Exception:
            pass
        return "cp437"
    return "utf-8"


def disable_crash_dialogs():
    """No "pythonw.exe has stopped working" box: a crash should just end the process so the launcher
    can restart it.  Child processes inherit this setting."""
    if IS_WINDOWS:
        try:
            # SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX | SEM_NOOPENFILEERRORBOX
            _kernel32.SetErrorMode(0x0001 | 0x0002 | 0x8000)
        except Exception:
            pass


def single_instance(name):
    """Return a handle if we are the only instance, None if another one runs."""
    if not IS_WINDOWS:
        return True
    try:
        handle = _kernel32.CreateMutexW(None, False, "Local\\" + name)
        if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            return None
        return handle or True
    except Exception:
        return True


def message_box(text, title="Helper"):
    if not IS_WINDOWS:
        log.info("message box: %s", text)
        return
    try:
        _user32.MessageBoxW(None, text, title, 0x40 | 0x40000)  # ICONINFORMATION | TOPMOST
    except Exception:
        pass


def kill_tree(pid):
    if IS_WINDOWS:
        try:
            subprocess.call(["taskkill", "/T", "/F", "/PID", str(pid)],
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, timeout=30, **subprocess_flags())
        except Exception as e:
            log.warning("taskkill failed: %s", e)


def open_with_default_app(path):
    if IS_WINDOWS:
        os.startfile(path)
    else:
        opener = shutil.which("xdg-open")
        if opener:
            subprocess.Popen([opener, path], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        else:
            log.info("Would open %s", path)
