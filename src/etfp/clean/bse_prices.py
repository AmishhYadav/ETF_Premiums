"""bse_prices: end-of-day BSE price, units traded, rupees traded and trades per ETF (R9).

Source: BSE's daily bhavcopy (``bse_bhavcopy``), in either of its two formats (see
``etfp.collect.bse_bhavcopy``). The file lists every security BSE traded that day, so we
keep only our ETFs, matched by ISIN (never by name or ticker: the legacy file has no
ticker, and BSE and NSE may spell a symbol differently).

Finance notes (plain words):
* The same ETF trades on both NSE and BSE. This table is the BSE half. ``etf_day`` puts
  the two side by side (``close`` vs ``bse_close``) and adds the units traded on both
  exchanges to get a fair ``turnover``.
* An ETF that did not trade on BSE on a given day (or was not yet listed there) simply
  has NO ROW. We do not invent a zero: a missing row means "no BSE figure", and
  ``etf_day`` then flags the day as NSE-only.
* Prices are exactly as published, not adjusted for unit splits (hook
  ``apply_corporate_actions``, applied later in ``etf_day`` with the NSE prices).
"""

from __future__ import annotations

import logging

import pandas as pd

from etfp.clean.common import (
    CleanContext,
    CleanError,
    RawFile,
    apply_corporate_actions,
    list_raw_files,
    log_gaps,
    parse_file,
)
from etfp.collect import bse_bhavcopy
from etfp.store import SCHEMAS, Column, TableSchema, register_schema

logger = logging.getLogger("etfp.clean")

TABLE = "bse_prices"

COLUMNS = [
    "symbol", "date", "isin", "scrip_code", "series", "open", "high", "low", "close", "last",
    "prev_close", "volume", "value", "trades", "file_format", "source_file",
]  # fmt: skip

if TABLE not in SCHEMAS:
    register_schema(
        TableSchema(
            name=TABLE,
            primary_key=("symbol", "date"),
            doc="BSE end-of-day prices and volumes per ETF per trading day (R9; unadjusted).",
            columns=(
                Column("symbol", "string", "Our (NSE) trading symbol, matched to BSE by ISIN"),
                Column("date", "date", "Trading date (IST)"),
                Column("isin", "string", "ISIN from the BSE file"),
                Column("scrip_code", "int", "BSE's numeric security code (e.g. 533385)"),
                Column("series", "string", "BSE group letter (B, E, ...), as published"),
                Column("open", "float", "BSE opening price, rupees"),
                Column("high", "float", "BSE day high, rupees"),
                Column("low", "float", "BSE day low, rupees"),
                Column("close", "float", "BSE official close, rupees"),
                Column("last", "float", "Price of the last BSE trade, rupees"),
                Column("prev_close", "float", "Previous BSE close, rupees"),
                Column("volume", "int", "Units traded on BSE"),
                Column("value", "float", "Rupees traded on BSE"),
                Column("trades", "int", "Number of trades on BSE"),
                Column("file_format", "string", "udiff (new BSE file) or legacy (EQ_ISINCODE zip)"),
                Column(
                    "source_file", "string", "Raw-store path of the BSE file this row came from"
                ),
            ),
        )
    )


def isin_to_symbol(ctx: CleanContext) -> dict[str, str]:
    """Our symbol for each ISIN: the cleaned etf_master if it exists, else config only."""
    mapping: dict[str, str] = {}
    if ctx.master is not None:
        for r in ctx.master[["isin", "symbol"]].dropna().itertuples(index=False):
            mapping[str(r.isin)] = str(r.symbol)
    for etf in ctx.universe.etfs:  # config wins (it was checked by hand)
        mapping[etf.isin] = etf.symbol
    return mapping


def build(ctx: CleanContext) -> pd.DataFrame:
    """Build the bse_prices table from every BSE bhavcopy in the raw store."""
    wanted = isin_to_symbol(ctx)
    frames: list[pd.DataFrame] = []
    days = set()
    for rf in list_raw_files(ctx.raw_root, bse_bhavcopy.SOURCE):
        frame = parse_file(
            TABLE, rf, lambda b, rf=rf: bse_bhavcopy.parse(b, source_file=rf.relpath)
        )
        bad = [
            (line, text)
            for line, text in frame.attrs.get("malformed_lines", [])
            if any(isin in text for isin in wanted)
        ]
        if bad:
            raise CleanError(
                f"{TABLE}: raw file {rf.relpath} line {bad[0][0]} is malformed and mentions one "
                f"of our funds' ISINs, so a figure of ours may be lost: {bad[0][1][:200]}"
            )
        _check_file_date(rf, frame)
        days.add(rf.day)
        ours = frame[frame["isin"].isin(list(wanted))].copy()
        ours["symbol"] = ours["isin"].map(wanted).astype("string")
        frames.append(_one_row_per_day(ours))
    log_gaps(TABLE, days)
    if not frames:
        logger.info("%s: no raw files for %s", TABLE, bse_bhavcopy.SOURCE)
        return pd.DataFrame({c: [] for c in COLUMNS})
    out = pd.concat(frames, ignore_index=True)
    out = apply_corporate_actions(out, table=TABLE)
    return out[COLUMNS].reset_index(drop=True)


def _check_file_date(rf: RawFile, frame: pd.DataFrame) -> None:
    """The trade date inside the file must be the folder date. If not, stop: something is off."""
    other = sorted({d.isoformat() for d in frame["date"] if d != rf.day})
    if other:
        raise CleanError(
            f"{TABLE}: raw file {rf.relpath} is stored under {rf.day} but holds trade date(s) "
            f"{', '.join(other[:3])}; has BSE changed its file layout?"
        )


def _one_row_per_day(df: pd.DataFrame) -> pd.DataFrame:
    """Keep one row per ISIN per file: the busiest (most units) if BSE lists it twice."""
    if df.empty:
        return df
    df = df.assign(_vol=pd.to_numeric(df["volume"]).fillna(0))
    df = df.sort_values(["isin", "_vol"], ascending=[True, False])
    dup = df.duplicated(["isin"], keep="first")
    if dup.any():
        first = df[dup].iloc[0]
        logger.warning(
            "%s: %d extra row(s) for an ISIN in one file, e.g. %s %s scrip %s (kept the busiest)",
            TABLE,
            int(dup.sum()),
            first["isin"],
            first["date"],
            first["scrip_code"],
        )
    return df[~dup].drop(columns="_vol")
