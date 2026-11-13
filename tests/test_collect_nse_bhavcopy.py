"""Tests for the NSE full bhavcopy collector. Parsing uses real saved samples; no network."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import httpx
import pandas as pd
import pytest

from etfp.collect import nse_bhavcopy as bhav
from etfp.http import PoliteClient, SourceNotFoundError
from etfp.rawstore import RawStore

FIXTURES = Path(__file__).parent / "fixtures" / "nse_bhavcopy"
DAY = date(2026, 11, 9)
SRC = "nse_bhavcopy_full/2026-11-09/sec_bhavdata_full_09112026.csv"


@pytest.fixture(scope="module")
def raw_2026() -> bytes:
    return (FIXTURES / "sec_bhavdata_full_09112026.csv").read_bytes()


@pytest.fixture(scope="module")
def raw_2021() -> bytes:
    return (FIXTURES / "sec_bhavdata_full_04012021.csv").read_bytes()


@pytest.fixture(scope="module")
def df_2026(raw_2026: bytes):
    return bhav.parse(raw_2026, source_file=SRC)


def one(df, symbol: str, series: str = "EQ"):
    rows = df[(df["symbol"] == symbol) & (df["series"] == series)]
    assert len(rows) == 1
    return rows.iloc[0]


# -- names -----------------------------------------------------------------------------


def test_url_and_raw_name():
    assert (
        bhav.url_for(DAY)
        == "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_09112026.csv"
    )
    assert bhav.raw_name(DAY) == "sec_bhavdata_full_09112026.csv"
    assert bhav.raw_name(date(2021, 1, 4)) == "sec_bhavdata_full_04012021.csv"
    assert bhav.SOURCE == "nse_bhavcopy_full"


# -- parsing: values ----------------------------------------------------------------------


def test_known_values_monq50(df_2026):
    row = one(df_2026, "MONQ50")
    assert row["date"] == date(2026, 11, 9)
    assert row["prev_close"] == pytest.approx(231.90)
    assert row["open"] == pytest.approx(237.69)
    assert row["high"] == pytest.approx(239.74)
    assert row["low"] == pytest.approx(228.25)
    assert row["last"] == pytest.approx(231.76)
    assert row["close"] == pytest.approx(229.67)
    assert row["vwap"] == pytest.approx(231.73)
    assert row["volume"] == 274573
    assert row["value"] == pytest.approx(63_628_000.0)  # 636.28 lacs x 100,000
    assert row["trades"] == 8468
    assert row["deliv_qty"] == 122295
    assert row["deliv_pct"] == pytest.approx(44.54)


def test_close_differs_from_last_price(df_2026):
    # the official close is a 30-minute average, not the final trade
    row = one(df_2026, "MONQ50")
    assert row["close"] != row["last"]


def test_niftybees_2026(df_2026):
    row = one(df_2026, "NIFTYBEES")
    assert row["close"] == pytest.approx(285.01)
    assert row["volume"] == 11312101


def test_hngsngbees_2021(raw_2021):
    df = bhav.parse(raw_2021, source_file="nse_bhavcopy_full/2021-01-04/x.csv")
    row = one(df, "HNGSNGBEES")
    assert row["date"] == date(2021, 1, 4)
    assert row["close"] == pytest.approx(354.46)
    assert row["volume"] == 1389
    assert row["trades"] == 80


def test_value_is_exact_rupees(df_2026):
    # 636.28 lacs must come out as exactly 63,628,000, not 63628000.00000001
    assert one(df_2026, "MONQ50")["value"] == 63_628_000.0


def test_dash_delivery_is_missing_not_zero(df_2026):
    row = one(df_2026, "21STCENMGM", "BE")
    assert row["deliv_qty"] is pd.NA
    assert pd.isna(row["deliv_pct"])
    assert row["volume"] == 22289  # other columns of that row are fine


def test_other_series_and_odd_symbols_present(df_2026):
    assert {"EQ", "BE", "SM"} <= set(df_2026["series"])
    assert {"BAJAJ-AUTO", "ARE&M"} <= set(df_2026["symbol"])


def test_first_and_last_rows_kept(df_2026):
    assert df_2026["symbol"].iloc[0] == "20MICRONS"
    assert df_2026["symbol"].iloc[-1] == "ZYDUSWELL"


# -- parsing: shape, dtypes, lineage ------------------------------------------------------------


def test_columns_and_dtypes(df_2026):
    assert tuple(df_2026.columns) == bhav.OUTPUT_COLUMNS
    for name in ("symbol", "series", "source_file"):
        assert df_2026[name].dtype == "string"
    for name in ("volume", "trades", "deliv_qty"):
        assert df_2026[name].dtype == "Int64"
    for name in (
        "prev_close",
        "open",
        "high",
        "low",
        "last",
        "close",
        "vwap",
        "value",
        "deliv_pct",
    ):
        assert df_2026[name].dtype == "float64"
    assert df_2026["date"].dtype == object
    assert all(type(d) is date for d in df_2026["date"])


def test_source_file_on_every_row(df_2026):
    assert (df_2026["source_file"] == SRC).all()


def test_no_duplicate_symbol_series(df_2026, raw_2021):
    assert not df_2026.duplicated(["symbol", "series"]).any()
    df21 = bhav.parse(raw_2021, source_file="x")
    assert not df21.duplicated(["symbol", "series"]).any()


def test_returns_every_data_row(raw_2026, df_2026):
    lines = [line for line in raw_2026.decode().splitlines() if line.strip()]
    assert len(df_2026) == len(lines) - 1


def test_tolerates_crlf_and_trailing_blank_lines(raw_2026, df_2026):
    crlf = raw_2026.replace(b"\n", b"\r\n") + b"\r\n\r\n"
    again = bhav.parse(crlf, source_file=SRC)
    assert again.equals(df_2026)


# -- parsing: malformed input ------------------------------------------------------------------


def test_missing_column_raises(raw_2026):
    broken = raw_2026.replace(b"CLOSE_PRICE", b"CLOSE", 1)
    with pytest.raises(ValueError, match=r"nse_bhavcopy_full.*CLOSE_PRICE"):
        bhav.parse(broken, source_file=SRC)


def test_html_page_raises(raw_2026):
    html = b"<!DOCTYPE html>\n<html><body>Access Denied</body></html>\n"
    with pytest.raises(ValueError, match="nse_bhavcopy_full"):
        bhav.parse(html, source_file=SRC)


def test_gzip_or_binary_garbage_raises():
    with pytest.raises(ValueError, match="nse_bhavcopy_full"):
        bhav.parse(b"\x1f\x8b\x08\x00\xff\xfe\xfd garbage", source_file=SRC)


def test_empty_and_header_only_raise(raw_2026):
    with pytest.raises(ValueError, match="empty"):
        bhav.parse(b"", source_file=SRC)
    header = raw_2026.split(b"\n", 1)[0] + b"\n"
    with pytest.raises(ValueError, match="no data rows"):
        bhav.parse(header, source_file=SRC)


def test_wrong_field_count_names_the_line(raw_2026):
    broken = raw_2026 + b"BADROW, EQ, 09-Nov-2026, 1.0, 2.0\n"
    last_line = len(broken.decode().splitlines())
    with pytest.raises(ValueError, match=rf"nse_bhavcopy_full: line {last_line}: expected 15"):
        bhav.parse(broken, source_file=SRC)


def test_non_numeric_value_raises(raw_2026):
    broken = raw_2026.replace(b"237.69", b"abc", 1)
    with pytest.raises(ValueError, match=r"line \d+: column OPEN_PRICE: 'abc'"):
        bhav.parse(broken, source_file=SRC)


def test_bad_date_raises(raw_2026):
    broken = raw_2026.replace(b"09-Nov-2026", b"2026-11-09", 1)
    with pytest.raises(ValueError, match="DATE1"):
        bhav.parse(broken, source_file=SRC)
    impossible = raw_2026.replace(b"09-Nov-2026", b"31-Feb-2026", 1)
    with pytest.raises(ValueError, match="calendar"):
        bhav.parse(impossible, source_file=SRC)


def test_non_integer_volume_raises(raw_2026):
    broken = raw_2026.replace(b" 274573,", b" 274573.5,", 1)
    with pytest.raises(ValueError, match="TTL_TRD_QNTY"):
        bhav.parse(broken, source_file=SRC)


def test_extra_columns_are_ignored(raw_2026, df_2026):
    lines = raw_2026.decode().split("\n")
    widened = "\n".join(
        (line + ", EXTRA" if i == 0 else line + ", 1") if line else line
        for i, line in enumerate(lines)
    ).encode()
    again = bhav.parse(widened, source_file=SRC)
    assert again.equals(df_2026)


# -- fetch ------------------------------------------------------------------------------------


def make_client(handler) -> PoliteClient:
    return PoliteClient(transport=httpx.MockTransport(handler), sleep=lambda s: None)


def test_fetch_stores_untouched_bytes(tmp_path, raw_2026):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=raw_2026)

    store = RawStore(tmp_path / "raw")
    record = bhav.fetch(make_client(handler), store, DAY)

    assert len(seen) == 1
    assert str(seen[0].url) == bhav.url_for(DAY)
    assert seen[0].headers["accept"] == "*/*"
    assert seen[0].headers["referer"] == "https://www.nseindia.com/"
    assert record.relpath == SRC
    assert record.path == tmp_path / "raw" / "nse_bhavcopy_full" / "2026-11-09" / bhav.raw_name(DAY)
    assert record.path.read_bytes() == raw_2026
    assert record.sha256 and record.size_bytes == len(raw_2026)


def test_fetch_then_parse_roundtrip(tmp_path, raw_2026, df_2026):
    store = RawStore(tmp_path / "raw")
    client = make_client(lambda request: httpx.Response(200, content=raw_2026))
    record = bhav.fetch(client, store, DAY)
    parsed = bhav.parse(record.path.read_bytes(), source_file=record.relpath)
    assert parsed.equals(df_2026)


def test_fetch_404_propagates_and_stores_nothing(tmp_path):
    store = RawStore(tmp_path / "raw")
    client = make_client(lambda request: httpx.Response(404, text="not found"))
    with pytest.raises(SourceNotFoundError) as caught:
        bhav.fetch(client, store, DAY)
    assert caught.value.source == "nse_bhavcopy_full"
    assert caught.value.day == DAY
    assert not store.root.exists()
