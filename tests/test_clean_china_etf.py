"""china_etf_daily (R17): China QDII ETFs from the Bloomberg importer. Synthetic data only."""

from __future__ import annotations

import math
import shutil
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from etfp.clean import china_etf, make_context
from etfp.collect import bloomberg

FIXTURES = Path(__file__).parent / "fixtures" / "bloomberg_china"
HEADER = "date,time,timezone,instrument,field,value,note\n"


def root_with(tmp_path: Path, csv_text: str | None = None, *, name: str = "x.csv") -> Path:
    """A project-like root whose data/raw/bloomberg holds the synthetic fixture or given text."""
    folder = tmp_path / "data" / "raw" / "bloomberg" / "2026-11-13"
    folder.mkdir(parents=True)
    if csv_text is None:
        shutil.copy(FIXTURES / "2026-11-13" / "china_synthetic.csv", folder / name)
    else:
        (folder / name).write_text(csv_text, encoding="utf-8")
    return tmp_path


def build(root: Path) -> pd.DataFrame:
    return china_etf.build(make_context(root))


def row(df: pd.DataFrame, code: str, day: date) -> pd.Series:
    sel = df[(df["code"] == code) & (df["date"] == day)]
    assert len(sel) == 1
    return sel.iloc[0]


def test_close_nav_shares_and_premium_from_the_synthetic_fixture(tmp_path: Path) -> None:
    df = build(root_with(tmp_path))
    assert list(df.columns) == china_etf.COLUMNS
    r = row(df, "513100", date(2026, 11, 9))
    assert (r["close"], r["nav"], r["shares_outstanding"]) == (1.2, 1.0, 5000.0)
    assert r["premium"] == pytest.approx(0.2)  # hand check: 1.200 / 1.000 - 1
    assert row(df, "513100", date(2026, 11, 10))["premium"] == pytest.approx(-0.08)  # 1.15/1.25-1
    assert r["source_file"] == "bloomberg/2026-11-13/x.csv"


def test_missing_nav_leaves_premium_empty_not_zero(tmp_path: Path) -> None:
    r = row(build(root_with(tmp_path)), "159941", date(2026, 11, 10))
    assert r["close"] == 2.0 and r["shares_outstanding"] == 800.0
    assert math.isnan(r["nav"]) and math.isnan(r["premium"])


def test_other_instruments_in_the_same_file_are_ignored(tmp_path: Path) -> None:
    df = build(root_with(tmp_path))
    assert set(df["code"]) == {"513100", "159941"}  # the NQ1 row did not become an ETF


def test_date_is_the_shanghai_date_of_the_row(tmp_path: Path) -> None:
    # 07:30 UTC on 9 Nov is 15:30 in Shanghai on 9 Nov; 17:00 UTC on 9 Nov is already 10 Nov.
    text = (
        HEADER
        + "2026-11-09,07:30,UTC,510300 CH,PX_LAST,4.0,\n"
        + "2026-11-09,17:00,UTC,510300 CH,FUND_NET_ASSET_VAL,3.5,\n"
    )
    df = build(root_with(tmp_path, text))
    assert sorted(df["date"]) == [date(2026, 11, 9), date(2026, 11, 10)]
    assert row(df, "510300", date(2026, 11, 9))["close"] == 4.0
    assert row(df, "510300", date(2026, 11, 10))["nav"] == 3.5


def test_last_observation_of_the_day_wins_and_both_files_are_in_the_lineage(
    tmp_path: Path,
) -> None:
    root = root_with(
        tmp_path,
        HEADER
        + "2026-11-09,10:00,Asia/Shanghai,513100 CH,PX_LAST,1.10,\n"
        + "2026-11-09,15:00,Asia/Shanghai,513100 CH,PX_LAST,1.20,\n",
        name="a.csv",
    )
    other = root / "data" / "raw" / "bloomberg" / "2026-11-14"
    other.mkdir()
    (other / "b.csv").write_text(
        HEADER + "2026-11-09,15:00,Asia/Shanghai,513100 CH,FUND_NET_ASSET_VAL,1.00,\n"
    )
    r = row(build(root), "513100", date(2026, 11, 9))
    assert r["close"] == 1.2 and r["premium"] == pytest.approx(0.2)
    assert r["source_file"] == "bloomberg/2026-11-13/a.csv;bloomberg/2026-11-14/b.csv"


def test_no_bloomberg_folder_gives_an_empty_table(tmp_path: Path) -> None:
    df = build(tmp_path)
    assert df.empty and list(df.columns) == china_etf.COLUMNS


def test_a_bad_bloomberg_file_stops_the_build_naming_the_file(tmp_path: Path) -> None:
    root = root_with(tmp_path, HEADER + "2026-11-09,15:00,Asia/Shanghai,513100 CH,PX_LAST,#N/A,\n")
    with pytest.raises(bloomberg.BloombergImportError, match=r"x\.csv"):
        build(root)


def test_a_zero_nav_is_refused_by_the_importer_so_premium_never_divides_by_zero(
    tmp_path: Path,
) -> None:
    root = root_with(
        tmp_path, HEADER + "2026-11-09,15:00,Asia/Shanghai,513100 CH,FUND_NET_ASSET_VAL,0,\n"
    )
    with pytest.raises(bloomberg.BloombergImportError, match="must be > 0"):
        build(root)


def test_shanghai_is_an_allowed_time_zone_and_the_table_is_registered() -> None:
    from etfp.export import COPIED_TABLES
    from etfp.store import SCHEMAS

    assert "Asia/Shanghai" in bloomberg.ALLOWED_TIMEZONES
    assert SCHEMAS["china_etf_daily"].primary_key == ("code", "date")
    assert "china_etf_daily" in COPIED_TABLES
