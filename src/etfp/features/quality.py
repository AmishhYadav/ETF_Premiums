"""Data-quality flags (SPEC 12). Pure functions that return flag columns.

Rule from SPEC 12: a flag means "a human should look". Rows are never dropped or
"corrected" here. Every function returns a boolean Series with the input's index:
``True`` = problem found, ``False`` = checked and fine, ``<NA>`` = could not be
checked because an input was missing (a missing input is reported separately by
:func:`flag_missing`, not guessed at).

Thresholds are arguments, with the SPEC defaults, so the build can read them from
config later. Premiums are fractions (0.5 = 50 percentage points).
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd

DQ_COLUMNS = (
    "dq_duplicate_row",
    "dq_prem_fv_extreme",
    "dq_prem_fv_jump",
    "dq_fv_far_from_nav",
    "dq_negative_units",
    "dq_missing_close",
    "dq_missing_nav_known",
    "dq_nav_stale",
)


def _bool(values: pd.Series | np.ndarray, na_mask: pd.Series, index: pd.Index) -> pd.Series:
    out = pd.Series(np.asarray(values, dtype=bool), index=index).astype("boolean")
    out[na_mask.to_numpy()] = pd.NA
    return out


def flag_duplicates(df: pd.DataFrame, keys: Sequence[str] = ("symbol", "date")) -> pd.Series:
    """True on EVERY row that shares its key with another row (not just the later ones).

    Hand example: two rows for (MONQ50, 2026-09-04) -> both True; a lone row False.
    """
    for k in keys:
        if k not in df.columns:
            raise ValueError(f"flag_duplicates: missing key column {k!r}")
    return df.duplicated(list(keys), keep=False).astype("boolean")


def flag_extreme_premium(prem: pd.Series, threshold: float = 3.0) -> pd.Series:
    """|premium| > threshold (default 3.0 = 300%). NA if the premium is missing.

    Hand example: 3.5 -> True; -3.5 -> True; 3.0 -> False (not strictly greater).
    """
    p = pd.to_numeric(prem, errors="coerce")
    return _bool((p.abs() > threshold).fillna(False), p.isna(), p.index)


def flag_premium_jump(
    df: pd.DataFrame,
    *,
    prem_col: str = "prem_fv",
    symbol_col: str = "symbol",
    date_col: str = "date",
    max_points: float = 0.5,
) -> pd.Series:
    """Day-over-day change in premium larger than ``max_points`` (default 0.5 = 50 points).

    Compared with the previous ROW of the same symbol in date order (so a missing
    trading day makes the comparison span the gap; gaps are flagged by
    :func:`flag_missing`). The first row of a symbol is NA. The result is aligned to
    ``df``'s original index.

    Hand example: premium 0.30 then 0.95 -> change 0.65 > 0.5 -> True on the second day.
    """
    for c in (prem_col, symbol_col, date_col):
        if c not in df.columns:
            raise ValueError(f"flag_premium_jump: missing column {c!r}")
    work = df.sort_values([symbol_col, date_col], kind="stable")
    prem = pd.to_numeric(work[prem_col], errors="coerce")
    change = prem - prem.groupby(work[symbol_col], sort=False).shift(1)
    flag = _bool((change.abs() > max_points).fillna(False), change.isna(), change.index)
    return flag.reindex(df.index)


def flag_fv_far_from_nav(
    fv: pd.Series,
    nav_known: pd.Series,
    underlying_move: pd.Series | None = None,
    *,
    max_gap: float = 0.05,
    move_excuse: float = 0.05,
) -> pd.Series:
    """FV differs from the known NAV by more than ``max_gap`` with no big underlying move.

    Fair value is the NAV moved by futures and FX, so a big gap is expected only when
    the market itself moved a lot. ``underlying_move`` is the futures return since the
    NAV's close (``futures_t / futures_a - 1``) as a fraction; where it is missing there
    is no excuse and the gap alone decides. The flag is False when ``|underlying_move|
    > move_excuse``.

    Hand example: NAV 100, FV 107, futures move +1% -> gap 7% > 5% and no excuse -> True.
    Same gap with a futures move of +8% -> False (the market moved). NA if FV or NAV is
    missing.
    """
    f = pd.to_numeric(fv, errors="coerce")
    n = pd.to_numeric(nav_known, errors="coerce")
    gap = (f / n - 1.0).where((f > 0) & (n > 0))
    if underlying_move is None:
        moved = pd.Series(False, index=f.index)
    else:
        mv = pd.to_numeric(underlying_move, errors="coerce")
        moved = (mv.abs() > move_excuse).fillna(False)
    flagged = (gap.abs() > max_gap) & ~moved
    return _bool(flagged.fillna(False), gap.isna(), gap.index)


def flag_negative_units(units: pd.Series) -> pd.Series:
    """Units outstanding below zero (impossible). NA if missing. Zero is allowed here."""
    u = pd.to_numeric(units, errors="coerce")
    return _bool((u < 0).fillna(False), u.isna(), u.index)


def flag_missing(values: pd.Series, *, expected: pd.Series | None = None) -> pd.Series:
    """True where a value that should exist is missing.

    ``expected`` (boolean, same index) says on which rows the value is required, e.g.
    "ETF traded that day". Default: required everywhere.

    Hand example: close [10.0, NaN, 12.0] with expected all True -> [False, True, False].
    """
    miss = pd.to_numeric(values, errors="coerce").isna()
    if expected is not None:
        miss &= expected.astype(bool)
    return miss.astype("boolean")


def flag_nav_stale(lag_days: pd.Series, *, max_days: int = 5, min_days: int = 0) -> pd.Series:
    """NAV alignment check (SPEC 12 against calendars): the NAV used is too old or from the future.

    ``lag_days`` is ``nav_publication_lag_days`` (calendar days from the NAV's date to
    the trading day). A long US weekend can legitimately give 4-5; more than
    ``max_days`` means NAV data is missing for a stretch. A negative lag means a NAV
    dated AFTER the day was used, which would be look-ahead. NA if missing.

    Hand example: lag 3 (Monday using Friday) False; lag 8 True; lag -1 True.
    """
    lag = pd.to_numeric(lag_days, errors="coerce")
    return _bool(((lag > max_days) | (lag < min_days)).fillna(False), lag.isna(), lag.index)


def add_quality_flags(
    df: pd.DataFrame,
    *,
    prem_col: str = "prem_fv",
    fv_col: str = "fv",
    nav_known_col: str = "nav_known_at_close",
    units_col: str = "units_outstanding",
    close_col: str = "close",
    lag_col: str = "nav_publication_lag_days",
    futures_t_col: str = "fv_in_futures_t",
    futures_a_col: str = "fv_in_futures_a",
    extreme_threshold: float = 3.0,
    jump_points: float = 0.5,
) -> pd.DataFrame:
    """Return a copy of ``df`` with the ``DQ_COLUMNS`` appended (absent input columns give NA).

    Never changes or drops existing rows. Call ``dq_summary`` for counts.
    """
    out = df.copy()
    na = pd.Series(pd.NA, index=df.index, dtype="boolean")

    def has(c: str) -> bool:
        return c in df.columns

    out["dq_duplicate_row"] = flag_duplicates(df) if has("symbol") and has("date") else na
    out["dq_prem_fv_extreme"] = (
        flag_extreme_premium(df[prem_col], extreme_threshold) if has(prem_col) else na
    )
    out["dq_prem_fv_jump"] = (
        flag_premium_jump(df, prem_col=prem_col, max_points=jump_points)
        if has(prem_col) and has("symbol") and has("date")
        else na
    )
    if has(fv_col) and has(nav_known_col):
        move = None
        if has(futures_t_col) and has(futures_a_col):
            move = (
                pd.to_numeric(df[futures_t_col], errors="coerce")
                / pd.to_numeric(df[futures_a_col], errors="coerce")
                - 1.0
            )
        out["dq_fv_far_from_nav"] = flag_fv_far_from_nav(df[fv_col], df[nav_known_col], move)
    else:
        out["dq_fv_far_from_nav"] = na
    out["dq_negative_units"] = flag_negative_units(df[units_col]) if has(units_col) else na
    out["dq_missing_close"] = flag_missing(df[close_col]) if has(close_col) else na
    out["dq_missing_nav_known"] = flag_missing(df[nav_known_col]) if has(nav_known_col) else na
    out["dq_nav_stale"] = flag_nav_stale(df[lag_col]) if has(lag_col) else na
    return out


def dq_summary(df: pd.DataFrame) -> dict[str, int]:
    """Count of True per flag column present in ``df`` (for the export manifest)."""
    return {c: int(df[c].fillna(False).astype(bool).sum()) for c in DQ_COLUMNS if c in df.columns}
