"""bse_prices (R9), slb_eligible and slb_daily (R8): values checked against the sample files."""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from etfp.clean import CleanError, bse_prices, make_context, slb
from etfp.clean.etf_master import build as build_master
from etfp.config import load_etfs
from etfp.store import SCHEMAS, Store
from test_clean_helpers import build_sample, fixture_bytes, make_raw_root, save_raw

D21 = date(2021, 1, 4)
D26 = date(2026, 11, 12)
UDIFF = "BhavCopy_BSE_CM_0_0_0_20261112_F_0000.CSV"


@pytest.fixture(scope="module")
def sample(tmp_path_factory: pytest.TempPathFactory) -> Store:
    return build_sample(tmp_path_factory)[1]


def one(df: pd.DataFrame, symbol: str, day: date, **extra) -> pd.Series:
    sel = df[(df["symbol"] == symbol) & (df["date"] == day)]
    for k, v in extra.items():
        sel = sel[sel[k] == v]
    assert len(sel) == 1, f"{symbol} {day} {extra}: {len(sel)} rows"
    return sel.iloc[0]


def ctx_for(root: Path):
    ctx = make_context(root)
    ctx.master = build_master(ctx)
    return ctx


def empty_root(tmp_path: Path) -> Path:
    return make_raw_root(
        tmp_path, skip_sources=("bse_bhavcopy", "nse_slb_eligible", "nse_slb_bhavcopy")
    )


# -- bse_prices -------------------------------------------------------------------------


def test_bse_prices_keeps_only_our_etfs_matched_by_isin(sample: Store) -> None:
    df = sample.read_table("bse_prices")
    assert len(df) == 6 + 12  # 6 configured ETFs in the 2021 file, all 12 in the 2026 file
    assert "ABB" not in set(df["symbol"]) and "INE117A01022" not in set(df["isin"])
    assert df[["symbol", "date"]].duplicated().sum() == 0


def test_isin_maps_where_bse_and_nse_names_differ(sample: Store) -> None:
    # The 2021 BSE file calls this fund "ICICINIFTY"; we know it by ISIN as NIFTYIETF.
    df = sample.read_table("bse_prices")
    r = one(df, "NIFTYIETF", D21)
    assert r["isin"] == "INF109K012R6" and r["scrip_code"] == 537007
    assert r["file_format"] == "legacy"


def test_bse_row_values_2021_legacy_and_2026_udiff(sample: Store) -> None:
    df = sample.read_table("bse_prices")
    n = one(df, "NIFTYBEES", D21)
    assert (n["open"], n["high"], n["low"], n["close"]) == (150.31, 152.00, 148.87, 150.71)
    assert n["volume"] == 89_596 and n["value"] == 13_482_264.00 and n["trades"] == 846
    assert n["source_file"] == "bse_bhavcopy/2021-01-04/EQ_ISINCODE_040121.zip"
    m = one(df, "MON100", D26)
    assert m["close"] == 305.18 and m["volume"] == 38_350 and m["value"] == 11_729_902.00
    assert m["trades"] == 1_560 and m["file_format"] == "udiff"
    assert m["source_file"] == f"bse_bhavcopy/2026-11-12/{UDIFF}"


def test_funds_not_yet_listed_on_bse_simply_have_no_row(sample: Store) -> None:
    df = sample.read_table("bse_prices")
    assert df[(df["symbol"] == "MON100") & (df["date"] == D21)].empty  # not in the 2021 file


def test_bse_prices_without_raw_files_is_an_empty_table(tmp_path: Path) -> None:
    out = bse_prices.build(ctx_for(empty_root(tmp_path)))
    assert out.empty and list(out.columns) == bse_prices.COLUMNS


