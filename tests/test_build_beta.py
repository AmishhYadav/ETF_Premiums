"""Tests for features/beta.py (R12): hand-computed slope, synthetic NAV series, NaN paths."""

from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from etfp.features.beta import BETA_CUTOFF, beta_fair_value, fund_beta, ols_slope


def test_ols_slope_hand_example() -> None:
    # x-mean 0, y-mean 0.004, Sxx 0.001, Sxy 0.0015 -> slope 1.5; SSR 7e-5 -> SE 0.152753
    x = np.array([-0.02, -0.01, 0.0, 0.01, 0.02])
    y = np.array([-0.03, -0.01, 0.01, 0.02, 0.03])
    slope, se, intercept, r2, n = ols_slope(x, y)
    assert slope == pytest.approx(1.5)
    assert intercept == pytest.approx(0.004)
    assert se == pytest.approx(math.sqrt(7e-5 / 3 / 0.001))
    assert n == 5
    assert 0 < r2 < 1


def test_ols_slope_rejects_constant_x_and_tiny_samples() -> None:
    with pytest.raises(ValueError, match="does not vary"):
        ols_slope(np.zeros(5), np.arange(5.0))
    with pytest.raises(ValueError, match="at least 3"):
        ols_slope(np.array([1.0, 2.0]), np.array([1.0, 2.0]))


def _weekday_sessions(n: int, start: date = date(2025, 1, 6)) -> list[date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _synthetic(beta: float, n: int = 120, fx_drift: bool = True):
    """NAV that follows a future with exactly ``beta`` and a moving rupee. No noise."""
    days = _weekday_sessions(n)
    rng = np.random.default_rng(7)
    f_ret = rng.normal(0, 0.01, n)
    f_ret[10] = 0.0  # a "US holiday": the future does not move
    fx_ret = rng.normal(0, 0.002, n) if fx_drift else np.zeros(n)
    fut = pd.Series(20000 * np.exp(np.cumsum(f_ret)), index=days)
    fx = pd.Series(84 * np.exp(np.cumsum(fx_ret)), index=days)
    log_nav = np.cumsum(beta * f_ret + fx_ret)
    nav = pd.Series(100 * np.exp(log_nav), index=days)
    return nav, fut, fx


def test_fund_beta_recovers_known_beta_with_zero_error() -> None:
    nav, fut, fx = _synthetic(0.8)
    est = fund_beta(nav, fut, lambda d: d, fx)
    assert est.ok
    assert est.beta == pytest.approx(0.8, abs=1e-9)
    assert est.se == pytest.approx(0.0, abs=1e-9)
    assert est.n_obs == 119  # 120 NAV dates -> 119 returns


def test_fund_beta_net_of_fx_matters() -> None:
    # Without the FX term the rupee noise leaks into the slope, so beta is not 0.8.
    nav, fut, _ = _synthetic(0.8)
    est = fund_beta(nav, fut, lambda d: d, None)
    assert abs(est.beta - 0.8) > 1e-4


def test_fund_beta_uses_only_dates_before_cutoff_and_last_250() -> None:
    days = _weekday_sessions(400, start=date(2025, 1, 6))
    rng = np.random.default_rng(1)
    f_ret = rng.normal(0, 0.01, 400)
    fut = pd.Series(20000 * np.exp(np.cumsum(f_ret)), index=days)
    # beta 1.0 before the cutoff, beta 0.1 afterwards: the estimate must ignore the later part.
    beta_path = np.where(np.array([d < BETA_CUTOFF for d in days]), 1.0, 0.1)
    nav = pd.Series(100 * np.exp(np.cumsum(beta_path * f_ret)), index=days)
    est = fund_beta(nav, fut, lambda d: d)
    assert est.beta == pytest.approx(1.0, abs=1e-9)
    assert est.n_obs == 250
    assert est.last_nav_date is not None and est.last_nav_date < BETA_CUTOFF


def test_fund_beta_nan_when_too_few_or_no_future() -> None:
    nav, fut, fx = _synthetic(0.8, n=30)
    est = fund_beta(nav, fut, lambda d: d, fx)
    assert not est.ok and math.isnan(est.beta) and "need at least" in est.reason
    est2 = fund_beta(nav, pd.Series(dtype="float64"), lambda d: d, fx, min_obs=5)
    assert not est2.ok and math.isnan(est2.se)


def test_fund_beta_skips_pairs_with_missing_inputs_instead_of_filling() -> None:
    nav, fut, fx = _synthetic(0.5, n=120)
    fut = fut.drop(fut.index[40])  # one missing future level kills the two returns touching it
    est = fund_beta(nav, fut, lambda d: d, fx)
    assert est.ok and est.n_obs == 119 - 2
    assert est.beta == pytest.approx(0.5, abs=1e-9)


def test_beta_fair_value_hand_examples() -> None:
    nav = pd.Series([100.0, 100.0])
    ft, fa = pd.Series([20200.0, np.nan]), pd.Series([20000.0, 20000.0])
    # beta 1 with the rupee term reproduces SPEC 6.2: 100 x 1.01 x 1.005 = 101.505
    full = beta_fair_value(nav, ft, fa, pd.Series([84.42, 84.42]), pd.Series([84.0, 84.0]), 1.0)
    assert full.iloc[0] == pytest.approx(101.505)
    assert math.isnan(full.iloc[1])  # missing future -> NaN, not NAV
    half = beta_fair_value(nav, ft, fa, None, None, 0.5)
    assert half.iloc[0] == pytest.approx(100 * math.sqrt(1.01))
    assert beta_fair_value(nav, ft, fa, None, None, float("nan")).isna().all()
