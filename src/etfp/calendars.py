"""Trading calendars for the three markets the project touches.

Why calendars matter here (plain words)
---------------------------------------
An India-listed ETF that holds US shares publishes a NAV (net asset value: what
its holdings are worth per unit). On an Indian trading day that NAV is built
from the *last US close that had already happened*. If the US was shut (Labor
Day, Thanksgiving) the "latest" US price is older than usual, so the NAV is
stale in a way that changes the premium we compute. Likewise the price-band
rules count "two Indian trading days back" (T-2), so one missing or extra
holiday in our calendar silently shifts every band and every premium measure.

So: every "which day is the previous one?" question in this project goes
through this module, never through ``timedelta(days=1)``.

Markets and library calendars
-----------------------------
- ``"IN"`` NSE (India). The library has no NSE calendar; BSE (``XBOM``) shares
  exchange holidays with NSE, so we use ``XBOM``.
- ``"US"`` NYSE (``XNYS``).
- ``"HK"`` HKEX (``XHKG``).

``exchange_calendars`` only records Indian holidays up to a fixed year (2026 in
the version we tested). The upper bound is read from the library itself
(``bound_max()``), not hard-coded. Beyond it we fall back to "Monday-Friday
minus the holidays typed into ``config/calendars/nse_holidays.yaml``". If a
year has no holiday list at all we treat every weekday as a session and call
the answer *provisional*: a recorder that runs on a holiday wastes a request,
but a recorder that skips a real trading day loses data that cannot be
re-created.

Everything is cached because building a calendar object is slow. Tests that
point at a different yaml should call :func:`clear_caches`.
"""

from __future__ import annotations

import os
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import cache, lru_cache
from pathlib import Path
from typing import Any, Literal

import exchange_calendars as xc
import pandas as pd
import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from etfp.paths import config_dir
from etfp.timeutil import HONG_KONG, IST, NEW_YORK

Market = Literal["IN", "US", "HK"]

SAMPLE_START = date(2021, 1, 1)
"""Earliest date the project needs (start of the research sample)."""

MAX_DATE = date(2030, 12, 31)
"""Dates after this are refused: a typo like 2206 should fail loudly."""

NSE_HOLIDAYS_ENV = "ETFP_NSE_HOLIDAYS_FILE"
"""Environment variable that points at an alternative overrides yaml (for tests)."""

_LIBRARY_NAME = {"IN": "XBOM", "US": "XNYS", "HK": "XHKG"}
_ZONE = {"IN": IST, "US": NEW_YORK, "HK": HONG_KONG}

# Regular NSE hours, used where the library has no data (years beyond its range).
_IN_OPEN = time(9, 15)
_IN_CLOSE = time(15, 30)

_Date = date  # alias: lets a model field be called "date" without shadowing the type


# --------------------------------------------------------------------------
# Overrides file (config/calendars/nse_holidays.yaml)
# --------------------------------------------------------------------------
class DatedReason(BaseModel):
    """One date plus the reason it is listed (so a future reader knows why)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    date: _Date
    reason: str = Field(min_length=1)


class NseOverrides(BaseModel):
    """Manual corrections to the NSE calendar.

    - ``extra_holidays``: dates the library thinks are sessions but NSE was shut.
    - ``extra_sessions``: dates the library thinks are closed but NSE traded
      (for example a special Saturday session).
    - ``holidays``: year -> holiday dates, used *only* for years beyond the
      library's recorded range. A year that is absent here is "unknown".
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    extra_holidays: tuple[DatedReason, ...] = ()
    extra_sessions: tuple[DatedReason, ...] = ()
    holidays: dict[int, tuple[_Date, ...]] = Field(default_factory=dict)

    @field_validator("holidays", mode="before")
    @classmethod
    def _unwrap_holiday_entries(cls, value: Any) -> Any:
        """Accept plain dates or ``{date:, reason:}`` mappings in a year's list."""
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("holidays must be a mapping of year -> list of dates")
        out: dict[Any, list[Any]] = {}
        for year, entries in value.items():
            items = entries or []
            out[year] = [e["date"] if isinstance(e, dict) and "date" in e else e for e in items]
        return out

    @field_validator("extra_holidays", "extra_sessions", mode="before")
    @classmethod
    def _none_is_empty(cls, value: Any) -> Any:
        return () if value is None else value

    @model_validator(mode="after")
    def _consistent(self) -> NseOverrides:
        for year, days in self.holidays.items():
            for d in days:
                if d.year != year:
                    raise ValueError(f"holiday {d} is listed under year {year}")
        clash = {r.date for r in self.extra_holidays} & {r.date for r in self.extra_sessions}
        if clash:
            raise ValueError(f"dates listed as both extra holiday and extra session: {clash}")
        return self


