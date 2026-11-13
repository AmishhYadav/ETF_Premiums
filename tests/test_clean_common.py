"""Tests for the raw-store walker, gap finder and the corporate-action hook."""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from etfp.clean import common
from etfp.clean.common import CleanError, RawFile, find_gaps, list_raw_files, log_gaps, parse_file
from test_clean_helpers import save_raw


def _write(root: Path, source: str, day: str, name: str, body: bytes = b"x") -> None:
    folder = root / source / day
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(body)


def test_split_version() -> None:
    assert common._split_version("a.csv") == ("a.csv", 1)
    assert common._split_version("a.v2.csv") == ("a.csv", 2)
    assert common._split_version("NSE_CM_security_09112026.v3.csv.gz") == (
        "NSE_CM_security_09112026.csv.gz",
        3,
    )
    assert common._split_version("plain") == ("plain", 1)
    assert common._split_version("inav_155000.v10.json") == ("inav_155000.json", 10)


def test_highest_version_wins_and_relpath_names_it(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    _write(raw, "src", "2026-11-09", "f.csv", b"old")
    _write(raw, "src", "2026-11-09", "f.v2.csv", b"revised")
    _write(raw, "src", "2026-11-09", "g.csv")
    files = list_raw_files(raw, "src")
    assert [(f.base_name, f.version) for f in files] == [("f.csv", 2), ("g.csv", 1)]
    assert files[0].relpath == "src/2026-11-09/f.v2.csv"
    assert files[0].path.read_bytes() == b"revised"


def test_oldest_first_and_junk_ignored(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    _write(raw, "src", "2026-11-10", "b.csv")
    _write(raw, "src", "2021-01-04", "a.csv")
    _write(raw, "src", "2021-01-04", ".tmp-abc.part")  # interrupted save
    _write(raw, "src", "notes", "readme.txt")  # not a date folder
    (raw / "_manifest.jsonl").write_text("{}")
    assert [f.relpath for f in list_raw_files(raw, "src")] == [
        "src/2021-01-04/a.csv",
        "src/2026-11-10/b.csv",
    ]


def test_missing_source_is_empty_not_an_error(tmp_path: Path) -> None:
    assert list_raw_files(tmp_path / "raw", "never_collected") == []


def test_impossible_date_folder_is_an_error(tmp_path: Path) -> None:
    _write(tmp_path / "raw", "src", "2026-13-45", "a.csv")
    with pytest.raises(CleanError, match="2026-13-45"):
        list_raw_files(tmp_path / "raw", "src")


def test_parse_failure_names_table_and_file(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    _write(raw, "src", "2026-11-09", "bad.csv", b"junk")
    (rf,) = list_raw_files(raw, "src")

    def boom(_: bytes) -> None:
        raise ValueError("src: header is missing column(s) ['X']")

    with pytest.raises(CleanError) as err:
        parse_file("prices_daily", rf, boom)
    assert "prices_daily" in str(err.value)
    assert "src/2026-11-09/bad.csv" in str(err.value)
    assert "header is missing" in str(err.value)
    assert isinstance(err.value, RuntimeError)


def test_find_gaps_uses_the_trading_calendar() -> None:
    # Mon 4 Jan and Fri 8 Jan 2021: Tue-Thu are missing trading days; the weekend is not.
    present = [date(2021, 1, 4), date(2021, 1, 8)]
    assert find_gaps(present) == [date(2021, 1, 5), date(2021, 1, 6), date(2021, 1, 7)]
    # 10 Nov 2026 is an NSE holiday: not a gap.
    assert find_gaps([date(2026, 11, 9), date(2026, 11, 11)]) == []
    assert find_gaps([date(2026, 11, 9)]) == []  # one date: nothing to compare
    assert find_gaps([]) == []


def test_log_gaps_warns_and_does_not_raise(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="etfp.clean"):
        missing = log_gaps("prices_daily", [date(2021, 1, 4), date(2021, 1, 8)])
    assert len(missing) == 3
    assert "prices_daily" in caplog.text and "3 trading day(s)" in caplog.text
    assert "2021-01-05" in caplog.text


def test_corporate_action_hook_leaves_numbers_unadjusted() -> None:
    # MON100 split 10:1 in June 2021. Today the clean tables keep NAVs as published.
    frame = pd.DataFrame({"nav": [920.27, 92.03]})
    assert common.apply_corporate_actions(frame, table="nav_daily") is frame


def test_save_raw_helper_writes_where_the_walker_looks(tmp_path: Path) -> None:
    save_raw(tmp_path, "amfi_navall", date(2026, 11, 10), "NAVAll.txt", b"abc")
    (rf,) = list_raw_files(tmp_path / "data" / "raw", "amfi_navall")
    assert isinstance(rf, RawFile) and rf.day == date(2026, 11, 10)
