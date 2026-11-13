"""Tests for the Bloomberg CSV importer. Fixtures are SYNTHETIC (made-up numbers)."""

from __future__ import annotations

import shutil
from datetime import date, time
from pathlib import Path

import pandas as pd
import pytest

from etfp.collect import bloomberg as bb
from etfp.config import load_etfs

FIXTURES = Path(__file__).parent / "fixtures" / "bloomberg"
HEADER = "date,time,timezone,instrument,field,value,note\n"
GOOD_ROW = "2026-09-04,15:30,Asia/Kolkata,NQ1,PX_LAST,19950.00,x\n"


def parse_text(text: str, name: str = "t.csv") -> pd.DataFrame:
    return bb.parse(text, name)


def test_parse_fixture_file() -> None:
    path = FIXTURES / "2026-11-13" / "export_synthetic.csv"
    df = bb.parse(path.read_bytes(), "bloomberg/2026-11-13/export_synthetic.csv")
    assert len(df) == 10
    first = df[
        (df.instrument == "NQ1")
        & (df.date == date(2026, 9, 4))
        & (df.timezone == "America/New_York")
    ]
    row = first.iloc[0]
    # 16:00 New York on 4 Sep 2026 (summer time, UTC-4) = 20:00 UTC = 01:30 IST on 5 Sep.
    assert row.ts_utc.isoformat() == "2026-09-04T20:00:00+00:00"
    assert row.ts_ist.isoformat() == "2026-09-05T01:30:00+05:30"
    assert row.value == 20000.0 and row.local_time == time(16, 0)
    assert row.source_file == "bloomberg/2026-11-13/export_synthetic.csv" and row.source_line == 2
    assert df["ts_utc"].dt.tz is not None  # never naive
    assert list(df.columns) == list(bb.OUTPUT_COLUMNS)


def test_bom_and_column_order_and_optional_note() -> None:
    text = (
        "\ufeffvalue,instrument,field,timezone,time,date\n"
        "19950,NQ1,PX_LAST,Asia/Kolkata,15:30,2026-09-04\n"
    )
    df = parse_text(text)
    assert df.value.iloc[0] == 19950.0 and df.note.iloc[0] is None


@pytest.mark.parametrize(
    ("text", "needle"),
    [
        ("", "empty"),
        ("date,time,timezone,instrument,field\n", "missing=['value']"),
        (HEADER.replace("note", "notes"), "unknown=['notes']"),
        (HEADER.replace("note", "value"), "duplicates"),
        (HEADER, "no data rows"),
        (HEADER + "04/09/2026,15:30,Asia/Kolkata,NQ1,PX_LAST,1,\n", "line 2: date '04/09/2026'"),
        (HEADER + "2026-02-30,15:30,Asia/Kolkata,NQ1,PX_LAST,1,\n", "not a real calendar date"),
        (HEADER + "2026-09-04,3.30pm,Asia/Kolkata,NQ1,PX_LAST,1,\n", "time '3.30pm'"),
        (HEADER + "2026-09-04,25:00,Asia/Kolkata,NQ1,PX_LAST,1,\n", "not a real clock time"),
        (HEADER + "2026-09-04,15:30,IST,NQ1,PX_LAST,1,\n", "timezone 'IST'"),
        (HEADER + "2026-09-04,15:30,Asia/Kolkata,,PX_LAST,1,\n", "instrument"),
        (HEADER + "2026-09-04,15:30,Asia/Kolkata,NQ1,px last,1,\n", "UPPER_SNAKE_CASE"),
        (HEADER + "2026-09-04,15:30,Asia/Kolkata,NQ1,PX_LAST,#N/A N/A,\n", "not a plain number"),
        (HEADER + '2026-09-04,15:30,Asia/Kolkata,NQ1,PX_LAST,"20,200.5",\n', "not a plain number"),
        (HEADER + "2026-09-04,15:30,Asia/Kolkata,NQ1,PX_LAST,,\n", "blank"),
        (HEADER + "2026-09-04,15:30,Asia/Kolkata,NQ1,PX_LAST,nan,\n", "not a plain number"),
        (HEADER + "2026-09-04,15:30,Asia/Kolkata,NQ1,PX_LAST,0,\n", "must be > 0"),
        (HEADER + "2026-09-04,15:30,Asia/Kolkata,NQ1,PX_LAST\n", "expected 7 fields"),
        (HEADER + GOOD_ROW + GOOD_ROW, "duplicate of line 2"),
    ],
)
def test_bad_input_fails_loudly_naming_file(text: str, needle: str) -> None:
    with pytest.raises(bb.BloombergImportError) as exc:
        parse_text(text, "bloomberg/2026-11-13/bad.csv")
    assert "bloomberg/2026-11-13/bad.csv" in str(exc.value)
    assert needle in str(exc.value)


