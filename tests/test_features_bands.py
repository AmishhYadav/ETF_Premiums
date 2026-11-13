"""Tests for the price-band calculator. Every expected number is worked out by hand in a comment."""

from __future__ import annotations

import math
from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from etfp.collect import nse_secmaster as sm
from etfp.config import BandRules, load_rules
from etfp.features import bands as b

FIXTURES = Path(__file__).parent / "fixtures" / "nse_secmaster"


@pytest.fixture(scope="module")
def rules() -> BandRules:
    return load_rules()


# --------------------------------------------------------------------------
# Rounding: exact decimal arithmetic, low up / high down (D5)
# --------------------------------------------------------------------------
def test_band_limits_d5_example_monq50_9_nov() -> None:
    # base 229.67, +/-10%:
    #   low  = 229.67 x 90 / 100 = 206.703  -> round UP to the tick   -> 206.71
    #   high = 229.67 x 110 / 100 = 252.637 -> round DOWN to the tick -> 252.63
    low, high = b.band_limits(229.67, 10, tick=0.01, low_mode="up", high_mode="down")
    assert (low, high) == (Decimal("206.71"), Decimal("252.63"))


def test_band_limits_exact_where_float_arithmetic_would_round_wrong() -> None:
    # base 0.10 at -10%: exactly 0.09. In floats 0.10 x 0.9 = 0.09000000000000001, and
    # rounding UP would wrongly give 0.10. Decimal arithmetic gives exactly 0.09.
    low, _ = b.band_limits(0.10, 10, tick=0.01, low_mode="up", high_mode="down")
    assert low == Decimal("0.09")
    # base 2.30 at +10%: exactly 2.53. In floats 2.3 x 1.1 = 2.5300000000000002 but
    # 2.30 x 110 / 100 in binary can land under 2.53 and floor to 2.52.
    _, high = b.band_limits(2.30, 10, tick=0.01, low_mode="up", high_mode="down")
    assert high == Decimal("2.53")


def test_snap_to_tick_modes() -> None:
    v, t = Decimal("10.036"), Decimal("0.01")
    assert b.snap_to_tick(v, t, "up") == Decimal("10.04")
    assert b.snap_to_tick(v, t, "down") == Decimal("10.03")
    assert b.snap_to_tick(v, t, "nearest") == Decimal("10.04")
    assert b.snap_to_tick(Decimal("10.03"), t, "up") == Decimal("10.03")  # already on a tick
    # A different tick (0.05): 10.03 up -> 10.05, down -> 10.00.
    assert b.snap_to_tick(Decimal("10.03"), Decimal("0.05"), "up") == Decimal("10.05")
    assert b.snap_to_tick(Decimal("10.03"), Decimal("0.05"), "down") == Decimal("10.00")


def test_band_limits_low_never_negative() -> None:
    low, _ = b.band_limits(1.0, 150, tick=0.01, low_mode="up", high_mode="down")
    assert low == Decimal(0)


# --------------------------------------------------------------------------
# compute_band: regimes from config/rules.yaml
# --------------------------------------------------------------------------
def test_prev_close_equity_9_nov_2026(rules: BandRules) -> None:
    band = b.compute_band(date(2026, 11, 11), "equity", rules, prev_close=229.67)
    assert band.regime == "prev_close" and band.base_kind == "prev_close"
    # Official file dated 9 Nov (10 Nov was a holiday, so it applies to 11 Nov): 206.71-252.63
    # (DECISIONS D4).
    assert (band.low_initial, band.high_initial) == (206.71, 252.63)
    # Maximum +/-20%: low 229.67 x 0.8 = 183.736 up -> 183.74;
    # high 229.67 x 1.2 = 275.604 down -> 275.60.
    assert (band.low_max, band.high_max) == (183.74, 275.60)
    assert not band.uncapped and band.max_widenings == 2