def test_isin_listed_twice_in_one_file_keeps_the_busiest_row_and_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    root = empty_root(tmp_path)
    lines = fixture_bytes(f"bse_bhavcopy/{UDIFF}").decode().splitlines()
    mon = next(x for x in lines if "INF247L01AP3" in x)
    twin = mon.replace("38350", "99999", 1).replace("533385", "999999", 1)  # busier twin
    text = "\n".join([*lines, twin]) + "\n"
    save_raw(root, "bse_bhavcopy", D26, UDIFF, text.encode())
    with caplog.at_level(logging.WARNING, logger="etfp.clean"):
        out = bse_prices.build(ctx_for(root))
    r = one(out, "MON100", D26)
    assert r["volume"] == 99_999 and r["scrip_code"] == 999_999
    assert "extra row(s) for an ISIN" in caplog.text


def test_file_under_the_wrong_date_folder_stops_the_build(tmp_path: Path) -> None:
    root = empty_root(tmp_path)
    save_raw(
        root, "bse_bhavcopy", date(2026, 11, 11), UDIFF, fixture_bytes(f"bse_bhavcopy/{UDIFF}")
    )
    with pytest.raises(
        CleanError, match=r"bse_prices: raw file bse_bhavcopy/2026-11-11/.*2026-11-12"
    ):
        bse_prices.build(ctx_for(root))


def test_unreadable_bse_file_names_table_and_file(tmp_path: Path) -> None:
    root = empty_root(tmp_path)
    save_raw(root, "bse_bhavcopy", D26, UDIFF, b"<!DOCTYPE html><html></html>")
    with pytest.raises(
        CleanError, match=r"bse_prices: cannot clean raw file bse_bhavcopy/2026-11-12/"
    ):
        bse_prices.build(ctx_for(root))


# -- slb_eligible -----------------------------------------------------------------------


def test_slb_eligible_has_one_row_per_etf_per_file(sample: Store) -> None:
    df = sample.read_table("slb_eligible")
    assert len(df) == 2 * len(load_etfs().etfs)  # two files x every ETF in config
    assert df[["date", "symbol"]].duplicated().sum() == 0


def test_mon100_was_not_eligible_in_jan_2021_but_is_in_nov_2026(sample: Store) -> None:
    df = sample.read_table("slb_eligible")
    old = one(df, "MON100", D21)
    assert bool(old["eligible"]) is False and old["series_count"] == 0  # explicit no, not a gap
    new = one(df, "MON100", D26)
    assert bool(new["eligible"]) is True and new["series_count"] == 28
    assert new["source_file"] == "nse_slb_eligible/2026-11-12/SLB_ELG_SEC_12112026.csv"


def test_nippon_etfs_eligible_in_2021_and_the_other_config_etfs_not(sample: Store) -> None:
    df = sample.read_table("slb_eligible")
    for symbol in ("NIFTYBEES", "BANKBEES", "JUNIORBEES"):
        r = one(df, symbol, D21)
        assert bool(r["eligible"]) and r["series_count"] == 22
    assert not bool(one(df, "HNGSNGBEES", D21)["eligible"])
    for symbol in ("MONQ50", "MAFANG", "MASPTOP50", "MAHKTECH"):
        assert bool(one(df, symbol, D26)["eligible"]) and one(df, symbol, D26)["series_count"] == 28


def test_a_listed_row_without_normal_eligibility_e_does_not_count(tmp_path: Path) -> None:
    root = empty_root(tmp_path)
    text = (
        "Sr.No.,Symbol,Series,Normal Eligibility,Recall Eligibility,Repay Eligibility,Market Type\n"
        "1,MON100,XD,D,E,E,N\n"
        "2,MONQ50,XD,E,E,E,N\n"
        "3,MONQ50,X1,D,E,E,N\n"
    )
    save_raw(root, "nse_slb_eligible", D26, "SLB_ELG_SEC_12112026.csv", text.encode())
    out = slb.build_eligible(ctx_for(root))
    assert not bool(one(out, "MON100", D26)["eligible"])  # listed, but not eligible to lend
    q = one(out, "MONQ50", D26)
    assert bool(q["eligible"]) and q["series_count"] == 1


