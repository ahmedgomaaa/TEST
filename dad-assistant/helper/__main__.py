"""Start the assistant window:  pythonw -m helper  (normally via "Start Assistant.pyw")."""

import faulthandler
import logging
import os
import sys
import threading
import time
import traceback


def _crash_tail(path, max_chars=4000):
    try:
        with open(path, "r", errors="replace") as f:
            return f.read()[-max_chars:] or "(empty)"
    except OSError:
        return "(none)"


def _tell_about_update(app, reporter, notice):
    """Show "updated" / "rolled back" in the chat and e-mail Ahmed."""
    if not notice:
        return
    from . import strings
    try:
        if notice.get("kind") == "updated":
            app._write(strings.UPDATED.format(build=notice.get("to")), "notice", record=False)
            reporter.send_async("Updated to build %s" % notice.get("to"),
                                "The helper updated itself from build %s to build %s (commit %s).\n\n%s"
                                % (notice.get("from"), notice.get("to"), notice.get("commit"),
                                   notice.get("notes") or ""), attach_log=False)
        elif notice.get("kind") == "rolled_back":
            app._write(strings.ROLLED_BACK, "notice", record=False)
    except Exception:
        logging.getLogger("assistant").exception("Could not show the update notice")


def main():
    from . import config, logs, paths, report, winapi

    logs.setup()
    winapi.disable_crash_dialogs()
    log = logging.getLogger("assistant")
    try:
        # Native crashes (inside Tk or Windows) leave a Python stack trace here for Ahmed.
        crash_file = open(paths.CRASH_FILE, "a")
        crash_file.write("\n=== %s ===\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
        crash_file.flush()
        faulthandler.enable(crash_file)
    except (OSError, RuntimeError):
        pass
    cfg, problems = config.load()
    logs.set_secrets(config.secrets(cfg))
    log.info("=== Starting (Python %s, %s) ===", sys.version.split()[0], sys.platform)
    reporter = report.Reporter(cfg)

    def excepthook(exc_type, exc, tb):
        details = "".join(traceback.format_exception(exc_type, exc, tb))
        log.critical("Unhandled error:\n%s", details)
        try:
            reporter.send_now("Crash: %s" % exc, details)
        except Exception:
            pass

    def thread_excepthook(args):
        if args.exc_type is SystemExit:
            return
        details = "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
        log.error("Unhandled error in thread %s:\n%s", getattr(args.thread, "name", "?"), details)
        reporter.report_error("Background error: %s" % args.exc_value, details)

    sys.excepthook = excepthook
    threading.excepthook = thread_excepthook

    if not winapi.single_instance("HomeAssistantWindow"):
        from . import strings
        winapi.message_box(strings.ALREADY_OPEN, cfg["assistant_name"])
        return 0

    # If the last run did not close normally, tell Ahmed.
    if os.path.exists(paths.RUNNING_FLAG):
        reporter.send_async("The assistant closed unexpectedly last time",
                            "The previous session did not shut down normally. The log is attached.\n\n"
                            "Last part of crash.log:\n" + _crash_tail(paths.CRASH_FILE))
    try:
        with open(paths.RUNNING_FLAG, "w") as f:
            f.write(str(os.getpid()))
    except OSError:
        pass
    reporter.start_outbox_worker()

    from . import ui, updater
    app = ui.App(cfg, problems, reporter)
    # The window is up: an update just installed works, so it will not be rolled back.
    updater.confirm_started()
    _tell_about_update(app, reporter, updater.take_notice())
    app.run()

    try:
        os.remove(paths.RUNNING_FLAG)
    except OSError:
        pass
    log.info("=== Closed normally ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
