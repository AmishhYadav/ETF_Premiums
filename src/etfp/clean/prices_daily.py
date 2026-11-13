"""prices_daily: end-of-day price, volume, trades and delivery per ETF per trading day.

Primary source: NSE's full bhavcopy (``sec_bhavdata_full``, DECISIONS D1), which
exists for the whole 2021-2027 sample and carries delivery figures.
From the newer UDiFF bhavcopy (DECISIONS D2, mid-2024 onwards) we merge in what
the full bhavcopy lacks: the ISIN, the settlement price and the exact rupee
value traded (the full bhavcopy rounds value to 0.01 lac = Rs 1,000).
If only a UDiFF file exists for a day, its row is used on its own.

Finance notes (plain words):
* ``close`` is NOT the last trade. NSE's official close is the average price of
  the last 30 minutes (D3); ``last`` is the final trade. Both are kept.
* Prices are exactly as published, not adjusted for unit splits (see the hook
  ``apply_corporate_actions``).
* An ETF normally trades in series EQ. If one symbol appears in several series
  on the same day we keep EQ, otherwise the series with the most volume, so the
  key (symbol, date) stays unique; a warning says when this happens.
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
from etfp.collect import nse_bhavcopy, nse_udiff

logger = logging.getLogger("etfp.clean")

TABLE = "prices_daily"
_PRICE_TOLERANCE = 0.005  # rupees; half a paisa. Bigger gaps between the two files are logged.

COLUMNS = [
    "symbol", "date", "series", "isin", "open", "high", "low", "close", "last", "avg_price",
    "prev_close", "settle_price", "volume", "value", "value_source", "trades", "deliv_qty",
    "deliv_pct", "source_file", "source_file_udiff",
]  # fmt: skip


def build(ctx: CleanContext) -> pd.DataFrame:
    """Build the prices_daily table from every bhavcopy and UDiFF file in the raw store."""
    symbols = ctx.etf_symbols()
    full = _read(ctx, nse_bhavcopy.SOURCE, symbols, _parse_full)
    udiff = _read(ctx, nse_udiff.SOURCE, symbols, _parse_udiff)

    log_gaps(TABLE, {*full["date"], *udiff["date"]})
    merged = _merge(full, udiff)
    merged = _one_row_per_day(merged)
    merged = apply_corporate_actions(merged, table=TABLE)
    return merged[COLUMNS]


# -- reading ------------------------------------------------------------------


def _parse_full(rf: RawFile, content: bytes) -> pd.DataFrame:
    return nse_bhavcopy.parse(content, source_file=rf.relpath)


def _parse_udiff(rf: RawFile, content: bytes) -> pd.DataFrame:
    return nse_udiff.parse(content, source_file=rf.relpath)


def _read(ctx: CleanContext, source: str, symbols: set[str], parser) -> pd.DataFrame:
    frames = []
    for rf in list_raw_files(ctx.raw_root, source):
        frame = parse_file(TABLE, rf, lambda b, rf=rf: parser(rf, b))
        _check_file_date(rf, frame)
        frames.append(frame[frame["symbol"].isin(symbols)])
    if not frames:
        logger.info("%s: no raw files for %s", TABLE, source)
        return _empty(source)
    return pd.concat(frames, ignore_index=True)


def _check_file_date(rf: RawFile, frame: pd.DataFrame) -> None:
    """The trade date inside the file must be the folder date. If not, stop: something is off."""
    other = sorted({d.isoformat() for d in frame["date"] if d != rf.day})
    if other:
        raise CleanError(
            f"{TABLE}: raw file {rf.relpath} is stored under {rf.day} but holds trade date(s) "
            f"{', '.join(other[:3])}; has the source changed its file layout?"
        )


def _empty(source: str) -> pd.DataFrame:
    cols = (
        nse_bhavcopy.OUTPUT_COLUMNS if source == nse_bhavcopy.SOURCE else nse_udiff.OUTPUT_COLUMNS
    )
    return pd.DataFrame({c: [] for c in cols})


# -- merging ------------------------------------------------------------------

_KEY = ["symbol", "series", "date"]
_FROM_UDIFF = ["isin", "settle_price", "value", "value_source", "source_file_udiff"]


def _shape_full(full: pd.DataFrame) -> pd.DataFrame:
    """Full bhavcopy rows in the final column layout."""
    out = full.rename(columns={"vwap": "avg_price"}).copy()
    out["isin"] = pd.array([None] * len(out), dtype="string")
    out["settle_price"] = float("nan")
    out["value_source"] = pd.array(["bhavcopy"] * len(out), dtype="string")
    out["source_file_udiff"] = pd.array([None] * len(out), dtype="string")
    return out[COLUMNS]


def _shape_udiff(udiff: pd.DataFrame) -> pd.DataFrame:
    """UDiFF rows in the final column layout (no average price or delivery figures there)."""
    out = udiff.copy()
    out["avg_price"] = float("nan")
    out["deliv_qty"] = pd.array([None] * len(out), dtype="Int64")
    out["deliv_pct"] = float("nan")
    out["value_source"] = pd.array(["udiff"] * len(out), dtype="string")
    out["source_file_udiff"] = out["source_file"]
    return out[COLUMNS]


def _merge(full: pd.DataFrame, udiff: pd.DataFrame) -> pd.DataFrame:
    """One row per (symbol, series, date): full bhavcopy, topped up from UDiFF where both exist."""
    f = _shape_full(full).set_index(_KEY)
    if udiff.empty:
        return f.reset_index()[COLUMNS]
    u = _shape_udiff(udiff).set_index(_KEY)

    common = f.index.intersection(u.index)
    _warn_if_different(f.loc[common], u.loc[common])
    for col in _FROM_UDIFF:  # exact value, ISIN and settlement price come from UDiFF
        f.loc[common, col] = u.loc[common, col]
    only_udiff = u.index.difference(f.index)
    return pd.concat([f, u.loc[only_udiff]]).reset_index()[COLUMNS]


def _warn_if_different(f: pd.DataFrame, u: pd.DataFrame) -> None:
    """The two NSE files should agree on the official close. Say so loudly when they do not."""
    diff = (f["close"] - u["close"]).abs() > _PRICE_TOLERANCE
    for key in f.index[diff.to_numpy()]:
        logger.warning(
            "%s: %s: close is %s in %s but %s in %s", TABLE, key,
            f.loc[key, "close"], f.loc[key, "source_file"],
            u.loc[key, "close"], u.loc[key, "source_file"],
        )  # fmt: skip


def _one_row_per_day(df: pd.DataFrame) -> pd.DataFrame:
    """Keep one series per (symbol, date): EQ first, otherwise the busiest."""
    if df.empty:
        return df
    df = df.copy()
    df["_eq"] = (df["series"] == "EQ").astype(int)
    df["_vol"] = pd.to_numeric(df["volume"]).fillna(0)
    df = df.sort_values(["symbol", "date", "_eq", "_vol"], ascending=[True, True, False, False])
    dup = df.duplicated(["symbol", "date"], keep="first")
    if dup.any():
        dropped = df[dup]
        logger.warning(
            "%s: %d row(s) in extra series dropped, e.g. %s %s series %s (kept the %s row)",
            TABLE,
            len(dropped),
            dropped.iloc[0]["symbol"],
            dropped.iloc[0]["date"],
            dropped.iloc[0]["series"],
            "EQ-first",
        )
    return df[~dup].drop(columns=["_eq", "_vol"]).reset_index(drop=True)
