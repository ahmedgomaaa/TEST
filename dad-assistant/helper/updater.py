"""Automatic updates from Ahmed's GitHub releases, so a fix can reach the laptop remotely.

"Start Assistant.pyw" calls run_at_start() every time, before the window starts:

1. Download the small update.json published next to HelperSetup.exe (8-second limit; offline = skip).
2. If its "build" is newer than ours (helper/build_info.json) and was not rolled back before, download
   helper-app.zip, check its size and SHA-256, unpack it into data/update/staging, check that it is a
   complete app and that every .py file compiles.
3. Swap: the current app files go to data/update/backup, the new ones take their place.  Only the app's
   own files are touched, never config.json or data/.  If anything fails half-way, the backup is put back.
4. The window then says it was updated, and Ahmed gets an e-mail.

If the new version fails to start twice in a row right after an update, the launcher calls rollback():
the backup is restored, that build is remembered as bad (so it is not downloaded again), and Ahmed is told.

Nothing here raises: every problem is logged and the current version simply starts.
Only files published by the repository's own build (GitHub Actions) are used; there is no other source.
"""

import hashlib
import io
import json
import logging
import os
import py_compile
import shutil
import tempfile
import threading
import time
import zipfile

from . import net, paths

log = logging.getLogger("assistant.updater")

# Everything the update package may contain; anything else in the app folder (config.json, data/,
# uninstall.exe) is never touched.
MANAGED = ("helper", "Start Assistant.pyw", "install_helper.py", "setup_assistant.py", "SETUP.bat",
           "config.example.json", "README.md", "helper.ico")
REQUIRED = ("helper/__init__.py", "helper/__main__.py", "helper/updater.py", "Start Assistant.pyw")
MAX_PACKAGE_BYTES = 20 * 1024 * 1024
MANIFEST_TIMEOUT = 8
ZIP_TIMEOUT = 90
ROLLBACK_WINDOW_SECONDS = 30 * 60

BUILD_INFO = os.path.join(paths.PACKAGE_DIR, "build_info.json")
UPDATE_DIR = os.path.join(paths.DATA_DIR, "update")
STAGING_DIR = os.path.join(UPDATE_DIR, "staging")
BACKUP_DIR = os.path.join(UPDATE_DIR, "backup")
PENDING_FILE = os.path.join(UPDATE_DIR, "pending.json")      # updated, not yet proven to start
NOTICE_FILE = os.path.join(UPDATE_DIR, "notice.json")        # for the window: "updated" / "rolled back"
BAD_BUILDS_FILE = os.path.join(UPDATE_DIR, "bad_builds.json")


class UpdateError(Exception):
    pass


# ------------------------------------------------------------------ small helpers

def _read_json(path, default=None):
    try:
        with io.open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _remove(path):
    try:
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path)
        elif os.path.lexists(path):
            os.remove(path)
    except OSError as e:
        log.warning("Could not remove %s: %s", path, e)


def current_build(app_dir=None):
    info = _read_json(os.path.join(app_dir, "helper", "build_info.json") if app_dir else BUILD_INFO, {})
    try:
        return int(info.get("build") or 0)
    except (TypeError, ValueError, AttributeError):
        return 0


def _bad_builds():
    data = _read_json(BAD_BUILDS_FILE, [])
    return set(int(b) for b in data if isinstance(b, int)) if isinstance(data, list) else set()


# ------------------------------------------------------------------ checking

def fetch_manifest(cfg):
    """The published update.json, or None if updates are off or the internet is not there."""
    settings = cfg.get("updates") or {}
    url = str(settings.get("manifest_url") or "").strip()
    if not settings.get("enabled", True) or not url:
        return None
    try:
        body = net.with_retries(lambda: net.request(url, timeout=MANIFEST_TIMEOUT), delays=())
        manifest = json.loads(body.decode("utf-8-sig"))
    except (net.NetworkError, net.HttpError) as e:
        log.info("Update check skipped: %s", e)
        return None
    except ValueError as e:
        log.warning("update.json is not valid JSON: %s", e)
        return None
    if not isinstance(manifest, dict):
        return None
    manifest["_url"] = url
    return manifest


def is_newer(manifest, app_dir=None):
    try:
        build = int(manifest.get("build"))
    except (TypeError, ValueError):
        log.warning("update.json has no valid build number")
        return False
    if build in _bad_builds():
        log.info("Build %s was rolled back before; not installing it again", build)
        return False
    return build > current_build(app_dir)


