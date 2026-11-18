"""Units outstanding, creation/redemption events and a data-quality cross-check (R1, D4, D7).

Why units matter (plain words)
------------------------------
An ETF can only get new units when a market maker hands the fund house a basket
of securities (a "creation") and can only lose units when units are handed back
(a "redemption"). So the number of units outstanding is the direct evidence of
whether creation was possible: if it never rises, the supply is shut.
NSE publishes the count every day in its security-master file as ``IssdCptl``
(``issued_units`` after parsing). A creation or redemption always moves the count
by a whole number of "creation units" (the basket size, for example 600,000 units
for MONQ50), which is a good check that the count is read correctly (D7).

Definitions (R1)
----------------
``units_change``           units today - units at the previous observation.
``creation_units_change``  ``units_change`` / creation-unit size (positive = creation,
                           negative = redemption). NaN when the size is not in
                           ``config/etfs.yaml`` (use :func:`missing_creation_unit_sizes`
                           to list the gaps). The size is the one in force on the row's
                           ``date`` (the later observation) when the fund has a
                           ``creation_unit_history`` (HDFCNIFTY: 4,000 units until
                           19 Aug 2025, 20,000 from 20 Aug 2025); a change observed on or
                           after a step date uses the new size, even if part of it was
                           created before. Without a history, one size for all dates.
``creation_multiple_ok``   True when the change is a whole number of creation units
                           (to 1e-9). False flags a data problem or a corporate
                           action (a unit split changes the count without any creation).
``days_since_prev``        calendar days since the previous observation. A change over
                           a long gap (monthly data) is attributed to the later date;
                           the gap length is kept so nobody reads it as a one-day event.

Hand example (D7, MONQ50, creation unit 600,000): 3,275,018 on 1 Jul 2024 and
8,075,018 on 2 Jan 2025 -> change +4,800,000 = 8 creation units; then 14,675,018
on 1 Apr 2026 -> +6,600,000 = 11 more. Total 19 (R19 c).

Cross-check (data quality only, not the valve definition)
---------------------------------------------------------
AMC month-end AUM divided by month-end NAV should be close to the units count.
:func:`units_aum_crosscheck` flags months where they differ by more than a
tolerance. PRELIMINARY: D4 lists the update lag of ``IssdCptl`` as OPEN, so a flag
means "look at this month", not "the number is wrong".

Pure functions only.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

CHANGE_COLUMNS = (
    "symbol",
    "date",
    "units_outstanding",
    "units_prev",
    "units_prev_date",
    "days_since_prev",
    "units_change",
    "creation_unit_size",
    "creation_units_change",
    "creation_multiple_ok",
    "event_type",
    "source_file",
)

EVENT_COLUMNS = (
    "symbol",
    "date",
    "prev_date",
    "units_prev",
    "units_new",
    "units_change",
    "creation_units_change",
    "creation_multiple_ok",
    "event_type",
    "days_since_prev",
    "source_file",
)


def creation_unit_sizes(universe: Any) -> dict[str, int | None]:
    """Symbol -> creation-unit size from an ``EtfUniverse`` (None where not configured)."""
    return {e.symbol: e.creation_unit_size for e in universe.etfs}


def creation_unit_histories(universe: Any) -> dict[str, list[tuple[date, int]]]:
    """Symbol -> [(first date, size), ...] oldest first, only for ETFs with a history."""
    return {
        e.symbol: [(st.from_date, st.size) for st in e.creation_unit_history]
        for e in universe.etfs
        if e.creation_unit_history
    }


def size_on_dates(
    dates: pd.Series,
    symbols: pd.Series,
    sizes: Mapping[str, int | None],
    histories: Mapping[str, Sequence[tuple[date, int]]] | None = None,
) -> pd.Series:
    """Creation-unit size in force on each row's date (float; NaN where unknown).

    ``sizes`` is the single current size per symbol. For a symbol in ``histories`` the size
    is the last step whose first date is on or before the row's date; before the first step
    it is NaN (the size then is not documented). Hand example (HDFCNIFTY): steps
    ``[(2024-02-05, 4000), (2025-08-20, 20000)]`` give 4,000 on 2025-08-19 and 20,000 on
    2025-08-20.
    """
    hist = histories or {}
    d = pd.to_datetime(dates)
    out = symbols.map(lambda s: sizes.get(s)).astype("float64")
    for sym, steps in hist.items():
        mask = symbols == sym
        if not mask.any():
            continue
        starts = np.array([np.datetime64(f, "ns") for f, _ in steps])
        vals = np.array([sz for _, sz in steps], dtype="float64")
        pos = np.searchsorted(starts, d[mask].to_numpy(dtype="datetime64[ns]"), side="right") - 1
        out.loc[mask] = np.where(pos >= 0, vals[np.clip(pos, 0, None)], np.nan)
    return out.where(out > 0)


def missing_creation_unit_sizes(
    symbols: list[str] | tuple[str, ...], sizes: Mapping[str, int | None]
) -> list[str]:
    """Symbols whose creation-unit size is unknown (so ``creation_units_change`` is NaN)."""
    return sorted(s for s in set(symbols) if not sizes.get(s))


def units_changes(
    units_daily: pd.DataFrame,
    sizes: Mapping[str, int | None],
    *,
    histories: Mapping[str, Sequence[tuple[date, int]]] | None = None,
    symbol_col: str = "symbol",
    date_col: str = "date",
    units_col: str = "units_outstanding",
) -> pd.DataFrame:
    """Per-observation change in units for every symbol (see module docstring).

    ``units_daily`` has one row per (symbol, date). The first observation of a symbol
    has no previous value, so its change is NaN and it is not an event. A row with
    missing units keeps NaN changes; the next valid row is compared with the last
    valid one. ``histories`` (from :func:`creation_unit_histories`) gives dated size steps for
    funds whose basket size changed; see :func:`size_on_dates`. Raises on duplicate
    (symbol, date) or negative units (silently "fixing" them would hide a bad file; the
    quality module flags them first).
    """
    for col in (symbol_col, date_col, units_col):
        if col not in units_daily.columns:
            raise ValueError(f"units_changes needs column {col!r}; has {list(units_daily.columns)}")
    if units_daily.duplicated([symbol_col, date_col]).any():
        raise ValueError("duplicate (symbol, date) rows in units_daily")
    work = units_daily.copy()
    work["_d"] = pd.to_datetime(work[date_col])
    work["_u"] = pd.to_numeric(work[units_col], errors="coerce").astype("float64")
    if (work["_u"] < 0).any():
        bad = work.loc[work["_u"] < 0, [symbol_col, date_col]].head(5).to_dict("records")
        raise ValueError(f"negative units outstanding: {bad}")
    work = work.sort_values([symbol_col, "_d"], kind="stable").reset_index(drop=True)

    valid_u = work["_u"]
    valid_d = work["_d"].where(valid_u.notna())
    prev_u = valid_u.groupby(work[symbol_col], sort=False).transform(lambda s: s.ffill().shift(1))
    prev_d = valid_d.groupby(work[symbol_col], sort=False).transform(lambda s: s.ffill().shift(1))

    change = (work["_u"] - prev_u).where(work["_u"].notna() & prev_u.notna())
    size = size_on_dates(work["_d"], work[symbol_col], sizes, histories)
    cu = change / size
    multiple_ok = pd.Series(pd.NA, index=work.index, dtype="boolean")
    known = cu.notna()
    multiple_ok[known] = (np.abs(cu[known] - np.round(cu[known])) < 1e-9).astype(bool)

    event_type = pd.Series(
        np.select([change > 0, change < 0], ["creation", "redemption"], default=""),
        index=work.index,
        dtype="object",
    ).where(change.notna() & (change != 0))

    out = pd.DataFrame(
        {
            "symbol": work[symbol_col],
            "date": work[date_col],
            "units_outstanding": work["_u"].astype("Int64"),
            "units_prev": prev_u.astype("Int64"),
            "units_prev_date": [None if pd.isna(d) else d.date() for d in prev_d],
            "days_since_prev": (work["_d"] - prev_d).dt.days.astype("Int64"),
            "units_change": change.astype("Int64"),
            "creation_unit_size": size.astype("Int64"),
            "creation_units_change": cu,
            "creation_multiple_ok": multiple_ok,
            "event_type": event_type,
            "source_file": work["source_file"] if "source_file" in work.columns else None,
        }
    )
    return out[list(CHANGE_COLUMNS)]


def unit_change_events(changes: pd.DataFrame) -> pd.DataFrame:
    """One row per observation where the unit count moved (R1 "event row").

    Takes the output of :func:`units_changes`. ``event_type`` is ``'creation'`` for
    an increase and ``'redemption'`` for a decrease.
    """
    ev = changes[changes["event_type"].notna()]
    out = pd.DataFrame(
        {
            "symbol": ev["symbol"],
            "date": ev["date"],
            "prev_date": ev["units_prev_date"],
            "units_prev": ev["units_prev"],
            "units_new": ev["units_outstanding"],
            "units_change": ev["units_change"],
            "creation_units_change": ev["creation_units_change"],
            "creation_multiple_ok": ev["creation_multiple_ok"],
            "event_type": ev["event_type"],
            "days_since_prev": ev["days_since_prev"],
            "source_file": ev["source_file"],
        }
    )
    return out[list(EVENT_COLUMNS)].reset_index(drop=True)


def units_aum_crosscheck(
    units_daily: pd.DataFrame,
    monthly: pd.DataFrame,
    *,
    tolerance: float = 0.02,
    max_gap_days: int = 10,
) -> pd.DataFrame:
    """Compare AMC month-end AUM / NAV with the exchange unit count (data-quality flag).

    ``monthly`` columns: ``symbol``, ``month_end`` (date), ``aum_rs`` (rupees, NOT crore)
    and ``nav`` (rupees per unit at month end). ``units_daily`` as for
    :func:`units_changes`. For each month the latest exchange count on or before
    ``month_end`` (within ``max_gap_days``) is compared.

    Output columns: ``symbol, month_end, implied_units, units_exchange,
    units_exchange_date, rel_diff, flag_units_aum_mismatch``. ``rel_diff`` =
    implied / exchange - 1; the flag is True when ``|rel_diff| > tolerance`` and null
    when no exchange count is close enough to compare.

    Hand example: AUM Rs 1,467,501,800 / NAV Rs 100 = 14,675,018 implied units against
    14,675,018 on the exchange -> rel_diff 0; AUM 10% higher -> rel_diff 0.10, flagged.
    """
    for col in ("symbol", "month_end", "aum_rs", "nav"):
        if col not in monthly.columns:
            raise ValueError(f"monthly frame needs column {col!r}")
    m = monthly.copy()
    m["_m"] = pd.to_datetime(m["month_end"])
    m["implied_units"] = np.where(
        (m["nav"] > 0) & (m["aum_rs"] >= 0), m["aum_rs"] / m["nav"], np.nan
    )
    u = units_daily[["symbol", "date", "units_outstanding"]].copy()
    u["_d"] = pd.to_datetime(u["date"])
    u["units_outstanding"] = pd.to_numeric(u["units_outstanding"], errors="coerce").astype(float)
    u = u.dropna(subset=["units_outstanding"]).sort_values("_d")
    u = u.rename(columns={"date": "units_exchange_date", "units_outstanding": "units_exchange"})
    merged = pd.merge_asof(
        m.sort_values("_m"),
        u,
        left_on="_m",
        right_on="_d",
        by="symbol",
        direction="backward",
        tolerance=pd.Timedelta(days=max_gap_days),
    )
    rel = merged["implied_units"] / merged["units_exchange"] - 1.0
    comparable = rel.notna() & np.isfinite(rel)
    flag = pd.Series(pd.NA, index=merged.index, dtype="boolean")
    flag[comparable] = (rel[comparable].abs() > tolerance).astype(bool)
    out = pd.DataFrame(
        {
            "symbol": merged["symbol"],
            "month_end": merged["month_end"],
            "implied_units": merged["implied_units"],
            "units_exchange": merged["units_exchange"],
            "units_exchange_date": merged["units_exchange_date"],
            "rel_diff": rel.where(comparable),
            "flag_units_aum_mismatch": flag,
        }
    )
    return out.sort_values(["symbol", "month_end"]).reset_index(drop=True)
