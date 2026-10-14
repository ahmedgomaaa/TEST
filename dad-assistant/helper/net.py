"""HTTPS with the standard library only.

Windows 7 problems this module avoids:
- Old/missing root certificates: we load our own bundled cacert.pem (certifi) in
  addition to the Windows store.  Python 3.8 ships its own OpenSSL, so TLS 1.2/1.3
  work no matter what the OS (or PowerShell 2.0 / .NET) supports.
- Flaky Wi-Fi: every request is retried with backoff.
"""

import http.client
import json
import logging
import socket
import ssl
import threading
import time
import urllib.error
import urllib.request

from . import paths

log = logging.getLogger("assistant.net")

USER_AGENT = "HomeAssistant-Win7/1.0 (Python urllib)"
RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 520, 522, 524, 529}
RETRY_DELAYS = (2, 4, 8, 16)


class NetworkError(Exception):
    """Could not talk to the server at all (offline, DNS, TLS, timeout)."""

    @property
    def looks_like_clock_problem(self):
        text = str(self).lower()
        return "certificate" in text and ("not yet valid" in text or "expired" in text or "verify failed" in text)


class HttpError(Exception):
    def __init__(self, status, body, retry_after=None):
        Exception.__init__(self, "HTTP %s: %s" % (status, (body or "")[:800]))
        self.status = status
        self.body = body or ""
        self.retry_after = retry_after


class Cancelled(Exception):
    pass


_context = None


def ssl_context():
    global _context
    if _context is None:
        ctx = ssl.create_default_context()
        try:
            ctx.load_verify_locations(cafile=paths.CA_BUNDLE)
        except (OSError, ssl.SSLError) as e:
            log.warning("Could not load bundled CA file: %s", e)
        _context = ctx
    return _context


def request(url, data=None, headers=None, timeout=60, method=None):
    """One attempt.  Returns the body as bytes or raises HttpError/NetworkError."""
    all_headers = {"User-Agent": USER_AGENT}
    all_headers.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=all_headers, method=method)
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl_context()))
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            body = ""
        retry_after = None
        try:
            retry_after = e.headers.get("retry-after") if e.headers else None
        except Exception:
            pass
        raise HttpError(e.code, body, retry_after)
    except (urllib.error.URLError, socket.timeout, ssl.SSLError, http.client.HTTPException, OSError, ValueError) as e:
        reason = getattr(e, "reason", None)
        raise NetworkError("%s: %s" % (type(e).__name__, reason if reason is not None else e))


def _sleep(seconds, cancel):
    if cancel is None:
        time.sleep(seconds)
    elif cancel.wait(seconds):
        raise Cancelled()


def _retry_after_seconds(value, default):
    try:
        return max(1.0, min(60.0, float(value)))
    except (TypeError, ValueError):
        return default


def _cancellable(func, cancel):
    """Run func() but stop waiting as soon as `cancel` is set (the Stop button).

    A blocking HTTPS request cannot be interrupted, so it runs in a helper thread; if the user presses
    Stop we simply stop waiting for it and its late answer is thrown away.
    """
    if cancel is None:
        return func()
    box = {}

    def work():
        try:
            box["result"] = func()
        except BaseException as e:  # handed over to the waiting thread
            box["error"] = e
    worker = threading.Thread(target=work, name="http", daemon=True)
    worker.start()
    while worker.is_alive():
        worker.join(0.2)
        if cancel.is_set():
            raise Cancelled()
    if "error" in box:
        raise box["error"]
    return box["result"]


def with_retries(func, cancel=None, delays=None):
    if delays is None:
        delays = RETRY_DELAYS
    attempt = 0
    while True:
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        try:
            return _cancellable(func, cancel)
        except HttpError as e:
            if e.status not in RETRY_STATUS or attempt >= len(delays):
                raise
            wait = _retry_after_seconds(e.retry_after, delays[attempt])
            log.warning("HTTP %s, retrying in %.0fs (attempt %d)", e.status, wait, attempt + 1)
        except NetworkError as e:
            if attempt >= len(delays):
                raise
            wait = delays[attempt]
            log.warning("Network problem (%s), retrying in %ss (attempt %d)", e, wait, attempt + 1)
        _sleep(wait, cancel)
        attempt += 1


def post_json(url, payload, headers=None, timeout=300, cancel=None, delays=None):
    data = json.dumps(payload).encode("utf-8")
    all_headers = {"Content-Type": "application/json"}
    all_headers.update(headers or {})
    body = with_retries(lambda: request(url, data, all_headers, timeout, "POST"), cancel, delays)
    try:
        return json.loads(body.decode("utf-8"))
    except ValueError:
        raise NetworkError("The server sent an unreadable answer: %r" % body[:200])


def get_bytes(url, timeout=120, cancel=None):
    return with_retries(lambda: request(url, None, None, timeout, "GET"), cancel)
