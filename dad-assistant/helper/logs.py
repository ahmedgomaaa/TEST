"""Logging to a rotating file, with secrets redacted."""

import logging
import logging.handlers
import os
import re
import sys

from . import paths

_SECRETS = []
_KEY_PATTERN = re.compile(r"\b(sk-[A-Za-z0-9_\-]{8,}|AIza[0-9A-Za-z_\-]{20,})")


def set_secrets(values):
    del _SECRETS[:]
    _SECRETS.extend(sorted(set(v for v in values if v), key=len, reverse=True))


def redact(text):
    if not text:
        return text
    text = str(text)
    for secret in _SECRETS:
        text = text.replace(secret, "[REDACTED]")
    return _KEY_PATTERN.sub("[REDACTED]", text)


class _RedactingFormatter(logging.Formatter):
    def format(self, record):
        return redact(logging.Formatter.format(self, record))


class _NullStream(object):
    """Stand-in for sys.stdout/sys.stderr under pythonw.exe, where they are None."""

    def write(self, text):
        try:
            if text and text.strip():
                logging.getLogger("assistant.stderr").warning(text.rstrip())
        except Exception:
            pass
        return len(text or "")

    def flush(self):
        pass

    def isatty(self):
        return False


def setup():
    paths.ensure_dirs()
    if sys.stdout is None:
        sys.stdout = _NullStream()
    if sys.stderr is None:
        sys.stderr = _NullStream()
    root = logging.getLogger()
    if any(getattr(h, "_assistant", False) for h in root.handlers):
        return
    root.setLevel(logging.INFO)
    try:
        handler = logging.handlers.RotatingFileHandler(
            paths.LOG_FILE, maxBytes=1024 * 1024, backupCount=5, encoding="utf-8", delay=True
        )
    except OSError:
        handler = logging.StreamHandler(sys.stderr)
    handler._assistant = True
    handler.setFormatter(_RedactingFormatter("%(asctime)s %(levelname)s [%(threadName)s] %(name)s: %(message)s"))
    root.addHandler(handler)


def tail(max_bytes=300 * 1024):
    """Last part of the current log plus the previous one if the current one is short."""
    chunks = []
    remaining = max_bytes
    for name in (paths.LOG_FILE, paths.LOG_FILE + ".1"):
        if remaining <= 0 or not os.path.exists(name):
            continue
        try:
            with open(name, "rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - remaining))
                data = f.read()
            chunks.insert(0, data.decode("utf-8", "replace"))
            remaining -= len(data)
        except OSError:
            pass
    return redact("".join(chunks))
