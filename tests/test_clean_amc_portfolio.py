"""Automatic fof_monthly / units_monthly rows from the AMCs' monthly portfolio files.

Uses the trimmed real workbooks in tests/fixtures/amc_portfolio/ copied into a throwaway raw
store. Worked numbers (all rupee amounts in the files are lakhs; 1 lakh = 100,000 rupees):

* Motilal, 30 Sep 2026: the Nasdaq 100 feeder holds 250,248,956 units of MON100 and has net
  assets of 803,183.82 lakh = Rs 80,318,382,000. MON100's own net assets are 1,435,905.07 lakh
  = Rs 143,590,507,000; at an AMFI NAV of Rs 284.8257 that is 143,590,507,000 / 284.8257
  = 504,134,658.49 units, so the feeder holds 250,248,956 / 504,134,658 = 49.6% of the ETF.
* Nippon, 31 Oct 2026 (a Saturday): net assets 99,128.724 lakh = Rs 9,912,872,406.75; the NAV
  of Fri 30 Oct is Rs 420.2847, so units = 9,912,872,406.75 / 420.2847 = 23,586,089.16.
"""

# ruff: noqa: E501

from __future__ import annotations

import logging
import shutil
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from etfp.clean import CLEANERS, CleanContext
from etfp.clean import amc_portfolio as cap
from etfp.clean.manual_fof import build as build_fof
from etfp.clean.manual_units import build as build_units
from etfp.config import load_etfs

FIX = Path(__file__).parent / "fixtures" / "amc_portfolio"


def make_root(tmp_path: Path, files: dict[str, tuple[str, str, str]]) -> CleanContext:
    """files: {fixture name: (source folder, download day, name in raw store)}."""
    raw = tmp_path / "data" / "raw"
    for fixture, (source, day, name) in files.items():
        dest = raw / source / day
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy(FIX / fixture, dest / name)
    (tmp_path / "data" / "clean").mkdir(parents=True, exist_ok=True)
    return CleanContext(raw_root=raw, universe=load_etfs())


def write_nav(tmp_path: Path, rows: list[tuple[str, str, float]]) -> None:
    frame = pd.DataFrame(rows, columns=["symbol", "nav_date", "nav"])
    frame["scheme_code"] = 0
    frame["nav_source"] = "history"
    frame["source_file"] = "x"
    frame.to_parquet(tmp_path / "data" / "clean" / "nav_daily.parquet", index=False)


MOTILAL = {
    "motilal_scheme_portfolio_2026-09.xlsx": (
        "amc_portfolio_motilal", "2026-11-18", "2026-oct__Scheme_Portfolio_Details_September_2026.xlsx"
    )
}  # fmt: skip


def test_feeder_row_sits_on_the_regular_plan_code(tmp_path: Path) -> None:
    ctx = make_root(tmp_path, MOTILAL)
    out = cap.fof_rows(ctx)
    assert list(out.columns) == cap.FOF_COLUMNS
    row = out[out["etf_symbol"] == "MON100"].iloc[0]
    assert row["amfi_scheme_code"] == 145551  # Regular plan; Direct (145552) gets no row
    assert row["month_end"] == date(2026, 9, 30)
    assert row["etf_units_held"] == 250_248_956
    assert row["aum"] == pytest.approx(80_318_382_000)
    assert pd.isna(row["fof_net_flow"]) and pd.isna(row["subscription_status"])
    assert row["source_file"] == (
        "amc_portfolio_motilal/2026-11-18/2026-oct__Scheme_Portfolio_Details_September_2026.xlsx#YO13"
    )
    assert 145552 not in set(out["amfi_scheme_code"])


def test_old_isin_of_the_pre_split_etf_is_accepted(tmp_path: Path) -> None:
    ctx = make_root(
        tmp_path,
        {"motilal_month_end_2020-12.xlsx": ("amc_portfolio_motilal", "2026-11-18", "dec20.xlsx")},
    )
    row = cap.fof_rows(ctx).iloc[0]
    assert row["month_end"] == date(2020, 12, 31)
    assert row["etf_units_held"] == pytest.approx(17_739_747.0011)  # pre-split units, as published


