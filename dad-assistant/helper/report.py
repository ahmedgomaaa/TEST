"""E-mailing problem reports and logs to Ahmed.

Reports are sent from a background thread.  If the laptop is offline, the e-mail
is saved in the outbox folder and sent automatically later.  Secrets are
redacted from everything that is sent.
"""

import datetime
import email
import email.policy
import email.utils
import logging
import os
import platform
import smtplib
import sys
import threading
import time
import uuid
from email.message import EmailMessage

from . import __version__, logs, net, paths

log = logging.getLogger("assistant.report")

ERROR_REPORT_MIN_INTERVAL = 20 * 60  # at most one automatic error e-mail every 20 minutes
MAX_OUTBOX = 20


def _windows_version():
    try:
        v = sys.getwindowsversion()
        names = {(6, 1): "Windows 7", (6, 2): "Windows 8", (6, 3): "Windows 8.1", (10, 0): "Windows 10/11"}
        return "%s (%d.%d build %d %s)" % (names.get((v.major, v.minor), "Windows"), v.major, v.minor, v.build,
                                           v.service_pack)
    except AttributeError:
        return platform.platform()


class Reporter(object):
    def __init__(self, cfg):
        self.settings = cfg["email_reports"]
        self.assistant_name = cfg["assistant_name"]
        self._lock = threading.Lock()
        self._errors_lock = threading.Lock()
        self._last_error_report = 0.0
        self._pending_errors = []

    def configured(self):
        s = self.settings
        return bool(s.get("enabled") and s.get("send_to") and s.get("smtp_username") and s.get("smtp_app_password"))

    # ------------------------------------------------------------------ building

    def build(self, subject, body, attach_log=True):
        msg = EmailMessage()
        msg["Subject"] = "[%s] %s" % (self.assistant_name, subject)
        msg["From"] = self.settings.get("smtp_username")
        msg["To"] = self.settings.get("send_to")
        msg["Date"] = email.utils.formatdate(localtime=True)
        msg["Message-ID"] = email.utils.make_msgid()
        info = [
            logs.redact(body),
            "",
            "-- ",
            "Time on the laptop: %s" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "App version: %s" % __version__,
            "Python: %s (%s)" % (sys.version.split()[0], platform.architecture()[0]),
            "Windows: %s" % _windows_version(),
            "Data folder: %s" % paths.DATA_DIR,
        ]
        msg.set_content("\n".join(info))
        if attach_log:
            # Attach as text so the charset (UTF-8) is declared and Arabic file names stay readable.
            msg.add_attachment(logs.tail(), subtype="plain", charset="utf-8", filename="assistant-log.txt")
        return msg

    # ------------------------------------------------------------------ sending

    def _smtp_send(self, msg):
        s = self.settings
        host, port = s.get("smtp_host") or "smtp.gmail.com", int(s.get("smtp_port") or 465)
        if port not in (25, 587):  # 465 and others: TLS from the start (like Gmail on 465)
            server = smtplib.SMTP_SSL(host, port, timeout=40, context=net.ssl_context())
        else:
            server = smtplib.SMTP(host, port, timeout=40)
            server.ehlo()
            server.starttls(context=net.ssl_context())
            server.ehlo()
        try:
            server.login(s["smtp_username"], s["smtp_app_password"].replace(" ", ""))
            server.send_message(msg)
        finally:
            try:
                server.quit()
            except Exception:
                pass

    def send_now(self, subject, body, attach_log=True):
        """Send synchronously.  Returns True if sent, False if queued for later."""
        if not self.configured():
            log.warning("E-mail reports are not configured; report not sent: %s", subject)
            return False
        msg = self.build(subject, body, attach_log)
        with self._lock:
            try:
                self._smtp_send(msg)
                log.info("Report e-mailed: %s", subject)
                return True
            except Exception as e:
                log.warning("Could not e-mail report (%s); saved to outbox.", e)
                self._save_to_outbox(msg)
                return False

    def send_async(self, subject, body, attach_log=True, done=None):
        def work():
            ok = False
            try:
                ok = self.send_now(subject, body, attach_log)
            except Exception:
                log.exception("Report thread failed")
            if done:
                try:
                    done(ok)
                except Exception:
                    log.exception("Report callback failed")
        threading.Thread(target=work, name="report", daemon=True).start()

    def report_error(self, summary, details=""):
        """Automatic error report, throttled so a broken setting cannot flood the inbox."""
        if not self.settings.get("send_on_errors", True):
            return
        with self._errors_lock:  # not self._lock: that one is held while an e-mail is being sent
            self._pending_errors.append("%s  %s\n%s" % (time.strftime("%H:%M:%S"), summary, details))
            if time.time() - self._last_error_report < ERROR_REPORT_MIN_INTERVAL:
                return
            self._last_error_report = time.time()
            errors, self._pending_errors = self._pending_errors, []
        body = "The assistant on Dad's laptop ran into %d problem(s):\n\n%s" % (len(errors), "\n\n".join(errors))
        self.send_async("Problem report: %s" % summary[:80], body)

    # ------------------------------------------------------------------ outbox

    def _save_to_outbox(self, msg):
        try:
            paths.ensure_dirs()
            name = "%s-%s.eml" % (time.strftime("%Y%m%d-%H%M%S"), uuid.uuid4().hex[:6])
            with open(os.path.join(paths.OUTBOX_DIR, name), "wb") as f:
                f.write(msg.as_bytes())
            files = sorted(os.listdir(paths.OUTBOX_DIR))
            for old in files[:-MAX_OUTBOX]:
                os.remove(os.path.join(paths.OUTBOX_DIR, old))
        except Exception as e:
            log.error("Could not save e-mail to outbox: %s", e)

    def flush_outbox(self):
        if not self.configured():
            return
        try:
            files = sorted(f for f in os.listdir(paths.OUTBOX_DIR) if f.endswith(".eml"))
        except OSError:
            return
        for name in files:
            full = os.path.join(paths.OUTBOX_DIR, name)
            try:
                with open(full, "rb") as f:
                    msg = email.message_from_bytes(f.read(), policy=email.policy.default)
                with self._lock:
                    self._smtp_send(msg)
                os.remove(full)
                log.info("Sent queued e-mail %s", name)
            except Exception as e:
                log.info("Outbox still offline (%s)", e)
                return

    def start_outbox_worker(self, interval=15 * 60):
        def loop():
            while True:
                try:
                    self.flush_outbox()
                except Exception:
                    log.exception("Outbox worker failed")
                time.sleep(interval)
        threading.Thread(target=loop, name="outbox", daemon=True).start()
