"""nav_daily: AMFI NAVs mapped to ETF symbols; history beats the daily file."""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from etfp.clean import make_context, nav_daily
from etfp.clean.etf_master import build as build_master
from etfp.config import load_etfs
from test_clean_helpers import build_sample, fixture_bytes, make_raw_root, save_raw

HIST_SEP = "amfi/navhistory_20260901_20260910.txt"
NAVALL = "amfi/NAVAll.txt"
NAVALL_ROW = b"114984;INF247L01AP3;-;Motilal Oswal Nasdaq 100 ETF;;;256.1057;09-Nov-2026"


@pytest.fixture(scope="module")
def nav(tmp_path_factory: pytest.TempPathFactory) -> pd.DataFrame:
    _, store = build_sample(tmp_path_factory)
    return store.read_table("nav_daily")


def one(df: pd.DataFrame, symbol: str, day: date) -> pd.Series:
    sel = df[(df["symbol"] == symbol) & (df["nav_date"] == day)]
    assert len(sel) == 1, f"{symbol} {day}: {len(sel)} rows"
    return sel.iloc[0]


def build_nav(root: Path) -> pd.DataFrame:
    ctx = make_context(root)
    ctx.master = build_master(ctx)
    return nav_daily.build(ctx)


def test_values_from_each_file_kind(nav: pd.DataFrame) -> None:
    mon100_sep = one(nav, "MON100", date(2026, 9, 4))
    assert mon100_sep["nav"] == 272.9167 and mon100_sep["nav_source"] == "history"
    assert mon100_sep["scheme_code"] == 114984
    assert mon100_sep["source_file"] == (
        "amfi_nav_history/2026-09-10/navhistory_20260901_20260910.txt"
    )
    latest = one(nav, "MON100", date(2026, 11, 9))  # only the daily file has this date
    assert latest["nav"] == 256.1057 and latest["nav_source"] == "navall"
    assert one(nav, "MONQ50", date(2026, 11, 9))["nav"] == 114.3849


def test_navs_are_not_split_adjusted(nav: pd.DataFrame) -> None:
    # MON100 split 10:1 on 2021-06-17: 921 in Jan 2021 vs 256 in Nov 2026, as published.
    assert one(nav, "MON100", date(2021, 1, 4))["nav"] == 921.2001
    assert one(nav, "MON100", date(2021, 1, 1))["nav"] == 920.2743


def test_us_holiday_nav_is_carried_forward_as_published(nav: pd.DataFrame) -> None:
    # 7 Sep 2026 was US Labor Day: the NAV barely moves (DECISIONS D10). We store what AMFI says.
    assert one(nav, "MON100", date(2026, 9, 7))["nav"] == 272.9035


def test_only_configured_etfs_one_row_per_day(nav: pd.DataFrame) -> None:
    assert not nav.duplicated(["symbol", "nav_date"]).any()
    assert nav["nav"].notna().all() and (nav["nav"] > 0).all()
    assert nav["source_file"].notna().all()
    # every symbol shown really is in the config
    assert set(nav["symbol"]) <= set(load_etfs().symbols())
    # AMFI lists ~20 other schemes in the sample files; none of them leak in
    assert set(nav["scheme_code"]) <= {e.amfi_scheme_code for e in load_etfs().etfs}


def test_history_wins_over_the_daily_file_and_the_clash_is_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    root = make_raw_root(tmp_path)
    clash = fixture_bytes(NAVALL).replace(
        NAVALL_ROW, NAVALL_ROW.replace(b"256.1057;09-Nov-2026", b"999.0000;10-Sep-2026")
    )
    assert clash != fixture_bytes(NAVALL)
    save_raw(root, "amfi_navall", date(2026, 11, 12), "NAVAll.txt", clash)
    with caplog.at_level(logging.WARNING, logger="etfp.clean"):
        df = build_nav(root)
    r = one(df, "MON100", date(2026, 9, 10))
    assert r["nav"] == 271.5596 and r["nav_source"] == "history"  # not 999.0
    assert "MON100" in caplog.text and "999.0" in caplog.text and "271.5596" in caplog.text


def test_later_history_download_wins(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    root = make_raw_root(tmp_path)
    revised = fixture_bytes(HIST_SEP).replace(b"271.5596", b"271.6000")
    assert revised != fixture_bytes(HIST_SEP)
    save_raw(
        root, "amfi_nav_history", date(2026, 9, 11), "navhistory_20260901_20260911.txt", revised
    )
    with caplog.at_level(logging.WARNING, logger="etfp.clean"):
        df = build_nav(root)
    r = one(df, "MON100", date(2026, 9, 10))
    assert r["nav"] == 271.6 and r["source_file"].startswith("amfi_nav_history/2026-09-11/")
    assert "revision" in caplog.text


def test_not_available_nav_is_skipped_with_a_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    root = make_raw_root(tmp_path, skip_sources=("amfi_navall",))
    broken = fixture_bytes(NAVALL).replace(b"114.3849;09-Nov-2026", b"N.A.;09-Nov-2026")
    assert broken != fixture_bytes(NAVALL)
    save_raw(root, "amfi_navall", date(2026, 11, 10), "NAVAll.txt", broken)
    with caplog.at_level(logging.WARNING, logger="etfp.clean"):
        df = build_nav(root)
    assert df[(df["symbol"] == "MONQ50") & (df["nav_date"] == date(2026, 11, 9))].empty  # no fake 0
    assert one(df, "MON100", date(2026, 11, 9))["nav"] == 256.1057  # others unaffected
    assert "not numbers" in caplog.text


def test_wrong_isin_for_a_scheme_code_is_flagged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    root = make_raw_root(tmp_path, skip_sources=("amfi_navall",))
    wrong = fixture_bytes(NAVALL).replace(b"149438;INF247L01AU3", b"149438;INF247L01ZZ9")
    assert wrong != fixture_bytes(NAVALL)
    save_raw(root, "amfi_navall", date(2026, 11, 10), "NAVAll.txt", wrong)
    with caplog.at_level(logging.WARNING, logger="etfp.clean"):
        build_nav(root)
    assert "INF247L01ZZ9" in caplog.text and "INF247L01AU3" in caplog.text


def test_no_files_gives_empty_table(tmp_path: Path) -> None:
    df = build_nav(tmp_path)
    assert df.empty and list(df.columns) == nav_daily.COLUMNS
