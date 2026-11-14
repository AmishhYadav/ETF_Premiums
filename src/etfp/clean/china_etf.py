"""china_etf_daily: daily price, NAV and shares of China-listed QDII ETFs (R17).

Plain words. China has the same problem India has: ETFs that hold US shares (Nasdaq-100,
S&P 500) are limited by China's overseas-investment quota ("QDII quota"). When the quota is
used up the fund cannot create new units, demand pushes the price above NAV, and a premium
appears. China is therefore an outside comparison for the Indian premiums (a "control").

The numbers come from Bloomberg, typed in by hand on the college terminal and saved in the
long-format CSV that the existing importer reads (``etfp.collect.bloomberg``,
``docs/bloomberg_template.md``). Nothing here is downloaded.

Naming convention (instrument column of the CSV): the 6-digit exchange code, one space, the
letters ``CH`` (Bloomberg writes the ticker as ``513100 CH Equity``), for example
``513100 CH``. Three fields per ETF and day, time zone ``Asia/Shanghai``:

* ``PX_LAST``            closing price per unit, yuan (CNY)
* ``FUND_NET_ASSET_VAL`` NAV per unit, yuan
* ``EQY_SH_OUT``         units outstanding, exactly as Bloomberg returns it (VERIFY the
                         unit: Bloomberg normally quotes millions of shares; this importer
                         does NOT rescale it)

Rules:

* One row per ETF and local (Shanghai) date that has at least one of the three fields. A
  field with no number that day stays empty, never zero.
* If a file holds several rows for the same ETF, field and day (an intraday export), the
  LAST one of the day is used.
* ``premium`` = close / nav - 1, empty unless both exist. It is a fraction (0.2 = 20%).
  Same-date NAV is used, as Chinese fund pages do. VERIFY: for ETFs holding US shares the
  NAV dated T probably reflects the US close of T-1, so premium here is NOT lined up for
  the one-day lag the way the Indian ``prem_nav_known`` is.
* Rows for other instruments (futures, FX, Indian ETFs) in the same files are ignored.
* ``source_file`` lists the raw file(s) the row came from, separated by ``;``.
* Currency: always CNY (yuan). Prices are not adjusted for splits.

Licence: the values are Bloomberg data. The research export is git-ignored (CLAUDE.md); this
table must never go into the public open dataset.
"""

from __future__ import annotations

import logging
import re

import pandas as pd

from etfp.clean.common import CleanContext
from etfp.collect import bloomberg
from etfp.store import SCHEMAS, Column, TableSchema, register_schema

logger = logging.getLogger("etfp.clean")

TABLE = "china_etf_daily"
COLUMNS = ["code", "date", "close", "nav", "shares_outstanding", "premium", "source_file"]
TIMEZONE = "Asia/Shanghai"

_INSTRUMENT = re.compile(r"(\d{6}) CH\Z")
FIELD_TO_COLUMN = {
    "PX_LAST": "close",
    "FUND_NET_ASSET_VAL": "nav",
    "EQY_SH_OUT": "shares_outstanding",
}

if TABLE not in SCHEMAS:
    register_schema(
        TableSchema(
            name=TABLE,
            primary_key=("code", "date"),
            doc=(
                "China-listed QDII ETFs, one row per ETF and day, from Bloomberg (R17). "
                "Licensed data: not for the open dataset."
            ),
            columns=(
                Column("code", "string", "Exchange code, 6 digits (instrument '513100 CH')"),
                Column("date", "date", "Trading date in China (Asia/Shanghai)"),
                Column("close", "float", "Closing price per unit, yuan (CNY)"),
                Column("nav", "float", "NAV per unit, yuan (CNY), dated the same day"),
                Column(
                    "shares_outstanding",
                    "float",
                    "Units outstanding as Bloomberg EQY_SH_OUT returns it (unit to VERIFY, "
                    "normally millions; not rescaled)",
                ),
                Column("premium", "float", "close / nav - 1 as a fraction; empty without both"),
                Column("source_file", "string", "Raw Bloomberg CSV file(s), ';'-separated"),
            ),
        )
    )


def build(ctx: CleanContext) -> pd.DataFrame:
    """Build china_etf_daily from the Bloomberg CSVs in ``data/raw/bloomberg/``."""
    folder = ctx.raw_root / bloomberg.SOURCE
    # A bad Bloomberg file raises BloombergImportError naming file and line; it is left to
    # propagate (build_all raises the same error for the same file).
    imported = bloomberg.import_directory(folder, on_conflict="latest")
    return from_frame(imported.frame)


def from_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """China ETF rows of an imported Bloomberg frame, one row per ETF and Shanghai date."""
    empty = pd.DataFrame({c: [] for c in COLUMNS})
    if frame.empty:
        return empty
    codes = frame["instrument"].map(lambda s: m.group(1) if (m := _INSTRUMENT.match(s)) else None)
    mine = frame[codes.notna() & frame["field"].isin(FIELD_TO_COLUMN)].copy()
    if mine.empty:
        return empty
    mine["code"] = codes[mine.index]
    local = mine["ts_utc"].dt.tz_convert(TIMEZONE)
    mine["date"] = [t.date() for t in local]
    mine["column"] = mine["field"].map(FIELD_TO_COLUMN)
    # Several rows for one ETF, field and day: keep the last one (an intraday export).
    last = mine.sort_values("ts_utc").drop_duplicates(["code", "date", "column"], keep="last")

    wide = last.pivot(index=["code", "date"], columns="column", values="value")
    files = last.groupby(["code", "date"])["source_file"].agg(lambda s: ";".join(sorted(set(s))))
    out = wide.reindex(columns=["close", "nav", "shares_outstanding"]).astype("float64")
    out["premium"] = out["close"] / out["nav"] - 1  # NaN when either is missing
    out["source_file"] = files
    out = out.reset_index().sort_values(["code", "date"]).reset_index(drop=True)
    logger.info("%s: %d row(s) for %d ETF code(s)", TABLE, len(out), out["code"].nunique())
    return out[COLUMNS]
