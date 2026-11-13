"""prices_daily: values checked against the numbers in the saved NSE sample files."""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from etfp.clean import CleanError, make_context, prices_daily
from etfp.clean.etf_master import build as build_master
from etfp.collect import nse_bhavcopy
from test_clean_helpers import (
    build_sample,
    fixture_bytes,
    make_raw_root,
    save_raw,
)

D = date(2026, 11, 9)
BHAV = "nse_bhavcopy/sec_bhavdata_full_09112026.csv"
BHAV_NAME = "sec_bhavdata_full_09112026.csv"


@pytest.fixture(scope="module")
def prices(tmp_path_factory: pytest.TempPathFactory) -> pd.DataFrame:
    _, store = build_sample(tmp_path_factory)
    return store.read_table("prices_daily")


def row(df: pd.DataFrame, symbol: str, day: date) -> pd.Series:
    sel = df[(df["symbol"] == symbol) & (df["date"] == day)]
    assert len(sel) == 1, f"{symbol} {day}: {len(sel)} rows"
    return sel.iloc[0]


def build_prices(root: Path) -> pd.DataFrame:
    ctx = make_context(root)
    ctx.master = build_master(ctx)
    return prices_daily.build(ctx)


def test_monq50_9_nov_2026_matches_the_files(prices: pd.DataFrame) -> None:
    r = row(prices, "MONQ50", D)
    # From sec_bhavdata_full_09112026.csv
    assert r["open"] == 237.69 and r["high"] == 239.74 and r["low"] == 228.25
    assert r["close"] == 229.67  # official close = last-30-minute average
    assert r["last"] == 231.76  # the very last trade is a different number (DECISIONS D3)
    assert r["prev_close"] == 231.90
    assert r["avg_price"] == 231.73
    assert r["volume"] == 274_573 and r["trades"] == 8_468
    assert r["deliv_qty"] == 122_295 and r["deliv_pct"] == 44.54
    assert r["series"] == "EQ"
    # From the UDiFF file for the same day
    assert r["isin"] == "INF247L01AU3"
    assert r["settle_price"] == 229.66
    assert r["value"] == 63_627_950.17  # exact rupees; the old file only says 636.28 lacs
    assert r["value_source"] == "udiff"


def test_exact_udiff_value_replaces_the_rounded_bhavcopy_value(prices: pd.DataFrame) -> None:
    old = nse_bhavcopy.parse(fixture_bytes(BHAV), source_file="x")
    old_value = old[old["symbol"] == "MONQ50"]["value"].iloc[0]
    assert old_value == 63_628_000.0  # 636.28 lacs x 100,000
    assert row(prices, "MONQ50", D)["value"] != old_value


def test_lineage_points_at_both_raw_files(prices: pd.DataFrame) -> None:
    r = row(prices, "MONQ50", D)
    assert r["source_file"] == f"nse_bhavcopy_full/2026-11-09/{BHAV_NAME}"
    assert r["source_file_udiff"] == (
        "nse_udiff_bhavcopy/2026-11-09/BhavCopy_NSE_CM_0_0_0_20261109_F_0000.csv.zip"
    )
    assert prices["source_file"].notna().all()


def test_day_without_udiff_uses_bhavcopy_alone(prices: pd.DataFrame) -> None:
    r = row(prices, "NIFTYBEES", date(2021, 1, 4))
    assert r["close"] == 150.71 and r["volume"] == 1_729_074
    assert r["value_source"] == "bhavcopy" and r["value"] == 259_720_000.0
    assert pd.isna(r["isin"]) and pd.isna(r["settle_price"]) and pd.isna(r["source_file_udiff"])
    assert r["deliv_pct"] == 30.52


def test_only_etfs_and_one_row_per_symbol_day(prices: pd.DataFrame) -> None:
    assert not prices.duplicated(["symbol", "date"]).any()
    assert "20MICRONS" not in set(prices["symbol"])  # an ordinary share in the same file
    assert {"MONQ50", "MON100", "MAFANG", "NIFTYBEES"} <= set(prices["symbol"])


