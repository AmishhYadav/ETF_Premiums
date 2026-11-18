# ruff: noqa: E501  (hand-computed data tables are kept on one line each)
"""Unit tests for the pieces of build.py: each one on a small hand-made table."""

from __future__ import annotations

import math
from datetime import date, datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from etfp import build, calendars, paths
from etfp.build import (
    Underlying,
    _add_rationed,
    _computed_band_block,
    _feeder_block,
    _nav_block,
    _official_band_block,
    _price_block,
    _regime_inferred_block,
    _Series,
    _units_block,
)
from etfp.features import premiums, valve
from etfp.features import units as units_mod

NY = ZoneInfo("America/New_York")
IST = ZoneInfo("Asia/Kolkata")


@pytest.fixture(scope="module")
def cfg() -> build.BuildConfig:
    return build.load_config(paths.project_root())


def _epoch(y, m, d, hh, mm, tz) -> int:
    return int(datetime(y, m, d, hh, mm, tzinfo=tz).timestamp())


# ---- NAV timing: Mirae has no NAV on US holidays (7 Sep 2026) --------------------------
def test_missing_nav_on_us_holiday_is_flagged_and_alignment_carries_forward(cfg) -> None:
    etf = cfg.universe.by_symbol("MASPTOP50")
    nav = pd.DataFrame(
        {
            "nav_date": [date(2026, 9, 3), date(2026, 9, 4), date(2026, 9, 8)],
            "nav": [66.0, 66.7667, 66.9],
            "source_file": ["f1", "f2", "f3"],
        }
    )
    cols, series = _nav_block(etf, [date(2026, 9, 7), date(2026, 9, 8)], nav, cfg)
    assert math.isnan(cols["nav_same_date"][0])  # no NAV dated 7 Sep: left empty, not invented
    assert cols["nav_known_date"][0] == date(2026, 9, 4)  # the 4 Sep NAV (US close of 4 Sep)
    assert cols["nav_known_at_close"][0] == pytest.approx(66.7667)
    # 8 Sep: NAV dated 8 Sep embeds the US close of 8 Sep (too late); the 7 Sep NAV is missing,
    # so the freshest knowable NAV is still the 4 Sep one.
    assert cols["nav_known_date"][1] == date(2026, 9, 4)
    assert cols["nav_publication_lag_days"][1] == 4
    assert cols["nav_known_source_file"][0] == "f2"
    assert list(series.index) == [date(2026, 9, 3), date(2026, 9, 4), date(2026, 9, 8)]


def test_nav_is_put_on_todays_scale_with_its_own_ex_date(cfg) -> None:
    etf = cfg.universe.by_symbol("MON100")
    nav = pd.DataFrame(
        {
            "nav_date": [date(2021, 6, 16), date(2021, 6, 18), date(2021, 6, 21)],
            "nav": [1005.5588, 1025.4998, 101.5224],
            "source_file": ["a", "b", "c"],
        }
    )
    _, series = _nav_block(etf, [date(2021, 6, 21)], nav, cfg)
    # NAV changed scale on 21 Jun, not on the price ex-date 17 Jun (corporate_actions.yaml).
    assert series[date(2021, 6, 16)] == pytest.approx(100.55588)
    assert series[date(2021, 6, 18)] == pytest.approx(102.54998)
    assert series[date(2021, 6, 21)] == pytest.approx(101.5224)