def test_prev_close_first_day_4_sep_file_127_74_to_156_12(rules: BandRules) -> None:
    # File dated 4 Sep 2026: base (the 4 Sep close) 141.93.
    #   low  = 141.93 x 0.9 = 127.737 -> up   127.74
    #   high = 141.93 x 1.1 = 156.123 -> down 156.12
    # midpoint (127.74 + 156.12) / 2 = 141.93 (the close), ratio 156.12 / 127.74 = 1.2222.
    band = b.compute_band(date(2026, 9, 7), "equity", rules, prev_close=141.93)
    assert (band.low_initial, band.high_initial) == (127.74, 156.12)
    assert (band.low_initial + band.high_initial) / 2 == pytest.approx(141.93)
    assert band.high_initial / band.low_initial == pytest.approx(1.1 / 0.9, rel=1e-3)


@pytest.mark.parametrize(
    ("nav_t2", "expected_high"),
    [
        (118.2753, 141.93),  # MONQ50: x1.2 = 141.93036 -> 141.93
        (270.5591, 324.67),  # MON100: 324.67092 -> 324.67
        (66.5513, 79.86),  # MASPTOP50: 79.86156 -> 79.86
        (172.9741, 207.56),  # MAFANG: 207.56892 -> floor 207.56 (not 207.57)
        (18.4215, 22.10),  # MAHKTECH: 22.1058 -> 22.10
        (455.4774, 546.57),  # HNGSNGBEES: 546.57288 -> 546.57
    ],
)
def test_nav_t2_limit_matches_paper_table_5(
    rules: BandRules, nav_t2: float, expected_high: float
) -> None:
    band = b.compute_band(date(2026, 9, 4), "equity", rules, nav_t2=nav_t2)
    assert band.regime == "nav_t2"
    assert band.high_initial == expected_high
    assert band.high_max == band.high_initial  # fixed band: no widening


def test_nav_t2_low_limit_rounds_up(rules: BandRules) -> None:
    # 118.2753 x 0.8 = 94.62024 -> up to 94.63.
    band = b.compute_band(date(2026, 9, 4), "equity", rules, nav_t2=118.2753)
    assert band.low_initial == 94.63 and band.low_max == 94.63


def test_gold_has_no_maximum(rules: BandRules) -> None:
    # Gold, base 100: +/-6% -> 94.00-106.00; widening steps of 3% have no cap.
    band = b.compute_band(date(2026, 9, 7), "gold", rules, prev_close=100.0)
    assert (band.low_initial, band.high_initial) == (94.0, 106.0)
    assert band.uncapped and math.isinf(band.high_max) and band.low_max == 0.0
    # Level 2 would be 12%: 88.00 / 112.00.
    spec = rules.regime_for(date(2026, 9, 7)).band_spec("gold")
    lo, hi = b.limits_at_level(100.0, spec, 2, rules)
    assert (lo, hi) == (Decimal("88.00"), Decimal("112.00"))


def test_nav_t1_regime_uses_nav_t1(rules: BandRules) -> None:
    band = b.compute_band(date(2027, 4, 1), "equity", rules, nav_t1=200.0, prev_close=999.0)
    # Base 200: 180.00-220.00, maximum 160.00-240.00. The prev_close input is ignored.
    assert band.base_kind == "nav_t1" and band.base_price == 200.0
    assert (band.low_initial, band.high_initial) == (180.0, 220.0)
    assert (band.low_max, band.high_max) == (160.0, 240.0)


def test_missing_or_invalid_base_and_gap_dates(rules: BandRules) -> None:
    with pytest.raises(b.MissingBaseError):
        b.compute_band(date(2026, 9, 7), "equity", rules, nav_t2=100.0)  # needs prev_close
    with pytest.raises(b.MissingBaseError):
        b.compute_band(date(2026, 9, 7), "equity", rules, prev_close=float("nan"))
    with pytest.raises(ValueError, match="positive"):
        b.compute_band(date(2026, 9, 7), "equity", rules, prev_close=0.0)
    with pytest.raises(ValueError, match="no price-band regime"):
        b.compute_band(date(2026, 9, 5), "equity", rules, prev_close=100.0)  # Saturday


