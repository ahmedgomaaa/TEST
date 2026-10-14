"""Desktop shortcut target.  Starts the assistant and restarts it if it ever crashes.

Run with pythonw.exe so no black console window appears.
"""

import os
import subprocess
import sys
import time

APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)

from helper import logs, winapi  # noqa: E402


def main():
    logs.setup()
    import logging
    log = logging.getLogger("assistant.launcher")
    if not winapi.single_instance("HomeAssistantLauncher"):
        winapi.message_box("The helper is already open. Look for it at the bottom of the screen.", "Helper")
        return
    crashes = []
    while True:
        started = time.time()
        try:
            code = subprocess.call([sys.executable, "-m", "helper"], cwd=APP_DIR,
                                   stdin=subprocess.DEVNULL, **winapi.subprocess_flags())
        except OSError as e:
            log.critical("Could not start the app: %s", e)
            code = -1
        if code == 0:
            return
        log.error("App exited with code %s after %.0f seconds; restarting", code, time.time() - started)
        now = time.time()
        crashes = [t for t in crashes if now - t < 180] + [now]
        if len(crashes) >= 3:
            try:
                from helper import config, report
                cfg, _problems = config.load()
                logs.set_secrets(config.secrets(cfg))
                report.Reporter(cfg).send_now("The assistant cannot start",
                                              "It crashed 3 times in a row (last exit code %s)." % code)
            except Exception:
                log.exception("Could not send the crash report")
            winapi.message_box(
                "Sorry, the helper has a problem and cannot start right now.\n\n"
                "Ahmed will get a report about it automatically. Please try again later.",
                "Helper")
            return
        time.sleep(2)


if __name__ == "__main__":
    main()
