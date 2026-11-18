"""symbol_changes: NSE renames that touch a configured ETF, checked against the config.

Plain words. NSE renames tickers (N100 became MON100 on 16 Jun 2021). Each NSE daily file
uses the ticker of its own day, so if ``config/etfs.yaml`` does not list the old ticker
under ``former_symbols`` (with ``until`` = the last session under the old ticker), the
fund's history before the rename is silently dropped by every cleaner (that is how
NIFTYIETF, MON100 and SILVERBEES lost years of rows until D42). NSE publishes its own list
of renames (``symbolchange.csv``); this cleaner reads the NEWEST saved copy and compares:

* ``consistent``          the config maps this rename: old symbol is a former symbol of the
                          fund, its ``until`` is the session before the change date, and the
                          new symbol is the fund's current or another former symbol.
* ``before_data_window``  the rename happened before the project's data starts (2021-01-01),
                          so no raw file we hold uses the old ticker; nothing to map.
* ``unconfigured``        NOT mapped, or mapped differently. Logged as a WARNING and counted
                          in the build result (``symbol_change_warnings``) and the research
                          export manifest. A person must update ``former_symbols`` (or
                          explain it in DECISIONS) before the numbers are trusted.

A row is kept only if its old or new symbol equals a configured ETF's current or former
symbol. NSE's list has no ISIN, so matching is by ticker; a coincidence with an unrelated
company's reused ticker would show as ``unconfigured`` and be dismissed by a person.
"""

from __future__ import annotations

import logging
from datetime import date

import pandas as pd

from etfp import calendars
from etfp.clean.common import CleanContext, list_raw_files, parse_file
from etfp.collect import nse_symbolchange
from etfp.config import Etf
from etfp.store import SCHEMAS, Column, TableSchema, register_schema

logger = logging.getLogger("etfp.clean")

TABLE = "symbol_changes"
KEY = ["old_symbol", "new_symbol", "change_date"]
COLUMNS = [
    *KEY,
    "company_name",
    "etf_symbol",
    "config_status",
    "detail",
    "source_file",
]
DATA_START = date(2021, 1, 1)
"""First day of the project's data (``etfp backfill --from 2021-01-01``)."""
STATUSES = ("consistent", "before_data_window", "unconfigured")

if TABLE not in SCHEMAS:
    register_schema(
        TableSchema(
            name=TABLE,
            primary_key=tuple(KEY),
            doc=(
                "NSE symbol renames that touch a configured ETF, with a check against "
                "config former_symbols (consistent / before_data_window / unconfigured)."
            ),
            columns=(
                Column("old_symbol", "string", "Ticker before the change", nullable=False),
                Column("new_symbol", "string", "Ticker from the change date", nullable=False),
                Column("change_date", "date", "First day the new ticker trades", nullable=False),
                Column("company_name", "string", "Name as NSE prints it (may be empty)"),
                Column("etf_symbol", "string", "Current symbol of the configured ETF touched"),
                Column("config_status", "string", "consistent | before_data_window | unconfigured"),
                Column("detail", "string", "Why the status is what it is"),
                Column("source_file", "string", "Raw symbolchange.csv this row came from"),
            ),
        )
    )


def classify(old: str, new: str, change_date: date, etf: Etf) -> tuple[str, str]:
    """Status and reason of one NSE rename that touches ``etf``."""
    if change_date < DATA_START:
        return "before_data_window", f"changed {change_date}, before our data starts"
    chain = {etf.symbol} | {f.symbol for f in etf.former_symbols}
    until = {f.symbol: f.until for f in etf.former_symbols}
    if old == etf.symbol:
        return "unconfigured", (
            f"NSE renamed the configured current symbol {old} to {new} on {change_date}; "
            "config/etfs.yaml still treats it as current"
        )
    if old not in until:
        return "unconfigured", (
            f"{old} -> {new} on {change_date}: {old} is not in former_symbols of {etf.symbol}"
        )
    if new not in chain:
        return "unconfigured", (
            f"{old} -> {new} on {change_date}: {new} is not a symbol of {etf.symbol} in config"
        )
    try:
        expected = calendars.previous_session("IN", change_date)
    except ValueError:
        return "unconfigured", f"{change_date}: outside the supported calendar range"
    if until[old] != expected:
        return "unconfigured", (
            f"{old} -> {new} on {change_date}: config until={until[old]}, the session before "
            f"the change is {expected}"
        )
    return "consistent", f"{old} until {until[old]} matches the change date {change_date}"


def check(changes: pd.DataFrame, etfs: list[Etf]) -> pd.DataFrame:
    """Compare a parsed symbol-change list with the configured ETFs (only touching rows)."""
    owner: dict[str, Etf] = {}
    for etf in etfs:
        for sym in [etf.symbol, *(f.symbol for f in etf.former_symbols)]:
            owner[sym] = etf
    rows = []
    for rec in changes.itertuples(index=False):
        etf = owner.get(rec.old_symbol) or owner.get(rec.new_symbol)
        if etf is None:
            continue
        status, detail = classify(rec.old_symbol, rec.new_symbol, rec.change_date, etf)
        rows.append(
            {
                "old_symbol": rec.old_symbol,
                "new_symbol": rec.new_symbol,
                "change_date": rec.change_date,
                "company_name": rec.company_name,
                "etf_symbol": etf.symbol,
                "config_status": status,
                "detail": detail,
                "source_file": rec.source_file,
            }
        )
    out = pd.DataFrame(rows, columns=COLUMNS)
    return out.sort_values(KEY).reset_index(drop=True) if len(out) else out


def warnings_in(frame: pd.DataFrame) -> pd.DataFrame:
    """The ``unconfigured`` rows."""
    return frame[frame["config_status"] == "unconfigured"]


def build(ctx: CleanContext) -> pd.DataFrame:
    """Check the newest saved NSE symbol-change list; warn loudly about unmapped renames."""
    files = list_raw_files(ctx.raw_root, nse_symbolchange.SOURCE)
    if not files:
        logger.warning(
            "%s: no raw %s file yet; renames of configured ETFs are NOT being checked "
            "(run `etfp collect daily`)",
            TABLE,
            nse_symbolchange.SOURCE,
        )
        return pd.DataFrame({c: [] for c in COLUMNS})
    rf = files[-1]  # NSE's file is cumulative: the newest copy holds every earlier rename
    parsed = parse_file(TABLE, rf, lambda b: nse_symbolchange.parse(b, source_file=rf.relpath))
    out = check(parsed, list(ctx.universe.etfs))
    for rec in warnings_in(out).itertuples(index=False):
        logger.warning(
            "%s: UNCONFIGURED symbol change for %s: %s (%s). Fix former_symbols in "
            "config/etfs.yaml or explain in docs/DECISIONS.md",
            TABLE,
            rec.etf_symbol,
            rec.detail,
            rec.source_file,
        )
    return out