# --------------------------------------------------------------------------
# Flags (D6 / R10)
# --------------------------------------------------------------------------
def equity_band(rules: BandRules) -> b.Band:
    # base 100: initial 90.00-110.00, +5% step -> 85.00-115.00, maximum 80.00-120.00.
    return b.compute_band(date(2026, 9, 8), "equity", rules, prev_close=100.0)


def test_flags_high_inside_initial_band(rules: BandRules) -> None:
    f = b.band_flags(equity_band(rules), rules, high=105.0, low=98.0, last=104.0)
    assert f["high_touched_initial_upper"] is False
    assert f["widened_up"] is False and f["widenings_min_up"] == 0
    assert f["ltp_at_upper"] is False  # 104 is not within 0.1% of 110
    assert f["low_touched_initial_lower"] is False and f["widened_down"] is False


def test_flags_touch_initial_limit_exactly(rules: BandRules) -> None:
    f = b.band_flags(equity_band(rules), rules, high=110.0, low=99.0, last=110.0, close=109.5)
    assert f["high_touched_initial_upper"] is True
    assert f["widened_up"] is False  # 110.00 is not above 110.00
    assert f["high_touched_max_upper"] is False
    assert f["ltp_at_upper"] is True
    assert f["close_at_upper"] is False  # 109.5 is 0.45% below the limit, tolerance is 0.1%


def test_flags_touch_within_one_tick_counts(rules: BandRules) -> None:
    # high 109.99 is one tick under the 110.00 limit: touched. 109.98 is two ticks: not.
    f1 = b.band_flags(equity_band(rules), rules, high=109.99, low=99.0, last=100.0)
    f2 = b.band_flags(equity_band(rules), rules, high=109.98, low=99.0, last=100.0)
    assert f1["high_touched_initial_upper"] is True
    assert f2["high_touched_initial_upper"] is False


def test_flags_widened_once_worked_example(rules: BandRules) -> None:
    # high 112.00 > 110.00: the band must have widened. Level 1 limit is 115.00 >= 112.
    f = b.band_flags(equity_band(rules), rules, high=112.0, low=99.0, last=111.9)
    assert f["high_touched_initial_upper"] is True
    assert f["high_touched_max_upper"] is False
    assert f["widened_up"] is True and f["widenings_min_up"] == 1
    # The limit in force at the end was at least 115.00; 111.90 is 2.7% below it.
    assert f["ltp_at_upper"] is False
    # Same day, last trade 114.95: within 0.1% of 115.00 (tolerance 0.115) -> at the limit.
    f = b.band_flags(equity_band(rules), rules, high=115.0, low=99.0, last=114.95)
    assert f["widenings_min_up"] == 1 and f["ltp_at_upper"] is True


def test_flags_reached_maximum(rules: BandRules) -> None:
    # high 120.00 is the maximum; needs both widenings (115.00 < 120.00).
    f = b.band_flags(equity_band(rules), rules, high=120.0, low=99.0, last=119.95)
    assert f["high_touched_max_upper"] is True
    assert f["widenings_min_up"] == 2 and f["widened_up"] is True
    assert f["ltp_at_upper"] is True  # |119.95 - 120| = 0.05 <= 0.12


def test_flags_lower_side_mirrors_upper(rules: BandRules) -> None:
    f = b.band_flags(equity_band(rules), rules, high=101.0, low=80.0, last=80.05)
    assert f["low_touched_initial_lower"] is True and f["low_touched_max_lower"] is True
    assert f["widened_down"] is True and f["widenings_min_down"] == 2
    assert f["ltp_at_lower"] is True
    f = b.band_flags(equity_band(rules), rules, high=101.0, low=86.0, last=90.0)
    assert f["widened_down"] is True and f["widenings_min_down"] == 1  # 86 < 90; level-1 low 85
    assert f["low_touched_max_lower"] is False


def test_flags_fixed_band_initial_equals_max(rules: BandRules) -> None:
    band = b.compute_band(date(2026, 9, 4), "equity", rules, nav_t2=118.2753)  # high 141.93
    f = b.band_flags(band, rules, high=141.93, low=130.0, last=141.93)
    assert f["high_touched_initial_upper"] is True and f["high_touched_max_upper"] is True
    assert f["widened_up"] is False and f["widenings_min_up"] == 0
    assert f["ltp_at_upper"] is True