def test_slb_file_listed_in_raw_exceptions_is_skipped_with_a_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # A proved source defect (cut-off file) is listed in config/raw_exceptions.yaml:
    # the cleaner skips it and warns instead of stopping the whole build.
    root = empty_root(tmp_path)
    good = (
        "Sr.No.,Symbol,Series,Normal Eligibility,Recall Eligibility,Repay Eligibility,Market Type\n"
        "1,MONQ50,XD,E,E,E,N\n"
    )
    d1, d2 = date(2021, 9, 15), date(2021, 9, 16)
    save_raw(root, "nse_slb_eligible", d1, "SLB_ELG_SEC_15092021.csv", good.encode())
    save_raw(root, "nse_slb_eligible", d2, "SLB_ELG_SEC_16092021.csv", b"Sr.No.,Symbol\n11846")
    skipped = "nse_slb_eligible/2021-09-16/SLB_ELG_SEC_16092021.csv"
    monkeypatch.setattr(slb, "skipped_raw_files", lambda: {skipped: "cut off at source"})
    with caplog.at_level(logging.WARNING, logger="etfp.clean"):
        out = slb.build_eligible(ctx_for(root))
    assert set(out["date"]) == {d1}
    assert any(skipped in r.getMessage() for r in caplog.records)


def test_slb_eligible_without_raw_files_is_empty(tmp_path: Path) -> None:
    out = slb.build_eligible(ctx_for(empty_root(tmp_path)))
    assert out.empty and list(out.columns) == slb.ELIGIBLE_COLUMNS


# -- slb_daily --------------------------------------------------------------------------


def test_slb_daily_has_only_the_mafang_trade(sample: Store) -> None:
    df = sample.read_table("slb_daily")
    assert len(df) == 1  # the 2021 file has no configured ETF; IRFC and 360ONE are filtered out
    r = df.iloc[0]
    assert (r["date"], r["symbol"], r["series"], r["maturity"]) == (
        D26,
        "MAFANG",
        "XD",
        date(2026, 12, 1),
    )
    assert r["volume"] == 8_000 and r["value"] == 2_400.00 and r["trades"] == 2
    assert r["fee_close"] == 0.30
    assert r["source_file"] == "nse_slb_bhavcopy/2026-11-12/SLBM_BC_12112026.DAT"


def test_slb_daily_wrong_date_folder_stops_the_build(tmp_path: Path) -> None:
    root = empty_root(tmp_path)
    save_raw(
        root,
        "nse_slb_bhavcopy",
        date(2026, 11, 13),
        "SLBM_BC_13112026.DAT",
        fixture_bytes("nse_slb/SLBM_BC_12112026.DAT"),
    )
    with pytest.raises(
        CleanError, match=r"slb_daily: raw file nse_slb_bhavcopy/2026-11-13/.*2026-11-12"
    ):
        slb.build_daily(ctx_for(root))


def test_slb_daily_empty_file_is_a_warning_not_a_crash(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    root = empty_root(tmp_path)
    save_raw(root, "nse_slb_bhavcopy", D26, "SLBM_BC_12112026.DAT", b"\n")
    with caplog.at_level(logging.WARNING, logger="etfp.clean"):
        out = slb.build_daily(ctx_for(root))
    assert out.empty and "has no rows" in caplog.text


def test_slb_daily_without_raw_files_is_empty(tmp_path: Path) -> None:
    out = slb.build_daily(ctx_for(empty_root(tmp_path)))
    assert out.empty and list(out.columns) == slb.DAILY_COLUMNS


# -- registry ---------------------------------------------------------------------------


def test_new_tables_are_registered_with_the_right_keys() -> None:
    assert SCHEMAS["bse_prices"].primary_key == ("symbol", "date")
    assert SCHEMAS["slb_eligible"].primary_key == ("date", "symbol")
    assert SCHEMAS["slb_daily"].primary_key == ("date", "symbol", "series")
    for name in ("bse_prices", "slb_eligible", "slb_daily"):
        assert "source_file" in SCHEMAS[name].required_columns()
