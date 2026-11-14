"""Plans and runs the downloads behind ``etfp collect daily`` and ``etfp backfill``.

The idea in one paragraph: every raw file we want is described as a *Unit* (which source,
which day folder, which file name, and how to fetch it). A *planner* turns "this day" or
"this date range" into a list of Units. One *executor* runs a list of Units politely and
reports an *Outcome* per unit. Daily and backfill differ only in the plan and in how a
404 is treated, so they share the executor.

Rules the executor enforces (project rules in CLAUDE.md):

* Idempotent: a Unit whose raw file is already in the raw store is not fetched again
  (``force`` overrides). Raw files are never overwritten; the store handles that.
* Failures are loud and specific (source, date, reason) and never stop the other sources.
* A block (HTTP 401/403/429) stops all further requests to that website for this run.
  Retrying a block is what gets an address banned.
* Five failures in a row to one website also stop that website for this run (it is down;
  hammering it helps nobody).
* In backfill, a 404 is remembered in ``data/state`` as "not available" and not asked
  again. In daily, a 404 is a failure: today's file should exist, and a human should look.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Literal

from etfp import calendars
from etfp.collect import (
    amfi_nav,
    nse_bhavcopy,
    nse_etf_listfile,
    nse_secmaster,
    nse_udiff,
    yahoo_intraday,
)
from etfp.collect.backfill_state import BackfillState
from etfp.http import (
    PoliteClient,
    SourceBlockedError,
    SourceError,
    SourceNotFoundError,
)
from etfp.rawstore import RawRecord, RawStore
from etfp.timeutil import IST, require_aware

logger = logging.getLogger("etfp.jobs")

# Names accepted by --sources, and what each one downloads.
BHAVCOPY = "bhavcopy"
UDIFF = "udiff"
SECMASTER = "secmaster"
ETF_LIST = "etf_list"
NAVALL = "navall"
AMFI_HISTORY = "amfi_history"
YAHOO = "yahoo"

DAILY_SOURCES: tuple[str, ...] = (BHAVCOPY, UDIFF, SECMASTER, ETF_LIST, NAVALL, AMFI_HISTORY, YAHOO)
# NSE's list file and AMFI's NAVAll are "latest only" snapshots, so they cannot be backfilled.
BACKFILL_SOURCES: tuple[str, ...] = (BHAVCOPY, UDIFF, SECMASTER, AMFI_HISTORY, YAHOO)
SOURCE_HELP = {
    BHAVCOPY: "NSE full bhavcopy (prices and delivery)",
    UDIFF: "NSE UDiFF bhavcopy (ISIN, exact turnover)",
    SECMASTER: "NSE security master (price band, units outstanding)",
    ETF_LIST: "NSE ETF list file (latest snapshot only)",
    NAVALL: "AMFI NAVAll.txt (latest snapshot only)",
    AMFI_HISTORY: "AMFI NAV history window",
    YAHOO: "Yahoo futures and FX 5-minute bars (interim free fair value)",
}

NSE_HOST = "nseindia.com"
AMFI_HOST = "amfiindia.com"
YAHOO_HOST = "finance.yahoo.com"

# AMFI answers a 12-day window (about 9 MB) but a 30-day window did not finish within 30 s
# (live check 2026-11-13). The maximum window is still OPEN (docs/DECISIONS.md D9), so we
# stay small: a 7-day window is about 6 MB.
AMFI_WINDOW_DAYS = 7
MAX_CONSECUTIVE_FAILURES = 5

Fetcher = Callable[[PoliteClient, RawStore], RawRecord]
Status = Literal["fetched", "exists", "unavailable", "failed"]


@dataclass(frozen=True)
class Unit:
    """One raw file we want: where it goes, and how to get it."""

    source: str  # raw-store source folder, e.g. "nse_bhavcopy_full"
    day: date  # raw-store day folder
    name: str  # file name (version 1) inside that folder
    host: str  # which website (rate limits and blocks are per website)
    fetch: Fetcher = field(repr=False, compare=False)

    @property
    def label(self) -> str:
        return f"{self.source} {self.day.isoformat()} {self.name}"


@dataclass(frozen=True)
class Outcome:
    """What happened to one Unit."""

    unit: Unit
    status: Status
    detail: str
    record: RawRecord | None = None


@dataclass(frozen=True)
class Plan:
    units: list[Unit]
    notes: list[str]  # things deliberately not planned, with the reason (always shown)


# -- planning -----------------------------------------------------------------------


def parse_sources(values: Iterable[str] | None, *, allowed: Sequence[str]) -> list[str]:
    """Turn ``--sources a,b --sources c`` into a validated, de-duplicated list in order."""
    if not values:
        return list(allowed)
    wanted: list[str] = []
    for value in values:
        for part in value.split(","):
            name = part.strip().lower()
            if not name:
                continue
            if name not in allowed:
                raise ValueError(f"unknown source {name!r}; choose from: {', '.join(allowed)}")
            if name not in wanted:
                wanted.append(name)
    if not wanted:
        raise ValueError(f"no source given; choose from: {', '.join(allowed)}")
    return wanted


def _nse_unit(
    module,
    day: date,
    fetch: Fetcher,
) -> Unit:
    return Unit(
        source=module.SOURCE, day=day, name=module.raw_name(day), host=NSE_HOST, fetch=fetch
    )


def _single_day_units(key: str, day: date) -> list[Unit]:
    """Units for the sources that have one file per trading day (and can be asked for any day)."""
    if key == BHAVCOPY:
        return [_nse_unit(nse_bhavcopy, day, lambda c, s: nse_bhavcopy.fetch(c, s, day))]
    if key == UDIFF:
        return [_nse_unit(nse_udiff, day, lambda c, s: nse_udiff.fetch(c, s, day))]
    if key == SECMASTER:
        return [_nse_unit(nse_secmaster, day, lambda c, s: nse_secmaster.fetch(c, s, day))]
    raise ValueError(f"not a per-day source: {key}")  # pragma: no cover


def _yahoo_units(day: date, now: datetime) -> tuple[list[Unit], str | None]:
    """Four units (two futures, two currencies), or none if Yahoo no longer holds the day."""
    start, _ = yahoo_intraday.window(day)
    limit = yahoo_intraday.MAX_HISTORY_DAYS[yahoo_intraday.DEFAULT_INTERVAL]
    if start < now - timedelta(days=limit):
        return [], (
            f"{YAHOO} {day.isoformat()}: older than the ~{limit + 1} days of "
            f"{yahoo_intraday.DEFAULT_INTERVAL} bars Yahoo keeps; NOT recoverable"
        )
    units = [
        Unit(
            source=yahoo_intraday.SOURCE,
            day=day,
            name=yahoo_intraday.raw_name(symbol),
            host=YAHOO_HOST,
            fetch=lambda c, s, symbol=symbol: yahoo_intraday.fetch(c, s, day, symbol, now=now),
        )
        for symbol in yahoo_intraday.SYMBOLS
    ]
    return units, None


def _amfi_history_unit(start: date, end: date) -> Unit:
    return Unit(
        source=amfi_nav.SOURCE_HISTORY,
        day=end,
        name=amfi_nav.history_raw_name(start, end),
        host=AMFI_HOST,
        fetch=lambda c, s: amfi_nav.fetch_history(c, s, start, end),
    )


def plan_daily(day: date, sources: Sequence[str], *, now: datetime) -> Plan:
    """What `etfp collect daily --date <day>` should download.

    * NSE files: one per source for `day` (the caller has already checked it is a session).
    * NSE ETF list and AMFI NAVAll: only the *latest* file exists, so they are fetched only
      when `day` is today; for a past date they are skipped with a note (storing today's
      file under an old date would be a lie in the raw folder).
    * AMFI history: the window is the previous NSE session AND `day`. Why two days: a
      US-linked ETF's NAV for day t is published the next morning (SEBI T+1, DECISIONS D10).
      Run in the evening of t, the window holds t-1's late NAV; run again the next
      morning, it holds t's. Saved under `day`; downstream cleaning keeps the newest copy
      of a (scheme, nav_date) pair.
    * Yahoo: four symbols, if Yahoo still holds the day.
    """
    require_aware(now, "now")
    today = now.astimezone(IST).date()
    units: list[Unit] = []
    notes: list[str] = []
    for key in sources:
        if key in (BHAVCOPY, UDIFF, SECMASTER):
            units += _single_day_units(key, day)
        elif key == ETF_LIST:
            if day == today:
                units.append(
                    _nse_unit(nse_etf_listfile, day, lambda c, s: nse_etf_listfile.fetch(c, s, day))
                )
            else:
                notes.append(
                    f"{nse_etf_listfile.SOURCE} {day.isoformat()}: skipped, the list file only "
                    "exists as today's latest version; run this on the day itself"
                )
        elif key == NAVALL:
            if day == today:
                units.append(
                    Unit(
                        source=amfi_nav.SOURCE_NAVALL,
                        day=day,
                        name=amfi_nav.NAVALL_NAME,
                        host=AMFI_HOST,
                        fetch=lambda c, s: amfi_nav.fetch_navall(c, s, day),
                    )
                )
            else:
                notes.append(
                    f"{amfi_nav.SOURCE_NAVALL} {day.isoformat()}: skipped, NAVAll.txt only holds "
                    "the latest NAVs; the AMFI history window covers past dates"
                )
        elif key == AMFI_HISTORY:
            try:
                start = calendars.previous_session("IN", day)
                # Skip weekend special sessions (Diwali Muhurat on a Sunday, D24):
                # overseas NAVs are dated by the last regular weekday, so the
                # window must reach back to it or Friday's late NAV is missed.
                while start.weekday() >= 5:
                    start = calendars.previous_session("IN", start)
            except ValueError:
                start = day
            units.append(_amfi_history_unit(start, day))
        elif key == YAHOO:
            yahoo_units, note = _yahoo_units(day, now)
            units += yahoo_units
            if note:
                notes.append(note)
        else:
            raise ValueError(f"unknown source {key!r}")
    return Plan(units, notes)


def amfi_windows(
    start: date, end: date, *, days: int = AMFI_WINDOW_DAYS
) -> list[tuple[date, date]]:
    """Split start..end into windows of at most `days`, on a fixed calendar grid.

    The grid is anchored to the calendar (not to `start`), so running a backfill again with
    a different --from reuses the same file names and the raw store skips them. Only the
    first and last windows can be shorter.
    """
    if days < 1:
        raise ValueError("days must be at least 1")
    windows: list[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        block_end = cursor + timedelta(days=days - 1 - cursor.toordinal() % days)
        stop = min(block_end, end)
        windows.append((cursor, stop))
        cursor = stop + timedelta(days=1)
    return windows


def plan_backfill(start: date, end: date, sources: Sequence[str], *, now: datetime) -> Plan:
    """What `etfp backfill --from start --to end` should download, oldest first per source.

    NSE per-day files are planned for NSE trading days only (calendar). If the calendar is
    wrong about a day, NSE answers 404 and the state file remembers it. AMFI history is
    planned in small windows ending no later than today. Yahoo is planned only for days it
    still holds; older days are listed in a note as lost.
    """
    require_aware(now, "now")
    today = now.astimezone(IST).date()
    if start > end:
        raise ValueError(f"--from {start} is after --to {end}")
    units: list[Unit] = []
    notes: list[str] = []
    session_days = calendars.sessions("IN", start, min(end, today))
    for key in sources:
        if key in (BHAVCOPY, UDIFF, SECMASTER):
            for day in session_days:
                units += _single_day_units(key, day)
        elif key == AMFI_HISTORY:
            if start <= today:
                units += [_amfi_history_unit(s, e) for s, e in amfi_windows(start, min(end, today))]
        elif key == YAHOO:
            lost = 0
            for day in session_days:
                yahoo_units, note = _yahoo_units(day, now)
                units += yahoo_units
                lost += note is not None
            if lost:
                notes.append(
                    f"{YAHOO}: {lost} trading day(s) are older than Yahoo's intraday history and "
                    "were not planned (NOT recoverable)"
                )
        elif key in (ETF_LIST, NAVALL):
            notes.append(f"{key}: latest-snapshot file, cannot be backfilled; ignored")
        else:
            raise ValueError(f"unknown source {key!r}")
    if end > today:
        notes.append(f"--to {end} is in the future; stopped at today ({today})")
    return Plan(units, notes)


# -- running ------------------------------------------------------------------------


def _not_found_hint(unit: Unit) -> str:
    if unit.host == NSE_HOST:
        return "not published (yet?). NSE posts the daily files in the evening, after ~19:00 IST"
    return "not found"


def run_units(
    units: Iterable[Unit],
    client: PoliteClient,
    store: RawStore,
    *,
    force: bool = False,
    state: BackfillState | None = None,
    retry_unavailable: bool = False,
    on_outcome: Callable[[Outcome], None] | None = None,
) -> list[Outcome]:
    """Fetch every Unit that is not already in the raw store; return one Outcome per unit.

    With `state` given (backfill mode) a 404 is recorded as "unavailable" and remembered;
    without it (daily mode) a 404 is a failure. Never raises for a failed download.
    """
    outcomes: list[Outcome] = []
    stopped_hosts: dict[str, str] = {}  # host -> why we stopped talking to it
    streak: dict[str, int] = {}  # host -> consecutive failures

    def done(outcome: Outcome) -> None:
        outcomes.append(outcome)
        if on_outcome is not None:
            on_outcome(outcome)

    for unit in units:
        if not force and store.exists(unit.source, unit.day, unit.name):
            done(Outcome(unit, "exists", "already in the raw store"))
            continue
        if state is not None and not force and not retry_unavailable:
            known = state.get(unit.source, unit.day, unit.name)
            if known is not None:
                done(
                    Outcome(
                        unit,
                        "unavailable",
                        f"known not available (HTTP {known.status_code}, checked "
                        f"{known.checked_at.date().isoformat()})",
                    )
                )
                continue
        if unit.host in stopped_hosts:
            done(Outcome(unit, "failed", f"not tried: {stopped_hosts[unit.host]}"))
            continue
        try:
            record = unit.fetch(client, store)
        except SourceNotFoundError as exc:
            streak[unit.host] = 0  # the site answered; a 404 is not an outage
            if state is not None:
                state.mark_unavailable(
                    unit.source,
                    unit.day,
                    unit.name,
                    status_code=exc.status_code,
                    url=exc.url,
                    note="HTTP 404 during backfill",
                )
                done(Outcome(unit, "unavailable", "HTTP 404, recorded as not available"))
            else:
                done(Outcome(unit, "failed", f"HTTP 404: {_not_found_hint(unit)}"))
            continue
        except SourceBlockedError as exc:
            reason = f"blocked by {unit.host} (HTTP {exc.status_code}); no more requests to it"
            stopped_hosts[unit.host] = reason
            done(Outcome(unit, "failed", f"{exc}"))
            continue
        except Exception as exc:  # report loudly, keep the other sources going
            logger.warning("%s failed: %s: %s", unit.label, type(exc).__name__, exc)
            streak[unit.host] = streak.get(unit.host, 0) + 1
            if streak[unit.host] >= MAX_CONSECUTIVE_FAILURES:
                stopped_hosts[unit.host] = (
                    f"{unit.host} failed {MAX_CONSECUTIVE_FAILURES} times in a row"
                )
            kind = "" if isinstance(exc, SourceError) else f"{type(exc).__name__}: "
            done(Outcome(unit, "failed", f"{kind}{exc}"))
            continue
        streak[unit.host] = 0
        if state is not None:
            state.forget(unit.source, unit.day, unit.name)  # it exists after all
        detail = f"{record.relpath} ({record.size_bytes} bytes"
        detail += ", identical to stored copy)" if record.duplicate else f", v{record.version})"
        done(Outcome(unit, "fetched", detail, record))
    return outcomes


def summarize(outcomes: Sequence[Outcome]) -> dict[str, int]:
    """Count outcomes by status."""
    counts = {"fetched": 0, "exists": 0, "unavailable": 0, "failed": 0}
    for outcome in outcomes:
        counts[outcome.status] += 1
    return counts
