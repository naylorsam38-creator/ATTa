"""UTC timestamps. Every stored or transmitted time is timezone-aware UTC, serialised as ISO-8601 with 'Z'."""
from __future__ import annotations

from datetime import datetime, timezone


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("naive datetime rejected: all timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


def iso_utc(value: datetime | None = None) -> str:
    v = to_utc(value) if value is not None else utc_now()
    return v.isoformat(timespec="milliseconds").replace("+00:00", "Z")
