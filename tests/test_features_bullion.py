"""Bullion comparison features. Every expected number is worked out by hand in a comment."""

from __future__ import annotations

import math
from datetime import date

import numpy as np
import pandas as pd
import pytest

from etfp.features.bullion import (
    bullion_monthly_summary,
    bullion_premium_frame,
    implied_metal_per_unit,
    metal_per_unit_stability,
)


def s(values: dict) -> pd.Series:
    return pd.Series(list(values.values()), index=list(values.keys()), dtype="float64")


# -- grams per unit ---------------------------------------------------------------------


def test_implied_metal_per_unit_hand_example():
    d1, d2, d3 = date(2026, 11, 11), date(2026, 11, 12), date(2026, 11, 13)
    nav = s({d1: 81.96, d2: 100.0, d3: 50.0})
    px = s({d1: 10_000.0, d2: 12_500.0})  # no price on d3 -> d3 dropped
    out = implied_metal_per_unit(nav, px)
    assert list(out["date"]) == [d1, d2]
    # 81.96 / 10,000 = 0.008196 g ; 100 / 12,500 = 0.008 g
    assert out["grams_per_unit"].tolist() == pytest.approx([0.008196, 0.008])


def test_implied_metal_rejects_bad_prices_and_duplicate_dates():
    d = date(2026, 11, 13)
    with pytest.raises(ValueError, match="positive"):
        implied_metal_per_unit(s({d: 1.0}), s({d: 0.0}))
    dup = pd.Series([1.0, 2.0], index=[d, d])
    with pytest.raises(ValueError, match="more than once"):
        implied_metal_per_unit(dup, s({d: 1.0}))


def test_stability_hand_example():
    g = s({date(2026, 1, 1): 0.0100, date(2026, 1, 2): 0.0101, date(2026, 1, 3): 0.0099})
    st = metal_per_unit_stability(g)
    # mean 0.01; deviations +0, +0.0001, -0.0001 -> sample var = 2e-8 / 2 = 1e-8, std 1e-4
    assert st["n"] == 3
    assert st["mean"] == pytest.approx(0.01)
    assert st["std"] == pytest.approx(1e-4)
    assert st["cv"] == pytest.approx(0.01)
    assert st["min"] == pytest.approx(0.0099) and st["max"] == pytest.approx(0.0101)
    assert st["max_abs_dev"] == pytest.approx(0.01)  # |0.0101 / 0.01 - 1|


def test_stability_drift_equals_fee_erosion():
    # Metal per unit shrinking by a 0.5%/year fee: g(t) = exp(-0.005 * years).
    d0, d1 = date(2025, 1, 1), date(2026, 1, 1)  # 365 days = 365/365.25 years
    years = 365 / 365.25
    g = s({d0: 1.0, d1: math.exp(-0.005 * years)})
    assert metal_per_unit_stability(g)["drift_per_year"] == pytest.approx(-0.005)


def test_stability_with_one_or_no_value():
    one = metal_per_unit_stability(s({date(2026, 1, 1): 0.01}))
    assert one["n"] == 1 and math.isnan(one["std"]) and math.isnan(one["drift_per_year"])
    assert one["max_abs_dev"] == 0
    none = metal_per_unit_stability(pd.Series([], dtype="float64"))
    assert none["n"] == 0 and math.isnan(none["mean"])


# -- daily premium frame --------------------------------------------------------------------


