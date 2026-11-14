"""NSE securities lending (SLB) collector: URLs, both files, and bad-input behaviour.

Fixtures are real NSE archive files (downloaded 2026-11-14): the eligible list trimmed to
the ETFs in config/etfs.yaml plus five other rows, and the daily SLB bhavcopy (trimmed for
2026-11-12, whole files for 2021-01-04 and 2024-01-02). Numbers below are read by hand.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import httpx
import pandas as pd
import pytest

from etfp.collect import nse_slb as slb
from etfp.http import PoliteClient, SourceNotFoundError
from etfp.rawstore import RawStore

FIXTURES = Path(__file__).parent / "fixtures" / "nse_slb"
DAY = date(2026, 11, 12)


def raw(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def make_client(handler) -> PoliteClient:
    return PoliteClient(transport=httpx.MockTransport(handler), sleep=lambda s: None)


# -- names ------------------------------------------------------------------------------


def test_urls_and_raw_names():
    assert slb.eligible_url(DAY) == (
        "https://nsearchives.nseindia.com/archives/slbs/seclist/SLB_ELG_SEC_12112026.csv"
    )
    assert slb.bhavcopy_url(DAY) == (
        "https://nsearchives.nseindia.com/archives/slbs/bhavcopy/SLBM_BC_12112026.DAT"
    )
    assert slb.eligible_raw_name(DAY) == "SLB_ELG_SEC_12112026.csv"
    assert slb.bhavcopy_raw_name(date(2021, 1, 4)) == "SLBM_BC_04012021.DAT"


# -- eligible list ----------------------------------------------------------------------


@pytest.fixture(scope="module")
def elig_2026() -> pd.DataFrame:
    return slb.parse_eligible(raw("SLB_ELG_SEC_12112026.csv"), source_file="f")


@pytest.fixture(scope="module")
def elig_2021() -> pd.DataFrame:
    return slb.parse_eligible(raw("SLB_ELG_SEC_04012021.csv"), source_file="g")


def test_eligible_2026_has_the_five_international_etfs(elig_2026):
    for symbol in ("MON100", "MONQ50", "MAFANG", "MASPTOP50", "MAHKTECH"):
        rows = elig_2026[elig_2026["symbol"] == symbol]
        assert len(rows) == 28, symbol  # 12 monthly + 12 X-series + D1, D2, DF, DJ
        assert set(rows["normal_eligibility"]) == {"E"}
        assert set(rows["market_type"]) == {"N"}
    mon = elig_2026[elig_2026["symbol"] == "MON100"].set_index("series")
    assert mon.loc[
        "XD", ["normal_eligibility", "recall_eligibility", "repay_eligibility"]
    ].tolist() == ["E", "E", "E"]
    # the D-series are eligible to lend but recall/repay are switched off (file says E,D,D)
    assert mon.loc[
        "D1", ["normal_eligibility", "recall_eligibility", "repay_eligibility"]
    ].tolist() == ["E", "D", "D"]


def test_eligible_2021_has_only_the_three_nippon_etfs_and_no_mon100(elig_2021):
    symbols = set(elig_2021["symbol"])
    assert {"NIFTYBEES", "BANKBEES", "JUNIORBEES"} <= symbols
    assert not symbols & {"MON100", "MONQ50", "MAFANG", "MASPTOP50", "MAHKTECH"}
    assert (elig_2021["symbol"] == "NIFTYBEES").sum() == 22
    assert elig_2021["source_file"].eq("g").all()


def test_eligible_wrong_header_is_an_error_naming_the_source():
    with pytest.raises(ValueError, match=r"nse_slb_eligible: unexpected header.*Has NSE changed"):
        slb.parse_eligible(b"Symbol,Series\nABB,R3\n", source_file="x")


def test_eligible_header_only_empty_and_html_are_errors():
    header = raw("SLB_ELG_SEC_12112026.csv").decode().splitlines()[0]
    with pytest.raises(ValueError, match="no data rows"):
        slb.parse_eligible((header + "\n").encode(), source_file="x")
    with pytest.raises(ValueError, match="empty"):
        slb.parse_eligible(b"", source_file="x")
    with pytest.raises(ValueError, match="web page"):
        slb.parse_eligible(b"<!DOCTYPE html><html></html>", source_file="x")


def test_eligible_short_row_is_an_error():
    header = raw("SLB_ELG_SEC_12112026.csv").decode().splitlines()[0]
    with pytest.raises(ValueError, match="line 2: expected 7 fields"):
        slb.parse_eligible((header + "\n1,ABB,R3\n").encode(), source_file="x")


# -- bhavcopy ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def bc_2026() -> pd.DataFrame:
    return slb.parse_bhavcopy(raw("SLBM_BC_12112026.DAT"), source_file="bc")


def pick(df: pd.DataFrame, symbol: str, series: str) -> pd.Series:
    sel = df[(df["symbol"] == symbol) & (df["series"] == series)]
    assert len(sel) == 1
    return sel.iloc[0]


def test_mafang_lending_trade_on_12_nov_2026(bc_2026):
    r = pick(bc_2026, "MAFANG", "XD")
    assert r["date"] == DAY and r["maturity"] == date(2026, 12, 1)
    assert r["name"] == "MIRAEAMC - MAFANG" and r["market_type"] == "N"
    # all five fee figures are Rs 0.30 per unit; 8,000 units x 0.30 = Rs 2,400
    assert [r[c] for c in ("fee_prev_close", "fee_open", "fee_high", "fee_low", "fee_close")] == [
        0.30
    ] * 5
    assert r["volume"] == 8_000 and r["value"] == 2_400.00 and r["trades"] == 2
    assert r["value"] == pytest.approx(r["volume"] * r["fee_close"])


def test_field_order_prev_open_high_low_close(bc_2026):
    # IRFC D1: 0000000.50, 0000000.50, 0000000.75, 0000000.50, 0000000.67
    r = pick(bc_2026, "IRFC", "D1")
    assert (r["fee_prev_close"], r["fee_open"], r["fee_high"], r["fee_low"], r["fee_close"]) == (
        0.50,
        0.50,
        0.75,
        0.50,
        0.67,
    )
    assert r["volume"] == 2_076_500 and r["value"] == 1_327_480.15 and r["trades"] == 836
    # the average fee (value / volume = 0.639) lies between the day's low and high
    assert r["fee_low"] <= r["value"] / r["volume"] <= r["fee_high"]
    # 360ONE D1 (leading digit in the name and a high above the open)
    o = pick(bc_2026, "360ONE", "D1")
    assert (o["fee_prev_close"], o["fee_open"], o["fee_high"], o["fee_low"], o["fee_close"]) == (
        10.00,
        1.11,
        10.00,
        1.11,
        10.00,
    )
    assert o["volume"] == 8_036 and o["value"] == 22_259.96 and o["trades"] == 8


def test_maturity_follows_the_series(bc_2026):
    assert pick(bc_2026, "IRFC", "X1")["maturity"] == date(2027, 1, 5)
    assert pick(bc_2026, "IRFC", "XD")["maturity"] == date(2026, 12, 1)


def test_bhavcopy_2021_whole_file():
    df = slb.parse_bhavcopy(raw("SLBM_BC_04012021.DAT"), source_file="x")
    assert len(df) == 27 and set(df["date"]) == {date(2021, 1, 4)}
    r = pick(df, "ABFRL", "X2")
    assert r["maturity"] == date(2021, 2, 4)
    assert (r["fee_prev_close"], r["fee_open"], r["fee_high"], r["fee_low"], r["fee_close"]) == (
        0.99,
        0.50,
        0.60,
        0.50,
        0.60,
    )
    assert r["volume"] == 5_500 and r["value"] == 3_250.00 and r["trades"] == 2


def test_bhavcopy_dtypes(bc_2026):
    assert str(bc_2026["volume"].dtype) == "Int64" and str(bc_2026["trades"].dtype) == "Int64"
    assert bc_2026["value"].dtype == "float64"
    assert list(bc_2026.columns) == list(slb.BHAVCOPY_COLUMNS)


def test_bhavcopy_with_no_rows_is_an_empty_table_not_an_error():
    df = slb.parse_bhavcopy(b"\n", source_file="x")
    assert df.empty and list(df.columns) == list(slb.BHAVCOPY_COLUMNS)


def test_bhavcopy_wrong_width_bad_number_bad_date_html():
    good = raw("SLBM_BC_12112026.DAT").decode().splitlines()[0]
    with pytest.raises(ValueError, match=r"nse_slb_bhavcopy: line 1: expected 17 fields.*changed"):
        slb.parse_bhavcopy((",".join(good.split(",")[:16]) + "\n").encode(), source_file="x")
    with pytest.raises(ValueError, match="line 1: volume"):
        slb.parse_bhavcopy(good.replace("8036", "80x6", 1).encode(), source_file="x")
    with pytest.raises(ValueError, match="not a date like 12-NOV-2026"):
        slb.parse_bhavcopy(good.replace("12-NOV-2026", "2026-11-12").encode(), source_file="x")
    with pytest.raises(ValueError, match="web page"):
        slb.parse_bhavcopy(b"<!DOCTYPE html><html></html>", source_file="x")


# -- fetch ------------------------------------------------------------------------------


def test_fetch_both_files_store_untouched_bytes(tmp_path):
    bodies = {"seclist": raw("SLB_ELG_SEC_12112026.csv"), "bhavcopy": raw("SLBM_BC_12112026.DAT")}
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        key = "seclist" if "seclist" in str(request.url) else "bhavcopy"
        return httpx.Response(200, content=bodies[key])

    store = RawStore(tmp_path / "raw")
    client = make_client(handler)
    a = slb.fetch_eligible(client, store, DAY)
    b = slb.fetch_bhavcopy(client, store, DAY)
    assert a.relpath == "nse_slb_eligible/2026-11-12/SLB_ELG_SEC_12112026.csv"
    assert b.relpath == "nse_slb_bhavcopy/2026-11-12/SLBM_BC_12112026.DAT"
    assert a.path.read_bytes() == bodies["seclist"] and b.path.read_bytes() == bodies["bhavcopy"]
    assert seen[0].headers["referer"] == "https://www.nseindia.com/"


def test_fetch_404_and_web_page_store_nothing(tmp_path):
    store = RawStore(tmp_path / "raw")
    with pytest.raises(SourceNotFoundError):
        slb.fetch_bhavcopy(make_client(lambda r: httpx.Response(404)), store, DAY)
    with pytest.raises(SourceNotFoundError, match="web page"):
        slb.fetch_eligible(
            make_client(lambda r: httpx.Response(200, content=b"<!DOCTYPE html><html>")),
            store,
            DAY,
        )
    assert not store.root.exists()
