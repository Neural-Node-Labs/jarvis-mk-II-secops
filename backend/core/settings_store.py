"""
core/settings_store.py — Runtime Settings Store
=================================================
Replaces ".env-and-restart" for the handful of operational settings that
should be editable from the Settings UI without a container restart:
workspace root, default project, swarm concurrency, task timeout, default
LLM provider/model.

Precedence, highest first:
  1. settings.json override (written via POST /api/settings)
  2. process environment variable (.env / docker-compose / shell export)
  3. hardcoded default in EDITABLE_SETTINGS

This is intentionally NOT a place for most secrets — other API keys stay in
the environment / your process manager's secret store. The one deliberate
exception is LLM_API_KEY: the operator asked for it to be settable from the
Settings UI instead of editing .env and restarting. It's stored the same way
as everything else here (plaintext in settings.json — protect that file's
permissions same as you would a .env file) but describe_settings() always
masks it before it goes back over the wire: the UI only ever sees whether a
key is set and its last 4 characters, never the full value.

Every read goes back to disk-backed state (an in-memory cache, invalidated
on write) rather than a module-level constant captured once at import —
that's what makes an edit here take effect immediately, for every request,
without restarting the process. A constant like `X = os.getenv(...)`
evaluated at import time is exactly the bug this module exists to avoid.

version: 1.0.0
"""
import json
import logging
import os
import re
import threading
from typing import Any, Optional

logger = logging.getLogger("settings_store")

SETTINGS_PATH = os.getenv("JARVIS_SETTINGS_PATH", "./data/settings.json")

_lock  = threading.Lock()
_cache: Optional[dict] = None   # invalidated on every save_settings() call


# ── Registry of editable settings ────────────────────────────────────────────
# type: "path" | "string" | "int" | "float"
# validate: optional callable(value) -> (ok: bool, error_or_normalised_value)
def _validate_path(value: str):
    value = (value or "").strip()
    if not value:
        return False, "Path cannot be empty."
    if not os.path.isabs(value):
        return False, "Path must be absolute (e.g. /app/workspace)."
    try:
        os.makedirs(value, exist_ok=True)
    except Exception as exc:
        return False, f"Cannot create/access this path: {exc}"
    return True, os.path.realpath(value)


def _validate_project_name(value: str):
    value = (value or "").strip()
    if not value or not re.match(r"^[a-zA-Z0-9_\-]{1,48}$", value):
        return False, "Project name must be 1-48 chars: letters, numbers, _ or -."
    return True, value


def _validate_positive_int(value):
    try:
        v = int(value)
    except (TypeError, ValueError):
        return False, "Must be a whole number."
    if v <= 0:
        return False, "Must be greater than 0."
    return True, v


def _validate_url_or_blank(value: str):
    value = (value or "").strip()
    if not value:
        return True, ""   # blank = fall back to the provider's built-in default URL
    if not re.match(r"^https?://[^\s]+$", value):
        return False, "Must be a full http(s):// URL, or blank to use the provider default."
    return True, value.rstrip("/")


def _validate_secret(value: str):
    value = (value or "").strip()
    if not value:
        return True, ""   # blank = clear the override, fall back to env var
    if len(value) < 8:
        return False, "That doesn't look like a valid API key (too short)."
    return True, value


EDITABLE_SETTINGS: dict[str, dict] = {
    "JARVIS_WORKSPACE_ROOT": {
        "label": "Workspace root",
        "description": "Absolute path on this server where agent workspaces/projects live.",
        "type": "path",
        "default": "/app/workspace",
        "validate": _validate_path,
    },
    "JARVIS_DEFAULT_PROJECT": {
        "label": "Default project name",
        "description": "Project name used when none is specified.",
        "type": "string",
        "default": "default",
        "validate": _validate_project_name,
    },
    "JARVIS_MAX_PARALLEL": {
        "label": "Max parallel swarm tasks",
        "description": "Concurrency cap for the planner/swarm and JarvisMKII.",
        "type": "int",
        "default": 10,
        "validate": _validate_positive_int,
    },
    "JARVIS_TASK_TIMEOUT": {
        "label": "Task timeout (seconds)",
        "description": "Hard per-task timeout for background/scheduled tasks.",
        "type": "int",
        "default": 300,
        "validate": _validate_positive_int,
    },
    "LLM_PROVIDER": {
        "label": "Default LLM provider",
        "description": "Used for sessions that don't specify a provider explicitly.",
        "type": "string",
        "default": "deepseek",
        "validate": lambda v: (True, v.strip().lower()) if (v or "").strip() else (False, "Cannot be empty."),
    },
    "LLM_MODEL": {
        "label": "Default LLM model",
        "description": "Used for sessions that don't specify a model explicitly.",
        "type": "string",
        "default": "deepseek-coder",
        "validate": lambda v: (True, v.strip()) if (v or "").strip() else (False, "Cannot be empty."),
    },
    "LLM_BASE_URL": {
        "label": "LLM API base URL",
        "description": "Override the endpoint the default provider talks to (e.g. a "
                        "self-hosted DeepSeek-compatible gateway, a proxy, or a custom "
                        "Ollama host). Leave blank to use the provider's built-in default.",
        "type": "string",
        "default": "",
        "validate": _validate_url_or_blank,
    },
    "LLM_API_KEY": {
        "label": "LLM API key",
        "description": "Saved server-side and used for every LLM call going forward — "
                        "no .env edit or restart needed. Leave blank to clear the "
                        "override and fall back to the {PROVIDER}_API_KEY environment "
                        "variable, if set.",
        "type": "secret",
        "default": "",
        "validate": _validate_secret,
    },
}


