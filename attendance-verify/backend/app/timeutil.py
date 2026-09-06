"""One place that decides what a timestamp means.

SQLite gives back naive datetimes for columns we wrote as aware ones, so every
module that compared a stored time against `now()` grew its own private
`_aware()` helper. Five copies of the same three lines is five chances for one of
them to be wrong about the default zone — and being wrong here means a class is
open when it should be shut, or shut when a hall full of students is waiting.
"""
from __future__ import annotations

from datetime import UTC, datetime


def now() -> datetime:
    """The current instant, always tz-aware, always UTC."""
    return datetime.now(UTC)


def aware(dt: datetime | None) -> datetime | None:
    """Read a stored timestamp as UTC when the database dropped its zone."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def aware_or_now(dt: datetime | None) -> datetime:
    """`aware`, for callers that have nothing sensible to do with None."""
    return aware(dt) or now()


def iso(dt: datetime | None) -> str:
    """ISO-8601 in UTC, or "" for a missing timestamp."""
    value = aware(dt)
    return value.isoformat() if value else ""
