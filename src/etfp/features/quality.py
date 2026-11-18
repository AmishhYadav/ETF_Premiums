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

import logging
from collections.abc import Mapping, Sequence
from datetime import date

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

DQ_COLUMNS = (
    "dq_duplicate_row",
    "dq_prem_fv_extreme",
    "dq_prem_fv_jump",
    "dq_fv_far_from_nav",
    "dq_negative_units",
    "dq_missing_close",
    "dq_missing_nav_known",
    "dq_nav_stale",
    "dq_nav_misaligned",
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


class DuplicateRowsError(ValueError):
    """The analysis table has two rows for one (symbol, date): the build must stop."""


def require_no_duplicates(df: pd.DataFrame, keys: Sequence[str] = ("symbol", "date")) -> None:
    """Raise :class:`DuplicateRowsError` if any key appears twice (SPEC 12: fails the build).

    A duplicate is not "a row to look at": every average, return and premium computed
    over it would silently double-count. So unlike the other checks this one stops
    the build and names the first offending keys.

    Hand example: rows (A, 1 Sep), (A, 1 Sep), (B, 1 Sep) -> raises, naming (A, 1 Sep).
    """
    mask = flag_duplicates(df, keys)
    if bool(mask.any()):
        bad = df.loc[mask.astype(bool), list(keys)].drop_duplicates().head(5)
        names = ", ".join(str(tuple(r)) for r in bad.itertuples(index=False, name=None))
        raise DuplicateRowsError(
            f"duplicate {tuple(keys)} rows in the analysis table (first: {names}); "
            "fix the clean table that produced them, do not drop rows here"
        )


def expected_underlying_session(market: str, day: date, calendar: object | None = None) -> date:
    """The latest underlying-market session closed by 15:30 IST (India's close) on ``day``.

    This is the "a" in fair value: the NAV the Indian market can know at its close is
    struck from this underlying close (SPEC 6.1). Computed straight from the exchange
    calendars, independently of the NAV-alignment code, so the two can check each other.

    Hand example (US): Tue 8 Sep 2026 -> 4 Sep (Labor Day Mon 7 Sep; the 8 Sep close
    comes at ~01:30 IST on the 9th, after India has closed). Wed 9 Sep -> 8 Sep.
    Hong Kong closes at 13:30 IST, before India, so HK on 9 Sep -> 9 Sep.
    """
    from etfp import calendars

    cal = calendar if calendar is not None else calendars
    india_close = cal.market_close("IN", day)  # type: ignore[attr-defined]
    s = cal.last_session_on_or_before(market, day)  # type: ignore[attr-defined]
    while cal.market_close(market, s) > india_close:  # type: ignore[attr-defined]
        s = cal.previous_session(market, s)  # type: ignore[attr-defined]
    return s


def flag_nav_misaligned(
    df: pd.DataFrame,
    markets: Mapping[str, str],
    *,
    calendar: object | None = None,
    include_behind: bool = False,
    symbol_col: str = "symbol",
    date_col: str = "date",
    underlying_col: str = "nav_known_underlying_date",
    known_date_col: str = "nav_known_date",
) -> pd.Series:
    """NAV date alignment against the calendars (SPEC 12, 6.1).

    True when the NAV we used embeds an underlying close that had NOT happened yet at
    15:30 IST on the trading day (``nav_known_underlying_date`` later than
    ``expected_underlying_session``), or the NAV's own date is after the trading day.
    That is look-ahead: the fair value would use information the market could not
    have. NA when no NAV was used or the symbol's market is unknown.

    A NAV BEHIND the calendar (an older session than the freshest possible) is not
    flagged by default. It is a hole in the fund's NAV history, usually an Indian
    holiday (funds strike no NAV on one, so the US close of that day has no NAV until
    the next business day) or a skipped US-holiday NAV; ``dq_nav_same_date_missing``,
    ``dq_nav_stale`` and ``nav_publication_lag_days`` already show those, and ~60 such
    days per US fund over five years would drown real problems. Pass
    ``include_behind=True`` to flag them too.

    Hand example (US ETF, Tue 8 Sep 2026): used underlying 4 Sep -> False; used 8 Sep
    (closes after India does) -> True; used 3 Sep -> False (behind), True with
    ``include_behind``.
    """
    out = pd.Series(pd.NA, index=df.index, dtype="boolean")
    if underlying_col not in df.columns:
        return out
    cache: dict[tuple[str, date], date] = {}
    vals: list[object] = []
    known = df[known_date_col] if known_date_col in df.columns else [None] * len(df)
    for sym, d, a, k in zip(df[symbol_col], df[date_col], df[underlying_col], known, strict=True):
        market = markets.get(str(sym))
        if market is None or pd.isna(a):
            vals.append(pd.NA)
            continue
        day, used = pd.Timestamp(d).date(), pd.Timestamp(a).date()
        key = (market, day)
        if key not in cache:
            cache[key] = expected_underlying_session(market, day, calendar)
        expected = cache[key]
        bad = used > expected or (include_behind and used < expected)
        if k is not None and not pd.isna(k) and pd.Timestamp(k).date() > day:
            bad = True
        vals.append(bool(bad))
    out[:] = pd.array(vals, dtype="boolean")
    return out


def session_gaps(
    df: pd.DataFrame, *, symbol_col: str = "symbol", date_col: str = "date"
) -> dict[str, list[date]]:
    """NSE sessions with no row for a symbol between its first and last row (SPEC 12 gaps).

    A missing row means no price was collected that day, so close, NAV and every
    premium are absent for it. The days are returned (and logged by
    :func:`log_build_summary`), never invented or filled.

    Hand example: rows on Mon, Tue, Thu with no holiday -> Wed is the gap.
    """
    from etfp.clean.common import find_gaps

    out: dict[str, list[date]] = {}
    for sym, grp in df.groupby(symbol_col, sort=True):
        gaps = find_gaps([pd.Timestamp(d).date() for d in grp[date_col]])
        if gaps:
            out[str(sym)] = gaps
    return out


def log_build_summary(df: pd.DataFrame) -> dict[str, object]:
    """Log one warning per raised dq flag and per symbol with session gaps; return the numbers.

    The same counts reach the research manifest (``dq_flag_counts``,
    ``missing_nse_sessions_by_symbol``); this is the loud line in the build log.
    """
    counts = {
        c: int(df[c].fillna(False).astype(bool).sum()) for c in df.columns if c.startswith("dq_")
    }
    for name, n in counts.items():
        if n:
            logger.warning("data quality: %s is True on %d etf_day rows", name, n)
    gaps = session_gaps(df)
    for sym, days in gaps.items():
        logger.warning(
            "data quality: %s has no row on %d NSE sessions (first %s)",
            sym,
            len(days),
            [d.isoformat() for d in days[:3]],
        )
    return {"dq_counts": counts, "session_gaps": {s: len(d) for s, d in gaps.items()}}


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
    markets: Mapping[str, str] | None = None,
    calendar: object | None = None,
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
    out["dq_nav_misaligned"] = (
        flag_nav_misaligned(df, markets, calendar=calendar)
        if markets is not None and has("symbol") and has("date")
        else na
    )
    return out


def dq_summary(df: pd.DataFrame) -> dict[str, int]:
    """Count of True per flag column present in ``df`` (for the export manifest)."""
    return {c: int(df[c].fillna(False).astype(bool).sum()) for c in DQ_COLUMNS if c in df.columns}
