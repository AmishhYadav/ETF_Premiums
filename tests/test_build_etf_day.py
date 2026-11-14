# ruff: noqa: E501
"""build_all on the saved fixtures: shape of etf_day, the numbers on 9 Nov 2026, events."""

from __future__ import annotations

import math
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from etfp.build import build_all, build_events
from etfp.collect.bloomberg import BloombergImportError
from etfp.export import ETF_DAY_COLUMNS, etf_day_schema_registered
from etfp.store import Store
from test_clean_helpers import fixture_bytes, make_raw_root, save_raw

D9 = date(2026, 11, 9)
AMFI_6NOV = (
    "Scheme Code;NAV Name;Plan;Option;ISIN Div Payout/ISIN Growth;ISIN Div Reinvestment;"
    "Net Asset Value;Date\n"
    "149438;Motilal Oswal Nasdaq Q50 ETF;;;INF247L01AU3;;110.8852;06-Nov-2026\n"
)
# All numbers made up except NQ/USDINR relations: SPEC 6.2 example (x1.01, x1.005).
BLOOMBERG_CSV = (
    "date,time,timezone,instrument,field,value\n"
    "2026-11-06,16:00,America/New_York,NQ1,PX_LAST,20000.00\n"
    "2026-11-09,15:30,Asia/Kolkata,NQ1,PX_LAST,20200.00\n"
    "2026-11-06,12:00,America/New_York,USDINR,PX_LAST,84.00\n"
    "2026-11-09,15:30,Asia/Kolkata,USDINR,PX_LAST,84.42\n"
)


def _read(store: Store) -> pd.DataFrame:
    with etf_day_schema_registered():
        return store.read_table("etf_day")


def _row(df: pd.DataFrame, symbol: str, day: date) -> pd.Series:
    sel = df[(df["symbol"] == symbol) & (df["date"] == day)]
    assert len(sel) == 1, f"{symbol} {day}: {len(sel)} rows"
    return sel.iloc[0]


@pytest.fixture(scope="module")
def plain(tmp_path_factory) -> tuple[Path, Store, pd.DataFrame]:
    root = make_raw_root(tmp_path_factory.mktemp("plain"))
    build_all(root)
    store = Store(root / "data" / "clean")
    return root, store, _read(store)


@pytest.fixture(scope="module")
def with_bloomberg(tmp_path_factory) -> tuple[Path, Store, pd.DataFrame]:
    """Fixtures plus a made-up Bloomberg file and the NAV dated 6 Nov 2026 (110.8852)."""
    root = make_raw_root(tmp_path_factory.mktemp("bbg"))
    save_raw(root, "amfi_nav_history", D9, "navhistory_20261106_20261106.txt", AMFI_6NOV.encode())
    folder = root / "data" / "raw" / "bloomberg" / "2026-11-13"
    folder.mkdir(parents=True)
    (folder / "export.csv").write_text(BLOOMBERG_CSV, encoding="utf-8")
    build_all(root)
    store = Store(root / "data" / "clean")
    return root, store, _read(store)


def test_columns_are_exactly_the_documented_ones(plain) -> None:
    _, _, df = plain
    assert list(df.columns) == list(ETF_DAY_COLUMNS)  # nothing undocumented, nothing missing
    assert not df.duplicated(["symbol", "date"]).any()
    assert len(df) == 16  # one row per ETF per price day: 5 on 2021-01-04, 11 on 2026-11-09
    assert set(df["date"]) == {date(2021, 1, 4), D9}


def test_prem_raw_on_9_nov_is_the_press_number(plain) -> None:
    _, _, df = plain
    row = _row(df, "MONQ50", D9)
    assert row["close"] == pytest.approx(229.67)
    assert row["nav_same_date"] == pytest.approx(114.3849)
    assert row["prem_raw"] == pytest.approx(229.67 / 114.3849 - 1)  # 100.79%
    assert round(row["prem_raw"] * 100, 1) == 100.8


def test_stale_nav_is_flagged_and_its_value_blanked(plain) -> None:
    # The fixture has no AMFI NAV dated 6 Nov, so the freshest knowable NAV is from 10 Sep.
    # 60 days old is a hole in the NAV history, not a lag: the date and lag stay visible
    # and flagged, but the value is blanked so no premium is invented from it (D31).
    _, _, df = plain
    row = _row(df, "MONQ50", D9)
    assert row["nav_known_date"] == date(2026, 9, 10)
    assert row["nav_publication_lag_days"] == 60
    assert bool(row["dq_nav_stale"])
    assert pd.isna(row["nav_known_at_close"]) and pd.isna(row["prem_nav_known"])
    assert row["fv_method"] == "no_nav" and pd.isna(row["fv"])


def test_units_turnover_and_creation_total_on_9_nov(plain) -> None:
    _, _, df = plain
    row = _row(df, "MONQ50", D9)
    assert row["units_outstanding"] == 14_675_018
    assert row["units_file_date"] == D9 and row["units_source"] == "security_master"
    # sparse fixture: the 2025 and 2026 files both land on the first price row after them
    assert row["units_change"] == 14_675_018 - 3_275_018
    assert row["creation_units_change"] == pytest.approx(19.0)  # (8 + 11) baskets of 600,000
    assert bool(row["creation_multiple_ok"])
    assert row["turnover"] == pytest.approx(274_573 / 14_675_018)  # 1.87% of units in a day
    assert bool(row["turnover_is_nse_only"]) and pd.isna(row["bse_volume"])
    # MASPTOP50 basket = 550,000 units (D36): the fixture's change is 7 redemption baskets
    assert _row(df, "MASPTOP50", D9)["creation_units_change"] == pytest.approx(-7.0)