def test_prices_are_not_adjusted_for_splits(prices: pd.DataFrame) -> None:
    # Unadjusted on purpose: see apply_corporate_actions.
    assert row(prices, "NIFTYBEES", D)["close"] == 285.01


def test_revised_raw_file_wins_and_disagreement_is_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    root = make_raw_root(tmp_path)
    revised = fixture_bytes(BHAV).replace(b"229.67", b"229.68")
    assert revised != fixture_bytes(BHAV)
    save_raw(root, "nse_bhavcopy_full", D, BHAV_NAME, revised)  # becomes ".v2"
    with caplog.at_level(logging.WARNING, logger="etfp.clean"):
        df = build_prices(root)
    r = row(df, "MONQ50", D)
    assert r["close"] == 229.68
    assert r["source_file"].endswith("sec_bhavdata_full_09112026.v2.csv")
    # UDiFF still says 229.67: the two NSE files disagree by a paisa, which must be said out loud.
    assert "MONQ50" in caplog.text and "229.68" in caplog.text and "229.67" in caplog.text


def test_udiff_only_day_is_used_on_its_own(tmp_path: Path) -> None:
    root = make_raw_root(tmp_path, skip_sources=("nse_bhavcopy_full",))
    r = row(build_prices(root), "MONQ50", D)
    assert r["close"] == 229.67 and r["volume"] == 274_573 and r["trades"] == 8_468
    assert r["source_file"].startswith("nse_udiff_bhavcopy/")
    assert pd.isna(r["deliv_qty"]) and pd.isna(r["avg_price"])  # UDiFF has neither


def test_extra_series_row_never_beats_eq(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    root = make_raw_root(tmp_path, skip_sources=("nse_bhavcopy_full", "nse_udiff_bhavcopy"))
    text = fixture_bytes(BHAV).decode()
    eq_line = next(line for line in text.splitlines() if line.startswith("MONQ50, EQ"))
    busier_be = eq_line.replace(" EQ", " BE", 1).replace("274573", "999999")
    save_raw(root, "nse_bhavcopy_full", D, BHAV_NAME, (text + busier_be + "\n").encode())
    with caplog.at_level(logging.WARNING, logger="etfp.clean"):
        df = build_prices(root)
    r = row(df, "MONQ50", D)
    assert r["series"] == "EQ" and r["volume"] == 274_573
    assert "extra series dropped" in caplog.text


def test_trade_date_must_match_folder_date(tmp_path: Path) -> None:
    root = make_raw_root(tmp_path, skip_sources=("nse_bhavcopy_full",))
    save_raw(root, "nse_bhavcopy_full", date(2026, 11, 10), BHAV_NAME, fixture_bytes(BHAV))
    with pytest.raises(CleanError) as err:
        build_prices(root)
    assert "nse_bhavcopy_full/2026-11-10/" in str(err.value)
    assert "2026-11-09" in str(err.value)


def test_changed_file_format_stops_the_build_naming_source_and_file(tmp_path: Path) -> None:
    root = make_raw_root(tmp_path, skip_sources=("nse_bhavcopy_full",))
    broken = fixture_bytes(BHAV).replace(b"CLOSE_PRICE", b"CLOSING")
    save_raw(root, "nse_bhavcopy_full", D, BHAV_NAME, broken)
    with pytest.raises(CleanError) as err:
        build_prices(root)
    assert "prices_daily" in str(err.value)
    assert f"nse_bhavcopy_full/2026-11-09/{BHAV_NAME}" in str(err.value)
    assert "CLOSE_PRICE" in str(err.value)


def test_missing_days_are_logged_not_fatal(
    caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    root = make_raw_root(tmp_path)
    with caplog.at_level(logging.WARNING, logger="etfp.clean"):
        df = build_prices(root)
    assert len(df) > 0
    assert "prices_daily" in caplog.text and "have no raw file" in caplog.text


def test_no_raw_files_gives_an_empty_table(tmp_path: Path) -> None:
    df = build_prices(tmp_path)
    assert df.empty and list(df.columns) == prices_daily.COLUMNS