def test_units_are_aum_over_the_amfi_nav(tmp_path: Path) -> None:
    ctx = make_root(tmp_path, MOTILAL)
    write_nav(tmp_path, [("MON100", "2026-09-30", 284.8257), ("MON100", "2026-09-29", 280.0)])
    out = cap.units_rows(ctx)
    r = out[out["symbol"] == "MON100"].iloc[0]
    assert r["nav"] == 284.8257 and r["aum"] == pytest.approx(143_590_507_000)
    assert r["units"] == pytest.approx(504_134_658.49, abs=0.01)
    assert "AMFI NAV of 2026-09-30" in r["source"]
    assert r["nav_date"] == date(2026, 9, 30)  # explicit column (D51), same as the text
    assert r["source_file"].endswith("September_2026.xlsx#YO03")
    # the feeder's share of the ETF
    fof = cap.fof_rows(ctx).iloc[0]
    assert fof["etf_units_held"] / r["units"] == pytest.approx(0.4964, abs=1e-4)


def test_month_end_on_a_saturday_uses_the_friday_nav(tmp_path: Path) -> None:
    ctx = make_root(
        tmp_path,
        {"nippon_monthly_2026-10.xlsx": ("amc_portfolio_nippon", "2026-11-18", "oct26.xls")},
    )
    write_nav(
        tmp_path,
        [("HNGSNGBEES", "2026-10-30", 420.2847), ("HNGSNGBEES", "2026-10-29", 419.0)],
    )
    r = cap.units_rows(ctx).iloc[0]
    assert r["month_end"] == date(2026, 10, 31) and r["symbol"] == "HNGSNGBEES"
    assert r["units"] == pytest.approx(23_586_089.16, abs=0.01)
    assert "NAV of 2026-10-30" in r["source"]


def test_row_is_skipped_when_the_nav_is_missing_or_stale(tmp_path: Path, caplog) -> None:
    ctx = make_root(tmp_path, MOTILAL)
    write_nav(tmp_path, [("MON100", "2026-09-20", 270.0)])  # 10 days before month end
    with caplog.at_level(logging.WARNING, logger="etfp.clean"):
        out = cap.units_rows(ctx)
    assert out.empty
    assert "no AMFI NAV for MON100" in caplog.text


def test_unreadable_file_is_named_in_a_warning_not_skipped_silently(tmp_path: Path, caplog) -> None:
    raw = tmp_path / "data" / "raw" / "amc_portfolio_mirae" / "2026-11-18"
    raw.mkdir(parents=True)
    (raw / "broken.xlsx").write_bytes(b"<html>nope</html>")
    ctx = CleanContext(raw_root=tmp_path / "data" / "raw", universe=load_etfs())
    with caplog.at_level(logging.WARNING, logger="etfp.clean"):
        assert cap.fof_rows(ctx).empty
    assert "amc_portfolio_mirae/2026-11-18/broken.xlsx" in caplog.text


def test_later_download_of_the_same_month_wins_and_a_disagreement_is_logged(
    tmp_path: Path, caplog
) -> None:
    ctx = make_root(
        tmp_path,
        {"mirae_maspf_2026-10.xlsx": ("amc_portfolio_mirae", "2026-11-10", "first.xlsx")},
    )
    # a second download with a different AUM on a later day
    import openpyxl

    wb = openpyxl.load_workbook(FIX / "mirae_maspf_2026-10.xlsx")
    ws = wb.active
    for row in ws.iter_rows():
        for cell in row:
            if cell.value == "GRAND TOTAL":
                ws.cell(cell.row, cell.column + 4).value = 200_000.0  # lakh
    later = tmp_path / "data" / "raw" / "amc_portfolio_mirae" / "2026-11-17"
    later.mkdir(parents=True)
    wb.save(later / "second.xlsx")
    with caplog.at_level(logging.WARNING, logger="etfp.clean"):
        out = cap.fof_rows(ctx)
    assert len(out) == 1 and out.iloc[0]["aum"] == pytest.approx(20_000_000_000)
    assert "files disagree on AUM" in caplog.text


