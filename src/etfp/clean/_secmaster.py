"""Shared reader for the NSE security master, used by units_daily and band_official.

The files are big (about 11 MB unzipped each), so they are parsed once per build
and the result is kept in ``ctx.cache``.

We keep only ETF symbols, and only series EQ (normal trading) or BE (the
trade-for-trade variant some funds are moved into). The other series in the
file are special sessions (block deals, retail auctions, ...) with their own
bands; they are not the ETF's regular band and are ignored. If a fund has both
EQ and BE rows in one file, EQ wins.
"""

from __future__ import annotations

import logging

import pandas as pd

from etfp.clean.common import (
    CleanContext,
    CleanError,
    list_raw_files,
    map_former_symbols,
    parse_file,
)
from etfp.collect import nse_secmaster

logger = logging.getLogger("etfp.clean")

_CACHE_KEY = "secmaster"
_SERIES_PRIORITY = {"EQ": 0, "BE": 1}


def load(ctx: CleanContext, table: str) -> pd.DataFrame:
    """One row per (symbol, file_date) for every ETF, from every security-master file."""
    if _CACHE_KEY in ctx.cache:
        return ctx.cache[_CACHE_KEY]
    # Former NSE symbols are read too and relabelled to the current one (symbol changes).
    symbols = ctx.etf_symbols() | ctx.former_symbols()
    frames = []
    for rf in list_raw_files(ctx.raw_root, nse_secmaster.SOURCE):
        try:
            file_date = nse_secmaster.file_date_from_name(rf.name)
        except ValueError as exc:
            raise CleanError(f"{table}: cannot clean raw file {rf.relpath} ({exc})") from exc
        if file_date != rf.day:
            raise CleanError(
                f"{table}: raw file {rf.relpath} is stored under {rf.day} but is named for "
                f"{file_date}; the raw store layout was changed by hand?"
            )
        frame = parse_file(
            table,
            rf,
            lambda b, rf=rf, d=file_date: nse_secmaster.parse(
                b, file_date=d, source_file=rf.relpath
            ),
        )
        frame = frame[frame["symbol"].isin(symbols) & frame["series"].isin(list(_SERIES_PRIORITY))]
        frames.append(
            map_former_symbols(
                frame, ctx.universe, date_col="file_date", table=table, source_file=rf.relpath
            )
        )
    if frames:
        rows = pd.concat(frames, ignore_index=True)
        rows["_rank"] = rows["series"].map(_SERIES_PRIORITY)
        rows = rows.sort_values(["symbol", "file_date", "_rank"], kind="stable")
        rows = rows.drop_duplicates(["symbol", "file_date"], keep="first").drop(columns="_rank")
        rows = rows.reset_index(drop=True)
    else:
        logger.info("%s: no security-master files in the raw store", table)
        rows = pd.DataFrame({c: [] for c in nse_secmaster.OUTPUT_COLUMNS})
    ctx.cache[_CACHE_KEY] = rows
    return rows