# ── Disk I/O ──────────────────────────────────────────────────────────────────

def _read_disk() -> dict:
    try:
        if not os.path.isfile(SETTINGS_PATH):
            return {}
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.warning("[settings_read_error] path=%s err=%s", SETTINGS_PATH, exc)
        return {}


def _write_disk(data: dict) -> None:
    os.makedirs(os.path.dirname(SETTINGS_PATH) or ".", exist_ok=True)
    tmp_path = f"{SETTINGS_PATH}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    os.replace(tmp_path, SETTINGS_PATH)   # atomic on POSIX — no half-written file on crash


def _load_cached() -> dict:
    global _cache
    with _lock:
        if _cache is None:
            _cache = _read_disk()
        return dict(_cache)


# ── Public API ────────────────────────────────────────────────────────────────

def get_setting(key: str, default: Any = None) -> Any:
    """
    Effective value for `key`: settings.json override → env var → registry
    default → caller-supplied `default`. Always re-checks the on-disk cache
    (invalidated on save), so edits are visible to the very next call —
    no restart required.
    """
    overrides = _load_cached()
    if key in overrides:
        return overrides[key]
    env_val = os.getenv(key)
    if env_val is not None:
        return env_val
    if key in EDITABLE_SETTINGS:
        return EDITABLE_SETTINGS[key]["default"]
    return default


def get_setting_int(key: str, default: int) -> int:
    try:
        return int(get_setting(key, default))
    except (TypeError, ValueError):
        return default


def describe_settings() -> dict:
    """Full registry + effective value + source, for the Settings UI.
    "secret"-type values (LLM_API_KEY) are never returned in the clear —
    only whether one is set and its last 4 characters, e.g. "•••• sk91"."""
    overrides = _load_cached()
    out = {}
    for key, meta in EDITABLE_SETTINGS.items():
        if key in overrides:
            value, source = overrides[key], "override"
        elif os.getenv(key) is not None:
            value, source = os.getenv(key), "environment"
        else:
            value, source = meta["default"], "default"

        is_secret = meta["type"] == "secret"
        display_value = _mask_secret(value) if is_secret else value

        out[key] = {
            "value": display_value, "source": source, "label": meta["label"],
            "description": meta["description"], "type": meta["type"],
            "default": meta["default"],
        }
        if is_secret:
            out[key]["is_set"] = bool(value)
    return out


def _mask_secret(value: str) -> str:
    if not value:
        return ""
    tail = value[-4:] if len(value) >= 4 else value
    return f"{'•' * 8}{tail}"


def save_settings(updates: dict) -> dict:
    """
    Validate and persist `updates` (subset of EDITABLE_SETTINGS keys).
    Raises ValueError with an actionable message on the first invalid key.
    Returns the full effective settings (describe_settings()) after saving.
    """
    global _cache
    unknown = [k for k in updates if k not in EDITABLE_SETTINGS]
    if unknown:
        raise ValueError(f"Unknown setting(s): {', '.join(unknown)}. "
                          f"Editable keys: {', '.join(EDITABLE_SETTINGS)}")

    normalised: dict = {}
    for key, raw_value in updates.items():
        validator = EDITABLE_SETTINGS[key].get("validate")
        if validator:
            ok, result = validator(raw_value)
            if not ok:
                raise ValueError(f"{key}: {result}")
            normalised[key] = result
        else:
            normalised[key] = raw_value

    with _lock:
        current = _read_disk()
        current.update(normalised)
        _write_disk(current)
        _cache = current   # refresh cache under the same lock — no stale read window

    logger.info("[settings_saved] keys=%s", ", ".join(normalised.keys()))
    return describe_settings()


def reset_settings(keys: Optional[list] = None) -> dict:
    """Remove override(s) so the key falls back to env/default. keys=None clears all."""
    global _cache
    with _lock:
        current = _read_disk()
        if keys is None:
            current = {}
        else:
            for k in keys:
                current.pop(k, None)
        _write_disk(current)
        _cache = current

    logger.info("[settings_reset] keys=%s", "ALL" if keys is None else ", ".join(keys))
    return describe_settings()
