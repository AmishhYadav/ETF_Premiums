"""Tests for the NSE UDiFF bhavcopy collector. Parsing uses a real saved sample; no network."""

from __future__ import annotations

import io
import zipfile
from datetime import date
from pathlib import Path

import httpx
import pandas as pd
import pytest

from etfp.collect import nse_udiff as udiff
from etfp.http import PoliteClient, SourceNotFoundError
from etfp.rawstore import RawStore

FIXTURES = Path(__file__).parent / "fixtures" / "nse_udiff"
DAY = date(2026, 11, 9)
NAME = "BhavCopy_NSE_CM_0_0_0_20261109_F_0000.csv.zip"
SRC = f"nse_udiff_bhavcopy/2026-11-09/{NAME}"


@pytest.fixture(scope="module")
def raw_zip() -> bytes:
    return (FIXTURES / NAME).read_bytes()


@pytest.fixture(scope="module")
def inner_csv(raw_zip: bytes) -> bytes:
    with zipfile.ZipFile(io.BytesIO(raw_zip)) as z:
        return z.read(NAME[: -len(".zip")])


@pytest.fixture(scope="module")
def df(raw_zip: bytes) -> pd.DataFrame:
    return udiff.parse(raw_zip, source_file=SRC)


def make_zip(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in members.items():
            z.writestr(name, data)
    return buf.getvalue()


def one(frame: pd.DataFrame, symbol: str, series: str = "EQ") -> pd.Series:
    rows = frame[(frame["symbol"] == symbol) & (frame["series"] == series)]
    assert len(rows) == 1
    return rows.iloc[0]


# -- names -----------------------------------------------------------------------------


def test_url_and_raw_name():
    assert udiff.url_for(DAY) == (
        "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_20261109_F_0000.csv.zip"
    )
    assert udiff.raw_name(DAY) == NAME
    assert udiff.SOURCE == "nse_udiff_bhavcopy"


# -- parsing: values ----------------------------------------------------------------------


def test_known_values_monq50(df):
    row = one(df, "MONQ50")
    assert row["date"] == date(2026, 11, 9)
    assert row["isin"] == "INF247L01AU3"
    assert row["name"] == "MOTILALAMC - MONQ50"
    assert row["fin_instrm_id"] == 7422
    assert row["open"] == pytest.approx(237.69)
    assert row["high"] == pytest.approx(239.74)
    assert row["low"] == pytest.approx(228.25)
    assert row["close"] == pytest.approx(229.67)
    assert row["last"] == pytest.approx(231.76)
    assert row["prev_close"] == pytest.approx(231.90)
    assert row["settle_price"] == pytest.approx(229.66)
    assert row["volume"] == 274573
    assert row["value"] == pytest.approx(63627950.17)
    assert row["trades"] == 8468


def test_same_symbol_in_several_series_is_kept(df):
    assert set(df.loc[df["symbol"] == "IMC1", "series"]) == {"N1", "N2", "N3"}
    cohance = df[df["symbol"] == "COHANCE"]
    assert set(cohance["series"]) == {"BL", "EQ"}
    assert cohance["isin"].nunique() == 1  # one ISIN, two series


def test_first_and_last_rows_kept(df):
    assert df["symbol"].iloc[0] == "SGBJUN28"
    assert df["series"].iloc[0] == "GB"


# -- parsing: shape, dtypes, lineage ------------------------------------------------------------


def test_columns_and_dtypes(df):
    assert tuple(df.columns) == udiff.OUTPUT_COLUMNS
    for name in ("symbol", "series", "isin", "name", "source_file"):
        assert df[name].dtype == "string"
    for name in ("fin_instrm_id", "volume", "trades"):
        assert df[name].dtype == "Int64"
    for name in ("open", "high", "low", "close", "last", "prev_close", "settle_price", "value"):
        assert df[name].dtype == "float64"
    assert df["date"].dtype == object
    assert all(type(d) is date for d in df["date"])


def test_source_file_on_every_row(df):
    assert (df["source_file"] == SRC).all()


def test_no_duplicate_symbol_series(df):
    assert not df.duplicated(["symbol", "series"]).any()


def test_returns_every_data_row(inner_csv, df):
    assert len(df) == len([x for x in inner_csv.decode().splitlines() if x.strip()]) - 1


# -- parsing: malformed input ------------------------------------------------------------------


def test_not_a_zip_raises():
    with pytest.raises(ValueError, match="nse_udiff_bhavcopy: not a zip"):
        udiff.parse(b"<html>Access Denied</html>", source_file=SRC)


def test_empty_zip_raises():
    with pytest.raises(ValueError, match="empty"):
        udiff.parse(make_zip({}), source_file=SRC)


def test_two_csvs_raise(inner_csv):
    both = make_zip({"a.csv": inner_csv, "b.csv": inner_csv})
    with pytest.raises(ValueError, match="exactly one CSV"):
        udiff.parse(both, source_file=SRC)


def test_zip_without_csv_raises():
    with pytest.raises(ValueError, match="exactly one CSV"):
        udiff.parse(make_zip({"readme.txt": b"hello"}), source_file=SRC)


def test_missing_column_raises(inner_csv):
    broken = make_zip({"x.csv": inner_csv.replace(b"ClsPric", b"Close", 1)})
    with pytest.raises(ValueError, match=r"nse_udiff_bhavcopy.*ClsPric"):
        udiff.parse(broken, source_file=SRC)


def test_wrong_field_count_names_the_line(inner_csv):
    broken = make_zip({"x.csv": inner_csv + b"2026-11-09,2026-11-09,CM\n"})
    last = len(inner_csv.decode().splitlines()) + 1
    with pytest.raises(ValueError, match=rf"nse_udiff_bhavcopy: line {last}: expected 34"):
        udiff.parse(broken, source_file=SRC)


def test_non_numeric_price_raises(inner_csv):
    broken = make_zip({"x.csv": inner_csv.replace(b"229.67", b"n/a", 1)})
    with pytest.raises(ValueError, match=r"column ClsPric: 'n/a'"):
        udiff.parse(broken, source_file=SRC)


def test_bad_date_raises(inner_csv):
    broken = make_zip({"x.csv": inner_csv.replace(b"2026-11-09", b"09-Nov-2026", 1)})
    with pytest.raises(ValueError, match="TradDt"):
        udiff.parse(broken, source_file=SRC)


def test_header_only_raises(inner_csv):
    header = inner_csv.split(b"\n", 1)[0] + b"\n"
    with pytest.raises(ValueError, match="no data rows"):
        udiff.parse(make_zip({"x.csv": header}), source_file=SRC)


# -- fetch ------------------------------------------------------------------------------------


def make_client(handler) -> PoliteClient:
    return PoliteClient(transport=httpx.MockTransport(handler), sleep=lambda s: None)


def test_fetch_stores_untouched_bytes(tmp_path, raw_zip):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=raw_zip)

    store = RawStore(tmp_path / "raw")
    record = udiff.fetch(make_client(handler), store, DAY)

    assert len(seen) == 1
    assert str(seen[0].url) == udiff.url_for(DAY)
    assert seen[0].headers["accept"] == "*/*"
    assert seen[0].headers["referer"] == "https://www.nseindia.com/"
    assert record.relpath == SRC
    assert record.path.read_bytes() == raw_zip


def test_fetch_404_propagates_and_stores_nothing(tmp_path):
    store = RawStore(tmp_path / "raw")
    client = make_client(lambda request: httpx.Response(404))
    with pytest.raises(SourceNotFoundError):
        udiff.fetch(client, store, DAY)
    assert not store.root.exists()
