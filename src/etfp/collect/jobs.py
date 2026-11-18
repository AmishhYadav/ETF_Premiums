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
* Three "could not connect at all" errors in a row, spread over at least two different
  websites, stop ALL remaining downloads of the run: when several unrelated sites are
  unreachable at once, the problem is our own network, not theirs (``NetworkBreaker``).
* In backfill (and the daily catch-up of older days), a 404 is remembered in ``data/state``
  as "not available" and not asked again. For the run's own day, a 404 is a failure:
  today's file should exist, and a human should look.
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
    bse_bhavcopy,
    fbil_refrates,
    ibja_rates,
    notices,
    nse_bhavcopy,
    nse_etf_listfile,
    nse_secmaster,
    nse_slb,
    nse_symbolchange,
    nse_udiff,
    yahoo_intraday,
)
from etfp.collect.backfill_state import BackfillState
from etfp.http import (
    PoliteClient,
    SourceBlockedError,
    SourceError,
    SourceNotFoundError,
    is_connection_failure,
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
BSE = "bse"  # BSE daily bhavcopy (R9)
SLB = "slb"  # NSE securities lending: eligible list + SLB bhavcopy (R8)
NOTICES = "notices"  # SEBI + NSE notice listings (notice reader, SPEC section 3)
IBJA = "ibja"  # IBJA gold/silver reference rates (bullion module, D35; research only, D41)
FBIL = "fbil"  # FBIL official rupee reference rates, 31-day rolling window (D43, D45)
SYMBOLCHANGE = "symbolchange"  # NSE symbol-change list, checked against config at build (D45)

DAILY_SOURCES: tuple[str, ...] = (
    BHAVCOPY,
    UDIFF,
    SECMASTER,
    ETF_LIST,
    NAVALL,
    AMFI_HISTORY,
    YAHOO,
    BSE,
    SLB,
    NOTICES,
    IBJA,
    FBIL,
    SYMBOLCHANGE,
)
# NSE's list file and AMFI's NAVAll are "latest only" snapshots, so they cannot be backfilled.
BACKFILL_SOURCES: tuple[str, ...] = (BHAVCOPY, UDIFF, SECMASTER, AMFI_HISTORY, YAHOO, BSE, SLB)
SOURCE_HELP = {
    BHAVCOPY: "NSE full bhavcopy (prices and delivery)",
    UDIFF: "NSE UDiFF bhavcopy (ISIN, exact turnover)",
    SECMASTER: "NSE security master (price band, units outstanding)",
    ETF_LIST: "NSE ETF list file (latest snapshot only)",
    NAVALL: "AMFI NAVAll.txt (latest snapshot only)",
    AMFI_HISTORY: "AMFI NAV history window",
    YAHOO: "Yahoo futures and FX 5-minute bars (interim free fair value)",
    BSE: "BSE daily bhavcopy (BSE close, units, value, trades)",
    SLB: "NSE securities lending: eligible list and SLB bhavcopy",
    NOTICES: "SEBI RSS + Legal listing and NSE circulars (last 7 days)",
    IBJA: "IBJA gold/silver reference rates page (latest snapshot only; research use only)",
    FBIL: "FBIL rupee reference rates, the 31 days ending on the run day (one request)",
    SYMBOLCHANGE: "NSE symbol-change list (latest snapshot only; build checks it against config)",
}

NSE_HOST = "nseindia.com"
BSE_HOST = "bseindia.com"
AMFI_HOST = "amfiindia.com"
YAHOO_HOST = "finance.yahoo.com"
IBJA_HOST = "ibjarates.com"
FBIL_HOST = fbil_refrates.HOST
SEBI_HOST = notices.SEBI_HOST

# AMFI answers a 12-day window (about 9 MB) but a 30-day window did not finish within 30 s
# (live check 2026-11-13). The maximum window is still OPEN (docs/DECISIONS.md D9), so we
# stay small: a 7-day window is about 6 MB.
AMFI_WINDOW_DAYS = 7
MAX_CONSECUTIVE_FAILURES = 5
# Connection failures in a row (over at least two websites) that mean "our network is down".
NETWORK_DOWN_AFTER = 3

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


def _bse_unit(day: date) -> Unit:
    return Unit(
        source=bse_bhavcopy.SOURCE,
        day=day,
        name=bse_bhavcopy.raw_name(day),
        host=BSE_HOST,
        fetch=lambda c, s: bse_bhavcopy.fetch(c, s, day),
    )


def _slb_units(day: date) -> list[Unit]:
    """Two NSE archive files per day: the eligible list and the SLB bhavcopy."""
    return [
        Unit(
            source=nse_slb.SOURCE_ELIGIBLE,
            day=day,
            name=nse_slb.eligible_raw_name(day),
            host=NSE_HOST,
            fetch=lambda c, s: nse_slb.fetch_eligible(c, s, day),
        ),
        Unit(
            source=nse_slb.SOURCE_BHAVCOPY,
            day=day,
            name=nse_slb.bhavcopy_raw_name(day),
            host=NSE_HOST,
            fetch=lambda c, s: nse_slb.fetch_bhavcopy(c, s, day),
        ),
    ]


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
        elif key == BSE:
            units.append(_bse_unit(day))
        elif key == SLB:
            units += _slb_units(day)
        elif key == NOTICES:
            start = day - timedelta(days=NOTICE_WINDOW_DAYS)
            units += notice_window_units(start, day)
            if day == today:
                units.append(
                    Unit(
                        source=notices.SOURCE_SEBI_RSS,
                        day=day,
                        name=notices.SEBI_RSS_NAME,
                        host=SEBI_HOST,
                        fetch=lambda c, s: notices.fetch_sebi_rss(c, s, day),
                    )
                )
            else:
                notes.append(
                    f"{notices.SOURCE_SEBI_RSS} {day.isoformat()}: skipped, the feed only holds "
                    "the latest items; the SEBI listing window covers past dates"
                )
        elif key == IBJA:
            # The IBJA page is a "latest" snapshot (today's AM/PM rates plus ~84 days of PM
            # history in its chart data), so like NAVAll it is only fetched for today.
            # The evening run (19:45 IST) comes after IBJA's PM poll (16:40-17:00 IST).
            # Research use only: IBJA asks valuation users to buy its API, so these rates
            # never go into the open dataset (DECISIONS D41).
            if day == today:
                units.append(
                    Unit(
                        source=ibja_rates.SOURCE,
                        day=day,
                        name=ibja_rates.RAW_NAME,
                        host=IBJA_HOST,
                        fetch=lambda c, s: ibja_rates.fetch(c, s, day),
                    )
                )
            else:
                notes.append(
                    f"{ibja_rates.SOURCE} {day.isoformat()}: skipped, the IBJA page only holds "
                    "the latest rates (its chart data covers recent past days)"
                )
        elif key == FBIL:
            # One request for the 31 calendar days ending on the run day. The public feed
            # lags about a week (D43), so each run re-asks a rolling window; overlapping
            # windows are fine (the cleaner keeps the newest file's rate per date). Not in
            # the catch-up: the window already reaches back further than the catch-up does.
            start = day - timedelta(days=fbil_refrates.DAILY_WINDOW_DAYS - 1)
            units.append(
                Unit(
                    source=fbil_refrates.SOURCE,
                    day=day,
                    name=fbil_refrates.raw_name(start, day),
                    host=FBIL_HOST,
                    fetch=lambda c, s, start=start: fbil_refrates.fetch(c, s, day, start, day),
                )
            )
        elif key == SYMBOLCHANGE:
            # NSE overwrites one list in place, so only "today" can be saved honestly.
            # Daily (not weekly): ~70 KB, one request, and an identical list is stored
            # once; a rename takes effect the morning NSE announces it, so the daily check
            # is what gives the build a chance to warn before a fund's history splits.
            if day == today:
                units.append(
                    _nse_unit(
                        nse_symbolchange,
                        day,
                        lambda c, s: nse_symbolchange.fetch(c, s, day),
                    )
                )
            else:
                notes.append(
                    f"{nse_symbolchange.SOURCE} {day.isoformat()}: skipped, the list only "
                    "exists as today's latest version; run this on the day itself"
                )
        else:
            raise ValueError(f"unknown source {key!r}")
    return Plan(units, notes)


# Notices: each daily run asks for the last 7 days, so a missed run or a long weekend
# loses nothing (windows overlap; the cleaner merges repeats by notice_id).
NOTICE_WINDOW_DAYS = 7


def notice_window_units(start: date, end: date) -> list[Unit]:
    """SEBI Legal listing + NSE circulars for [start, end], stored under `end`."""
    return [
        Unit(
            source=notices.SOURCE_SEBI_LISTING,
            day=end,
            name=notices.sebi_listing_name(start, end),
            host=SEBI_HOST,
            fetch=lambda c, s: notices.fetch_sebi_listing(c, s, start, end),
        ),
        Unit(
            source=notices.SOURCE_NSE_CIRCULARS,
            day=end,
            name=notices.nse_circulars_name(start, end),
            host=NSE_HOST,
            fetch=lambda c, s: notices.fetch_nse_circulars(c, s, start, end),
        ),
    ]


def notice_archive_unit(start: date, end: date) -> Unit:
    """SEBI's circulars "Archive" view for [start, end] (one wide window, stored under `end`)."""
    return Unit(
        source=notices.SOURCE_SEBI_ARCHIVE,
        day=end,
        name=notices.sebi_archive_name(start, end),
        host=SEBI_HOST,
        fetch=lambda c, s: notices.fetch_sebi_archive(c, s, start, end),
    )


def notice_month_windows(start: date, end: date) -> list[tuple[date, date]]:
    """Calendar-month windows covering [start, end] (first and last may be partial).

    NSE answers a whole month (~700 circulars, ~300 KB) in one request; SEBI pages it
    25 rows at a time.
    """
    if start > end:
        raise ValueError(f"--from {start} is after --to {end}")
    windows = []
    cur = start
    while cur <= end:
        nxt = date(cur.year + (cur.month == 12), cur.month % 12 + 1, 1)
        windows.append((cur, min(end, nxt - timedelta(days=1))))
        cur = nxt
    return windows


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
        elif key == BSE:
            units += [_bse_unit(day) for day in session_days]
        elif key == SLB:
            for day in session_days:
                units += _slb_units(day)
        elif key in (ETF_LIST, NAVALL):
            notes.append(f"{key}: latest-snapshot file, cannot be backfilled; ignored")
        else:
            raise ValueError(f"unknown source {key!r}")
    if end > today:
        notes.append(f"--to {end} is in the future; stopped at today ({today})")
    return Plan(units, notes)


# -- catch-up of missed sessions -------------------------------------------------------
#
# Each daily run works on ONE trading day (today in the evening, the previous session in the
# morning). If both runs for a day fail (laptop offline, asleep, a source down), no later run
# would ever ask for that day's files again. The catch-up closes that hole: every daily run
# also looks back a few calendar days and fetches the dated files that are still missing.
CATCH_UP_DAYS = 7
# Sources that can be asked for any past day. The "latest only" snapshots (NSE ETF list,
# AMFI NAVAll, IBJA, SEBI RSS, NSE symbol changes) cannot be caught up; notices and FBIL
# already re-ask a rolling window (7 and 31 days) every run.
CATCH_UP_SOURCES: tuple[str, ...] = (BHAVCOPY, UDIFF, SECMASTER, AMFI_HISTORY, YAHOO, BSE, SLB)


def plan_catch_up(
    day: date,
    store: RawStore,
    *,
    now: datetime,
    state: BackfillState | None = None,
    days_back: int = CATCH_UP_DAYS,
    sources: Sequence[str] = CATCH_UP_SOURCES,
) -> Plan:
    """Dated files for NSE sessions in the ``days_back`` calendar days BEFORE ``day`` that
    are not in the raw store yet (and not known to be unavailable).

    Each missed session gets exactly the units ``plan_daily`` would have planned for it, so
    file names match and a file already stored is never asked for again. ``day`` itself is
    left out: the run's own plan covers it.
    """
    require_aware(now, "now")
    try:
        missed_days = calendars.sessions("IN", day - timedelta(days=days_back), day)
    except ValueError:  # before the calendar starts: nothing to catch up
        return Plan([], [])
    units: list[Unit] = []
    notes: list[str] = []
    wanted = [s for s in sources if s in CATCH_UP_SOURCES]
    for session in missed_days:
        if session >= day:
            continue
        plan = plan_daily(session, wanted, now=now)
        for unit in plan.units:
            if store.exists(unit.source, unit.day, unit.name):
                continue
            if state is not None and state.is_unavailable(unit.source, unit.day, unit.name):
                continue
            units.append(unit)
        notes += [n for n in plan.notes if n.startswith(YAHOO)]  # "too old for Yahoo"
    if units:
        days_text = ", ".join(sorted({u.day.isoformat() for u in units}))
        notes.append(f"catch-up: {len(units)} missing file(s) from earlier sessions: {days_text}")
    return Plan(units, notes)


# -- running ------------------------------------------------------------------------


class NetworkBreaker:
    """Stops a whole run when our own network is down, not just one website.

    The per-website rule (5 failures in a row) cannot tell "NSE is down" from "the laptop
    is offline". This one watches connection failures across ALL websites: if the last
    ``limit`` downloads each failed to connect at all, and they were aimed at at least two
    different websites, the problem must be on our side, so every remaining download is
    reported as "not tried: network down" instead of being retried for minutes each.

    Any answer from a server (a file, a 404, even a block) resets the count. One breaker
    can be shared by several ``run_units`` calls in the same run.
    """

    def __init__(self, limit: int = NETWORK_DOWN_AFTER) -> None:
        self.limit = limit
        self._streak: list[str] = []  # hosts of the latest connection failures, in order
        self.reason: str | None = None  # set once tripped

    @property
    def tripped(self) -> bool:
        return self.reason is not None

    def server_answered(self) -> None:
        self._streak.clear()

    def connection_failed(self, host: str) -> None:
        self._streak.append(host)
        hosts = sorted(set(self._streak))
        if self.reason is None and len(self._streak) >= self.limit and len(hosts) >= 2:
            self.reason = (
                f"network down: {len(self._streak)} connection failures in a row across "
                f"{', '.join(hosts)}; no further downloads tried in this run"
            )
            logger.error("%s", self.reason)


def _not_found_hint(unit: Unit) -> str:
    if unit.host == NSE_HOST:
        return "not published (yet?). NSE posts the daily files in the evening, after ~19:00 IST"
    if unit.host == BSE_HOST:
        return "not published (yet?). BSE posts the daily file in the evening; try again later"
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
    breaker: NetworkBreaker | None = None,
    state_note: str = "backfill",
) -> list[Outcome]:
    """Fetch every Unit that is not already in the raw store; return one Outcome per unit.

    With `state` given (backfill mode) a 404 is recorded as "unavailable" and remembered;
    without it (daily mode) a 404 is a failure. Never raises for a failed download.
    Pass one ``breaker`` to several calls so "network down" carries over between them.
    ``state_note`` says in the state file which job recorded a 404 (backfill, catch-up).
    """
    outcomes: list[Outcome] = []
    stopped_hosts: dict[str, str] = {}  # host -> why we stopped talking to it
    streak: dict[str, int] = {}  # host -> consecutive failures
    breaker = breaker if breaker is not None else NetworkBreaker()

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
        if breaker.tripped:
            done(Outcome(unit, "failed", f"not tried: {breaker.reason}"))
            continue
        if unit.host in stopped_hosts:
            done(Outcome(unit, "failed", f"not tried: {stopped_hosts[unit.host]}"))
            continue
        try:
            record = unit.fetch(client, store)
        except SourceNotFoundError as exc:
            streak[unit.host] = 0  # the site answered; a 404 is not an outage
            breaker.server_answered()
            # Normally a real HTTP 404. BSE instead answers 200 with a web page for a missing
            # file; its collector raises the same error with the real status code (200).
            what = (
                "HTTP 404"
                if exc.status_code in (None, 404)
                else f"HTTP {exc.status_code} web page instead of the file"
            )
            if state is not None:
                state.mark_unavailable(
                    unit.source,
                    unit.day,
                    unit.name,
                    status_code=exc.status_code,
                    url=exc.url,
                    note=f"{what} during {state_note}",
                )
                done(Outcome(unit, "unavailable", f"{what}, recorded as not available"))
            else:
                done(Outcome(unit, "failed", f"{what}: {_not_found_hint(unit)}"))
            continue
        except SourceBlockedError as exc:
            breaker.server_answered()  # a block is an answer: the network works
            reason = f"blocked by {unit.host} (HTTP {exc.status_code}); no more requests to it"
            stopped_hosts[unit.host] = reason
            done(Outcome(unit, "failed", f"{exc}"))
            continue
        except Exception as exc:  # report loudly, keep the other sources going
            logger.warning("%s failed: %s: %s", unit.label, type(exc).__name__, exc)
            if is_connection_failure(exc):
                breaker.connection_failed(unit.host)
            else:
                breaker.server_answered()
            streak[unit.host] = streak.get(unit.host, 0) + 1
            if streak[unit.host] >= MAX_CONSECUTIVE_FAILURES:
                stopped_hosts[unit.host] = (
                    f"{unit.host} failed {MAX_CONSECUTIVE_FAILURES} times in a row"
                )
            kind = "" if isinstance(exc, SourceError) else f"{type(exc).__name__}: "
            done(Outcome(unit, "failed", f"{kind}{exc}"))
            continue
        streak[unit.host] = 0
        breaker.server_answered()
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
