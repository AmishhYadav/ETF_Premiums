"""Beta of a fund against its futures proxy, and the beta-adjusted fair value (REQUESTS R12).

Why a beta at all (plain words)
-------------------------------
Fair value (``fair_value.py``) moves the last known NAV forward with the index
future: if the Nasdaq future is up 1% since the NAV was struck, the fund is
assumed to be worth 1% more. That assumes the fund moves one-for-one with the
future (beta = 1). But the funds do not track the index the future is written on:
the Nasdaq Q-50, the NYSE FANG+ and the S&P 500 Top 50 are not the Nasdaq-100 or
the S&P 500. A fund that moves only 0.8% when the future moves 1% has beta 0.8.

Definition (R12)
----------------
For each NAV date *d* in the estimation window (the 250 NAV dates before
2026-09-07)::

    y_d = ln(NAV_d / NAV_prev)  -  ln(FX_d / FX_prev)       NAV return net of the currency
    x_d = ln(F_u(d) / F_u(prev))                             future's close-to-close return

``u(d)`` is the underlying-market session whose close is baked into the NAV dated
*d* (on a US holiday the NAV is carried forward, so the session repeats and the
future's return is exactly 0). ``prev`` is the previous NAV date. Beta is the slope
of the ordinary-least-squares line ``y = a + beta x`` (an intercept is included: it
absorbs the fund's daily expense drag). The standard error is the usual
``sqrt(s^2 / Sxx)`` with ``s^2 = SSR / (n - 2)``.

Beta-adjusted fair value (log scale, because beta was estimated on log returns)::

    fv_beta = NAV_a x (F_t / F_a) ** beta x (S_t / S_a)

With beta = 1 this is exactly ``fv``.

Missing inputs
--------------
The estimate is NaN (never a guess) when there are fewer than ``min_obs`` usable
return pairs or when the future does not vary. ``BetaEstimate.reason`` says why.
Funds without a futures term (Hong Kong, domestic) have no beta; the build then
copies ``fv`` into ``fv_beta`` (nothing to scale) and says so in ``fv_beta_method``.

Pure functions: no files, no clock. Calendars are passed in.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

BETA_CUTOFF = date(2026, 9, 7)
"""Estimation uses NAV dates strictly before this day (the day the band anchor changed)."""
BETA_WINDOW = 250
"""Number of NAV dates (return observations) used, counted back from the cutoff."""
MIN_OBS = 60
"""Fewer usable observations than this gives NaN."""


@dataclass(frozen=True)
class BetaEstimate:
    """Result of one regression. All numbers are NaN when ``reason`` is not empty."""

    beta: float
    se: float
    intercept: float
    r_squared: float
    n_obs: int
    first_nav_date: date | None
    last_nav_date: date | None
    reason: str = ""

    @property
    def ok(self) -> bool:
        return not self.reason


def _failed(reason: str, n_obs: int = 0) -> BetaEstimate:
    nan = float("nan")
    return BetaEstimate(nan, nan, nan, nan, n_obs, None, None, reason)


def ols_slope(x: np.ndarray, y: np.ndarray) -> tuple[float, float, float, float, int]:
    """Slope, its standard error, intercept, R-squared and n of ``y = a + b x``.

    Hand example: x = [-.02, -.01, 0, .01, .02], y = [-.03, -.01, .01, .02, .03].
    x-mean 0, y-mean 0.004, Sxx = 0.001, Sxy = 0.0015 -> slope 1.5. Residuals are
    [-.004, .001, .006, .001, -.004], SSR = 7e-5, s^2 = 7e-5 / 3, SE =
    sqrt(s^2 / Sxx) = 0.152753.

    Raises ``ValueError`` for fewer than 3 points or a constant ``x``.
    """
    xs = np.asarray(x, dtype="float64")
    ys = np.asarray(y, dtype="float64")
    n = len(xs)
    if n != len(ys):
        raise ValueError("x and y must have the same length")
    if n < 3:
        raise ValueError("need at least 3 points for a slope and its standard error")
    xm, ym = xs.mean(), ys.mean()
    sxx = float(((xs - xm) ** 2).sum())
    if sxx <= 0.0 or not math.isfinite(sxx):
        raise ValueError("x does not vary; the slope is undefined")
    sxy = float(((xs - xm) * (ys - ym)).sum())
    slope = sxy / sxx
    intercept = float(ym - slope * xm)
    resid = ys - (intercept + slope * xs)
    ssr = float((resid**2).sum())
    sst = float(((ys - ym) ** 2).sum())
    se = math.sqrt(ssr / (n - 2) / sxx)
    r2 = 1.0 - ssr / sst if sst > 0 else float("nan")
    return slope, se, intercept, r2, n


def fund_beta(
    nav: pd.Series,
    futures_at_close: pd.Series,
    underlying_session: Callable[[date], date],
    fx: pd.Series | None = None,
    *,
    cutoff: date = BETA_CUTOFF,
    window: int = BETA_WINDOW,
    min_obs: int = MIN_OBS,
) -> BetaEstimate:
    """Beta of a fund's NAV return (net of FX) on its futures proxy's return.

    Parameters
    ----------
    nav : NAV per unit indexed by NAV date (python ``date``), already on one unit scale.
    futures_at_close : the future's price at the close of each underlying session,
        indexed by session date.
    underlying_session : maps a NAV date to the underlying session it embeds
        (``calendars.last_session_on_or_before(market, d)``).
    fx : rupees per unit of the fund's currency at NAV time, indexed by the same
        session dates (None = no currency term).
    cutoff, window, min_obs : see module constants. NAV dates ``>= cutoff`` are ignored.

    Observations are taken for the last ``window`` NAV dates before ``cutoff`` that
    have a previous NAV date. A pair is dropped (not filled) if any of the NAV, the
    future or the FX is missing for either end.
    """
    series = pd.Series(nav).dropna()
    series = series[(series > 0) & (pd.Index(series.index) < cutoff)].sort_index()
    if len(series) < 2:
        return _failed("fewer than 2 NAV dates before the cutoff")
    dates = list(series.index)
    sessions: list[date | None] = []
    for d in dates:
        try:
            sessions.append(underlying_session(d))
        except ValueError:  # before the calendar range
            sessions.append(None)

    def level(s: pd.Series | None, sess: date | None) -> float:
        if s is None or sess is None or sess not in s.index:
            return float("nan")
        v = float(s.loc[sess])
        return v if v > 0 else float("nan")

    ys: list[float] = []
    xs: list[float] = []
    used: list[date] = []
    for i in range(1, len(dates)):
        f0, f1 = level(futures_at_close, sessions[i - 1]), level(futures_at_close, sessions[i])
        n0, n1 = float(series.iloc[i - 1]), float(series.iloc[i])
        y = math.log(n1 / n0)
        if fx is not None:
            s0, s1 = level(fx, sessions[i - 1]), level(fx, sessions[i])
            if math.isnan(s0) or math.isnan(s1):
                continue
            y -= math.log(s1 / s0)
        if math.isnan(f0) or math.isnan(f1):
            continue
        xs.append(math.log(f1 / f0))
        ys.append(y)
        used.append(dates[i])
    xs, ys, used = xs[-window:], ys[-window:], used[-window:]
    if len(xs) < min_obs:
        return _failed(f"only {len(xs)} usable NAV dates; need at least {min_obs}", len(xs))
    try:
        slope, se, intercept, r2, n = ols_slope(np.array(xs), np.array(ys))
    except ValueError as exc:
        return _failed(str(exc), len(xs))
    return BetaEstimate(slope, se, intercept, r2, n, used[0], used[-1])


def beta_fair_value(
    nav_a: pd.Series,
    futures_t: pd.Series,
    futures_a: pd.Series,
    fx_t: pd.Series | None,
    fx_a: pd.Series | None,
    beta: float,
) -> pd.Series:
    """``NAV_a x (F_t / F_a) ** beta x (S_t / S_a)``; NaN where any input is missing.

    Hand example: NAV 100, F 20,000 -> 20,200, beta 0.5, no currency term:
    (1.01) ** 0.5 = 1.004988 -> 100.4988. With beta 1 and S 84.00 -> 84.42 it gives
    the SPEC 6.2 example 101.505.
    """
    if not math.isfinite(beta):
        return pd.Series(np.nan, index=nav_a.index)
    ratio = np.exp(beta * np.log(futures_t / futures_a))
    out = nav_a * ratio
    if fx_t is not None and fx_a is not None:
        out = out * (fx_t / fx_a)
    return out.astype("float64")