# ------------------------------------------------------------------ installing

def _zip_url(manifest):
    name = str(manifest.get("zip") or "helper-app.zip")
    if name.startswith("https://"):
        return name
    return manifest["_url"].rsplit("/", 1)[0] + "/" + name


def download_package(manifest, cancel=None):
    data = net.with_retries(lambda: net.request(_zip_url(manifest), timeout=ZIP_TIMEOUT), cancel, delays=(3, 6))
    expected_size = manifest.get("size")
    if expected_size is not None and len(data) != int(expected_size):
        raise UpdateError("package size %d does not match update.json (%s)" % (len(data), expected_size))
    if len(data) > MAX_PACKAGE_BYTES:
        raise UpdateError("package is too big (%d bytes)" % len(data))
    digest = hashlib.sha256(data).hexdigest()
    if digest.lower() != str(manifest.get("sha256") or "").lower():
        raise UpdateError("package SHA-256 %s does not match update.json" % digest)
    return data


def unpack(data, target):
    """Unpack into `target`, refusing anything outside the managed app files."""
    _remove(target)
    os.makedirs(target)
    total = 0
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for item in z.infolist():
            name = item.filename.replace("\\", "/")
            parts = [p for p in name.split("/") if p]
            if not parts or name.startswith("/") or ":" in parts[0] or ".." in parts:
                raise UpdateError("unsafe path in package: %r" % item.filename)
            if parts[0] not in MANAGED:
                raise UpdateError("unexpected file in package: %r" % item.filename)
            total += item.file_size
            if total > MAX_PACKAGE_BYTES * 3:
                raise UpdateError("package unpacks to more than expected")
            dest = os.path.join(target, *parts)
            if item.is_dir() or name.endswith("/"):
                os.makedirs(dest, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with z.open(item) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)
    for required in REQUIRED:
        if not os.path.isfile(os.path.join(target, *required.split("/"))):
            raise UpdateError("package is incomplete: %s is missing" % required)


def check_compiles(folder):
    """Every Python file must compile, so a broken upload never replaces a working app."""
    tmp = tempfile.mkdtemp(prefix="helper-compile-")
    try:
        for root, _dirs, files in os.walk(folder):
            for name in files:
                if name.endswith((".py", ".pyw")):
                    src = os.path.join(root, name)
                    try:
                        py_compile.compile(src, cfile=os.path.join(tmp, "x.pyc"), doraise=True)
                    except py_compile.PyCompileError as e:
                        raise UpdateError("%s does not compile: %s" % (os.path.relpath(src, folder), e.msg))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def swap_in(staging, app_dir=None, backup=None):
    """Replace the app's files with the ones in `staging`; put everything back if that fails."""
    app_dir = app_dir or paths.APP_DIR
    backup = backup or BACKUP_DIR
    _remove(backup)
    os.makedirs(backup)
    moved_out, moved_in = [], []
    try:
        for name in MANAGED:
            src = os.path.join(app_dir, name)
            if os.path.lexists(src):
                shutil.move(src, os.path.join(backup, name))
                moved_out.append(name)
        for name in os.listdir(staging):
            shutil.move(os.path.join(staging, name), os.path.join(app_dir, name))
            moved_in.append(name)
    except (OSError, shutil.Error) as e:
        log.error("Swapping in the update failed (%s); putting the old version back", e)
        for name in moved_in:
            _remove(os.path.join(app_dir, name))
        for name in moved_out:
            try:
                shutil.move(os.path.join(backup, name), os.path.join(app_dir, name))
            except (OSError, shutil.Error) as e2:
                log.critical("Could not restore %s: %s", name, e2)
        raise UpdateError("could not replace the app files: %s" % e)


def install(manifest, cancel=None):
    """Download, check and install.  Returns the new build number; raises UpdateError/NetworkError."""
    old = current_build()
    new = int(manifest["build"])
    log.info("Updating from build %s to build %s", old, new)
    data = download_package(manifest, cancel)
    unpack(data, STAGING_DIR)
    check_compiles(STAGING_DIR)
    if current_build(STAGING_DIR) != new:
        raise UpdateError("package says build %s, update.json says %s" % (current_build(STAGING_DIR), new))
    swap_in(STAGING_DIR)
    _remove(STAGING_DIR)
    info = {"from": old, "to": new, "time": time.time(), "commit": manifest.get("commit"),
            "notes": manifest.get("notes")}
    _write_json(PENDING_FILE, info)
    _write_json(NOTICE_FILE, dict(info, kind="updated"))
    log.info("Updated to build %s", new)
    return new


