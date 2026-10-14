"""Start the assistant window:  pythonw -m helper  (normally via "Start Assistant.pyw")."""

import logging
import os
import sys
import threading
import traceback


def main():
    from . import config, logs, paths, report, winapi

    logs.setup()
    log = logging.getLogger("assistant")
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
        winapi.message_box("The helper is already open. Look for it at the bottom of the screen.",
                           cfg["assistant_name"])
        return 0

    # If the last run did not close normally, tell Ahmed.
    if os.path.exists(paths.RUNNING_FLAG):
        reporter.send_async("The assistant closed unexpectedly last time",
                            "The previous session did not shut down normally. The log is attached.")
    try:
        with open(paths.RUNNING_FLAG, "w") as f:
            f.write(str(os.getpid()))
    except OSError:
        pass
    reporter.start_outbox_worker()

    from . import ui
    app = ui.App(cfg, problems, reporter)
    app.run()

    try:
        os.remove(paths.RUNNING_FLAG)
    except OSError:
        pass
    log.info("=== Closed normally ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