# -- the two loaders: typed rows win ------------------------------------------------------


def test_manual_row_wins_and_a_big_difference_is_logged(tmp_path: Path, caplog) -> None:
    ctx = make_root(tmp_path, MOTILAL)
    write_nav(tmp_path, [("MON100", "2026-09-30", 284.8257)])
    (tmp_path / "data" / "manual").mkdir()
    (tmp_path / "data" / "manual" / "fof_monthly.csv").write_text(
        "amfi_scheme_code,month_end,fund_name,etf_symbol,aum,etf_units_held\n"
        "145551,2026-09-30,Typed,MON100,,300000000\n",
        encoding="utf-8",
    )
    (tmp_path / "data" / "manual" / "units_monthly.csv").write_text(
        "symbol,month_end,aum,nav,units,source\nMON100,2026-09-30,,,504000000,typed\n",
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING, logger="etfp.clean"):
        fof = build_fof(ctx)
        units = build_units(ctx)
    row = fof[fof["amfi_scheme_code"] == 145551].iloc[0]
    assert row["etf_units_held"] == 300_000_000 and row["fund_name"] == "Typed"  # typed row kept
    assert row["source_file"] == "data/manual/fof_monthly.csv:2"
    assert "differ on etf_units_held" in caplog.text  # 300.0M vs 250.2M: +19.9%
    u = units[(units["symbol"] == "MON100")]
    assert len(u) == 1 and u.iloc[0]["units"] == 504_000_000 and u.iloc[0]["source"] == "typed"
    assert "differ on units" not in caplog.text  # 504.0M vs 504.13M is within 1%


def test_automatic_rows_fill_when_there_is_no_manual_file(tmp_path: Path) -> None:
    ctx = make_root(tmp_path, MOTILAL)
    write_nav(tmp_path, [("MON100", "2026-09-30", 284.8257), ("MONQ50", "2026-09-30", 100.0)])
    fof, units = build_fof(ctx), build_units(ctx)
    assert list(fof.columns) == cap.FOF_COLUMNS and len(fof) == 1
    assert sorted(units["symbol"]) == ["MON100", "MONQ50"]


def test_no_raw_files_keeps_both_tables_empty(tmp_path: Path) -> None:
    (tmp_path / "data" / "raw").mkdir(parents=True)
    ctx = CleanContext(raw_root=tmp_path / "data" / "raw", universe=load_etfs())
    assert build_fof(ctx).empty and build_units(ctx).empty


def test_cleaners_are_still_registered() -> None:
    names = [t for t, _ in CLEANERS]
    assert names.index("fof_monthly") < names.index("units_monthly")
    assert names.index("nav_daily") < names.index("units_monthly")  # NAV must exist first


# -- cross-check --------------------------------------------------------------------------


def test_crosscheck_compares_with_nse_units_on_the_month_end(tmp_path: Path) -> None:
    ctx = make_root(tmp_path, MOTILAL)
    write_nav(tmp_path, [("MON100", "2026-09-30", 284.8257)])
    daily = pd.DataFrame(
        {
            "symbol": ["MON100", "MON100"],
            "date": ["2026-09-29", "2026-09-30"],
            "units_outstanding": [500_000_000, 504_000_000],
        }
    )
    out = cap.crosscheck(ctx, daily)
    r = out.iloc[0]
    assert r["nse_date"] == date(2026, 9, 30) and r["units_nse"] == 504_000_000
    # (504,134,658.49 - 504,000,000) / 504,000,000 = +0.0267%
    assert r["diff_pct"] == pytest.approx(0.02672, abs=1e-4)
