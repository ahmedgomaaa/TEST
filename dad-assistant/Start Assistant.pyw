"""Desktop shortcut target.  Starts the assistant and restarts it if it ever crashes.

Run with pythonw.exe so no black console window appears.
"""

import os
import subprocess
import sys
import time

APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)

from helper import logs, paths, strings, winapi  # noqa: E402


def main():
    logs.setup()
    winapi.disable_crash_dialogs()
    import logging
    log = logging.getLogger("assistant.launcher")
    if not winapi.single_instance("HomeAssistantLauncher"):
        winapi.message_box(strings.ALREADY_OPEN, strings.DEFAULT_ASSISTANT_NAME)
        return
    # Install a newer version from GitHub first, if there is one (Ahmed can fix things remotely).
    try:
        from helper import config, updater
        cfg, _problems = config.load()
        logs.set_secrets(config.secrets(cfg))
        log.info("Update check: %s", updater.run_at_start(cfg))
    except Exception:
        log.exception("Update check failed")

    crashes = []
    while True:
        started = time.time()
        try:
            code = subprocess.call([sys.executable, "-m", "helper"], cwd=APP_DIR,
                                   stdin=subprocess.DEVNULL, **winapi.subprocess_flags())
        except OSError as e:
            log.critical("Could not start the app: %s", e)
            code = -1
        # The app deletes its "running" marker when it closes normally.  Do not trust the exit code
        # alone: some crashes end with code 0.
        try:
            still_marked = os.path.getmtime(paths.RUNNING_FLAG) >= started - 2
        except OSError:
            still_marked = False
        if code == 0 and not still_marked:
            return
        log.error("App exited with code %s after %.0f seconds; restarting", code, time.time() - started)
        now = time.time()
        crashes = [t for t in crashes if now - t < 180] + [now]
        if len(crashes) >= 2 and _rollback_if_just_updated(log):
            crashes = []
            time.sleep(1)
            continue
        if len(crashes) >= 3:
            try:
                from helper import config, report
                cfg, _problems = config.load()
                logs.set_secrets(config.secrets(cfg))
                report.Reporter(cfg).send_now("The assistant cannot start",
                                              "It crashed 3 times in a row (last exit code %s)." % code)
            except Exception:
                log.exception("Could not send the crash report")
            winapi.message_box(strings.CANNOT_START, strings.DEFAULT_ASSISTANT_NAME)
            return
        time.sleep(2)


def _rollback_if_just_updated(log):
    """A version that crashes twice right after an update is replaced by the previous one."""
    try:
        from helper import config, report, updater
        info = updater.pending()
        if not info:
            return False
        restored = updater.rollback()
        if restored is None:
            return False
        cfg, _problems = config.load()
        logs.set_secrets(config.secrets(cfg))
        report.Reporter(cfg).send_now(
            "Update to build %s failed, rolled back" % info.get("to"),
            "Build %s (commit %s) crashed twice right after it was installed, so build %s was put back.\n"
            "Build %s will not be installed again; publish a newer build to fix it."
            % (info.get("to"), info.get("commit"), restored, info.get("to")))
        return True
    except Exception:
        log.exception("Rollback failed")
        return False


if __name__ == "__main__":
    main()
