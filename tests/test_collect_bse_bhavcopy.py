"""BSE daily bhavcopy collector: URLs, both file formats, and the "web page = no file" rule.

Fixtures are real BSE files (downloaded 2026-11-14) trimmed to a few rows plus every ETF in
config/etfs.yaml. Expected numbers below were read by hand from those files.
"""

from __future__ import annotations

import io
import zipfile
from datetime import date
from pathlib import Path

import httpx
import pandas as pd
import pytest

from etfp.collect import bse_bhavcopy as bse
from etfp.http import PoliteClient, SourceNotFoundError
from etfp.rawstore import RawStore

FIXTURES = Path(__file__).parent / "fixtures" / "bse_bhavcopy"
UDIFF_DAY = date(2026, 11, 12)
UDIFF_NAME = "BhavCopy_BSE_CM_0_0_0_20261112_F_0000.CSV"
LEGACY_DAY = date(2024, 7, 5)
LEGACY_NAME = "EQ_ISINCODE_050724.zip"
OLD_DAY = date(2021, 1, 4)
OLD_NAME = "EQ_ISINCODE_040121.zip"
EARLY_UDIFF_NAME = "BhavCopy_BSE_CM_0_0_0_20240102_F_0000.CSV"


def raw(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def row(df: pd.DataFrame, isin: str) -> pd.Series:
    sel = df[df["isin"] == isin]
    assert len(sel) == 1, f"{isin}: {len(sel)} rows"
    return sel.iloc[0]


def make_client(handler) -> PoliteClient:
    return PoliteClient(transport=httpx.MockTransport(handler), sleep=lambda s: None)


# -- URLs and names ----------------------------------------------------------------------


def test_new_format_url_and_name():
    assert bse.url_for(UDIFF_DAY) == (
        "https://www.bseindia.com/download/BhavCopy/Equity/BhavCopy_BSE_CM_0_0_0_20261112_F_0000.CSV"
    )
    assert bse.raw_name(UDIFF_DAY) == UDIFF_NAME


def test_legacy_format_url_and_name_use_a_two_digit_year():
    assert bse.url_for(OLD_DAY) == (
        "https://www.bseindia.com/download/BhavCopy/Equity/EQ_ISINCODE_040121.zip"
    )
    assert bse.raw_name(OLD_DAY) == OLD_NAME
    # 2024-07-05 had BOTH formats; we ask for the new one, but its legacy file still parses
    assert bse.raw_name(LEGACY_DAY) == "BhavCopy_BSE_CM_0_0_0_20240705_F_0000.CSV"


def test_the_switch_to_the_new_format_is_on_2_jan_2024():
    assert not bse.uses_udiff(date(2024, 1, 1))
    assert bse.uses_udiff(date(2024, 1, 2))  # earliest day confirmed live
    assert bse.raw_name(date(2024, 1, 1)) == "EQ_ISINCODE_010124.zip"


# -- parse: new format -------------------------------------------------------------------


@pytest.fixture(scope="module")
def udiff_df() -> pd.DataFrame:
    return bse.parse(raw(UDIFF_NAME), source_file=f"bse_bhavcopy/2026-11-12/{UDIFF_NAME}")


def test_udiff_mon100_row_matches_the_file(udiff_df):
    r = row(udiff_df, "INF247L01AP3")
    assert r["date"] == UDIFF_DAY
    assert r["scrip_code"] == 533385 and r["ticker"] == "MON100" and r["series"] == "B"
    assert (r["open"], r["high"], r["low"]) == (312.58, 312.58, 303.55)
    assert r["close"] == 305.18 and r["last"] == 305.18 and r["prev_close"] == 303.48
    assert r["volume"] == 38_350  # units
    assert r["value"] == 11_729_902.00  # rupees (TtlTrfVal is already rupees)
    assert r["trades"] == 1_560
    assert r["file_format"] == "udiff"
    assert r["source_file"] == f"bse_bhavcopy/2026-11-12/{UDIFF_NAME}"


def test_udiff_monq50_and_mafang_rows(udiff_df):
    q = row(udiff_df, "INF247L01AU3")
    assert (q["ticker"], q["close"], q["last"], q["prev_close"]) == (
        "MONQ50",
        231.07,
        231.56,
        231.69,
    )
    assert q["volume"] == 16_705 and q["value"] == 3_878_764.00 and q["trades"] == 680
    m = row(udiff_df, "INF769K01HF4")
    assert m["ticker"] == "MAFANG" and m["close"] == 241.85
    assert m["volume"] == 50_594 and m["value"] == 12_267_297.00 and m["trades"] == 1_393


def test_udiff_keeps_other_securities_too(udiff_df):
    abb = row(udiff_df, "INE117A01022")  # not an ETF; parse returns the whole file
    assert abb["ticker"] == "ABB" and abb["close"] == 6950.00 and abb["volume"] == 14_992
    assert len(udiff_df) == 16 and udiff_df["isin"].is_unique


def test_udiff_dtypes(udiff_df):
    assert str(udiff_df["volume"].dtype) == "Int64" and str(udiff_df["trades"].dtype) == "Int64"
    assert udiff_df["close"].dtype == "float64"
    assert list(udiff_df.columns) == list(bse.OUTPUT_COLUMNS)


def test_earliest_confirmed_udiff_day_parses():
    df = bse.parse(raw(EARLY_UDIFF_NAME), source_file="x")
    r = row(df, "INF204KB14I2")  # NIFTYBEES on 2024-01-02
    assert r["date"] == date(2024, 1, 2) and r["scrip_code"] == 590103
    assert (r["open"], r["high"], r["low"], r["close"]) == (240.25, 240.95, 237.91, 239.17)
    assert r["prev_close"] == 240.06 and r["volume"] == 154_869
    assert r["value"] == 37_021_550.00 and r["trades"] == 1_763


# -- parse: legacy format ----------------------------------------------------------------


@pytest.fixture(scope="module")
def legacy_df() -> pd.DataFrame:
    return bse.parse(raw(LEGACY_NAME), source_file=f"bse_bhavcopy/2024-07-05/{LEGACY_NAME}")


def test_legacy_mon100_row_matches_the_file(legacy_df):
    r = row(legacy_df, "INF247L01AP3")
    assert r["date"] == LEGACY_DAY  # '05-Jul-24' read as 2024-07-05
    assert r["scrip_code"] == 533385 and r["name"] == "MON100" and r["series"] == "E"
    assert pd.isna(r["ticker"])  # the legacy file has no ticker
    assert (r["open"], r["high"], r["low"]) == (165.50, 166.00, 163.81)
    assert r["close"] == 165.97 and r["last"] == 166.00 and r["prev_close"] == 165.20
    assert r["volume"] == 54_556  # NO_OF_SHRS
    assert r["value"] == 9_013_392.00  # NET_TURNOV, rupees
    assert r["trades"] == 776
    assert r["file_format"] == "legacy"


def test_legacy_monq50_and_mafang_rows(legacy_df):
    q = row(legacy_df, "INF247L01AU3")
    assert (q["open"], q["close"], q["volume"], q["value"], q["trades"]) == (
        67.25,
        67.65,
        1_103,
        74_418.00,
        70,
    )
    m = row(legacy_df, "INF769K01HF4")
    assert m["close"] == 98.66 and m["volume"] == 152_958 and m["value"] == 15_062_917.00


def test_legacy_2021_row_and_two_digit_year_rule():
    df = bse.parse(raw(OLD_NAME), source_file="x")
    r = row(df, "INF204KB14I2")  # NIFTYBEES on 2021-01-04
    assert r["date"] == OLD_DAY
    assert (r["open"], r["high"], r["low"], r["close"], r["last"]) == (
        150.31,
        152.00,
        148.87,
        150.71,
        150.97,
    )
    assert r["prev_close"] == 149.54 and r["volume"] == 89_596
    assert r["value"] == 13_482_264.00 and r["trades"] == 846
    assert set(df["isin"]) >= {"INF204KB15I9", "INF732E01045", "INF109K012R6"}


# -- parse: bad input is loud ------------------------------------------------------------


def test_web_page_instead_of_data_is_rejected():
    with pytest.raises(ValueError, match="web page"):
        bse.parse(raw("not_published.html"), source_file="x")


def test_unknown_header_names_the_source_and_asks_if_the_format_changed():
    with pytest.raises(ValueError, match=r"bse_bhavcopy.*neither.*Has BSE changed"):
        bse.parse(b"A,B,C\n1,2,3\n", source_file="x")


def test_missing_required_column_is_named():
    text = raw(UDIFF_NAME).decode().replace("TtlTrfVal", "TotalValue", 1)
    with pytest.raises(ValueError, match=r"missing column.*TtlTrfVal"):
        bse.parse(text.encode(), source_file="x")


def test_empty_file_and_header_only_file_are_errors():
    with pytest.raises(ValueError, match="empty"):
        bse.parse(b"", source_file="x")
    header = raw(UDIFF_NAME).decode().splitlines()[0]
    with pytest.raises(ValueError, match="no data rows"):
        bse.parse((header + "\n").encode(), source_file="x")


def test_bad_number_and_bad_date_name_the_line_and_column():
    lines = raw(UDIFF_NAME).decode().splitlines()
    bad_num = [lines[0], lines[1].replace("6950.00", "69x50.00", 1)]
    with pytest.raises(ValueError, match=r"line 2: column ClsPric"):
        bse.parse(("\n".join(bad_num) + "\n").encode(), source_file="x")
    bad_date = [lines[0], lines[1].replace("2026-11-12", "12/11/2026", 1)]
    with pytest.raises(ValueError, match=r"line 2: column TradDt"):
        bse.parse(("\n".join(bad_date) + "\n").encode(), source_file="x")


def test_row_with_missing_isin_is_rejected():
    lines = raw(UDIFF_NAME).decode().splitlines()
    no_isin = [lines[0], lines[1].replace("INE117A01022", "", 1)]
    with pytest.raises(ValueError, match="ISIN is empty"):
        bse.parse(("\n".join(no_isin) + "\n").encode(), source_file="x")


def test_short_row_is_rejected():
    lines = raw(UDIFF_NAME).decode().splitlines()
    with pytest.raises(ValueError, match="expected"):
        short = lines[0] + "\n" + ",".join(lines[1].split(",")[:10]) + "\n"
        bse.parse(short.encode(), source_file="x")


def test_zip_with_two_csvs_is_rejected():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("a.CSV", "x")
        z.writestr("b.CSV", "y")
    with pytest.raises(ValueError, match="exactly one CSV"):
        bse.parse(buf.getvalue(), source_file="x")


# -- fetch -------------------------------------------------------------------------------


def test_fetch_new_format_stores_untouched_bytes_with_browser_headers(tmp_path):
    seen: list[httpx.Request] = []
    body = raw(UDIFF_NAME)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=body)

    record = bse.fetch(make_client(handler), RawStore(tmp_path / "raw"), UDIFF_DAY)
    assert str(seen[0].url) == bse.url_for(UDIFF_DAY)
    assert seen[0].headers["referer"] == "https://www.bseindia.com/"
    assert "Mozilla" in seen[0].headers["user-agent"]
    assert record.relpath == f"bse_bhavcopy/2026-11-12/{UDIFF_NAME}"
    assert record.path.read_bytes() == body


def test_fetch_legacy_format_stores_the_zip(tmp_path):
    body = raw(OLD_NAME)
    record = bse.fetch(
        make_client(lambda r: httpx.Response(200, content=body)),
        RawStore(tmp_path / "raw"),
        OLD_DAY,
    )
    assert record.relpath == f"bse_bhavcopy/2021-01-04/{OLD_NAME}"
    assert record.path.read_bytes() == body


def test_a_200_web_page_is_a_missing_file_and_nothing_is_stored(tmp_path):
    """BSE answers HTTP 200 with its home page for a day without a file (live check)."""
    page = raw("not_published.html")
    store = RawStore(tmp_path / "raw")
    client = make_client(lambda r: httpx.Response(200, content=page))
    with pytest.raises(SourceNotFoundError, match="web page instead of the file") as err:
        bse.fetch(client, store, date(2026, 11, 14))
    assert err.value.status_code == 200
    assert not store.root.exists()  # not even a folder: the HTML never reached the raw store


def test_a_real_404_also_propagates(tmp_path):
    store = RawStore(tmp_path / "raw")
    with pytest.raises(SourceNotFoundError):
        bse.fetch(make_client(lambda r: httpx.Response(404)), store, UDIFF_DAY)
    assert not store.root.exists()
