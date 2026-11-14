"""bullion_ref_daily: the domestic (Indian) price of gold and silver, one row per rate.

Plain words. Gold and silver ETFs hold metal in India, so their NAV should equal
"metal per unit x Indian metal price". This table holds that Indian price, taken from
IBJA's twice-daily polled rates (``etfp.collect.ibja_rates``). It is the reference for
the bullion comparison group (``etfp.features.bullion``).

Rules:

* One row per (date, metal, purity, session). ``session`` is ``AM`` (poll 11:40-12:00
  IST) or ``PM`` (poll 16:40-17:00 IST). ``date`` is the Indian calendar date.
* ``price_inr_per_gram`` is the comparable unit: rupees per gram of metal of that
  purity, including import duty, excluding GST. ``quote_inr`` / ``quote_unit`` keep the
  number exactly as IBJA printed it (gold per 10 g, silver per 1 kg).
* Every daily download repeats the last ~4 months of PM rates. The same rate therefore
  appears in many raw files; the row keeps the NEWEST file that carries it (a later file
  wins if IBJA ever revised a rate, and a revision is logged as a warning).
"""

from __future__ import annotations

import logging

import pandas as pd

from etfp.clean.common import CleanContext, concat_frames, list_raw_files, parse_file
from etfp.collect import ibja_rates
from etfp.store import SCHEMAS, Column, TableSchema, register_schema

logger = logging.getLogger("etfp.clean")

TABLE = "bullion_ref_daily"
KEY = ["date", "metal", "purity", "session"]
COLUMNS = [*KEY, "price_inr_per_gram", "quote_inr", "quote_unit", "source", "source_file"]

if TABLE not in SCHEMAS:
    register_schema(
        TableSchema(
            name=TABLE,
            primary_key=tuple(KEY),
            doc=(
                "Domestic Indian gold and silver prices (IBJA polled AM/PM rates, duty "
                "included, GST excluded): the reference price for bullion ETFs."
            ),
            columns=(
                Column("date", "date", "Indian calendar date of the rate (Asia/Kolkata)"),
                Column("metal", "string", "'gold' or 'silver'"),
                Column("purity", "string", "Fineness in parts per thousand: '999', '995', ..."),
                Column(
                    "session",
                    "string",
                    "'AM' (poll 11:40-12:00 IST) or 'PM' (poll 16:40-17:00 IST)",
                ),
                Column(
                    "price_inr_per_gram",
                    "float",
                    "Rupees per gram of metal of this purity (duty in, GST out)",
                ),
                Column("quote_inr", "float", "The rate exactly as IBJA prints it"),
                Column("quote_unit", "string", "'INR per 10 g' (gold) or 'INR per 1 kg' (silver)"),
                Column("source", "string", "'ibja'"),
                Column("source_file", "string", "Newest raw IBJA page carrying this rate"),
            ),
        )
    )


def build(ctx: CleanContext) -> pd.DataFrame:
    """Build bullion_ref_daily from every saved IBJA page."""
    frames = []
    for rf in list_raw_files(ctx.raw_root, ibja_rates.SOURCE):
        frames.append(
            parse_file(TABLE, rf, lambda b, rf=rf: ibja_rates.parse(b, source_file=rf.relpath))
        )
    return combine(frames)


def combine(frames: list[pd.DataFrame]) -> pd.DataFrame:
    """Stack parsed pages (oldest first); for a repeated rate keep the newest page's row."""
    stacked = concat_frames(frames, ibja_rates.OUTPUT_COLUMNS)
    if stacked.empty:
        return pd.DataFrame({c: [] for c in COLUMNS})
    spread = stacked.groupby(KEY)["quote_inr"].nunique()
    revised = spread[spread > 1]
    if not revised.empty:
        logger.warning(
            "%s: IBJA revised %d rate(s) between downloads (newest kept), first %s",
            TABLE,
            len(revised),
            revised.index[0],
        )
    # frames arrive oldest first, so keep="last" keeps the newest file's row.
    out = stacked.drop_duplicates(KEY, keep="last").copy()
    out["source"] = "ibja"
    return out.sort_values(KEY).reset_index(drop=True)[COLUMNS]
