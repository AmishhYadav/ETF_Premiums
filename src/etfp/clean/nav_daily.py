"""nav_daily: AMFI's end-of-day NAV per unit for each configured ETF.

NAV (net asset value) is what one unit's holdings are worth. AMFI publishes it
in two ways, and we read both (DECISIONS D9):

* the history download (``amfi_nav_history``): every NAV between two dates;
* the daily file ``NAVAll.txt`` (``amfi_navall``): only the latest NAV of each
  scheme, as it stood the day we downloaded it.

Which one wins when both give a NAV for the same ETF and date? **The history
download.** It is a complete, later look at the published record. ``NAVAll`` is a
snapshot that never shows a later correction. Among history files the one
downloaded most recently wins, and likewise among ``NAVAll`` files. When two
files disagree on the number, a warning names both files (a revision).
The winner's kind is recorded in ``nav_source``.

Scheme codes are mapped to symbols through ``config/etfs.yaml`` only. ETFs that
are on NSE's list but not in config get no NAV rows here (an ISIN-based mapping
could be added later).

NAVs are exactly as published: not adjusted for unit splits (MON100's 10:1 split
in June 2021 is a visible jump). See ``apply_corporate_actions``.
"""

from __future__ import annotations

import logging

import pandas as pd

from etfp.clean.common import (
    CleanContext,
    RawFile,
    apply_corporate_actions,
    find_gaps,
    list_raw_files,
    parse_file,
)
from etfp.collect import amfi_nav

logger = logging.getLogger("etfp.clean")

TABLE = "nav_daily"
COLUMNS = ["symbol", "scheme_code", "nav_date", "nav", "nav_source", "source_file"]
_SAME_NAV = 1e-6  # NAVs have 4 decimals; anything beyond rounding noise counts as a revision

# Lower number wins. Within a kind, the later download wins (see _pick_winners).
_RANK = {"history": 0, "navall": 1}


def build(ctx: CleanContext) -> pd.DataFrame:
    """Build the nav_daily table from every AMFI file in the raw store."""
    by_code = {e.amfi_scheme_code: e for e in ctx.universe.etfs}
    frames = []
    for source, kind, parser in (
        (amfi_nav.SOURCE_HISTORY, "history", amfi_nav.parse_history),
        (amfi_nav.SOURCE_NAVALL, "navall", amfi_nav.parse_navall),
    ):
        for rf in list_raw_files(ctx.raw_root, source):
            frame = parse_file(TABLE, rf, lambda b, rf=rf, p=parser: p(b, source_file=rf.relpath))
            frames.append(_select(frame, rf, kind, by_code))

    if not frames:
        return pd.DataFrame({c: [] for c in COLUMNS})
    rows = pd.concat(frames, ignore_index=True)
    rows = _pick_winners(rows)
    _log_nav_gaps(rows)
    return apply_corporate_actions(rows, table=TABLE)[COLUMNS]


def _select(frame: pd.DataFrame, rf: RawFile, kind: str, by_code: dict) -> pd.DataFrame:
    """Keep configured ETFs with a real NAV; check the ISIN against config; add helper columns."""
    mine = frame[frame["scheme_code"].isin(list(by_code))].copy()
    unusable = mine["nav"].isna()
    if unusable.any():
        logger.warning(
            "%s: %d NAV(s) in %s are not numbers (N.A.) and were skipped, e.g. scheme %s on %s",
            TABLE,
            int(unusable.sum()),
            rf.relpath,
            mine[unusable].iloc[0]["scheme_code"],
            mine[unusable].iloc[0]["nav_date"],
        )
        mine = mine[~unusable]
    for row in mine.itertuples(index=False):
        etf = by_code[int(row.scheme_code)]
        if pd.notna(row.isin_growth) and row.isin_growth != etf.isin:
            logger.warning(
                "%s: scheme %s has ISIN %s in %s but %s in config (%s); check the scheme code",
                TABLE,
                row.scheme_code,
                row.isin_growth,
                rf.relpath,
                etf.isin,
                etf.symbol,
            )
            break  # one warning per file is enough
    out = pd.DataFrame(
        {
            "symbol": mine["scheme_code"].map(lambda c: by_code[int(c)].symbol).astype("string"),
            "scheme_code": mine["scheme_code"],
            "nav_date": mine["nav_date"],
            "nav": mine["nav"],
            "nav_source": pd.array([kind] * len(mine), dtype="string"),
            "source_file": mine["source_file"],
        }
    )
    out["_download"] = rf.day
    out["_version"] = rf.version
    return out


def _pick_winners(rows: pd.DataFrame) -> pd.DataFrame:
    """One row per (symbol, nav_date): history beats navall, later download beats earlier."""
    rows = rows.copy()
    rows["_rank"] = rows["nav_source"].map(_RANK)
    rows = rows.sort_values(
        ["symbol", "nav_date", "_rank", "_download", "_version"],
        ascending=[True, True, True, False, False],
        kind="stable",
    )
    key = ["symbol", "nav_date"]
    first = rows.drop_duplicates(key, keep="first").set_index(key)
    for loser in rows[rows.duplicated(key, keep="first")].itertuples(index=False):
        winner = first.loc[(loser.symbol, loser.nav_date)]
        if abs(winner["nav"] - loser.nav) > _SAME_NAV:
            logger.warning(
                "%s: %s on %s: NAV %s in %s replaces %s in %s (a revision, or a mismatch)",
                TABLE,
                loser.symbol,
                loser.nav_date,
                winner["nav"],
                winner["source_file"],
                loser.nav,
                loser.source_file,
            )
    return rows.drop_duplicates(key, keep="first").reset_index(drop=True)


def _log_nav_gaps(rows: pd.DataFrame) -> None:
    """One warning listing the ETFs that have trading days without a NAV (never fatal)."""
    gaps = {sym: find_gaps(g["nav_date"]) for sym, g in rows.groupby("symbol")}
    gaps = {sym: days for sym, days in gaps.items() if days}
    if gaps:
        detail = "; ".join(f"{s}: {len(d)} day(s) from {d[0]}" for s, d in sorted(gaps.items()))
        logger.warning("%s: %d ETF(s) have trading days with no NAV: %s", TABLE, len(gaps), detail)