def default_overrides_path() -> Path:
    """Overrides file to use: $ETFP_NSE_HOLIDAYS_FILE, else the project's own."""
    env = os.environ.get(NSE_HOLIDAYS_ENV)
    if env:
        return Path(env).expanduser()
    return config_dir() / "calendars" / "nse_holidays.yaml"


def load_nse_overrides(path: Path | None = None) -> NseOverrides:
    """Read and validate the NSE overrides yaml (a missing file means no overrides)."""
    target = path if path is not None else default_overrides_path()
    if not target.exists():
        return NseOverrides()
    with target.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{target}: expected a mapping at the top level")
    try:
        return NseOverrides.model_validate(raw)
    except ValueError as exc:
        raise ValueError(f"{target}: invalid NSE overrides: {exc}") from exc


# --------------------------------------------------------------------------
# Building one market's session list
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class _MarketData:
    """Everything we need to answer calendar questions for one market."""

    sessions: tuple[date, ...]  # ascending, SAMPLE_START..MAX_DATE
    session_set: frozenset[date]
    library: Any  # the exchange_calendars calendar object (covers up to library_end)
    library_end: date  # last day the library has recorded holidays for
    holiday_years: frozenset[int]  # years with an explicit holidays list


@cache
def _library_end(market: str) -> date:
    """Last date the library records for this market, capped at MAX_DATE.

    ``bound_max()`` is the library's own statement of how far its holiday
    rules go (None means "no fixed limit", e.g. NYSE which is rule-based).
    """
    default_cal = xc.get_calendar(_LIBRARY_NAME[market])
    bound = type(default_cal).bound_max()
    if bound is None:
        return MAX_DATE
    return min(pd.Timestamp(bound).date(), MAX_DATE)


@cache
def _library_calendar(market: str) -> Any:
    """The (slow to build) library calendar covering SAMPLE_START..library end."""
    end = _library_end(market)
    try:
        return xc.get_calendar(_LIBRARY_NAME[market], start=SAMPLE_START, end=end)
    except ValueError as exc:  # library refuses the range: say so clearly
        raise ValueError(
            f"exchange_calendars cannot build {_LIBRARY_NAME[market]} for "
            f"{SAMPLE_START}..{end}: {exc}"
        ) from exc


def _weekdays(start: date, end: date) -> list[date]:
    out: list[date] = []
    day = start
    while day <= end:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


def _overrides_key(path: Path) -> tuple[str, int | None]:
    """Cache key for the overrides file: its path plus last-modified time."""
    try:
        return (str(path), path.stat().st_mtime_ns)
    except OSError:
        return (str(path), None)


@lru_cache(maxsize=32)
def _build(market: str, overrides_key: tuple[str, int | None]) -> _MarketData:
    library = _library_calendar(market)
    lib_end = _library_end(market)
    sessions = {ts.date() for ts in library.sessions}
    holiday_years: frozenset[int] = frozenset()

    if market == "IN":
        ov = load_nse_overrides(Path(overrides_key[0]))
        holiday_years = frozenset(ov.holidays)
        # Beyond the library's range: weekdays minus the listed holidays.
        # (Years with no list at all are "provisional": every weekday counts.)
        if lib_end < MAX_DATE:
            listed = {d for days in ov.holidays.values() for d in days}
            for day in _weekdays(lib_end + timedelta(days=1), MAX_DATE):
                if day not in listed:
                    sessions.add(day)
        sessions -= {r.date for r in ov.extra_holidays}
        sessions |= {r.date for r in ov.extra_sessions}
    elif lib_end < MAX_DATE:
        # No holiday source for this market beyond the library: plain weekdays.
        sessions |= set(_weekdays(lib_end + timedelta(days=1), MAX_DATE))

    ordered = tuple(sorted(d for d in sessions if SAMPLE_START <= d <= MAX_DATE))
    return _MarketData(
        sessions=ordered,
        session_set=frozenset(ordered),
        library=library,
        library_end=lib_end,
        holiday_years=holiday_years,
    )


def clear_caches(*, libraries: bool = True) -> None:
    """Forget cached calendars (tests that switch the overrides yaml call this).

    ``libraries=False`` keeps the slow-to-build library calendars and only drops
    the session lists that depend on the overrides yaml.
    """
    _build.cache_clear()
    if libraries:
        _library_calendar.cache_clear()
        _library_end.cache_clear()


