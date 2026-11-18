"""feeder_nav_daily: the daily NAV of each feeder fund (fund of funds), from AMFI (R14).

Plain words. A feeder fund (see ``manual_fof``) holds only one of our ETFs. Its NAV is
what one feeder unit is worth at the end of the day, so it moves with the ETF's own NAV
(minus the feeder's costs) and with nothing else. Why we keep it: the feeder's NAV is
a second, independent reading of what the ETF's holdings are worth, and the research
side compares the two (and uses the feeder's flows as supply/demand evidence).

Where it comes from: the very same AMFI files as the ETF NAVs (``nav_daily``), the history
download and the daily ``NAVAll.txt``. Which feeder schemes to keep is read from
``config/feeders.yaml``. Each plan (Regular, Direct) is its own scheme with its own NAV.

Same rules as ``nav_daily``:

* AMFI's history download beats the daily file for the same scheme and date; among files
  of one kind the later download wins; if two files disagree on the number, a warning
  names both (a revision).
* "N.A." NAVs are skipped and logged, never turned into zero.
* NAVs are exactly as published.
* A feeder has no NAV for a day on which AMFI printed none (for example an overseas market
  holiday for some fund houses, DECISIONS D22). That day is simply missing, never filled.

Speed: the AMFI files are big (about 2.5 MB each, 130+ of them) and hold every mutual
fund, so ``clean._amfi`` parses each file once per build and keeps only the rows of our
scheme codes (ETFs and feeders); ``nav_daily`` and this table share that one parse (D50).
"""

from __future__ import annotations

import logging

import pandas as pd

from etfp.clean import _amfi
from etfp.clean.common import CleanContext, RawFile
from etfp.config import Feeder
from etfp.store import SCHEMAS, Column, TableSchema, register_schema

logger = logging.getLogger("etfp.clean")

TABLE = "feeder_nav_daily"
COLUMNS = ["scheme_code", "fund_name", "etf_symbol", "nav_date", "nav", "source_file"]
_SAME_NAV = 1e-6  # NAVs have 3-4 decimals; more than rounding noise counts as a revision
_RANK = {"history": 0, "navall": 1}  # lower wins

if TABLE not in SCHEMAS:
    register_schema(
        TableSchema(
            name=TABLE,
            primary_key=("scheme_code", "nav_date"),
            doc="AMFI daily NAV of each feeder fund (fund of funds) of our ETFs (R14).",
            columns=(
                Column("scheme_code", "int", "AMFI scheme code of the feeder (one per plan)"),
                Column("fund_name", "string", "Feeder scheme name (config/feeders.yaml)"),
                Column("etf_symbol", "string", "NSE symbol of the ETF the feeder holds"),
                Column("nav_date", "date", "Date the NAV is for (not when it was published)"),
                Column("nav", "float", "Feeder NAV per unit, rupees", nullable=False),
                Column("source_file", "string", "Raw-store path of the AMFI file of this row"),
            ),
        )
    )


def _same_name(a: str, b: str) -> bool:
    return " ".join(a.split()).casefold() == " ".join(b.split()).casefold()


def build(ctx: CleanContext) -> pd.DataFrame:
    """Build feeder_nav_daily from every AMFI file in the raw store."""
    feeders = _amfi.feeders_for(ctx)
    known = ctx.config_symbols()
    unknown = sorted({f.etf_symbol for f in feeders.feeders} - known)
    if unknown:
        raise ValueError(f"{TABLE}: config/feeders.yaml names unknown ETF symbol(s) {unknown}")
    by_code = {f.amfi_scheme_code: f for f in feeders.feeders}

    frames = [_select(frame, rf, kind, by_code) for rf, kind, frame in _amfi.load(ctx, TABLE)]

    frames = [f for f in frames if not f.empty]
    if not frames:
        return pd.DataFrame({c: [] for c in COLUMNS})
    rows = _pick_winners(pd.concat(frames, ignore_index=True))
    _log_missing_schemes(rows, feeders.feeders)
    return rows[COLUMNS].sort_values(["scheme_code", "nav_date"]).reset_index(drop=True)


def _select(
    frame: pd.DataFrame, rf: RawFile, kind: str, by_code: dict[int, Feeder]
) -> pd.DataFrame:
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
    for code, name in mine.drop_duplicates("scheme_code")[
        ["scheme_code", "scheme_name"]
    ].itertuples(index=False):
        configured = by_code[int(code)].name
        if not _same_name(str(name), configured):
            logger.warning(
                "%s: scheme %s is %r in %s but %r in config/feeders.yaml; check the scheme code",
                TABLE,
                code,
                name,
                rf.relpath,
                configured,
            )
    out = pd.DataFrame(
        {
            "scheme_code": mine["scheme_code"].astype("int64"),
            "fund_name": mine["scheme_code"].map(lambda c: by_code[int(c)].name),
            "etf_symbol": mine["scheme_code"].map(lambda c: by_code[int(c)].etf_symbol),
            "nav_date": mine["nav_date"],
            "nav": mine["nav"].astype("float64"),
            "source_file": mine["source_file"],
        }
    )
    out["nav_source"] = kind
    out["_download"] = rf.day
    out["_version"] = rf.version
    return out


def _pick_winners(rows: pd.DataFrame) -> pd.DataFrame:
    """One row per (scheme_code, nav_date): history beats navall, later download beats earlier."""
    rows = rows.copy()
    rows["_rank"] = rows["nav_source"].map(_RANK)
    rows = rows.sort_values(
        ["scheme_code", "nav_date", "_rank", "_download", "_version"],
        ascending=[True, True, True, False, False],
        kind="stable",
    )
    key = ["scheme_code", "nav_date"]
    first = rows.drop_duplicates(key, keep="first").set_index(key)
    for loser in rows[rows.duplicated(key, keep="first")].itertuples(index=False):
        winner = first.loc[(loser.scheme_code, loser.nav_date)]
        if abs(winner["nav"] - loser.nav) > _SAME_NAV:
            logger.warning(
                "%s: scheme %s on %s: NAV %s in %s replaces %s in %s (a revision, or a mismatch)",
                TABLE,
                loser.scheme_code,
                loser.nav_date,
                winner["nav"],
                winner["source_file"],
                loser.nav,
                loser.source_file,
            )
    return rows.drop_duplicates(key, keep="first").reset_index(drop=True)


def _log_missing_schemes(rows: pd.DataFrame, feeders: tuple[Feeder, ...]) -> None:
    """Warn once about configured feeders for which no NAV was found at all."""
    seen = set(rows["scheme_code"])
    absent = [
        f"{f.amfi_scheme_code} ({f.etf_symbol})" for f in feeders if f.amfi_scheme_code not in seen
    ]
    if absent:
        logger.warning("%s: no NAV found yet for feeder scheme(s) %s", TABLE, ", ".join(absent))
