"""events: dated rule changes, caps, cautions and notices, loaded from data/manual/events.csv.

Why this table exists (plain words): the research asks "what happened to the
premium when X was announced?". For that, each X needs a date, the time of day it
became public, and the first trading session on which the market could react. A
circular published at 16:00 cannot move prices until the next session, so the
"reaction window" starts later than the calendar date suggests.

The research side supplies the CSV (requests R5, R16); the pipeline only checks it
and turns it into the ``events`` table. A row is ``verified = false`` until the
primary document (the circular, the notice) is saved under ``data/raw/rules/`` and
checked; the loader refuses ``verified = true`` without a source that exists.

Columns of ``data/manual/events.csv``
-------------------------------------
event_id                 optional; stable id. Blank = ``<date>_<category>_<hash>``
date                     the event date (IST), YYYY-MM-DD
ts_ist                   publication time, IST (blank if unknown): 2026-09-25T16:10
type                     rule, limit, suspension, warning or other
category                 rule, cap, caution, amc_notice, fund_status or media
direction                premium_reducing, premium_preserving or neutral; set BEFORE
                         looking at prices. Blank = not classified yet (kept, counted)
symbols                  affected ETF symbols separated by ';'. Blank = not recorded /
                         market-wide
first_tradable_session   blank = computed (see ``compute_first_tradable_session``)
description, url, source_primary, verified
"""

from __future__ import annotations

import hashlib
import logging
from datetime import date, datetime, time
from pathlib import Path

import pandas as pd

from etfp import calendars
from etfp.clean.common import CleanContext, CleanError, concat_frames
from etfp.clean.manual_csv import Row, project_root, read_rows
from etfp.timeutil import IST

logger = logging.getLogger("etfp.clean")

TABLE = "events"
FILENAME = "events.csv"
COLUMNS = [
    "event_id", "date", "ts_ist", "type", "category", "direction", "symbols",
    "first_tradable_session", "description", "url", "source_primary", "verified", "source_file",
]  # fmt: skip

EVENT_TYPES = ("rule", "limit", "suspension", "warning", "other")
CATEGORIES = ("rule", "cap", "caution", "amc_notice", "fund_status", "media")
DIRECTIONS = ("premium_reducing", "premium_preserving", "neutral")

# NSE's normal session ends at 15:30 IST. Something published at or after that moment
# cannot be traded on until the next session.
MARKET_CLOSE_IST = time(15, 30)


def compute_first_tradable_session(day: date, ts_ist: datetime | None) -> date:
    """The first NSE session that could react to an event.

    * Publication time known: published BEFORE 15:30 IST on a session day -> that
      same day; at/after 15:30, or on a weekend/holiday -> the next session.
    * Time unknown: assume it was public during that day's trading, so the same
      day if it is a session, else the next session. (If the true time was after
      the close, fill ``ts_ist`` or ``first_tradable_session`` in the CSV.)

    The day used is the IST calendar date of ``ts_ist`` when given.
    """
    if ts_ist is None:
        base, after_close = day, False
    else:
        stamp = ts_ist.astimezone(IST)
        base, after_close = stamp.date(), stamp.time() >= MARKET_CLOSE_IST
    if calendars.is_session("IN", base) and not after_close:
        return base
    return calendars.next_session("IN", base)


def make_event_id(day: date, category: str, symbols: list[str], description: str) -> str:
    """Stable id for a row without one: date, category and a hash of the content."""
    digest = hashlib.sha1(
        f"{day}|{category}|{';'.join(symbols)}|{description}".encode()
    ).hexdigest()
    return f"{day.isoformat()}_{category}_{digest[:8]}"


def _check_link(row: Row, col: str, value: str | None) -> None:
    if value and value.lower().startswith(("http://", "https://")) and " " in value:
        raise row.fail(f"column {col!r}: {value!r} looks like a link but contains a space")


