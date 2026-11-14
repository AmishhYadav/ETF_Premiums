"""slb_eligible and slb_daily: could the ETFs be borrowed (to short them), and were they? (R8)

Plain-words background: to *short* an ETF you borrow units, sell them, and buy them back
later. NSE runs a lending and borrowing market (SLB) for this. Two raw daily files:

* the **eligible list**: which securities may be lent and borrowed that day;
* the **SLB bhavcopy**: which lending contracts actually traded, how many units, and the
  fee paid.

The paper must *show* whether these ETFs could be borrowed, not assume it, so the tables
keep the evidence for every tracked ETF:

``slb_eligible``  one row per (date, ETF) for every date with an eligible-list file.
    ``eligible`` is True when the ETF appears in that day's list with Normal Eligibility
    "E", and False when the list does not contain it at all (an explicit "no", not a gap).
    ``series_count`` is how many lending contract series were open for it.

``slb_daily``  one row per (date, ETF, SLB series) that traded. Fees are rupees per unit
    for the whole contract period; ``volume`` is units lent; ``value`` is the total fee in
    rupees (units x fee), not the value of the shares. An ETF with no row on a date had no
    lending trade that day (when a bhavcopy exists for that date).

Only the ETFs in config/etfs.yaml are kept (the six international ETFs and the controls);
the files themselves hold about 1,100 securities.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pandas as pd

from etfp.clean.common import (
    CleanContext,
    CleanError,
    RawFile,
    list_raw_files,
    log_gaps,
    parse_file,
)
from etfp.collect import nse_slb
from etfp.store import SCHEMAS, Column, TableSchema, register_schema

logger = logging.getLogger("etfp.clean")

TABLE_ELIGIBLE = "slb_eligible"
TABLE_DAILY = "slb_daily"

ELIGIBLE_COLUMNS = ["date", "symbol", "eligible", "series_count", "source_file"]
DAILY_COLUMNS = [
    "date", "symbol", "series", "maturity", "name", "market_type", "fee_prev_close", "fee_open",
    "fee_high", "fee_low", "fee_close", "volume", "value", "trades", "source_file",
]  # fmt: skip

_ELIGIBLE_CODE = "E"  # "Normal Eligibility" letter meaning "may be lent and borrowed"

if TABLE_ELIGIBLE not in SCHEMAS:
    register_schema(
        TableSchema(
            name=TABLE_ELIGIBLE,
            primary_key=("date", "symbol"),
            doc="Was the ETF on NSE's securities-lending (SLB) eligible list on that date? (R8)",
            columns=(
                Column("date", "date", "Date of the eligible-list file (IST)"),
                Column("symbol", "string", "NSE trading symbol (ETFs in config/etfs.yaml)"),
                Column(
                    "eligible",
                    "bool",
                    "True if listed with Normal Eligibility E; False if not in the list at all",
                ),
                Column("series_count", "int", "Number of SLB contract series listed as eligible"),
                Column("source_file", "string", "Raw-store path of the eligible-list file"),
            ),
        )
    )
if TABLE_DAILY not in SCHEMAS:
    register_schema(
        TableSchema(
            name=TABLE_DAILY,
            primary_key=("date", "symbol", "series"),
            doc="SLB contracts that traded, per ETF per day per contract series (R8).",
            columns=(
                Column("date", "date", "Trade date (IST)"),
                Column("symbol", "string", "NSE trading symbol (ETFs in config/etfs.yaml)"),
                Column("series", "string", "SLB contract series code as published, e.g. XD"),
                Column("maturity", "date", "Date the lending contract ends"),
                Column("name", "string", "Security name as printed in the file"),
                Column("market_type", "string", "Market type letter, N"),
                Column("fee_prev_close", "float", "Previous close of the fee, rupees per unit"),
                Column("fee_open", "float", "First fee of the day, rupees per unit"),
                Column("fee_high", "float", "Highest fee of the day, rupees per unit"),
                Column("fee_low", "float", "Lowest fee of the day, rupees per unit"),
                Column("fee_close", "float", "Closing fee of the day, rupees per unit"),
                Column("volume", "int", "Units lent (quantity traded)"),
                Column("value", "float", "Total fee in rupees = units x average fee"),
                Column("trades", "int", "Number of trades"),
                Column("source_file", "string", "Raw-store path of the SLB bhavcopy file"),
            ),
        )
    )


# -- slb_eligible -------------------------------------------------------------------


def build_eligible(ctx: CleanContext) -> pd.DataFrame:
    """One row per (eligible-list file, configured ETF)."""
    symbols = sorted(ctx.config_symbols())
    rows: list[dict] = []
    days = set()
    for rf in list_raw_files(ctx.raw_root, nse_slb.SOURCE_ELIGIBLE):
        frame = parse_file(
            TABLE_ELIGIBLE,
            rf,
            lambda b, rf=rf: nse_slb.parse_eligible(b, source_file=rf.relpath),
        )
        days.add(rf.day)
        ok = frame[frame["normal_eligibility"] == _ELIGIBLE_CODE]
        counts = ok.groupby("symbol").size()
        for symbol in symbols:
            n = int(counts.get(symbol, 0))
            rows.append(
                {
                    "date": rf.day,
                    "symbol": symbol,
                    "eligible": n > 0,
                    "series_count": n,
                    "source_file": rf.relpath,
                }
            )
    log_gaps(TABLE_ELIGIBLE, days)
    return pd.DataFrame(rows, columns=ELIGIBLE_COLUMNS)


# -- slb_daily ----------------------------------------------------------------------


def build_daily(ctx: CleanContext) -> pd.DataFrame:
    """One row per (bhavcopy file, configured ETF, contract series that traded)."""
    symbols = ctx.config_symbols()
    frames: list[pd.DataFrame] = []
    days = set()
    for rf in list_raw_files(ctx.raw_root, nse_slb.SOURCE_BHAVCOPY):
        frame = parse_file(
            TABLE_DAILY,
            rf,
            lambda b, rf=rf: nse_slb.parse_bhavcopy(b, source_file=rf.relpath),
        )
        if frame.empty:
            logger.warning(
                "%s: %s has no rows (nothing was lent that day?)", TABLE_DAILY, rf.relpath
            )
        else:
            _check_file_date(rf, frame)
        days.add(rf.day)
        frames.append(frame[frame["symbol"].isin(symbols)])
    log_gaps(TABLE_DAILY, days)
    if not frames:
        return pd.DataFrame({c: [] for c in DAILY_COLUMNS})
    return pd.concat(frames, ignore_index=True)[DAILY_COLUMNS]


def _check_file_date(rf: RawFile, frame: pd.DataFrame) -> None:
    """The trade date inside the file must be the folder date. If not, stop: something is off."""
    other = sorted({d.isoformat() for d in frame["date"] if d != rf.day})
    if other:
        raise CleanError(
            f"{TABLE_DAILY}: raw file {rf.relpath} is stored under {rf.day} but holds trade "
            f"date(s) {', '.join(other[:3])}; has NSE changed its file layout?"
        )


# ``CLEANERS`` in etfp.clean calls ``module.build(ctx)``; one module builds two tables, so
# each table gets a tiny namespace with its own ``build``.
ELIGIBLE = SimpleNamespace(build=build_eligible)
DAILY = SimpleNamespace(build=build_daily)