def test_flags_gold_never_touches_a_maximum(rules: BandRules) -> None:
    band = b.compute_band(date(2026, 9, 8), "gold", rules, prev_close=100.0)  # 94-106, steps of 3
    f = b.band_flags(band, rules, high=106.5, low=95.0, last=106.0)
    assert f["high_touched_max_upper"] is False and f["low_touched_max_lower"] is False
    assert f["widened_up"] is True and f["widenings_min_up"] == 1  # level-1 high is 109.00
    f = b.band_flags(band, rules, high=112.0, low=95.0, last=111.95)
    assert f["widenings_min_up"] == 2 and f["ltp_at_upper"] is True  # level-2 high 112.00


def test_flags_missing_inputs_give_none(rules: BandRules) -> None:
    f = b.band_flags(equity_band(rules), rules, high=None, low=99.0, last=None)
    assert f["high_touched_initial_upper"] is None and f["widened_up"] is None
    assert f["ltp_at_upper"] is None and f["ltp_at_lower"] is None
    assert f["low_touched_initial_lower"] is False


def test_flags_tolerance_is_configurable(rules: BandRules) -> None:
    band = equity_band(rules)
    # last 114.80 vs level-1 limit 115.00: off by 0.20. 0.1% of 115 = 0.115 -> not at limit;
    # 0.2% = 0.23 -> at limit.
    assert b.band_flags(band, rules, high=115.0, low=99.0, last=114.80)["ltp_at_upper"] is False
    assert (
        b.band_flags(band, rules, high=115.0, low=99.0, last=114.80, at_tolerance=0.002)[
            "ltp_at_upper"
        ]
        is True
    )


# --------------------------------------------------------------------------
# Official band (R2) and comparison
# --------------------------------------------------------------------------
def test_compare_bands_one_tick_tolerance() -> None:
    same = b.compare_bands(206.71, 252.63, 206.71, 252.63, tick=0.01)
    assert same.mismatch is False and same.low_diff == 0 and same.high_diff == 0
    one = b.compare_bands(206.72, 252.63, 206.71, 252.63, tick=0.01)
    assert one.mismatch is False and one.low_diff == pytest.approx(0.01)
    two = b.compare_bands(206.73, 252.63, 206.71, 252.63, tick=0.01)
    assert two.mismatch is True
    high = b.compare_bands(206.71, 252.60, 206.71, 252.63, tick=0.01)
    assert high.mismatch is True and high.high_diff == pytest.approx(-0.03)
    assert b.compare_bands(None, 252.63, 206.71, 252.63, tick=0.01).mismatch is None
    assert b.compare_bands(206.71, 252.63, float("nan"), 252.63, tick=0.01).mismatch is None


def test_computed_band_matches_official_d4_files(rules: BandRules) -> None:
    """The 4 Sep 2026 file's band is what compute_band gives for the 4 Sep close."""
    official = b.compute_band(date(2026, 9, 7), "equity", rules, prev_close=141.93)
    cmp = b.compare_bands(official.low_initial, official.high_initial, 127.74, 156.12, tick=0.01)
    assert cmp.mismatch is False and cmp.low_diff == 0 and cmp.high_diff == 0


def _secmaster(day: date) -> pd.DataFrame:
    name = sm.raw_name(day)
    return sm.parse((FIXTURES / name).read_bytes(), file_date=day, source_file=name)


