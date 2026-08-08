"""Time / scheduling helpers."""

from __future__ import annotations

import random
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .config import load_settings


def get_timezone() -> ZoneInfo:
    """Return the configured schedule timezone."""
    settings = load_settings()
    tz_name = settings.get("app", {}).get("timezone", "America/New_York")
    return ZoneInfo(tz_name)


def now_in_tz() -> datetime:
    """Current wall-clock time in the configured timezone."""
    return datetime.now(get_timezone())


def parse_hhmm(value: str) -> timezone_time:
    """Parse an 'HH:MM' string into a time-like tuple (hour, minute)."""
    parts = value.split(":")
    return timezone_time(int(parts[0]), int(parts[1]))


class timezone_time:
    """Lightweight immutable representation of an HH:MM wall-clock time."""

    __slots__ = ("hour", "minute")

    def __init__(self, hour: int, minute: int) -> None:
        self.hour = hour
        self.minute = minute

    def as_utc_on(self, date: datetime) -> datetime:
        """Convert this wall-clock time to a UTC datetime on the given (tz-aware) date."""
        local = datetime(
            date.year, date.month, date.day, self.hour, self.minute,
            tzinfo=date.tzinfo,
        )
        return local.astimezone(timezone.utc)


def is_due(now: datetime, time_value: str) -> bool:
    """True when the current wall-clock time is at/past the given HH:MM slot."""
    if not time_value:
        return True
    t = parse_hhmm(time_value)
    current_minutes = now.hour * 60 + now.minute
    slot_minutes = t.hour * 60 + t.minute
    return current_minutes >= slot_minutes


def apply_jitter(time_value: str, jitter_minutes: int) -> str:
    """Add a random jitter (in minutes) to an HH:MM slot and return the new HH:MM."""
    t = parse_hhmm(time_value)
    total = t.hour * 60 + t.minute + int(jitter_minutes)
    total = total % (24 * 60)
    return f"{total // 60:02d}:{total % 60:02d}"


def random_jitter(low: int = 1, high: int = 15) -> int:
    """Return a random jitter value in [low, high] minutes."""
    return random.randint(int(low), int(high))


def format_ts(dt: datetime) -> str:
    """Format a tz-aware datetime for reports."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def iso_ts(dt: datetime) -> str:
    """ISO 8601 UTC timestamp (required by Discord embeds)."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