# ------------------------------------------------------------------ after the update

def pending():
    """The last update if the new version has not yet started successfully, else None."""
    info = _read_json(PENDING_FILE)
    if not isinstance(info, dict):
        return None
    if time.time() - float(info.get("time") or 0) > ROLLBACK_WINDOW_SECONDS:
        return None
    return info


def confirm_started():
    """Called by the window once it is up: the new version works, so no rollback is needed."""
    if os.path.exists(PENDING_FILE):
        _remove(PENDING_FILE)


def rollback():
    """Put the previous version back.  Returns the restored build number, or None if impossible."""
    info = _read_json(PENDING_FILE, {}) or {}
    if not os.path.isdir(BACKUP_DIR) or not os.path.isfile(os.path.join(BACKUP_DIR, "helper", "__init__.py")):
        log.error("Rollback impossible: no backup of the previous version")
        return None
    bad = current_build()
    failed = os.path.join(UPDATE_DIR, "failed")
    try:
        swap_in(BACKUP_DIR, backup=failed)
    except UpdateError as e:
        log.critical("Rollback failed: %s", e)
        return None
    _remove(BACKUP_DIR)
    _remove(PENDING_FILE)
    bad_builds = sorted(_bad_builds() | {bad})
    _write_json(BAD_BUILDS_FILE, bad_builds)
    restored = current_build()
    _write_json(NOTICE_FILE, {"kind": "rolled_back", "from": bad, "to": restored, "time": time.time(),
                              "commit": info.get("commit")})
    log.warning("Rolled back from build %s to build %s", bad, restored)
    return restored


def take_notice():
    """The "updated" / "rolled back" note for the window, once."""
    info = _read_json(NOTICE_FILE)
    if info is not None:
        _remove(NOTICE_FILE)
    return info if isinstance(info, dict) else None


# ------------------------------------------------------------------ the launcher's entry point

def run_at_start(cfg, show_splash=True):
    """Check for an update and install it.  Never raises.  Returns a short status for the log."""
    try:
        manifest = fetch_manifest(cfg)
        if manifest is None:
            return "no update information"
        if not is_newer(manifest):
            return "up to date (build %s)" % current_build()
        work = lambda: install(manifest)  # noqa: E731
        new = _with_splash(work, cfg) if show_splash else work()
        return "updated to build %s" % new
    except Exception as e:  # anything at all: start the version we have
        log.exception("Update failed; starting the current version")
        _remove(STAGING_DIR)
        return "update failed: %s" % e


def _with_splash(work, cfg):
    """Run `work` in a thread while a small Arabic "updating" window is shown."""
    box = {}

    def run():
        try:
            box["result"] = work()
        except BaseException as e:  # passed on to the caller
            box["error"] = e

    thread = threading.Thread(target=run, name="update", daemon=True)
    thread.start()
    root = None
    try:
        import tkinter as tk
        from . import strings, textutil
        textutil.REORDER = bool(cfg.get("arabic_word_order_fix", False))
        root = tk.Tk()
        root.title(cfg.get("assistant_name") or strings.DEFAULT_ASSISTANT_NAME)
        root.configure(bg="#F4F1EA")
        root.resizable(False, False)
        tk.Label(root, text=textutil.rtl(strings.UPDATING), font=("Tahoma", 16), bg="#F4F1EA",
                 padx=30, pady=24).pack()
        root.update_idletasks()
        x = (root.winfo_screenwidth() - root.winfo_reqwidth()) // 2
        y = (root.winfo_screenheight() - root.winfo_reqheight()) // 3
        root.geometry("+%d+%d" % (x, y))

        def poll():
            if thread.is_alive():
                root.after(200, poll)
            else:
                root.destroy()
        root.after(200, poll)
        root.mainloop()
    except Exception as e:
        log.warning("Could not show the update window (%s); updating without it", e)
    finally:
        thread.join()
    if "error" in box:
        raise box["error"]
    return box.get("result")