def test_attach_official_band_keeps_both_dates() -> None:
    frame = pd.concat([_secmaster(date(2026, 9, 4)), _secmaster(date(2026, 11, 9))])
    out = b.attach_official_band(frame)
    row = out[(out.symbol == "MONQ50") & (out.band_file_date == date(2026, 9, 4))].iloc[0]
    # File dated Friday 4 Sep applies to the NEXT session: Monday 7 Sep.
    assert row.band_applies_date == date(2026, 9, 7)
    assert (row.band_low_official, row.band_high_official) == (127.74, 156.12)
    row = out[(out.symbol == "MONQ50") & (out.band_file_date == date(2026, 11, 9))].iloc[0]
    assert row.band_applies_date == date(2026, 11, 11)
    assert (row.band_low_official, row.band_high_official) == (206.71, 252.63)
    # Only series EQ is kept: one row per symbol per file.
    assert not out.duplicated(["symbol", "band_file_date"]).any()


def test_attach_official_band_rejects_duplicates_and_missing_columns() -> None:
    frame = _secmaster(date(2026, 9, 4))
    with pytest.raises(ValueError, match="duplicate"):
        b.attach_official_band(pd.concat([frame, frame]))
    with pytest.raises(ValueError, match="missing columns"):
        b.attach_official_band(frame.drop(columns=["band_high"]))


def test_infer_regime_from_band_midpoint() -> None:
    # Official 127.74-156.12, midpoint 141.93: prev_close 141.93 matches, NAV 118.74 does not.
    cands = {"nav_t2": 118.2753, "prev_close": 141.93, "nav_t1": 118.7371}
    assert b.infer_regime_from_band(127.74, 156.12, cands, tick=0.01) == "prev_close"
    # Old regime: official 94.63-141.93 around NAV 118.2753 (midpoint 118.28, 0.0047 away).
    assert b.infer_regime_from_band(94.63, 141.93, cands, tick=0.01) == "nav_t2"
    # No candidate within a tick.
    assert b.infer_regime_from_band(94.63, 141.93, {"nav_t2": 100.0}, tick=0.01) == "other"
    # Two candidates within a tick of each other: cannot tell.
    both = {"nav_t2": 141.93, "prev_close": 141.94}
    assert b.infer_regime_from_band(127.74, 156.12, both, tick=0.01) == "ambiguous"
    assert b.infer_regime_from_band(None, 156.12, cands, tick=0.01) == "unknown"


# --------------------------------------------------------------------------
# DataFrame helper
# --------------------------------------------------------------------------
def test_compute_band_frame(rules: BandRules) -> None:
    df = pd.DataFrame(
        {
            "symbol": ["MONQ50", "MONQ50", "GOLDBEES"],
            "date": [date(2026, 9, 4), date(2026, 11, 11), date(2026, 9, 8)],
            "nav_t2": [118.2753, None, None],
            "prev_close": [None, 229.67, 100.0],
            "high": [141.93, 252.63, 106.5],
            "low": [130.0, 229.0, 95.0],
            "ltp": [141.93, 252.63, 106.0],
            "close": [141.93, 250.0, 105.0],
        }
    )
    out = b.compute_band_frame(df, rules, {"MONQ50": "equity", "GOLDBEES": "gold"})
    assert list(out["band_regime"]) == ["nav_t2", "prev_close", "prev_close"]
    assert list(out["band_high_initial"]) == [141.93, 252.63, 106.0]
    assert out["high_touched_initial_upper"].tolist() == [True, True, True]
    assert out["widened_up"].tolist() == [False, False, True]
    assert out["widenings_min_up"].tolist() == [0, 0, 1]
    assert math.isinf(out["band_high_max"].iloc[2])


def test_compute_band_frame_missing_base_gives_nulls_not_a_crash(rules: BandRules) -> None:
    df = pd.DataFrame(
        {"symbol": ["MONQ50"], "date": [date(2026, 11, 11)], "prev_close": [float("nan")]}
    )
    out = b.compute_band_frame(df, rules, {"MONQ50": "equity"})
    assert pd.isna(out["band_high_initial"].iloc[0]) and pd.isna(out["widened_up"].iloc[0])


def test_compute_band_frame_unknown_symbol_is_an_error(rules: BandRules) -> None:
    df = pd.DataFrame({"symbol": ["XYZ"], "date": [date(2026, 11, 11)], "prev_close": [10.0]})
    with pytest.raises(KeyError, match="asset class"):
        b.compute_band_frame(df, rules, {})
