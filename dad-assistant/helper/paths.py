"""Where the app keeps its files."""

import os
import tempfile

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
CONFIG_EXAMPLE_PATH = os.path.join(APP_DIR, "config.example.json")
CA_BUNDLE = os.path.join(PACKAGE_DIR, "cacert.pem")


def _writable(folder):
    try:
        os.makedirs(folder, exist_ok=True)
        probe = os.path.join(folder, ".write-test")
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
        return True
    except OSError:
        return False


def _pick_data_dir():
    preferred = os.path.join(APP_DIR, "data")
    if _writable(preferred):
        return preferred
    # The app folder may be read-only (for example if it was put in Program Files).
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or tempfile.gettempdir()
    fallback = os.path.join(base, "HomeAssistant")
    _writable(fallback)
    return fallback


DATA_DIR = _pick_data_dir()
LOG_DIR = os.path.join(DATA_DIR, "logs")
OUTBOX_DIR = os.path.join(DATA_DIR, "outbox")
TEMP_DIR = os.path.join(DATA_DIR, "temp")
UNDO_PATH = os.path.join(DATA_DIR, "undo.json")
RUNNING_FLAG = os.path.join(DATA_DIR, "running.flag")
LOG_FILE = os.path.join(LOG_DIR, "assistant.log")


def ensure_dirs():
    for folder in (DATA_DIR, LOG_DIR, OUTBOX_DIR, TEMP_DIR):
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError:
            pass
