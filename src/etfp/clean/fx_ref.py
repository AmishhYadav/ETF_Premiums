"""fx_ref_daily: FBIL official rupee reference rates, one row per (date, pair).

Plain words. FBIL fixes one official USD/INR rate per Indian business day from interbank
trades around midday and publishes it at about 13:00-13:30 IST, before the NSE close. The
fund houses name it ("RBI reference rate") as their NAV exchange rate or its fallback
(DECISIONS D36). ``etf_day.usdinr_ref`` is this table's USD/INR on the same date.

Rules:

* ``rate`` is rupees per ONE unit of the foreign currency (``rate_quoted`` / ``quote_units``
  keep the number exactly as FBIL printed it, e.g. rupees per 100 JPY).
* The daily job re-downloads overlapping windows, so the same rate sits in many raw files.
  The row keeps the NEWEST file carrying it; a changed value between files is logged as a
  warning (FBIL revising a published benchmark would be unusual and worth a look).
"""

from __future__ import annotations

import logging

import pandas as pd

from etfp.clean.common import CleanContext, concat_frames, list_raw_files, parse_file
from etfp.collect import fbil_refrates
from etfp.store import SCHEMAS, Column, TableSchema, register_schema

logger = logging.getLogger("etfp.clean")

TABLE = "fx_ref_daily"
KEY = ["date", "pair"]
COLUMNS = [*KEY, "rate", "quote_units", "rate_quoted", "published_ist", "source", "source_file"]

if TABLE not in SCHEMAS:
    register_schema(
        TableSchema(
            name=TABLE,
            primary_key=tuple(KEY),
            doc=(
                "FBIL daily rupee reference rates (USD/INR, GBP, EUR, JPY, ...): the official "
                "rate many fund houses use for NAV, published ~13:00-13:30 IST."
            ),
            columns=(
                Column("date", "date", "Indian business day the rate is for (Asia/Kolkata)"),
                Column("pair", "string", "'USDINR', 'EURINR', 'GBPINR', 'JPYINR', ..."),
                Column("rate", "float", "Rupees per ONE unit of the foreign currency"),
                Column("quote_units", "int", "Units of foreign currency FBIL quotes (100 for JPY)"),
                Column("rate_quoted", "float", "Rate exactly as FBIL printed it"),
                Column("published_ist", "timestamp", "FBIL's display (publication) time, IST"),
                Column("source", "string", "'fbil'"),
                Column("source_file", "string", "Newest raw FBIL file carrying this rate"),
            ),
        )
    )


def build(ctx: CleanContext) -> pd.DataFrame:
    """Build fx_ref_daily from every saved FBIL answer (oldest folder first)."""
    frames = []
    for rf in list_raw_files(ctx.raw_root, fbil_refrates.SOURCE):
        frames.append(
            parse_file(TABLE, rf, lambda b, rf=rf: fbil_refrates.parse(b, source_file=rf.relpath))
        )
    return combine(frames)


def combine(frames: list[pd.DataFrame]) -> pd.DataFrame:
    """Stack parsed files (oldest first); for a repeated (date, pair) keep the newest file."""
    stacked = concat_frames(frames, fbil_refrates.OUTPUT_COLUMNS)
    if stacked.empty:
        return pd.DataFrame({c: [] for c in COLUMNS})
    spread = stacked.groupby(KEY)["rate_quoted"].nunique()
    revised = spread[spread > 1]
    if not revised.empty:
        logger.warning(
            "%s: FBIL rate changed between downloads for %d (date, pair), newest kept; first %s",
            TABLE,
            len(revised),
            revised.index[0],
        )
    out = stacked.drop_duplicates(KEY, keep="last").copy()
    out["source"] = "fbil"
    return out.sort_values(KEY).reset_index(drop=True)[COLUMNS]
