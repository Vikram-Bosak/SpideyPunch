"""Configuration loader.

Loads config/settings.yaml plus environment variables.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import yaml

BASE_DIR = Path(__file__).resolve().parent.parent.parent
CONFIG_DIR = BASE_DIR / "config"
STATE_DIR = BASE_DIR / "state"

SETTINGS_PATH = CONFIG_DIR / "settings.yaml"
SCHEDULE_PATH = CONFIG_DIR / "schedule.json"
STATE_PATH = STATE_DIR / "workflow_state.json"


def _load_dotenv() -> None:
    """Minimal .env loader (avoids a hard dependency on python-dotenv)."""
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()


def load_settings() -> dict[str, Any]:
    """Load settings.yaml into a dict."""
    if not SETTINGS_PATH.exists():
        raise FileNotFoundError(f"Missing settings file: {SETTINGS_PATH}")
    with SETTINGS_PATH.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return data


def load_schedule() -> dict[str, Any]:
    """Load the upload schedule (5 staggered slots per 24h)."""
    if not SCHEDULE_PATH.exists():
        raise FileNotFoundError(f"Missing schedule file: {SCHEDULE_PATH}")
    with SCHEDULE_PATH.open(encoding="utf-8") as fh:
        data = json.load(fh)
    return data


def load_state() -> dict[str, Any]:
    """Load the workflow state file (duplicate tracking + retry state)."""
    if STATE_PATH.exists():
        with STATE_PATH.open(encoding="utf-8") as fh:
            return json.load(fh)
    return {"version": 1, "jobs": []}


def save_state(state: dict[str, Any]) -> None:
    """Persist workflow state atomically."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, ensure_ascii=False)
    tmp.replace(STATE_PATH)


def getenv(key: str, default: str | None = None) -> str | None:
    """Read an env var, returning None when unset (instead of raising)."""
    return os.environ.get(key, default)


def require_env(key: str) -> str:
    """Read a required env var or raise."""
    value = os.environ.get(key)
    if not value:
        raise RuntimeError(f"Required environment variable is not set: {key}")
    return value
