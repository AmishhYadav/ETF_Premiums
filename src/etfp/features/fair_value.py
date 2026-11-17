"""Fair value: the NAV moved forward to the Indian close (SPEC 6.2 as rewritten by D10).

The idea in plain words
-----------------------
The newest NAV we can know at the Indian close is old: for a US-linked ETF it
was struck on the previous US close (see ``nav_align``). Between that US close
and 15:30 IST the Nasdaq/S&P futures kept trading and the rupee kept moving. If
the futures are up 1% and the rupee is 0.5% weaker, then what the ETF's holdings
are worth *right now, in rupees* is about 1.5% more than the stale NAV. That
adjusted number is the "fair value" (FV). Comparing the exchange price with FV
instead of with the stale NAV removes the part of the "premium" that is only
the NAV being out of date.

Formula (D10)::

    FV_t = NAV_a  x  F_t[15:30 IST] / F[close of a]  x  S_t[15:30 IST] / S_a

* ``a``  the underlying-market session whose close is embedded in the NAV used
  (``nav_known_underlying_date``).
* ``F``  front-month index future (NQ for Nasdaq ETFs; ES as the proxy for S&P
  Top 50 and FANG+). ``F[close of a]`` is its price at the US close of ``a``,
  ``F_t`` its price at 15:30 IST on the Indian day ``t``.
* ``S``  rupees per unit of the underlying currency (USD/INR or HKD/INR);
  ``S_a`` at the time the fund uses for its NAV, ``S_t`` at 15:30 IST.

Hand example (SPEC 6.2): NAV 100, F 20,000 -> 20,200 (x 1.01), S 84.00 -> 84.42
(x 1.005): FV = 100 x 1.01 x 1.005 = 101.505.

Hong Kong ETFs have no futures term (the HK market closes before India, so the
index barely moves after the NAV is struck): only the FX ratio is applied.
Domestic ETFs need neither.

``fv_method`` says what quality of input produced each row:

* ``'bloomberg'``     all needed inputs came from Bloomberg.
* ``'free_futures'``  needed inputs came from free sources (D13).
* ``'nav_only'``      a needed input was missing, so FV = NAV_a. This also labels
                      domestic ETFs, which need no adjustment.
* ``'no_nav'``        there is no NAV to start from; FV is NaN.

For a Hong Kong ETF the label names the data tier of the FX input.

Every input used is echoed back in ``fv_in_*`` columns so a number can be traced
(CLAUDE.md: every number must be traceable). Pure functions only.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from etfp import calendars
from etfp.config import Etf

FV_METHODS = ("bloomberg", "free_futures", "nav_only", "no_nav")
_SOURCE_TO_METHOD = {"bloomberg": "bloomberg", "free": "free_futures"}

OUTPUT_COLUMNS = (
    "fv",
    "fv_method",
    "fv_futures_ratio",
    "fv_fx_ratio",
    "fv_in_nav_a",
    "fv_in_futures_t",
    "fv_in_futures_a",
    "fv_in_fx_t",
    "fv_in_fx_a",
)


# -- futures contract rolls (free continuous series) ---------------------------------
#
# Plain words. "NQ=F" is not one contract: it is whatever contract is "front" today. Each
# quarter (Mar, Jun, Sep, Dec) it switches from the expiring contract to the next one,
# which trades HIGHER by the cost of carry (about 1% a quarter at 2026 rates, the
# "calendar spread"). If F_a was read from the old contract and F_t from the new one, the
# ratio F_t / F_a contains that ~1% jump although the market did not move, and FV would be
# about 1% too high. We cannot see which contract a Yahoo bar belongs to, so on the few days
# where a switch can fall between the two readings we do not adjust at all (nav_only).
#
# When can the switch fall between the readings? F_a is read at the US close (16:00 New
# York) and F_t at 15:30 IST (05:00 or 06:00 New York) the next morning, so only a switch
# during the US night matters, i.e. at a CME session open (18:00 New York). The two
# candidate session opens per quarter are the one that starts the CME "roll date" (the
# Thursday 8 days before expiry, when volume moves) and the one that starts the expiry day
# (third Friday). Which one Yahoo uses is VERIFY (D43); both are guarded, which costs
# two rows per ETF per quarter. For the beta (close-to-close returns) a switch during the
# US day also matters, so the expiry-day open (09:30 New York) is added there.

_NEW_YORK = "America/New_York"
_QUARTER_MONTHS = (3, 6, 9, 12)


def cme_quarterly_expiry(year: int, month: int) -> date:
    """Expiry day of the Mar/Jun/Sep/Dec equity index future (final settlement at the 09:30
    New York open): the third Friday, or the US session before it when that Friday is an
    exchange holiday (19 Jun 2026, Juneteenth -> Thu 18 Jun 2026)."""
    if month not in _QUARTER_MONTHS:
        raise ValueError(f"equity index futures expire in Mar/Jun/Sep/Dec, not month {month}")
    first = date(year, month, 1)
    third_friday = first + timedelta(days=(4 - first.weekday()) % 7 + 14)
    try:
        return calendars.last_session_on_or_before("US", third_friday)
    except ValueError:  # outside the calendar's range: keep the plain rule
        return third_friday


def _third_friday(year: int, month: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(4 - first.weekday()) % 7 + 14)


def futures_roll_moments(
    start: datetime, end: datetime, *, include_expiry_open: bool = False
) -> list[datetime]:
    """Candidate front-contract switch moments (tz-aware UTC) with ``start < m <= end``.

    Per quarter, with R = third Friday minus 8 days (the CME roll date, a Thursday) and E =
    the expiry day (:func:`cme_quarterly_expiry`): 18:00 New York on R-1 (opens R's session)
    and 18:00 New York on E-1 (opens E's session); with ``include_expiry_open`` also 09:30
    New York on E.
    """
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("start and end must be timezone-aware")
    out: list[datetime] = []
    s = pd.Timestamp(start).tz_convert("UTC")
    e = pd.Timestamp(end).tz_convert("UTC")
    for year in range(s.year - 1, e.year + 2):
        for month in _QUARTER_MONTHS:
            exp = cme_quarterly_expiry(year, month)
            local = [
                (_third_friday(year, month) - timedelta(days=9), 18, 0),
                (exp - timedelta(days=1), 18, 0),
            ]
            if include_expiry_open:
                local.append((exp, 9, 30))
            for d, hh, mm in local:
                m = pd.Timestamp(d.year, d.month, d.day, hh, mm, tz=_NEW_YORK)
                m = m.tz_convert("UTC")
                if s < m <= e:
                    out.append(m.to_pydatetime())
    return sorted(out)


def crosses_futures_roll(
    start: datetime, end: datetime, *, include_expiry_open: bool = False
) -> bool:
    """True if a candidate contract switch lies in ``(start, end]`` (see above)."""
    return bool(futures_roll_moments(start, end, include_expiry_open=include_expiry_open))


def fv_terms_for(etf: Etf) -> tuple[bool, bool]:
    """(use_futures, use_fx) for an ETF: futures if it has a proxy, FX if not INR-based."""
    return etf.futures_proxy is not None, etf.currency != "INR"


def _checked(series: pd.Series, name: str) -> pd.Series:
    """Float series; NaN stays (= missing), anything else must be positive and finite."""
    s = pd.to_numeric(series, errors="raise").astype(float)
    bad = s.notna() & ~(np.isfinite(s) & (s > 0))
    if bad.any():
        raise ValueError(
            f"{name} must be positive and finite where present; bad rows {list(s.index[bad][:5])}"
        )
    return s


def fair_value_frame(
    df: pd.DataFrame,
    *,
    use_futures: bool = True,
    use_fx: bool = True,
    source: str | pd.Series = "free",
    nav_col: str = "nav_known_at_close",
    futures_t_col: str = "futures_t",
    futures_a_col: str = "futures_a",
    fx_t_col: str = "fx_t",
    fx_a_col: str = "fx_a",
) -> pd.DataFrame:
    """Fair value for many ETF-days of ONE kind of ETF (same ``use_futures``/``use_fx``).

    Columns read from ``df``: NAV used (``nav_col``), futures at 15:30 IST on t and at
    the close of ``a``, FX at 15:30 IST on t and at NAV time of ``a``. Columns for terms
    that are switched off may be absent. Returns a frame with the same index and
    ``OUTPUT_COLUMNS``.

    ``source`` is ``'bloomberg'`` or ``'free'`` (a string for all rows, or a Series
    aligned to ``df`` when days mix). A row falls back to ``nav_only`` when any
    needed input is NaN. Zero, negative or infinite inputs raise ``ValueError``.
    """
    index = df.index
    nan = pd.Series(np.nan, index=index)

    def col(name: str, needed: bool) -> pd.Series:
        if name in df.columns:
            return _checked(df[name], name)
        if needed:
            raise ValueError(f"fair value needs column {name!r}")
        return nan

    nav = col(nav_col, True)
    f_t, f_a = col(futures_t_col, use_futures), col(futures_a_col, use_futures)
    s_t, s_a = col(fx_t_col, use_fx), col(fx_a_col, use_fx)

    have = nav.notna()
    if use_futures:
        have &= f_t.notna() & f_a.notna()
    if use_fx:
        have &= s_t.notna() & s_a.notna()

    f_ratio = (f_t / f_a) if use_futures else pd.Series(1.0, index=index)
    s_ratio = (s_t / s_a) if use_fx else pd.Series(1.0, index=index)
    adjusted = nav * f_ratio * s_ratio

    src = source if isinstance(source, pd.Series) else pd.Series(source, index=index)
    unknown = set(src.unique()) - set(_SOURCE_TO_METHOD)
    if unknown:
        raise ValueError(f"source must be 'bloomberg' or 'free', got {sorted(map(str, unknown))}")
    tier = src.map(_SOURCE_TO_METHOD)

    needs_adjust = use_futures or use_fx
    if needs_adjust:
        method = pd.Series(np.where(have, tier, "nav_only"), index=index, dtype="object")
        fv = pd.Series(np.where(have, adjusted, nav), index=index)
    else:
        method = pd.Series("nav_only", index=index, dtype="object")
        fv = nav.copy()
    method[nav.isna()] = "no_nav"
    fv[nav.isna()] = np.nan

    adj_applied = have & needs_adjust
    return pd.DataFrame(
        {
            "fv": fv.astype(float),
            "fv_method": method,
            "fv_futures_ratio": f_ratio.where(adj_applied & use_futures),
            "fv_fx_ratio": s_ratio.where(adj_applied & use_fx),
            "fv_in_nav_a": nav,
            "fv_in_futures_t": f_t if use_futures else nan,
            "fv_in_futures_a": f_a if use_futures else nan,
            "fv_in_fx_t": s_t if use_fx else nan,
            "fv_in_fx_a": s_a if use_fx else nan,
        },
        index=index,
    )[list(OUTPUT_COLUMNS)]


def fair_value(
    nav_a: float | None,
    *,
    futures_t: float | None = None,
    futures_a: float | None = None,
    fx_t: float | None = None,
    fx_a: float | None = None,
    use_futures: bool = True,
    use_fx: bool = True,
    source: str = "free",
) -> dict[str, float | str | None]:
    """One-row convenience wrapper around :func:`fair_value_frame`.

    >>> r = fair_value(100, futures_t=20200, futures_a=20000, fx_t=84.42, fx_a=84.00)
    >>> round(r["fv"], 3), r["fv_method"]
    (101.505, 'free_futures')
    """
    row = pd.DataFrame(
        {
            "nav_known_at_close": [nav_a if nav_a is not None else np.nan],
            "futures_t": [futures_t if futures_t is not None else np.nan],
            "futures_a": [futures_a if futures_a is not None else np.nan],
            "fx_t": [fx_t if fx_t is not None else np.nan],
            "fx_a": [fx_a if fx_a is not None else np.nan],
        }
    )
    res = fair_value_frame(row, use_futures=use_futures, use_fx=use_fx, source=source)
    return {k: (None if pd.isna(v) else v) for k, v in res.iloc[0].to_dict().items()}