# ---- bands, official band, cap_pi, regime_inferred ----------------------------------------
def _base(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    for col in ("nav_t1", "price_adj_factor", "last", "close", "prev_close", "nav_t2"):
        if col not in df:
            df[col] = np.nan if col != "price_adj_factor" else 1.0
    df["symbol"] = "MONQ50"
    return df


def test_official_band_join_flags_and_cap_pi(cfg) -> None:
    etf = cfg.universe.by_symbol("MONQ50")
    # 7 Sep 2026: prev close 141.93 -> +/-10% = 127.74 - 156.12 (hand computed, D4/D5)
    base = _base(
        [
            {"date": date(2026, 9, 7), "prev_close": 141.93, "high": 156.12, "low": 140.0,
             "last": 156.12, "close": 155.0},
            {"date": date(2026, 9, 8), "prev_close": 155.0, "high": 160.0, "low": 150.0,
             "last": 159.0, "close": 158.0},
        ]
    )  # fmt: skip
    computed = _computed_band_block(etf, base, cfg)
    assert computed["band_low_initial"][0] == pytest.approx(127.74)
    assert computed["band_high_initial"][0] == pytest.approx(156.12)
    assert bool(computed["hit_up"][0]) and not bool(computed["hit_down"][0])
    assert computed["regime"][0] == "prev_close"
    band_o = pd.DataFrame(
        {
            "symbol": ["MONQ50", "MONQ50"],
            "file_date": [
                date(2026, 9, 4),
                date(2026, 9, 3),
            ],  # two files, same session: latest wins
            "session_date": [date(2026, 9, 7), date(2026, 9, 7)],
            "band_low_official": [127.74, 1.0],
            "band_high_official": [156.12, 2.0],
            "source_file": ["sec_0409", "sec_0309"],
        }
    )
    off = _official_band_block(base, computed, band_o, cfg)
    assert off["band_high_official"][0] == pytest.approx(156.12)
    assert off["band_file_date"][0] == date(2026, 9, 4)
    assert off["band_session_date"][0] == date(2026, 9, 7)
    assert off["band_source_file"][0] == "sec_0409"
    assert off["band_mismatch"][0] is False or off["band_mismatch"][0] == False  # noqa: E712
    assert bool(off["hit_up_official"][0])
    assert off["band_mismatch"].isna()[1]  # no official band for 8 Sep in this table
    # cap_pi = ln(official upper limit / fair value) = ln(156.12 / 130) = 0.183090 (hand computed)
    cap = premiums.log_ratio(off["band_high_official"], pd.Series([130.0, 130.0]))
    assert cap[0] == pytest.approx(0.183090, abs=1e-6)
    assert math.isnan(cap[1])  # no official band that day: no ceiling, not a guess


def test_official_band_is_put_on_todays_scale(cfg) -> None:
    etf = cfg.universe.by_symbol("MON100")
    base = _base([{"date": date(2021, 6, 17), "prev_close": 101.562, "high": 107.0, "low": 100.0,
                   "price_adj_factor": 0.1, "last": 100.9, "close": 100.87}])  # fmt: skip
    base["symbol"] = "MON100"
    computed = _computed_band_block(etf, base, cfg)
    band_o = pd.DataFrame(
        {"symbol": ["MON100"], "file_date": [date(2021, 6, 16)], "session_date": [date(2021, 6, 17)],
         "band_low_official": [812.0], "band_high_official": [1218.0], "source_file": ["x"]}
    )  # fmt: skip
    off = _official_band_block(base, computed, band_o, cfg)
    assert off["band_high_official"][0] == pytest.approx(121.8)  # 1218 x 0.1


def _inferred(cfg, base_rows, official, first):
    base = _base(base_rows)
    off = pd.DataFrame(
        {"band_low_official": [r[0] for r in official], "band_high_official": [r[1] for r in official]}
    )  # fmt: skip
    return _regime_inferred_block(base, off, first, cfg)


def test_regime_inferred_from_official_band_and_from_prices(cfg) -> None:
    nan = float("nan")
    rows = [
        # official 94.63-141.93 is centred on NAV(t-2) 118.2753 (mid 118.28, within a tick)
        {"date": date(2026, 9, 4), "nav_t2": 118.2753, "prev_close": 140.0, "high": 141.0, "low": 130.0},
        # official 127.74-156.12 is centred on prev close 141.93, not on NAV
        {"date": date(2026, 9, 7), "nav_t2": 118.2753, "prev_close": 141.93, "high": 150.0, "low": 140.0},
        # before any security-master file: a trade at 125 breaks 1.2 x 100 = 120 -> not NAV(t-2)
        {"date": date(2023, 5, 2), "nav_t2": 100.0, "prev_close": 100.0, "high": 125.0, "low": 99.0},
        {"date": date(2023, 5, 3), "nav_t2": 100.0, "prev_close": 100.0, "high": 119.9, "low": 80.1},
        {"date": date(2023, 5, 4), "nav_t2": nan, "prev_close": 100.0, "high": 110.0, "low": 99.0},
        # after the first file but no band for this fund: unknown, no guessing
        {"date": date(2026, 9, 8), "nav_t2": 118.0, "prev_close": 141.0, "high": 150.0, "low": 140.0},
    ]  # fmt: skip
    official = [(94.63, 141.93), (127.74, 156.12), (nan, nan), (nan, nan), (nan, nan), (nan, nan)]
    out = _inferred(cfg, rows, official, date(2024, 2, 6))
    assert list(out["regime_inferred"]) == [
        "nav_t2", "prev_close", "not_nav_t2", "nav_t2_consistent", "unknown", "unknown",
    ]  # fmt: skip
    method = list(out["regime_inferred_method"])
    assert method[:5] == [
        "official_band",
        "official_band",
        "price_test",
        "price_test",
        "price_test",
    ]
    assert pd.isna(method[5])  # no basis for a label: empty


# ---- units ------------------------------------------------------------------------------------
def _changes(rows, size=600000):
    ud = pd.DataFrame(rows, columns=["symbol", "date", "units_outstanding", "source_file"])
    return units_mod.units_changes(ud, {"MONQ50": size})


def _no_changes() -> pd.DataFrame:
    return pd.DataFrame({c: [] for c in units_mod.CHANGE_COLUMNS})


def test_units_carry_forward_and_change_lands_on_first_row_after_the_file() -> None:
    rows = [
        ("MONQ50", date(2024, 7, 1), 3_275_018, "f1"),
        ("MONQ50", date(2025, 1, 2), 8_075_018, "f2"),  # +4,800,000 = 8 creation units
        ("MONQ50", date(2025, 1, 6), 8_075_018, "f3"),
    ]
    ud = pd.DataFrame(rows, columns=["symbol", "date", "units_outstanding", "source_file"])
    days = [date(2024, 6, 28), date(2024, 7, 1), date(2025, 1, 3), date(2025, 1, 7)]
    out = _units_block(days, ud, _changes(rows), pd.DataFrame(), 600000)
    assert pd.isna(out["units_outstanding"][0])  # before the first file: unknown, not zero
    assert out["units_outstanding"][1] == 3_275_018
    assert out["units_outstanding"][2] == 8_075_018  # latest file on or before 3 Jan is f2
    assert out["units_file_date"][2] == date(2025, 1, 2)
    assert out["units_stale_days"][2] == 1
    assert pd.isna(out["units_change"][1])  # first observation: no previous value
    assert out["units_change"][2] == 4_800_000
    assert out["creation_units_change"][2] == pytest.approx(8.0)
    assert out["creation_event"][2] == pytest.approx(8.0)
    assert bool(out["creation_multiple_ok"][2])
    assert out["units_change_days_since_prev"][2] == 185
    assert out["units_change"][3] == 0  # the 6 Jan file changed nothing
    assert out["creation_units_change"][3] == 0.0


def test_unknown_creation_unit_size_gives_empty_creation_columns() -> None:
    rows = [("MONQ50", date(2024, 7, 1), 100, "f1"), ("MONQ50", date(2024, 7, 2), 700, "f2")]
    ud = pd.DataFrame(rows, columns=["symbol", "date", "units_outstanding", "source_file"])
    ch = units_mod.units_changes(ud, {})
    out = _units_block([date(2024, 7, 1), date(2024, 7, 2)], ud, ch, pd.DataFrame(), None)
    assert out["units_change"][1] == 600
    assert pd.isna(out["creation_units_change"][1])


def test_monthly_units_fill_days_before_the_first_file_but_not_forever() -> None:
    ud = pd.DataFrame(columns=["symbol", "date", "units_outstanding", "source_file"])
    monthly = pd.DataFrame(
        {"symbol": ["MONQ50"], "month_end": [date(2023, 1, 31)], "units": [1_234_567.5]}
    )
    days = [date(2023, 1, 30), date(2023, 2, 15), date(2023, 4, 3)]
    out = _units_block(days, ud, _no_changes(), monthly, 600000)
    assert math.isnan(out["units"][0])  # month end not reached yet
    assert out["units"][1] == 1_234_567.5 and out["units_source"][1] == "monthly_aum_nav"
    assert math.isnan(out["units"][2])  # older than 45 days: stale, not carried on
    assert out["units_outstanding"].isna().all()  # security-master column stays strict


# ---- feeders, rationed -------------------------------------------------------------------------------
def test_feeder_units_use_only_disclosures_published_by_that_day() -> None:
    fof = pd.DataFrame(
        {
            "amfi_scheme_code": [1, 1, 2],
            "month_end": [date(2026, 9, 30), date(2026, 10, 31), date(2026, 10, 31)],
            "etf_symbol": ["MAFANG"] * 3,
            "etf_units_held": [80.0, 100.0, 50.0],
        }
    )
    days = [date(2026, 11, 5), date(2026, 11, 11)]
    out = _feeder_block("MAFANG", days, fof)
    assert out["feeder_units"][0] == pytest.approx(80.0)  # Oct figures not public until 10 Nov
    assert out["feeder_units_asof"][0] == date(2026, 9, 30)
    assert out["feeder_units"][1] == pytest.approx(150.0)
    empty = _feeder_block("MON100", days, fof)
    assert empty["feeder_units"].isna().all()


def test_rationed_needs_high_premium_and_no_recent_creation() -> None:
    n = 30
    df = pd.DataFrame(
        {
            "symbol": ["A"] * n,
            "pi": [0.5] * n,
            "units_outstanding": pd.array([100] * n, dtype="Int64"),
            "units_change": pd.array([0] * n, dtype="Int64"),
        }
    )
    df.loc[3, "units_change"] = 600_000  # a creation on row 3
    df.loc[10, "pi"] = 0.01  # premium too low on row 10
    df.loc[11, "pi"] = np.nan
    r = _add_rationed(df)
    assert r[2]  # before the creation
    assert r[3]  # the creation day itself: only the PREVIOUS 20 rows count
    assert not r[4]  # from the next row on, the creation is remembered
    assert not r[10] and r[10] is not pd.NA
    assert pd.isna(r[11])  # unknown premium: unknown, not False
    assert r[24]  # 21 rows after the creation: the 20-row memory has passed
    assert not r[22]  # 19 rows after: still inside the memory


def test_rationed_unknown_without_units() -> None:
    df = pd.DataFrame(
        {"symbol": ["A"], "pi": [0.5], "units_outstanding": pd.array([pd.NA], dtype="Int64"),
         "units_change": pd.array([pd.NA], dtype="Int64")}
    )  # fmt: skip
    assert pd.isna(_add_rationed(df)[0])


# ---- fair-value inputs: Bloomberg first, Yahoo second, never a guess -----------------------------------
def _series(points: list[tuple[int, float, str]]) -> _Series:
    points = sorted(points)
    return _Series(
        np.array([p[0] for p in points], dtype="int64"),
        np.array([p[1] for p in points]),
        np.array([p[2] for p in points], dtype=object),
    )


def _bbg_data() -> Underlying:
    # 6 Nov 2026 is a Friday after the US clocks went back, so 16:00 New York = 21:00 UTC.
    return Underlying(
        bbg={
            "NQ1": _series(
                [
                    (_epoch(2026, 11, 6, 16, 0, NY), 20000.0, "b.csv"),
                    (_epoch(2026, 11, 9, 15, 30, IST), 20200.0, "b.csv"),
                ]
            ),
            "USDINR": _series(
                [
                    (_epoch(2026, 11, 6, 12, 0, NY), 84.0, "b.csv"),
                    (_epoch(2026, 11, 9, 15, 30, IST), 84.42, "b.csv"),
                ]
            ),
        },
        yahoo={},
    )


def test_fair_value_inputs_from_bloomberg_match_the_spec_example(cfg) -> None:
    etf = cfg.universe.by_symbol("MONQ50")
    got = build.fair_value_inputs(etf, [date(2026, 11, 9)], [date(2026, 11, 6)], _bbg_data())
    row = got.iloc[0]
    assert (row.futures_t, row.futures_a, row.fx_t, row.fx_a) == (20200.0, 20000.0, 84.42, 84.0)
    assert row.source == "bloomberg" and row.files == "b.csv"
    # SPEC 6.2: 100 x 20200/20000 x 84.42/84.00 = 101.505
    assert 100 * row.futures_t / row.futures_a * row.fx_t / row.fx_a == pytest.approx(101.505)


def test_fair_value_inputs_fall_back_to_yahoo_then_to_nothing(cfg) -> None:
    etf = cfg.universe.by_symbol("MONQ50")
    ist_close = _epoch(2026, 11, 9, 15, 30, IST)
    us_close = _epoch(2026, 11, 6, 16, 0, NY)
    # Yahoo keys are bar END times: a bar that ended at 15:30 is the price at 15:30.
    yahoo = {
        "NQ=F": _series([(us_close, 20000.0, "y"), (ist_close, 20200.0, "y")]),
        "USDINR=X": _series([(us_close - 600, 84.0, "y"), (ist_close, 84.42, "y")]),
    }
    got = build.fair_value_inputs(
        etf, [date(2026, 11, 9)], [date(2026, 11, 6)], Underlying({}, yahoo)
    )
    assert got.iloc[0].source == "free" and got.iloc[0].futures_a == 20000.0
    # a Yahoo bar 31+ minutes before 15:30 is too old: missing, not carried
    stale = {**yahoo, "NQ=F": _series([(us_close, 20000.0, "y"), (ist_close - 3600, 20200.0, "y")])}
    got2 = build.fair_value_inputs(
        etf, [date(2026, 11, 9)], [date(2026, 11, 6)], Underlying({}, stale)
    )
    assert math.isnan(got2.iloc[0].futures_t)
    none = build.fair_value_inputs(
        etf, [date(2026, 11, 9)], [date(2026, 11, 6)], Underlying({}, {})
    )
    assert none.iloc[0][["futures_t", "futures_a", "fx_t", "fx_a"]].isna().all()


def test_bloomberg_wins_only_when_complete(cfg) -> None:
    etf = cfg.universe.by_symbol("MONQ50")
    data = _bbg_data()
    broken = Underlying({"NQ1": data.bbg["NQ1"]}, {"USDINR=X": data.bbg["USDINR"]})  # no BBG FX
    ist_close = _epoch(2026, 11, 9, 15, 30, IST)
    us_close = _epoch(2026, 11, 6, 16, 0, NY)
    broken.yahoo["NQ=F"] = _series([(us_close, 19999.0, "y"), (ist_close, 20199.0, "y")])
    broken.yahoo["USDINR=X"] = _series([(us_close, 84.0, "y"), (ist_close, 84.42, "y")])
    got = build.fair_value_inputs(etf, [date(2026, 11, 9)], [date(2026, 11, 6)], broken)
    assert got.iloc[0].source == "free" and got.iloc[0].futures_t == 20199.0  # complete tier only


def test_hong_kong_fund_needs_no_future(cfg) -> None:
    etf = cfg.universe.by_symbol("MAHKTECH")
    hk_close = int(calendars.market_close("HK", date(2026, 11, 9)).timestamp())
    ist_close = _epoch(2026, 11, 9, 15, 30, IST)
    data = Underlying(
        {"HKDINR": _series([(hk_close - 600, 10.80, "b"), (ist_close, 10.85, "b")])}, {}
    )
    got = build.fair_value_inputs(etf, [date(2026, 11, 9)], [date(2026, 11, 9)], data)
    assert math.isnan(got.iloc[0].futures_t)  # not used
    assert (got.iloc[0].fx_t, got.iloc[0].fx_a) == (10.85, 10.80)
    assert got.iloc[0].source == "bloomberg"


def test_estimate_beta_through_the_lookup_layer(cfg) -> None:
    etf = cfg.universe.by_symbol("MONQ50")
    sessions = calendars.sessions("US", date(2025, 6, 2), date(2026, 6, 30))
    rng = np.random.default_rng(3)
    f_ret, s_ret = rng.normal(0, 0.01, len(sessions)), rng.normal(0, 0.002, len(sessions))
    fut, fx = 20000 * np.exp(np.cumsum(f_ret)), 84 * np.exp(np.cumsum(s_ret))
    nav = pd.Series(100 * np.exp(np.cumsum(0.8 * f_ret + s_ret)), index=pd.Index(sessions))
    close = [int(calendars.market_close("US", d).timestamp()) for d in sessions]
    data = Underlying(
        {
            "NQ1": _series([(c, v, "b") for c, v in zip(close, fut, strict=True)]),
            "USDINR": _series([(c, v, "b") for c, v in zip(close, fx, strict=True)]),
        },
        {},
    )
    est = build.estimate_beta(etf, nav, data)
    assert est.ok and est.n_obs == 250
    assert est.beta == pytest.approx(0.8, abs=1e-9)
    nothing = build.estimate_beta(etf, nav, Underlying({}, {}))
    assert not nothing.ok and math.isnan(nothing.beta)
    hk = build.estimate_beta(cfg.universe.by_symbol("MAHKTECH"), nav, data)
    assert not hk.ok and "no futures term" in hk.reason


def test_split_adjusts_prices_and_the_unadjusted_prev_close_on_the_ex_date(cfg) -> None:
    # Real numbers from config/corporate_actions.yaml: close 1015.62 on 16 Jun 2021, then
    # 100.87 on the ex-date 17 Jun with NSE's PREV_CLOSE still 1015.62 (unadjusted).
    etf = cfg.universe.by_symbol("MON100")
    px = pd.DataFrame(
        {
            "date": [date(2021, 6, 16), date(2021, 6, 17)],
            "open": [1010.0, 107.0], "high": [1020.0, 108.0], "low": [1005.0, 100.0],
            "close": [1015.62, 100.87], "last": [1016.0, 100.9], "avg_price": [1014.0, 103.0],
            "prev_close": [1005.0, 1015.62], "volume": [10, 20], "value": [1e4, 2e3],
            "trades": [2, 4], "deliv_qty": [5, 5], "deliv_pct": [50.0, 25.0],
            "source_file": ["a", "b"],
        }
    )  # fmt: skip
    out = _price_block(etf, px, cfg)
    assert out["price_adj_factor"].tolist() == pytest.approx([0.1, 1.0])
    assert out["close"].tolist() == pytest.approx([101.562, 100.87])
    assert out["close_raw"].tolist() == pytest.approx([1015.62, 100.87])  # raw is kept
    # the ex-date's PREV_CLOSE is brought to the new scale with the factor of the day before
    assert out["prev_close"][1] == pytest.approx(101.562)
    assert out["deliv_share"].tolist() == pytest.approx([0.5, 0.25])
    assert out["avg_trade"].tolist() == pytest.approx([5000.0, 500.0])


def test_dq_units_stale_flags_only_security_master_rows_in_frozen_files() -> None:
    frozen = frozenset({date(2024, 2, 12)})
    src = pd.Series(["security_master", "security_master", "monthly_aum_nav", None])
    fdate = pd.Series([date(2024, 2, 12), date(2024, 3, 14), None, None])
    out = build._units_stale_flag(src, fdate, frozen)
    assert list(out) == [True, False, False, False]


def test_valve_nav_lag_comes_from_nav_date_and_falls_back_to_the_text(
    cfg, tmp_path, monkeypatch
) -> None:
    from etfp.store import Store

    um = pd.DataFrame(
        {
            "symbol": ["MON100", "MON100", "MON100"],
            "month_end": [date(2022, 4, 30), date(2022, 5, 31), date(2022, 6, 30)],
            "aum": [1.0, 1.0, 1.0],
            "nav": [1.0, 1.0, 1.0],
            "units": [1.0, 1.0, 1.0],
            "source": [
                "amc_portfolio_motilal AUM / AMFI NAV of 2022-04-29",  # old row, no nav_date
                "typed",
                "amc_portfolio_motilal AUM / AMFI NAV of 2022-06-27",  # nav_date wins
            ],
            "source_file": ["a", "b", "c"],
            "nav_date": [None, date(2022, 5, 27), date(2022, 6, 29)],
        }
    )
    store = Store(tmp_path / "clean")
    store.write_table("units_monthly", um)
    seen = {}

    def fake(ud, umonthly, sizes, symbols):
        seen["lag"] = list(umonthly["nav_lag_days"])
        return pd.DataFrame(columns=valve.COLUMNS)

    monkeypatch.setattr(valve, "valve_monthly", fake)
    build.build_valve_monthly(store, cfg)
    # 30 Apr - 29 Apr = 1 (text fallback); 31 May - 27 May = 4; 30 Jun - 29 Jun = 1 (not 3)
    assert seen["lag"] == [1, 4, 1]
