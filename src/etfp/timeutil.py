"""Time zones and clock helpers.

Rule: no naive datetimes anywhere. Indian market data is stamped in
Asia/Kolkata; underlying markets in their own zones. The code must not assume
the machine's own time zone.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
NEW_YORK = ZoneInfo("America/New_York")
HONG_KONG = ZoneInfo("Asia/Hong_Kong")
UTC = ZoneInfo("UTC")


def now_ist() -> datetime:
    """Current time as a timezone-aware datetime in IST."""
    return datetime.now(tz=IST)


def today_ist() -> date:
    """Today's calendar date in India (not the machine's local date)."""
    return now_ist().date()


def require_aware(ts: datetime, name: str = "timestamp") -> datetime:
    """Raise if a datetime has no time zone; return it unchanged otherwise."""
    if ts.tzinfo is None or ts.tzinfo.utcoffset(ts) is None:
        raise ValueError(f"{name} must be timezone-aware, got naive datetime {ts!r}")
    return ts
