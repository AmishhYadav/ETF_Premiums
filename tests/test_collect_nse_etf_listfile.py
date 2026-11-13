"""Tests for the NSE ETF list collector. Parsing uses a real saved sample; no network."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import httpx
import pandas as pd
import pytest

from etfp.collect import nse_etf_listfile as etfs
from etfp.http import PoliteClient, SourceNotFoundError
from etfp.rawstore import RawStore

FIXTURE = Path(__file__).parent / "fixtures" / "nse_etf_listfile" / "eq_etfseclist.csv"
DAY = date(2026, 11, 10)
SRC = "nse_etf_seclist/2026-11-10/eq_etfseclist.csv"


@pytest.fixture(scope="module")
def raw() -> bytes:
    return FIXTURE.read_bytes()


@pytest.fixture(scope="module")
def df(raw: bytes) -> pd.DataFrame:
    return etfs.parse(raw, source_file=SRC)


def row(frame: pd.DataFrame, symbol: str) -> pd.Series:
    rows = frame[frame["symbol"] == symbol]
    assert len(rows) == 1
    return rows.iloc[0]


# -- names -----------------------------------------------------------------------------


def test_url_and_raw_name_ignore_the_day():
    url = "https://nsearchives.nseindia.com/content/equities/eq_etfseclist.csv"
    assert etfs.url_for(DAY) == url
    assert etfs.url_for(date(2021, 1, 4)) == url
    assert etfs.raw_name(DAY) == "eq_etfseclist.csv"
    assert etfs.SOURCE == "nse_etf_seclist"


# -- parsing: values ----------------------------------------------------------------------


def test_niftybees(df):
    r = row(df, "NIFTYBEES")
    assert r["listing_date"] == date(2002, 1, 8)
    assert r["isin"] == "INF204KB14I2"
    assert r["face_value"] == pytest.approx(1.0)
    assert r["market_lot"] == 1
    assert r["underlying_asset"] == "Nifty 50"
    assert r["underlying_type"] == "EQUITY"


def test_monq50_and_mon100(df):
    r = row(df, "MONQ50")
    assert r["isin"] == "INF247L01AU3"
    assert r["listing_date"] == date(2021, 12, 29)
    assert r["underlying_type"] == "GLOBAL INDICES"
    assert row(df, "MON100")["listing_date"] == date(2011, 3, 31)


def test_fractional_face_value(df):
    assert row(df, "JUNIORBEES")["face_value"] == pytest.approx(1.25)


def test_two_digit_years_pivot(df):
    assert row(df, "GOLDBEES")["listing_date"] == date(2007, 3, 19)
    # a listing date later than the download day exists in the real file
    assert row(df, "POWERIETF")["listing_date"] == date(2026, 11, 11)


def test_surrounding_spaces_are_stripped(df):
    # raw 'Nifty India Consumption ' and 'Liquid ' end in a space
    assert row(df, "CONSUMBEES")["underlying_key"] == "Nifty India Consumption"
    assert row(df, "LIQGRWBEES")["underlying_asset"] == "Liquid"


def test_four_digit_year_is_accepted():
    text = (
        b"Symbol,Underlying Asset,SecurityName,DateofListing,MarketLot,ISINNumber,FaceValue,"
        b"ETF Underlying,Underlying Key\r\n"
        b"X,Nifty 50,X ETF,08-Jan-1999,1,INF000000000,1,EQUITY,Nifty 50\r\n"
    )
    assert etfs.parse(text, source_file="x")["listing_date"].iloc[0] == date(1999, 1, 8)


# -- parsing: shape, dtypes, lineage ------------------------------------------------------------


def test_columns_and_dtypes(df):
    assert tuple(df.columns) == etfs.OUTPUT_COLUMNS
    for name in (
        "symbol",
        "underlying_asset",
        "name",
        "isin",
        "underlying_type",
        "underlying_key",
        "source_file",
    ):
        assert df[name].dtype == "string"
    assert df["market_lot"].dtype == "Int64"
    assert df["face_value"].dtype == "float64"
    assert df["listing_date"].dtype == object
    assert all(type(d) is date for d in df["listing_date"])


def test_source_file_on_every_row(df):
    assert (df["source_file"] == SRC).all()


def test_no_duplicate_symbols_or_isins(df):
    assert not df.duplicated("symbol").any()
    assert not df.duplicated("isin").any()


def test_returns_every_data_row(raw, df):
    assert len(df) == len([x for x in raw.decode().splitlines() if x.strip()]) - 1


# -- parsing: malformed input ------------------------------------------------------------------


def test_missing_column_raises(raw):
    with pytest.raises(ValueError, match=r"nse_etf_seclist.*ISINNumber"):
        etfs.parse(raw.replace(b"ISINNumber", b"ISIN", 1), source_file=SRC)


def test_html_page_raises():
    with pytest.raises(ValueError, match="nse_etf_seclist"):
        etfs.parse(b"<html><body>Access Denied</body></html>", source_file=SRC)


def test_empty_and_header_only_raise(raw):
    with pytest.raises(ValueError, match="empty"):
        etfs.parse(b"", source_file=SRC)
    with pytest.raises(ValueError, match="no data rows"):
        etfs.parse(raw.split(b"\r\n", 1)[0] + b"\r\n", source_file=SRC)


def test_wrong_field_count_names_the_line(raw):
    broken = raw + b"\r\nSHORT,Gold\r\n"  # the real file has no newline after its last row
    last = len(raw.decode().splitlines()) + 1
    with pytest.raises(ValueError, match=rf"nse_etf_seclist: line {last}: expected 9"):
        etfs.parse(broken, source_file=SRC)


def test_bad_date_raises(raw):
    with pytest.raises(ValueError, match="DateofListing"):
        etfs.parse(raw.replace(b"08-Jan-02", b"2002-01-08", 1), source_file=SRC)
    with pytest.raises(ValueError, match="calendar"):
        etfs.parse(raw.replace(b"08-Jan-02", b"31-Feb-02", 1), source_file=SRC)


def test_bad_numbers_raise(raw):
    with pytest.raises(ValueError, match="FaceValue"):
        etfs.parse(raw.replace(b",1.25,", b",lots,", 1), source_file=SRC)
    with pytest.raises(ValueError, match="MarketLot"):
        etfs.parse(raw.replace(b",1,INF2", b",x,INF2", 1), source_file=SRC)


# -- fetch ------------------------------------------------------------------------------------


def make_client(handler) -> PoliteClient:
    return PoliteClient(transport=httpx.MockTransport(handler), sleep=lambda s: None)


def test_fetch_stores_untouched_bytes_under_the_given_day(tmp_path, raw):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=raw)

    store = RawStore(tmp_path / "raw")
    record = etfs.fetch(make_client(handler), store, DAY)

    assert len(seen) == 1
    assert str(seen[0].url) == etfs.URL
    assert seen[0].headers["accept"] == "*/*"
    assert seen[0].headers["referer"] == "https://www.nseindia.com/"
    assert record.relpath == SRC
    assert record.path.read_bytes() == raw


def test_fetch_404_propagates_and_stores_nothing(tmp_path):
    store = RawStore(tmp_path / "raw")
    client = make_client(lambda request: httpx.Response(404))
    with pytest.raises(SourceNotFoundError):
        etfs.fetch(client, store, DAY)
    assert not store.root.exists()
