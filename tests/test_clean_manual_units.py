"""units_monthly (R11): month-end units = AUM / NAV from hand-typed disclosures."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from etfp.clean import CLEANERS, CleanContext, CleanError
from etfp.clean.manual_units import COLUMNS, build
from etfp.config import load_etfs
from etfp.store import Store

HEADER = "symbol,month_end,aum,nav,units,source"


def make_ctx(root: Path, csv_text: str | None) -> CleanContext:
    (root / "data" / "raw").mkdir(parents=True, exist_ok=True)
    if csv_text is not None:
        (root / "data" / "manual").mkdir(exist_ok=True)
        (root / "data" / "manual" / "units_monthly.csv").write_text(csv_text, encoding="utf-8")
    return CleanContext(raw_root=root / "data" / "raw", universe=load_etfs())


def rows(root: Path, *lines: str) -> object:
    return build(make_ctx(root, HEADER + "\n" + "\n".join(lines) + "\n"))


def test_units_are_aum_divided_by_nav(tmp_path: Path) -> None:
    # Rs 1.5 billion of assets at Rs 100 a unit = 15,000,000 units.
    out = rows(tmp_path, "MONQ50,2022-01-31,1500000000,100,,factsheet 2022-01")
    r = out.iloc[0]
    assert list(out.columns) == COLUMNS
    assert r["units"] == 15_000_000
    assert r["month_end"] == date(2022, 1, 31) and r["source"] == "factsheet 2022-01"
    assert r["source_file"] == "data/manual/units_monthly.csv:2"


def test_typed_units_are_kept_when_they_agree(tmp_path: Path) -> None:
    out = rows(tmp_path, "MONQ50,2022-01-31,1500000000,100,15100000,amfi")  # 0.67% apart
    assert out.iloc[0]["units"] == 15_100_000


def test_units_alone_are_enough(tmp_path: Path) -> None:
    out = rows(tmp_path, "MONQ50,2022-01-31,,,3275018,amc notice")
    assert out.iloc[0]["units"] == 3_275_018 and out["aum"].isna().all()


@pytest.mark.parametrize(
    ("line", "needle"),
    [
        ("MONQ50,2022-01-31,150,100,15000000,x", "does not match aum / nav"),  # aum in crores
        ("MONQ50,2022-01-31,1500000000,,,x", "give 'units', or both 'aum' and 'nav'"),
        ("MONQ50,2022-01-31,,100,,x", "give 'units', or both 'aum' and 'nav'"),
        ("MONQ50,2022-01-31,1500000000,0,,x", "must be greater than 0"),
        ("MONQ50,2022-01-30,1500000000,100,,x", "not the last day of a month"),
        ("MONQ50,2022-01-31,1500000000,100,,", "'source' is required"),
        ("XYZ,2022-01-31,1500000000,100,,x", "unknown ETF symbol 'XYZ'"),
        ("MONQ50,Jan 2022,1500000000,100,,x", "not a date YYYY-MM-DD"),
    ],
)
def test_bad_rows_name_file_and_line(tmp_path: Path, line: str, needle: str) -> None:
    with pytest.raises(CleanError) as err:
        rows(tmp_path, line)
    assert needle in str(err.value)
    assert "units_monthly: data/manual/units_monthly.csv line 2:" in str(err.value)


def test_duplicate_symbol_month_is_an_error(tmp_path: Path) -> None:
    line = "MONQ50,2022-01-31,1500000000,100,,x"
    with pytest.raises(CleanError, match=r"MONQ50 has two rows for 2022-01-31"):
        rows(tmp_path, line, line)


def test_missing_file_gives_an_empty_table_the_store_accepts(tmp_path: Path) -> None:
    out = build(make_ctx(tmp_path, None))
    assert out.empty and list(out.columns) == COLUMNS
    Store(tmp_path / "clean").write_table("units_monthly", out)


def test_round_trip_and_registration(tmp_path: Path) -> None:
    store = Store(tmp_path / "clean")
    store.write_table("units_monthly", rows(tmp_path, "MONQ50,2022-01-31,1500000000,100,,x"))
    assert store.read_table("units_monthly").iloc[0]["units"] == 15_000_000
    order = [t for t, _ in CLEANERS]
    assert order.index("units_monthly") > order.index("etf_master")