def library_end(market: Market) -> date:
    """Last date for which ``exchange_calendars`` records holidays for ``market``."""
    _check_market(market)
    return _library_end(market)


def _check_market(market: str) -> None:
    if market not in _LIBRARY_NAME:
        raise ValueError(f"unknown market {market!r}; expected one of {sorted(_LIBRARY_NAME)}")


def _check_day(day: date) -> None:
    if day < SAMPLE_START:
        raise ValueError(f"{day} is before the project sample start {SAMPLE_START}")
    if day > MAX_DATE:
        raise ValueError(f"{day} is after the supported calendar horizon {MAX_DATE}")


def _data(market: Market) -> _MarketData:
    _check_market(market)
    return _build(market, _overrides_key(default_overrides_path()))


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------
def is_session(market: Market, day: date) -> bool:
    """True if ``market`` is open for trading on ``day`` (a holiday or weekend is False)."""
    _check_day(day)
    return day in _data(market).session_set


def sessions(market: Market, start: date, end: date) -> list[date]:
    """All trading days from ``start`` to ``end``, both inclusive, ascending."""
    _check_day(start)
    _check_day(end)
    data = _data(market)
    lo = bisect_left(data.sessions, start)
    hi = bisect_right(data.sessions, end)
    return list(data.sessions[lo:hi])


def previous_session(market: Market, day: date) -> date:
    """Latest trading day strictly before ``day`` (``day`` need not be a session)."""
    _check_day(day)
    data = _data(market)
    idx = bisect_left(data.sessions, day)
    if idx == 0:
        raise ValueError(f"no {market} session before {day} inside the supported range")
    return data.sessions[idx - 1]


def next_session(market: Market, day: date) -> date:
    """Earliest trading day strictly after ``day`` (``day`` need not be a session)."""
    _check_day(day)
    data = _data(market)
    idx = bisect_right(data.sessions, day)
    if idx == len(data.sessions):
        raise ValueError(f"no {market} session after {day} inside the supported range")
    return data.sessions[idx]


def last_session_on_or_before(market: Market, day: date) -> date:
    """``day`` itself if it is a session, otherwise the closest earlier session.

    This is the "which US close was the latest one available?" question: on
    2026-09-07 (US Labor Day) the answer for ``"US"`` is 2026-09-04.
    """
    _check_day(day)
    data = _data(market)
    idx = bisect_right(data.sessions, day)
    if idx == 0:
        raise ValueError(f"no {market} session on or before {day} inside the supported range")
    return data.sessions[idx - 1]


def _session_time(market: Market, day: date, which: Literal["open", "close"]) -> datetime:
    _check_day(day)
    data = _data(market)
    if day not in data.session_set:
        raise ValueError(f"{day} is not a {market} trading session")
    zone = _ZONE[market]
    stamp = pd.Timestamp(day)
    if stamp in data.library.sessions:
        lib = data.library
        ts = lib.session_open(stamp) if which == "open" else lib.session_close(stamp)
        return ts.to_pydatetime().astimezone(zone)
    if market == "IN":
        # Beyond the library range, or a manually added special session.
        return datetime.combine(day, _IN_OPEN if which == "open" else _IN_CLOSE, tzinfo=zone)
    raise ValueError(f"no {which} time available for {market} on {day}")  # pragma: no cover


def market_close(market: Market, day: date) -> datetime:
    """Close time of the session on ``day``, timezone-aware in the market's own zone.

    Early-close days (for example the day after US Thanksgiving) come back
    earlier than the usual closing time. Raises ``ValueError`` if ``day`` is
    not a session.
    """
    return _session_time(market, day, "close")


def market_open(market: Market, day: date) -> datetime:
    """Opening time of the session on ``day``, timezone-aware in the market's own zone."""
    return _session_time(market, day, "open")


def is_provisional(market: Market, day: date) -> bool:
    """True when ``day`` is only a guess: India, past the library range, no holiday list.

    In that case every weekday is treated as a session. It becomes False once
    the year's holidays are typed into ``nse_holidays.yaml``.
    """
    _check_day(day)
    data = _data(market)
    if market != "IN":
        return False
    return day > data.library_end and day.year not in data.holiday_years


def is_nse_session(day: date) -> bool:
    """Shortcut for ``is_session("IN", day)``."""
    return is_session("IN", day)