def test_premium_frame_hand_example():
    d1, d2, d3 = date(2026, 11, 11), date(2026, 11, 12), date(2026, 11, 13)
    day = pd.DataFrame(
        {"date": [d2, d1, d3], "close": [103.0, 100.5, 104.0], "nav": [101.5, 100.0, np.nan]}
    )
    px = s({d1: 10_000.0, d2: 10_100.0, d3: 10_300.0})
    out = bullion_premium_frame(day, px).set_index("date")
    # d1: grams = 100 / 10,000 = 0.01; no earlier grams -> metal value NaN
    assert out.loc[d1, "grams_per_unit"] == pytest.approx(0.01)
    assert math.isnan(out.loc[d1, "metal_value"])
    assert out.loc[d1, "prem_nav"] == pytest.approx(0.005)  # 100.5 / 100 - 1
    # d2: metal value = 0.01 (yesterday's grams) x 10,100 = 101
    assert out.loc[d2, "metal_value"] == pytest.approx(101.0)
    assert out.loc[d2, "prem_metal"] == pytest.approx(103 / 101 - 1)  # 0.019802
    assert out.loc[d2, "nav_check"] == pytest.approx(101.5 / 101 - 1)  # 0.004950
    assert out.loc[d2, "prem_nav"] == pytest.approx(103 / 101.5 - 1)  # 0.014778
    # d3: NAV missing. Latest earlier grams = 101.5 / 10,100 = 0.0100495...
    g2 = 101.5 / 10_100
    assert out.loc[d3, "grams_per_unit_prev"] == pytest.approx(g2)
    assert out.loc[d3, "metal_value"] == pytest.approx(g2 * 10_300)  # 103.5099
    assert out.loc[d3, "prem_metal"] == pytest.approx(104 / (g2 * 10_300) - 1)
    assert math.isnan(out.loc[d3, "prem_nav"]) and math.isnan(out.loc[d3, "nav_check"])


def test_premium_frame_carries_grams_over_a_missing_nav_day():
    d1, d2, d3 = date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7)
    day = pd.DataFrame({"date": [d1, d2, d3], "close": [1.0, 1.0, 1.0],
                        "nav": [100.0, np.nan, 99.0]})  # fmt: skip
    px = s({d1: 10_000.0, d2: 10_000.0, d3: 9_000.0})
    out = bullion_premium_frame(day, px).set_index("date")
    assert out.loc[d3, "grams_per_unit_prev"] == pytest.approx(0.01)  # from d1, not d2
    assert out.loc[d3, "nav_check"] == pytest.approx(99 / 90 - 1)  # 0.1


def test_premium_frame_validates_input():
    with pytest.raises(ValueError, match="missing column"):
        bullion_premium_frame(pd.DataFrame({"date": [], "close": []}), pd.Series(dtype=float))
    d = date(2026, 1, 5)
    dup = pd.DataFrame({"date": [d, d], "close": [1.0, 1.0], "nav": [1.0, 1.0]})
    with pytest.raises(ValueError, match="repeats"):
        bullion_premium_frame(dup, s({d: 1.0}))


# -- monthly summary -----------------------------------------------------------------------


def test_monthly_summary_hand_example():
    frame = pd.DataFrame(
        {
            "date": [date(2025, 9, 29), date(2025, 9, 30), date(2025, 10, 8),
                     date(2025, 10, 9), date(2025, 10, 10)],
            "prem_nav": [0.002, -0.004, 0.0296, 0.0302, np.nan],
            "units": [1.0e9, 1.04e9, 1.10e9, np.nan, 1.20e9],
        }
    )  # fmt: skip
    out = bullion_monthly_summary(frame, threshold=0.01).set_index("month")
    sep, oct_ = out.loc[date(2025, 9, 1)], out.loc[date(2025, 10, 1)]
    assert sep["n_days"] == 2 and sep["prem_mean"] == pytest.approx(-0.001)
    assert sep["share_abs_gt"] == 0  # neither |0.2%| nor |0.4%| exceeds 1%
    assert sep["units_last"] == 1.04e9 and math.isnan(sep["units_growth"])
    assert oct_["n_days"] == 2  # the NaN premium day is not counted
    assert oct_["prem_max"] == pytest.approx(0.0302) and oct_["prem_min"] == pytest.approx(0.0296)
    assert oct_["share_abs_gt"] == 1.0
    # units 1.04e9 -> 1.20e9: 1.20 / 1.04 - 1 = 0.153846
    assert oct_["units_last"] == 1.20e9
    assert oct_["units_growth"] == pytest.approx(1.20 / 1.04 - 1)


def test_monthly_summary_without_units_and_bad_threshold():
    frame = pd.DataFrame({"date": [date(2026, 1, 5)], "prem_nav": [0.0]})
    out = bullion_monthly_summary(frame)
    assert math.isnan(out.loc[0, "units_last"]) and math.isnan(out.loc[0, "units_growth"])
    with pytest.raises(ValueError, match="threshold"):
        bullion_monthly_summary(frame, threshold=float("nan"))
    with pytest.raises(ValueError, match="needs"):
        bullion_monthly_summary(frame, premium_col="prem_metal")