def _check_verified(ctx: CleanContext, row: Row, source_primary: str | None) -> None:
    """verified = true needs a primary source; a file path must exist."""
    if source_primary is None:
        raise row.fail("verified is true but source_primary is empty")
    if source_primary.lower().startswith(("http://", "https://")):
        return
    if not (project_root(ctx) / Path(source_primary)).exists():
        raise row.fail(
            f"verified is true but source_primary {source_primary!r} is not an existing file "
            "(save the document under data/raw/rules/ first)"
        )


def _parse_row(ctx: CleanContext, row: Row, known: set[str]) -> dict[str, object]:
    day = row.get_date("date", required=True)
    assert day is not None  # required=True
    ts = row.get_datetime_ist("ts_ist")
    if ts is not None and ts.date() != day:
        raise row.fail(
            f"column 'ts_ist': {ts.isoformat()} falls on {ts.date()} (IST) but date is {day}; "
            "date must be the day the event became public"
        )
    etype = row.get_choice("type", EVENT_TYPES, required=True)
    category = row.get_choice("category", CATEGORIES, required=True)
    direction = row.get_choice("direction", DIRECTIONS)
    symbols = row.get_symbols("symbols", known)
    description = row.get_text("description", required=True)
    assert etype and category and description
    url = row.get_text("url")
    source_primary = row.get_text("source_primary")
    _check_link(row, "url", url)
    _check_link(row, "source_primary", source_primary)
    verified = row.get_bool("verified", default=False)
    if verified:
        _check_verified(ctx, row, source_primary)

    first = row.get_date("first_tradable_session")
    try:
        if first is None:
            # Before the sample start (Jan 2021) the calendar has no answer: leave it empty.
            first = (
                compute_first_tradable_session(day, ts) if day >= calendars.SAMPLE_START else None
            )
        else:
            if first < day:
                raise row.fail(
                    f"column 'first_tradable_session': {first} is before the event date {day}"
                )
            if not calendars.is_session("IN", first):
                raise row.fail(
                    f"column 'first_tradable_session': {first} is not an NSE trading day"
                )
    except ValueError as exc:  # calendar range problems
        raise row.fail(f"cannot work out the trading session: {exc}") from exc

    event_id = row.get_text("event_id") or make_event_id(day, category, symbols, description)
    return {
        "event_id": event_id,
        "date": day,
        "ts_ist": ts,
        "type": etype,
        "category": category,
        "direction": direction,
        "symbols": ";".join(symbols) if symbols else None,
        "first_tradable_session": first,
        "description": description,
        "url": url,
        "source_primary": source_primary,
        "verified": verified,
        "source_file": row.source_file,
    }


def build(ctx: CleanContext) -> pd.DataFrame:
    """Build the events table from data/manual/events.csv (empty table if the file is absent)."""
    rows = read_rows(
        ctx,
        FILENAME,
        TABLE,
        required=["date", "type", "category", "description"],
        optional=[
            "event_id", "ts_ist", "direction", "symbols", "first_tradable_session", "url",
            "source_primary", "verified",
        ],
    )  # fmt: skip
    if not rows:
        return concat_frames([], COLUMNS)
    known = ctx.etf_symbols()
    parsed = [_parse_row(ctx, r, known) for r in rows]

    seen: dict[str, str] = {}
    for rec in parsed:
        eid = str(rec["event_id"])
        if eid in seen:
            raise CleanError(
                f"{TABLE}: event_id {eid!r} appears twice: {seen[eid]} and {rec['source_file']}"
            )
        seen[eid] = str(rec["source_file"])

    out = pd.DataFrame(parsed, columns=COLUMNS)
    # Timestamps with a zone: build from UTC so pandas keeps them aware.
    out["ts_ist"] = pd.to_datetime(out["ts_ist"], utc=True).dt.tz_convert(IST)

    unclassified = int(out["direction"].isna().sum())
    unsourced = int((out["source_primary"].isna() & out["url"].isna()).sum())
    unverified = int((~out["verified"]).sum())
    logger.info(
        "%s: %d events (%d unverified, %d without direction, %d without a source link)",
        TABLE, len(out), unverified, unclassified, unsourced,
    )  # fmt: skip
    return out
