"""Tests for premium measures and derived columns. Hand-computed numbers in comments."""

from __future__ import annotations

import math
from datetime import date

import numpy as np
import pandas as pd
import pytest

from etfp.features import premiums as p


def S(*values: float) -> pd.Series:
    return pd.Series(list(values), dtype="float64")


def test_ratio_minus_one_hand_examples() -> None:
    # 120 / 100 - 1 = 0.20 ; 80 / 100 - 1 = -0.20
    out = p.ratio_minus_one(S(120, 80), S(100, 100))
    assert out.tolist() == pytest.approx([0.20, -0.20])


def test_ratio_minus_one_guards() -> None:
    out = p.ratio_minus_one(S(120, 120, np.nan, 120, -5), S(0, np.nan, 100, -1, 100))
    assert out.isna().all()  # zero / NaN / negative never produce a number or an inf


def test_log_ratio_hand_examples() -> None:
    # ln(120 / 100) = ln 1.2 = 0.1823215568 ; ln(100 / 120) = -0.1823215568
    out = p.log_ratio(S(120, 100, 0, 5), S(100, 120, 100, np.nan))
    assert out.iloc[0] == pytest.approx(0.1823215568, abs=1e-9)
    assert out.iloc[1] == pytest.approx(-0.1823215568, abs=1e-9)
    assert out.iloc[2:].isna().all()


def test_monq50_4_sep_2026_raw_premium() -> None:
    # SPEC M3 check: close 141.93, NAV dated 4 Sep 118.7371.
    # 141.93 / 118.7371 = 1.19532985... -> 19.53% (Value Research reported 19.5%).
    df = pd.DataFrame(
        {
            "close": [141.93],
            "nav_same_date": [118.7371],
            "nav_known_at_close": [117.0],
            "nav_t2": [118.2753],
            "fv": [130.0],
        }
    )
    out = p.premium_columns(df)
    assert out["prem_raw"].iloc[0] == pytest.approx(0.19532985, abs=1e-8)
    # NAV known at the close (made-up 117.0): 141.93 / 117 - 1 = 0.21307692.
    assert out["prem_nav_known"].iloc[0] == pytest.approx(141.93 / 117.0 - 1, abs=1e-12)
    assert out["prem_nav_known"].iloc[0] == pytest.approx(0.2130769231, abs=1e-9)
    # Band reference: NAV of T-2 = 118.2753: 141.93 / 118.2753 - 1 = 0.2000 (the old band limit,
    # since 141.93 = floor(1.2 x 118.2753)).
    assert out["prem_band_ref"].iloc[0] == pytest.approx(0.1999969, abs=1e-6)
    # FV 130: 141.93 / 130 - 1 = 0.0917692308 ; ln(141.93 / 130) = 0.0877995279.
    assert out["prem_fv"].iloc[0] == pytest.approx(0.0917692308, abs=1e-9)
    assert out["log_prem_fv"].iloc[0] == pytest.approx(0.0877995279, abs=1e-9)
    # x_april_fv = ln(141.93 / (1.2 x 130)) = ln(141.93 / 156) = -0.0945220289.
    assert out["x_april_fv"].iloc[0] == pytest.approx(-0.0945220289, abs=1e-9)
    # x_april on the known NAV (D30): ln(141.93 / (1.2 x 117)) = ln(141.93 / 140.4)
    # = ln(1.0108974359) = 0.0108384867.
    assert out["x_april"].iloc[0] == pytest.approx(0.0108384867, abs=1e-9)
    assert "x_april_nav_known" not in out.columns


def test_x_april_sign() -> None:
    # Price exactly 1.2 x NAV known -> x_april = 0 ; above -> positive ; below -> negative.
    df = pd.DataFrame(
        {
            "close": [120.0, 130.0, 100.0],
            "nav_same_date": [100.0] * 3,
            "nav_known_at_close": [100.0] * 3,
            "nav_t2": [100.0] * 3,
            "fv": [100.0] * 3,
        }
    )
    x = p.premium_columns(df)["x_april"]
    assert x.iloc[0] == pytest.approx(0.0, abs=1e-12)
    assert x.iloc[1] == pytest.approx(math.log(130 / 120))
    assert x.iloc[1] > 0 > x.iloc[2]


def test_premium_columns_missing_inputs_are_nan_not_zero() -> None:
    df = pd.DataFrame(
        {
            "close": [100.0, np.nan],
            "nav_same_date": [np.nan, 100.0],
            "nav_known_at_close": [100.0, 100.0],
            "nav_t2": [np.nan, np.nan],
            "fv": [np.nan, np.nan],
        }
    )
    out = p.premium_columns(df)
    assert out["prem_raw"].isna().all()
    assert out["prem_nav_known"].tolist()[0] == pytest.approx(0.0)
    assert np.isnan(out["prem_nav_known"].iloc[1])
    assert out[["prem_band_ref", "prem_fv", "log_prem_fv", "x_april_fv"]].isna().all().all()
    # x_april uses the known NAV (present): row 0 = ln(100 / 120); row 1 has no close -> NaN.
    assert out["x_april"].iloc[0] == pytest.approx(math.log(100 / 120))
    assert np.isnan(out["x_april"].iloc[1])