def test_official_band_columns_empty_when_no_file_covers_the_day(plain) -> None:
    # The file dated 9 Nov applies to 11 Nov; no security-master file covers 9 Nov in fixtures.
    _, _, df = plain
    row = _row(df, "MONQ50", D9)
    assert pd.isna(row["band_high_official"]) and pd.isna(row["cap_pi"])
    assert row["regime"] == "prev_close" and row["regime_inferred"] == "unknown"
    assert bool(row["dq_nav_t2_missing"]) or not pd.isna(row["nav_t2"])


def test_old_dates_get_the_price_based_regime_test(plain) -> None:
    _, _, df = plain
    row = _row(df, "NIFTYBEES", date(2021, 1, 4))
    assert row["regime"] == "nav_t2"
    assert row["regime_inferred_method"] == "price_test"


def test_fair_value_from_bloomberg_matches_hand_computation(with_bloomberg) -> None:
    _, _, df = with_bloomberg
    row = _row(df, "MONQ50", D9)
    # nav_known = NAV dated 6 Nov = 110.8852 (embeds the US close of 6 Nov)
    assert row["nav_known_date"] == date(2026, 11, 6)
    assert row["nav_known_underlying_date"] == date(2026, 11, 6)
    fv = 110.8852 * (20200 / 20000) * (84.42 / 84.00)
    assert row["fv_method"] == "bloomberg"
    assert row["fv"] == pytest.approx(fv)
    assert row["fv_futures_ratio"] == pytest.approx(1.01)
    assert row["fv_fx_ratio"] == pytest.approx(1.005)
    assert row["prem_nav_known"] == pytest.approx(229.67 / 110.8852 - 1)  # 107.1% (R19 b)
    assert round(row["prem_nav_known"] * 100, 1) == 107.1
    assert row["log_prem_fv"] == pytest.approx(math.log(229.67 / fv))
    assert row["pi"] == row["log_prem_fv"] and row["prem_known"] == row["prem_nav_known"]
    assert row["x_april"] == pytest.approx(math.log(229.67 / (1.2 * 110.8852)))  # D30
    assert row["x_april_fv"] == pytest.approx(math.log(229.67 / (1.2 * fv)))
    assert "bloomberg/2026-11-13/export.csv" in row["fv_input_files"]
    assert row["nav_known_source_file"].startswith("amfi_nav_history/")
    assert not bool(row["dq_nav_stale"])


def test_beta_fair_value_is_empty_not_guessed_without_history(with_bloomberg) -> None:
    _, _, df = with_bloomberg
    row = _row(df, "MONQ50", D9)
    assert row["fv_beta_method"] == "no_beta"  # 3 data points, not 60+
    assert pd.isna(row["fv_beta"]) and pd.isna(row["fv_beta_value"])
    # a fund with no futures term has nothing to scale: fv_beta = fv, and it says so
    hk = _row(df, "HNGSNGBEES", D9)
    assert hk["fv_beta_method"] == "no_futures_term"
    assert hk["fv_beta"] == pytest.approx(hk["fv"])


def test_build_is_repeatable(plain) -> None:
    root, store, first = plain
    build_all(root)
    pd.testing.assert_frame_equal(first, _read(store))


def test_bad_bloomberg_file_stops_the_build_and_names_it(tmp_path) -> None:
    root = make_raw_root(tmp_path)
    folder = root / "data" / "raw" / "bloomberg" / "2026-11-13"
    folder.mkdir(parents=True)
    (folder / "broken.csv").write_text("date,time\n2026-11-09,15:30\n", encoding="utf-8")
    with pytest.raises(BloombergImportError, match=r"broken\.csv"):
        build_all(root)


# ---- events -----------------------------------------------------------------------------------------------
def test_unit_changes_become_events_beside_manual_ones(plain) -> None:
    _, store, _ = plain
    ev = build_events(store)
    monq = ev[(ev["type"] == "units_change") & (ev["symbols"] == "MONQ50")]
    assert list(monq["date"]) == [date(2025, 1, 2), date(2026, 9, 4)]
    first = monq.iloc[0]
    assert first["event_id"] == "units_change:MONQ50:2025-01-02"
    assert "+4,800,000" in first["description"] and "+8.00 creation units" in first["description"]
    assert bool(first["verified"])  # whole number of creation units
    assert first["source_primary"].startswith("nse_security_master/2025-01-02/")
    assert pd.isna(first["category"]) and pd.isna(first["direction"])  # set by hand, not by us
    # MASPTOP50's basket size is now known (D36): its change is whole baskets -> verified
    masp = ev[(ev["type"] == "units_change") & (ev["symbols"] == "MASPTOP50")]
    assert not masp.empty and masp["verified"].all()


def test_manual_events_are_kept_and_not_duplicated(plain, tmp_path) -> None:
    root, _, _ = plain
    # work on a copy of the store so the shared fixture stays untouched
    copy = Store(tmp_path / "clean")
    for name in ("units_daily", "events"):
        copy.write_table(name, Store(root / "data" / "clean").read_table(name))
    manual = Store(root / "data" / "clean").read_table("events")
    row = {c: [None] for c in manual.columns}
    row.update(
        event_id=["rule:2026-09-07"],
        date=[date(2026, 9, 7)],
        type=["rule"],
        symbols=[""],
        description=["Band anchor changes to previous close"],
        verified=[True],
        source_file=["m.csv"],
    )
    copy.write_table("events", pd.DataFrame(row))
    ev = build_events(copy)
    assert "rule:2026-09-07" in set(ev["event_id"])
    assert (ev["type"] == "units_change").sum() >= 2
    assert not ev["event_id"].duplicated().any()
    assert list(ev["date"]) == sorted(ev["date"])


def test_fixture_helper_is_available() -> None:
    assert fixture_bytes("amfi/NAVAll.txt")
