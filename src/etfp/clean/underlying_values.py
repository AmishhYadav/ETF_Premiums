"""underlying_bar_values: free futures/FX level at 15:30 IST and at the US close, per day.

Plain words. Fair value (SPEC 6.2) needs, for each Indian trading day, the index future
and the rupee rate "at 15:30 IST", and for each US session the future "at the US close".
The free source (Yahoo chart bars, ``etfp.collect.yahoo_intraday``) gives bars, not
values at a moment. This table records which bar was used for each moment, so every
free fair-value input can be checked by hand:

* ``moment = 'IN_1530'``: for each NSE session ``date``, the close of the last bar that
  had ENDED by 15:30 IST (10:00 UTC). With 60-minute bars starting on the hour that is
  the 09:00-10:00 UTC bar (``stale_minutes`` 0); with 5-minute bars the 09:55-10:00 bar.
* ``moment = 'US_CLOSE'``: for each NYSE session ``date``, the last bar ended by 16:00
  New York (13:00 on early-close days): the 15:00-16:00 New York 60m bar.
* A bar more than 30 minutes stale gives NO row (missing, never carried forward).
  The build's own lookup uses the same rule for futures (30 min) and allows FX up to
  60 min stale (``build._level_at_*``); this table is the audit trail at the 30-min rule.

This is research-only input (Yahoo's terms forbid redistribution; the open dataset
denies it, config/dataset.yaml). It is empty until a Yahoo download succeeds.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from etfp import calendars
from etfp.clean.common import CleanContext, list_raw_files, parse_file
from etfp.collect import yahoo_intraday
from etfp.store import SCHEMAS, Column, TableSchema, register_schema

TABLE = "underlying_bar_values"
KEY = ["date", "moment", "symbol"]
COLUMNS = [
    *KEY,
    "close",
    "at",
    "bar_start",
    "bar_end",
    "interval_minutes",
    "stale_minutes",
    "source_file",
]

if TABLE not in SCHEMAS:
    register_schema(
        TableSchema(
            name=TABLE,
            primary_key=tuple(KEY),
            doc=(
                "Free futures/FX level at 15:30 IST (NSE days) and at the US close (NYSE days): "
                "close of the last bar ended by that moment, with the bar and its staleness."
            ),
            columns=(
                Column("date", "date", "NSE session (IN_1530) or NYSE session (US_CLOSE)"),
                Column("moment", "string", "'IN_1530' or 'US_CLOSE'"),
                Column("symbol", "string", "Yahoo symbol: NQ=F, ES=F, USDINR=X, HKDINR=X"),
                Column("close", "float", "Close of the chosen bar (index points or INR)"),
                Column("at", "timestamp", "The moment asked for"),
                Column("bar_start", "timestamp", "Start of the chosen bar"),
                Column("bar_end", "timestamp", "End of the chosen bar (<= at)"),
                Column("interval_minutes", "int", "Bar length: 60 (history) or 5 (daily)"),
                Column(
                    "stale_minutes", "float", "at - bar_end in minutes (0 = ended exactly then)"
                ),
                Column("source_file", "string", "Raw Yahoo file holding the bar"),
            ),
        )
    )


def build(ctx: CleanContext) -> pd.DataFrame:
    """Values at both moments for every day the saved bars cover."""
    frames = [
        parse_file(TABLE, rf, lambda b, rf=rf: yahoo_intraday.parse(b, source_file=rf.relpath))
        for rf in list_raw_files(ctx.raw_root, yahoo_intraday.SOURCE)
    ]
    frames = [f for f in frames if len(f)]
    if not frames:
        return pd.DataFrame({c: [] for c in COLUMNS})
    return values_from_bars(pd.concat(frames, ignore_index=True))


def _days(market: str, first: date, last: date) -> list[date]:
    try:
        return calendars.sessions(market, first, last)  # type: ignore[arg-type]
    except ValueError:
        return []


def values_from_bars(bars: pd.DataFrame) -> pd.DataFrame:
    """The table from concatenated parsed bars (several files, symbols, bar sizes)."""
    first = bars["ts"].min().date()
    last = bars["ts"].max().date()
    parts = [
        yahoo_intraday.values_at(bars, _days("IN", first, last), moment="IN_1530"),
        yahoo_intraday.values_at(bars, _days("US", first, last), moment="US_CLOSE"),
    ]
    out = pd.concat([p for p in parts if len(p)] or parts[:1], ignore_index=True)
    out = out.rename(
        columns={"at_utc": "at", "bar_start_utc": "bar_start", "bar_end_utc": "bar_end"}
    )
    return out.sort_values(KEY).reset_index(drop=True)[COLUMNS]