def test_inav_premium() -> None:
    # last price 126, iNAV 100 -> 0.26 ; missing iNAV -> NaN
    out = p.inav_premium(S(126, 126), S(100, np.nan))
    assert out.iloc[0] == pytest.approx(0.26) and np.isnan(out.iloc[1])
    assert out.name == "prem_inav"


def test_turnover_and_trade_size() -> None:
    # 1,000,000 units traded of 14,675,018 outstanding = 0.0681430169
    t = p.turnover_units(S(1_000_000, 5, 5, -1), S(14_675_018, 0, np.nan, 10))
    assert t.iloc[0] == pytest.approx(0.0681430169, abs=1e-9)
    assert t.iloc[1:].isna().all()
    # Rs 5 crore (50,000,000) over Rs 250 crore (2,500,000,000) AUM = 0.02
    assert p.turnover_value(S(50_000_000), S(2_500_000_000)).iloc[0] == pytest.approx(0.02)
    # Rs 12,000,000 over 400 trades = Rs 30,000 ; zero trades -> NaN (no division by zero)
    a = p.avg_trade_value(S(12_000_000, 5.0), S(400, 0))
    assert a.iloc[0] == 30_000 and np.isnan(a.iloc[1])


def test_log_returns_hand_example() -> None:
    # 100 -> 110 is ln(1.1) = 0.0953101798 ; 110 -> 99 is ln(0.9) = -0.1053605157
    r = p.log_returns(S(100, 110, 99))
    assert np.isnan(r.iloc[0])
    assert r.iloc[1] == pytest.approx(0.0953101798, abs=1e-9)
    assert r.iloc[2] == pytest.approx(-0.1053605157, abs=1e-9)
    # Two-period: ln(99/100) = -0.0100503359
    assert p.log_returns(S(100, 110, 99), 2).iloc[2] == pytest.approx(-0.0100503359, abs=1e-9)
    with pytest.raises(ValueError):
        p.log_returns(S(1, 2), 0)


def test_add_return_columns_per_symbol_and_lagged_version() -> None:
    d = [date(2026, 9, i) for i in (1, 2, 3, 4, 7, 8)]
    closes_a = [100.0, 101.0, 103.0, 102.0, 105.0, 106.0]
    df = pd.DataFrame(
        {
            "symbol": ["A"] * 6 + ["B"] * 2,
            "date": d + d[:2],
            "close": [*closes_a, 50.0, 55.0],
            "nav_known_at_close": [1.0] * 8,
            "fv": [1.0] * 8,
        }
    )
    # Shuffle the rows: the function must order by (symbol, date) itself and keep the index.
    df = df.sample(frac=1, random_state=3)
    out = p.add_return_columns(df)
    assert list(out.index) == list(df.index)
    a = out[out.symbol == "A"].sort_values("date")
    # ret_close_1 on 3 Sep = ln(103/101) ; ret_own_1 on 3 Sep = return up to the PREVIOUS session
    # = ln(101/100) (known before 3 Sep).
    assert a["ret_close_1"].iloc[2] == pytest.approx(math.log(103 / 101))
    assert a["ret_own_1"].iloc[2] == pytest.approx(math.log(101 / 100))
    # ret_close_5 on 8 Sep (6th row) = ln(106/100) ; ret_own_5 on 8 Sep = ln(105/?) -> NaN: only
    # 5 prior rows exist for a window ending 7 Sep that starts before the first row.
    assert a["ret_close_5"].iloc[5] == pytest.approx(math.log(106 / 100))
    assert np.isnan(a["ret_own_5"].iloc[5])
    assert np.isnan(a["ret_close_1"].iloc[0])
    # Symbol B never borrows from A.
    bb = out[out.symbol == "B"].sort_values("date")
    assert np.isnan(bb["ret_close_1"].iloc[0])
    assert bb["ret_close_1"].iloc[1] == pytest.approx(math.log(55 / 50))
    assert (a["ret_nav_1"].dropna() == 0).all()


def test_add_return_columns_rejects_duplicates() -> None:
    df = pd.DataFrame(
        {
            "symbol": ["A", "A"],
            "date": [date(2026, 9, 1)] * 2,
            "close": [1.0, 2.0],
            "nav_known_at_close": [1.0, 1.0],
            "fv": [1.0, 1.0],
        }
    )
    with pytest.raises(ValueError, match="duplicate"):
        p.add_return_columns(df)
