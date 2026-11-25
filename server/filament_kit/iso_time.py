"""Shared UTC ISO-8601 timestamp helpers."""
from __future__ import annotations

from datetime import datetime, timezone


def utc_now(now: datetime | None = None) -> datetime:
    """Return *now* normalized to UTC (naive values are treated as UTC; defaults to current UTC time)."""
    now = now or datetime.now(timezone.utc)
    return now.astimezone(timezone.utc) if now.tzinfo else now.replace(tzinfo=timezone.utc)


def parse_utc(text: object) -> datetime | None:
    """Parse an ISO-8601 string to an aware UTC datetime, returning None for empty or invalid values."""
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        dt = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
