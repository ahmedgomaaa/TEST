"""Loading settings from config.json.

The file is edited by hand in Notepad, so this module is forgiving: it accepts a
UTF-8 BOM (Notepad on Windows 7 always writes one), fills in every missing value
with a default, and never raises.  Problems are returned as a list of messages.
"""

import copy
import json
import os

from . import paths
from .strings import DEFAULT_ASSISTANT_NAME

DEFAULTS = {
    "assistant_name": DEFAULT_ASSISTANT_NAME,
    "user_name": "",
    "font_size": 15,
    "arabic_word_order_fix": False,
    "provider": "anthropic",
    "anthropic": {
        "api_key": "",
        "model": "claude-opus-5-5",
        "effort": "medium",
        "use_fallbacks": True,
        "base_url": "https://api.anthropic.com",
    },
    "openai_compatible": {
        "api_key": "",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
    },
    "images": {
        "provider": "openai",
        "api_key": "",
        "model": "gpt-image-1",
        "size": "1024x1024",
    },
    "email_reports": {
        "enabled": True,
        "send_to": "",
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 465,
        "smtp_username": "",
        "smtp_app_password": "",
        "send_on_errors": True,
    },
    "safety": {
        "confirm_changes": True,
        "allow_all_commands": False,
        "command_timeout_seconds": 120,
    },
    "max_steps_per_request": 30,
    "max_context_tokens": 0,
    "memory": {
        "enabled": True,
        "effort": "low",
    },
    "updates": {
        "enabled": True,
        "manifest_url": "https://github.com/ahmedgomaaa/TEST/releases/download/helper-installer-latest/update.json",
    },
}


def _merge(defaults, loaded):
    result = copy.deepcopy(defaults)
    if not isinstance(loaded, dict):
        return result
    for key, value in loaded.items():
        if key.startswith("_"):
            continue  # "_comment" style keys are documentation only
        if isinstance(result.get(key), dict) and isinstance(value, dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def load(path=None):
    """Return (config_dict, problems)."""
    path = path or paths.CONFIG_PATH
    problems = []
    loaded = {}
    if not os.path.exists(path):
        problems.append("config.json was not found next to the app. Run SETUP.bat first.")
    else:
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                loaded = json.load(f)
        except ValueError as e:
            problems.append("config.json is not valid JSON: %s" % e)
        except OSError as e:
            problems.append("config.json could not be read: %s" % e)
    cfg = _merge(DEFAULTS, loaded)
    _sanitize(cfg, problems)
    return cfg, problems


def _sanitize(cfg, problems):
    try:
        cfg["font_size"] = max(10, min(30, int(cfg.get("font_size") or 15)))
    except (TypeError, ValueError):
        cfg["font_size"] = 15
    try:
        cfg["max_steps_per_request"] = max(3, min(100, int(cfg.get("max_steps_per_request") or 30)))
    except (TypeError, ValueError):
        cfg["max_steps_per_request"] = 30
    safety = cfg["safety"]
    try:
        safety["command_timeout_seconds"] = max(10, min(1800, int(safety.get("command_timeout_seconds") or 120)))
    except (TypeError, ValueError):
        safety["command_timeout_seconds"] = 120
    try:
        cfg["email_reports"]["smtp_port"] = int(cfg["email_reports"].get("smtp_port") or 465)
    except (TypeError, ValueError):
        cfg["email_reports"]["smtp_port"] = 465
    for section in ("anthropic", "openai_compatible", "images"):
        cfg[section]["api_key"] = str(cfg[section].get("api_key") or "").strip()
    provider = str(cfg.get("provider") or "anthropic").strip().lower()
    if provider not in ("anthropic", "openai_compatible"):
        problems.append('"provider" must be "anthropic" or "openai_compatible"; using "anthropic".')
        provider = "anthropic"
    cfg["provider"] = _fix_provider(cfg, provider)
    if not cfg[cfg["provider"]]["api_key"]:
        problems.append("No API key is set for provider %r in config.json." % cfg["provider"])
    cfg["assistant_name"] = str(cfg.get("assistant_name") or "").strip() or DEFAULT_ASSISTANT_NAME
    cfg["user_name"] = str(cfg.get("user_name") or "").strip()


def detect_provider(key):
    """"anthropic" for a Claude key (sk-ant-...), "openai_compatible" (DeepSeek) for any other key."""
    key = str(key or "").strip()
    if not key:
        return None
    return "anthropic" if key.startswith("sk-ant-") else "openai_compatible"


def _fix_provider(cfg, provider):
    """Use the provider that the key really belongs to, so a DeepSeek key works wherever it was put."""
    claude_key = cfg["anthropic"]["api_key"]
    other_key = cfg["openai_compatible"]["api_key"]
    if claude_key and detect_provider(claude_key) == "openai_compatible" and not other_key:
        # A DeepSeek key typed into the Claude slot: move it.
        cfg["openai_compatible"]["api_key"], cfg["anthropic"]["api_key"] = claude_key, ""
        claude_key, other_key = "", claude_key
    if provider == "anthropic" and not claude_key and other_key:
        return "openai_compatible"
    if provider == "openai_compatible" and not other_key and claude_key:
        return "anthropic"
    return provider


def secrets(cfg):
    """All secret strings in the config, so logs and e-mails can redact them."""
    values = [
        cfg["anthropic"].get("api_key"),
        cfg["openai_compatible"].get("api_key"),
        cfg["images"].get("api_key"),
        cfg["email_reports"].get("smtp_app_password"),
    ]
    return [str(v) for v in values if v and len(str(v)) >= 6]
