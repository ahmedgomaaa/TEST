"""Loading settings from config.json.

The file is edited by hand in Notepad, so this module is forgiving: it accepts a
UTF-8 BOM (Notepad on Windows 7 always writes one), fills in every missing value
with a default, and never raises.  Problems are returned as a list of messages.
"""

import copy
import json
import os

from . import paths

DEFAULTS = {
    "assistant_name": "Helper",
    "user_name": "",
    "font_size": 15,
    "provider": "anthropic",
    "anthropic": {
        "api_key": "",
        "model": "claude-opus-5-5",
        "effort": "medium",
        "use_fallbacks": True,
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
        "command_timeout_seconds": 120,
    },
    "max_steps_per_request": 30,
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
    provider = str(cfg.get("provider") or "anthropic").strip().lower()
    if provider not in ("anthropic", "openai_compatible"):
        problems.append('"provider" must be "anthropic" or "openai_compatible"; using "anthropic".')
        provider = "anthropic"
    cfg["provider"] = provider
    if not str(cfg[provider].get("api_key") or "").strip():
        problems.append("No API key is set for provider %r in config.json." % provider)
    for section in ("anthropic", "openai_compatible", "images"):
        cfg[section]["api_key"] = str(cfg[section].get("api_key") or "").strip()
    cfg["assistant_name"] = str(cfg.get("assistant_name") or "Helper").strip() or "Helper"
    cfg["user_name"] = str(cfg.get("user_name") or "").strip()


def secrets(cfg):
    """All secret strings in the config, so logs and e-mails can redact them."""
    values = [
        cfg["anthropic"].get("api_key"),
        cfg["openai_compatible"].get("api_key"),
        cfg["images"].get("api_key"),
        cfg["email_reports"].get("smtp_app_password"),
    ]
    return [str(v) for v in values if v and len(str(v)) >= 6]
