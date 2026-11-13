"""Tests for fair value (D10): FV = NAV_a x F_t/F_a x S_t/S_a. Hand examples in comments."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from etfp.config import load_etfs
from etfp.features import fair_value as fvm


def test_spec_example_101_505() -> None:
    # NAV 100; futures 20,000 -> 20,200 is x1.01; rupee 84.00 -> 84.42 is x1.005.
    # FV = 100 x 1.01 x 1.005 = 101.505.
    r = fvm.fair_value(100, futures_t=20200, futures_a=20000, fx_t=84.42, fx_a=84.00)
    assert r["fv"] == pytest.approx(101.505, abs=1e-9)
    assert r["fv_method"] == "free_futures"
    assert r["fv_futures_ratio"] == pytest.approx(1.01)
    assert r["fv_fx_ratio"] == pytest.approx(1.005)
    # Inputs are echoed for traceability.
    assert (r["fv_in_nav_a"], r["fv_in_futures_t"], r["fv_in_futures_a"]) == (100, 20200, 20000)
    assert (r["fv_in_fx_t"], r["fv_in_fx_a"]) == (84.42, 84.0)


def test_bloomberg_label() -> None:
    r = fvm.fair_value(
        100, futures_t=20200, futures_a=20000, fx_t=84.42, fx_a=84.0, source="bloomberg"
    )
    assert r["fv_method"] == "bloomberg"


def test_futures_down_and_rupee_stronger() -> None:
    # NAV 200; futures 19,000 -> 18,810 is x0.99; rupee 85.00 -> 84.15 is x0.99.
    # FV = 200 x 0.99 x 0.99 = 196.02.
    r = fvm.fair_value(200, futures_t=18810, futures_a=19000, fx_t=84.15, fx_a=85.0)
    assert r["fv"] == pytest.approx(196.02)


def test_missing_futures_falls_back_to_nav_only() -> None:
    r = fvm.fair_value(100, futures_t=None, futures_a=20000, fx_t=84.42, fx_a=84.0)
    assert r["fv"] == 100 and r["fv_method"] == "nav_only"
    assert r["fv_futures_ratio"] is None and r["fv_fx_ratio"] is None
    # Missing FX also falls back: a partial adjustment would be a different (unlabelled) measure.
    r = fvm.fair_value(100, futures_t=20200, futures_a=20000, fx_t=None, fx_a=84.0)
    assert r["fv"] == 100 and r["fv_method"] == "nav_only"


def test_hong_kong_etf_uses_fx_only() -> None:
    # NAV 50; HKD/INR 10.80 -> 10.908 is x1.01. No futures term: FV = 50 x 1.01 = 50.5.
    r = fvm.fair_value(50, fx_t=10.908, fx_a=10.80, use_futures=False)
    assert r["fv"] == pytest.approx(50.5) and r["fv_method"] == "free_futures"
    assert r["fv_futures_ratio"] is None
    # Futures values that are present are ignored for HK.
    r = fvm.fair_value(50, futures_t=1, futures_a=2, fx_t=10.908, fx_a=10.80, use_futures=False)
    assert r["fv"] == pytest.approx(50.5)
    # FX missing: nav_only.
    r = fvm.fair_value(50, use_futures=False)
    assert r["fv"] == 50 and r["fv_method"] == "nav_only"


def test_domestic_etf_needs_nothing() -> None:
    r = fvm.fair_value(273.0, use_futures=False, use_fx=False)
    assert r["fv"] == 273.0 and r["fv_method"] == "nav_only"


def test_missing_nav_gives_nan_and_no_nav_label() -> None:
    r = fvm.fair_value(None, futures_t=20200, futures_a=20000, fx_t=84.42, fx_a=84.0)
    assert r["fv"] is None and r["fv_method"] == "no_nav"


def test_invalid_inputs_raise() -> None:
    with pytest.raises(ValueError, match="positive"):
        fvm.fair_value(100, futures_t=0, futures_a=20000, fx_t=84.42, fx_a=84.0)
    with pytest.raises(ValueError, match="positive"):
        fvm.fair_value(100, futures_t=20200, futures_a=-1, fx_t=84.42, fx_a=84.0)
    with pytest.raises(ValueError, match="positive"):
        fvm.fair_value(100, futures_t=math.inf, futures_a=2, fx_t=84.42, fx_a=84.0)
    with pytest.raises(ValueError, match="source"):
        fvm.fair_value(100, futures_t=1, futures_a=1, fx_t=1, fx_a=1, source="reuters")


def test_frame_mixed_rows_and_sources() -> None:
    df = pd.DataFrame(
        {
            "nav_known_at_close": [100.0, 100.0, np.nan, 100.0],
            "futures_t": [20200.0, np.nan, 20200.0, 20200.0],
            "futures_a": [20000.0, 20000.0, 20000.0, 20000.0],
            "fx_t": [84.42, 84.42, 84.42, 84.42],
            "fx_a": [84.0, 84.0, 84.0, 84.0],
        },
        index=[10, 11, 12, 13],
    )
    src = pd.Series(["bloomberg", "bloomberg", "free", "free"], index=df.index)
    out = fvm.fair_value_frame(df, source=src)
    assert list(out.index) == [10, 11, 12, 13]
    assert out["fv"].iloc[0] == pytest.approx(101.505)
    assert out["fv"].iloc[1] == 100.0
    assert np.isnan(out["fv"].iloc[2])
    assert out["fv"].iloc[3] == pytest.approx(101.505)
    assert out["fv_method"].tolist() == ["bloomberg", "nav_only", "no_nav", "free_futures"]


def test_frame_requires_columns_for_active_terms() -> None:
    df = pd.DataFrame({"nav_known_at_close": [100.0], "fx_t": [84.4], "fx_a": [84.0]})
    with pytest.raises(ValueError, match="futures_t"):
        fvm.fair_value_frame(df)  # US-style ETF but no futures columns
    out = fvm.fair_value_frame(df, use_futures=False)  # HK-style works without them
    assert out["fv"].iloc[0] == pytest.approx(100.0 * 84.4 / 84.0)


def test_terms_for_universe() -> None:
    uni = load_etfs()
    assert fvm.fv_terms_for(uni.by_symbol("MONQ50")) == (True, True)  # NQ future + USD
    assert fvm.fv_terms_for(uni.by_symbol("MAHKTECH")) == (False, True)  # HKD only
    assert fvm.fv_terms_for(uni.by_symbol("NIFTYBEES")) == (False, False)  # domestic