def test_all_problems_in_one_file_are_listed_with_line_numbers() -> None:
    text = (
        HEADER
        + "bad,15:30,Asia/Kolkata,NQ1,PX_LAST,1,\n"
        + GOOD_ROW
        + "2026-09-05,15:30,Asia/Kolkata,NQ1,PX_LAST,oops,\n"
    )
    with pytest.raises(bb.BloombergImportError) as exc:
        parse_text(text)
    msg = str(exc.value)
    assert "2 problem(s)" in msg and "line 2:" in msg and "line 4:" in msg


def test_zero_allowed_for_non_price_fields() -> None:
    df = parse_text(HEADER + "2026-09-04,15:30,Asia/Kolkata,NQ1,PX_VOLUME,0,\n")
    assert df.value.iloc[0] == 0.0


# --------------------------------------------------------------------------
# Folder import
# --------------------------------------------------------------------------
def make_root(tmp_path: Path) -> Path:
    root = tmp_path / "bloomberg"
    shutil.copytree(FIXTURES, root)
    (root / "README.md").unlink()  # not a CSV anyway
    return root


def test_import_directory_lineage_and_hash(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    res = bb.import_directory(root)
    assert len(res.frame) == 10 and res.superseded_rows == 0
    (info,) = res.files
    assert info.source_file == "bloomberg/2026-11-13/export_synthetic.csv"
    assert (
        info.rows == 10
        and info.first_date == date(2026, 9, 4)
        and info.last_date == date(2026, 9, 8)
    )
    assert len(info.sha256) == 64
    # The raw file is untouched (immutability): same bytes before and after.
    path = root / "2026-11-13" / "export_synthetic.csv"
    assert info.sha256 == __import__("hashlib").sha256(path.read_bytes()).hexdigest()


def test_import_directory_empty_and_missing(tmp_path: Path) -> None:
    res = bb.import_directory(tmp_path / "does_not_exist")
    assert res.frame.empty and res.files == ()
    with pytest.raises(bb.BloombergImportError, match="no Bloomberg CSV"):
        bb.import_directory(tmp_path / "does_not_exist", require_files=True)


def test_bad_file_in_folder_names_that_file(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    (root / "2026-11-14").mkdir()
    (root / "2026-11-14" / "broken.csv").write_text(HEADER + "x,y,z,a,b,c,d\n")
    with pytest.raises(bb.BloombergImportError, match=r"2026-11-14/broken\.csv"):
        bb.import_directory(root)


def test_hidden_and_underscore_files_are_skipped(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    (root / "2026-11-13" / "_notes.csv").write_text("garbage")
    (root / "2026-11-13" / ".hidden.csv").write_text("garbage")
    assert len(bb.import_directory(root).files) == 1


def test_overlapping_identical_export_is_collapsed(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    shutil.copytree(root / "2026-11-13", root / "2026-11-20")
    res = bb.import_directory(root)
    assert len(res.files) == 2 and len(res.frame) == 10 and res.superseded_rows == 0


def test_conflicting_values_across_files(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    (root / "2026-11-20").mkdir()
    # Same observation as the fixture's first NQ1 US-close row, different value.
    (root / "2026-11-20" / "revision.csv").write_text(
        HEADER + "2026-09-04,16:00,America/New_York,NQ1,PX_LAST,20001.00,revised\n"
    )
    with pytest.raises(bb.BloombergImportError) as exc:
        bb.import_directory(root)
    msg = str(exc.value)
    assert "conflicting values" in msg and "export_synthetic.csv" in msg and "revision.csv" in msg
    res = bb.import_directory(root, on_conflict="latest")
    assert len(res.frame) == 10 and res.superseded_rows == 1
    us_close = res.frame[
        (res.frame.instrument == "NQ1")
        & (res.frame.timezone == "America/New_York")
        & (res.frame.date == date(2026, 9, 4))
    ]
    assert us_close.value.iloc[0] == 20001.0  # the newer export wins, by explicit choice


# --------------------------------------------------------------------------
# Using the data: values at a time, coverage
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def frame() -> pd.DataFrame:
    return bb.parse((FIXTURES / "2026-11-13" / "export_synthetic.csv").read_bytes(), "fixture")


def test_series_at_exact_time_and_last_of_day(frame: pd.DataFrame) -> None:
    f_t = bb.series_at(frame, "NQ1", "PX_LAST", tz="Asia/Kolkata", local_time=time(15, 30))
    assert f_t.to_dict() == {
        date(2026, 9, 4): 19950.0,
        date(2026, 9, 7): 20100.0,
        date(2026, 9, 8): 20200.0,
    }
    f_a = bb.series_at(frame, "NQ1", "PX_LAST", tz="America/New_York")
    # Only the two US-close rows are dated in New York; the 15:30 IST rows convert to the same NY
    # date at 06:00 and are earlier in the day, so the 16:00 row is the last observation.
    assert f_a[date(2026, 9, 4)] == 20000.0 and f_a[date(2026, 9, 8)] == 20250.0
    # 15:30 IST on Monday 7 Sep is 06:00 in New York on 7 Sep (US shut): a row exists for that date.
    assert f_a[date(2026, 9, 7)] == 20100.0
    assert bb.series_at(frame, "XXX", "PX_LAST", tz="UTC").empty


def test_fair_value_inputs_end_to_end_from_fixture(frame: pd.DataFrame) -> None:
    # F_t = 15:30 IST rate on 7 Sep = 20100, F_a = US close of 4 Sep = 20000 -> x1.005
    # S_t = 84.21, S_a = NAV-time rate on 4 Sep = 83.95 -> x1.0030971
    from etfp.features.fair_value import fair_value

    f_t = bb.series_at(frame, "NQ1", "PX_LAST", tz="Asia/Kolkata", local_time=time(15, 30))
    f_a = bb.series_at(frame, "NQ1", "PX_LAST", tz="America/New_York", local_time=time(16, 0))
    s_t = bb.series_at(frame, "USDINR", "PX_LAST", tz="Asia/Kolkata", local_time=time(15, 30))
    s_a = bb.series_at(frame, "USDINR", "PX_LAST", tz="America/New_York", local_time=time(16, 0))
    r = fair_value(
        100.0,
        futures_t=f_t[date(2026, 9, 7)],
        futures_a=f_a[date(2026, 9, 4)],
        fx_t=s_t[date(2026, 9, 7)],
        fx_a=s_a[date(2026, 9, 4)],
        source="bloomberg",
    )
    assert r["fv"] == pytest.approx(100 * (20100 / 20000) * (84.21 / 83.95))
    assert r["fv_method"] == "bloomberg"


def test_coverage_complete_for_the_fixture_range(frame: pd.DataFrame) -> None:
    # India sessions 4, 7, 8 Sep; US sessions 4 and 8 Sep (7 Sep is Labor Day).
    reqs = [
        bb.Requirement("NQ1", "PX_LAST", "IN", "Asia/Kolkata", time(15, 30)),
        bb.Requirement("USDINR", "PX_LAST", "IN", "Asia/Kolkata", time(15, 30)),
        bb.Requirement("NQ1", "PX_LAST", "US", "America/New_York", time(16, 0)),
        bb.Requirement("USDINR", "PX_LAST", "US", "America/New_York", time(16, 0)),
    ]
    assert bb.check_coverage(frame, reqs, date(2026, 9, 4), date(2026, 9, 8)) == []
    bb.assert_coverage(frame, reqs, date(2026, 9, 4), date(2026, 9, 8))


def test_coverage_names_every_missing_trading_day(frame: pd.DataFrame) -> None:
    reqs = [
        bb.Requirement("NQ1", "PX_LAST", "IN", "Asia/Kolkata", time(15, 30)),
        bb.Requirement("HKDINR", "PX_LAST", "IN", "Asia/Kolkata", time(15, 30)),
    ]
    # 9 Sep is an Indian session with no rows at all; HKDINR has none anywhere.
    gaps = bb.check_coverage(frame, reqs, date(2026, 9, 4), date(2026, 9, 9))
    assert [g.requirement.instrument for g in gaps] == ["NQ1", "HKDINR"]
    assert gaps[0].missing_dates == (date(2026, 9, 9),)
    assert gaps[1].missing_dates == (
        date(2026, 9, 4),
        date(2026, 9, 7),
        date(2026, 9, 8),
        date(2026, 9, 9),
    )
    with pytest.raises(bb.BloombergImportError) as exc:
        bb.assert_coverage(frame, reqs, date(2026, 9, 4), date(2026, 9, 9))
    assert "NQ1 PX_LAST at 15:30 Asia/Kolkata missing for 1 IN trading day(s): 2026-09-09" in str(
        exc.value
    )
    assert "HKDINR" in str(exc.value)


def test_coverage_wrong_time_does_not_count(frame: pd.DataFrame) -> None:
    # A 16:00 IST row must not satisfy a 15:30 IST requirement.
    shifted = bb.parse(HEADER + "2026-09-04,16:00,Asia/Kolkata,NQ1,PX_LAST,19950,\n", "s.csv")
    req = [bb.Requirement("NQ1", "PX_LAST", "IN", "Asia/Kolkata", time(15, 30))]
    gaps = bb.check_coverage(shifted, req, date(2026, 9, 4), date(2026, 9, 4))
    assert gaps and gaps[0].missing_dates == (date(2026, 9, 4),)


def test_default_requirements_follow_the_universe() -> None:
    reqs = bb.default_requirements(load_etfs())
    names = {(r.instrument, r.market) for r in reqs}
    assert ("NQ1", "IN") in names and ("NQ1", "US") in names  # MONQ50, MON100
    assert ("ES1", "US") in names  # MASPTOP50, MAFANG proxies
    assert ("USDINR", "IN") in names and ("HKDINR", "HK") in names  # MAHKTECH is tracked
    assert len(reqs) == len(set(reqs))  # no duplicates


def test_coverage_not_before_rejects_a_non_close_row(frame: pd.DataFrame) -> None:
    # The fixture's 15:30 IST rows are dated 4/7/8 Sep in New York too (06:00 local). They must not
    # satisfy "the US close, at or after 13:00 New York" for 7 Sep when the US was shut... but 7 Sep
    # is not a US session, so only 4 and 8 Sep are required, and those have real 16:00 rows.
    req = [bb.Requirement("NQ1", "PX_LAST", "US", "America/New_York", None, time(13, 0))]
    assert bb.check_coverage(frame, req, date(2026, 9, 4), date(2026, 9, 8)) == []
    # Drop the 16:00 New York rows: only the 06:00-local rows remain, so both US days are gaps.
    no_close = frame[frame.timezone != "America/New_York"]
    gaps = bb.check_coverage(no_close, req, date(2026, 9, 4), date(2026, 9, 8))
    assert gaps and gaps[0].missing_dates == (date(2026, 9, 4), date(2026, 9, 8))
    assert "at or after 13:00" in gaps[0].describe()
