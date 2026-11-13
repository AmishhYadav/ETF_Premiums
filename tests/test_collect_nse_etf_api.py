"""Tests for the NSE ETF live-API collector. Parsing uses a real saved response; no network."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pandas as pd
import pytest

from etfp.collect import nse_etf_api as api
from etfp.http import PoliteClient, SourceBlockedError, SourceError, SourceNotFoundError
from etfp.rawstore import RawStore
from etfp.timeutil import IST

FIXTURE = Path(__file__).parent / "fixtures" / "nse_etf_api" / "api_etf.json"
SRC = "nse_etf_api/2026-11-09/etf_160000.json"
FETCHED = datetime(2026, 11, 9, 16, 0, 5, tzinfo=IST)


@pytest.fixture(scope="module")
def raw() -> bytes:
    return FIXTURE.read_bytes()


@pytest.fixture(scope="module")
def df(raw: bytes) -> pd.DataFrame:
    return api.parse(raw, source_file=SRC, fetched_at=FETCHED)


def row(frame: pd.DataFrame, symbol: str) -> pd.Series:
    rows = frame[frame["symbol"] == symbol]
    assert len(rows) == 1
    return rows.iloc[0]


def body_with(raw: bytes, edit) -> bytes:
    data = json.loads(raw)
    edit(data)
    return json.dumps(data).encode()


# -- names -----------------------------------------------------------------------------


def test_urls_and_source():
    assert api.PAGE_URL == "https://www.nseindia.com/market-data/exchange-traded-funds-etf"
    assert api.API_URL == "https://www.nseindia.com/api/etf"
    assert api.SOURCE == "nse_etf_api"


def test_raw_name_uses_indian_time():
    assert api.raw_name(datetime(2026, 11, 9, 9, 15, 3, tzinfo=IST)) == "etf_091503.json"
    # 03:45:03 UTC is 09:15:03 IST
    assert api.raw_name(datetime(2026, 11, 9, 3, 45, 3, tzinfo=UTC)) == "etf_091503.json"
    with pytest.raises(ValueError, match="timezone-aware"):
        api.raw_name(datetime(2026, 11, 9, 9, 15, 3))  # noqa: DTZ001 - naive on purpose


# -- parsing: values ----------------------------------------------------------------------


def test_snapshot_level_fields(df):
    assert (df["api_ts"] == pd.Timestamp("2026-11-09 16:00:00", tz="Asia/Kolkata")).all()
    assert df["api_ts"].iloc[0].isoformat() == "2026-11-09T16:00:00+05:30"
    assert (df["fetched_at"] == pd.Timestamp(FETCHED)).all()
    assert (df["nav_date"] == date(2026, 11, 8)).all()
    assert (df["market_status"] == "Close").all()


def test_monq50_values(df):
    r = row(df, "MONQ50")
    assert r["series"] == "EQ"
    assert r["underlying"] == "Nasdaq Q-50 Total Return Index"
    assert r["open"] == pytest.approx(237.69)
    assert r["high"] == pytest.approx(239.74)
    assert r["low"] == pytest.approx(228.25)
    assert r["last_price"] == pytest.approx(231.76)
    assert r["prev_close"] == pytest.approx(231.9)
    assert r["change"] == pytest.approx(-0.14)
    assert r["pct_change"] == pytest.approx(-0.06)
    assert r["volume"] == 274573
    assert r["value"] == pytest.approx(63626801.29)
    assert r["nav_prev"] == pytest.approx(110.8852)  # previous published NAV, not live iNAV


def test_null_nav_is_missing_not_zero(df):
    for symbol in ("LICMFGOLD", "HDFCLIQUID", "INSURADD", "MID150"):
        assert pd.isna(row(df, symbol)["nav_prev"])


def test_duplicate_symbol_entries_are_both_kept(df):
    # the real response lists NXT100CASE twice; the second entry has all-zero prices
    twice = df[df["symbol"] == "NXT100CASE"]
    assert len(twice) == 2
    assert sorted(twice["volume"].tolist()) == [0, 49347]


def test_rows_in_response_order(df):
    assert df["symbol"].tolist()[:3] == ["GOLDBEES", "SILVERBEES", "NIFTYBEES"]


def test_fetched_at_missing_when_not_given(raw):
    out = api.parse(raw, source_file=SRC)
    assert out["fetched_at"].isna().all()
    assert str(out["fetched_at"].dtype) == "datetime64[ns, Asia/Kolkata]"


def test_fetched_at_is_converted_to_indian_time(raw):
    utc = datetime(2026, 11, 9, 10, 30, 5, tzinfo=UTC)
    out = api.parse(raw, source_file=SRC, fetched_at=utc)
    assert out["fetched_at"].iloc[0].isoformat() == "2026-11-09T16:00:05+05:30"


def test_numbers_with_commas_dashes_and_json_numbers(raw):
    def edit(data):
        item = data["data"][0]
        item.update(qty="1,234", trdVal="-", ltP=117.5, chn="-", per=None)

    out = api.parse(body_with(raw, edit), source_file=SRC)
    r = out.iloc[0]
    assert r["volume"] == 1234
    assert pd.isna(r["value"]) and pd.isna(r["change"]) and pd.isna(r["pct_change"])
    assert r["last_price"] == pytest.approx(117.5)


# -- parsing: shape, dtypes, lineage ------------------------------------------------------------


def test_columns_and_dtypes(df):
    assert tuple(df.columns) == api.OUTPUT_COLUMNS
    for name in ("api_ts", "fetched_at"):
        assert str(df[name].dtype) == "datetime64[ns, Asia/Kolkata]"
    assert df["nav_date"].dtype == object
    assert all(type(d) is date for d in df["nav_date"])
    for name in ("market_status", "symbol", "series", "underlying", "source_file"):
        assert df[name].dtype == "string"
    assert df["volume"].dtype == "Int64"
    for name in (
        "open",
        "high",
        "low",
        "last_price",
        "prev_close",
        "change",
        "pct_change",
        "value",
        "nav_prev",
    ):
        assert df[name].dtype == "float64"


def test_source_file_on_every_row(df):
    assert (df["source_file"] == SRC).all()


def test_returns_every_item(raw, df):
    assert len(df) == len(json.loads(raw)["data"]) == 17


# -- parsing: malformed input ------------------------------------------------------------------


def test_html_page_raises():
    with pytest.raises(ValueError, match="nse_etf_api: body is not JSON"):
        api.parse(b"<html><body>Access Denied</body></html>", source_file=SRC)


def test_json_that_is_not_an_object_raises():
    with pytest.raises(ValueError, match="not an object"):
        api.parse(b"[1, 2, 3]", source_file=SRC)


@pytest.mark.parametrize("key", ["timestamp", "data", "navDate", "marketStatus"])
def test_missing_top_level_key_raises(raw, key):
    def edit(data):
        del data[key]

    with pytest.raises(ValueError, match=rf"nse_etf_api.*{key}"):
        api.parse(body_with(raw, edit), source_file=SRC)


def test_data_not_a_list_or_empty_raises(raw):
    with pytest.raises(ValueError, match="not a list"):
        api.parse(body_with(raw, lambda d: d.update(data={})), source_file=SRC)
    with pytest.raises(ValueError, match="empty"):
        api.parse(body_with(raw, lambda d: d.update(data=[])), source_file=SRC)


def test_item_missing_field_names_the_item(raw):
    with pytest.raises(ValueError, match=r"data\[0\] is missing field\(s\) \['ltP'\]"):
        api.parse(body_with(raw, lambda d: d["data"][0].pop("ltP")), source_file=SRC)


def test_bad_number_raises(raw):
    with pytest.raises(ValueError, match=r"data\[0\]\.ltP"):
        api.parse(body_with(raw, lambda d: d["data"][0].update(ltP="n/a")), source_file=SRC)
    with pytest.raises(ValueError, match=r"data\[0\]\.qty"):
        api.parse(body_with(raw, lambda d: d["data"][0].update(qty="1.5")), source_file=SRC)


def test_bad_timestamp_raises(raw):
    with pytest.raises(ValueError, match="timestamp"):
        api.parse(body_with(raw, lambda d: d.update(timestamp="2026-11-09T16:00")), source_file=SRC)
    with pytest.raises(ValueError, match="no time of day"):
        api.parse(body_with(raw, lambda d: d.update(timestamp="09-Nov-2026")), source_file=SRC)


def test_naive_fetched_at_raises(raw):
    with pytest.raises(ValueError, match="timezone-aware"):
        api.parse(raw, source_file=SRC, fetched_at=datetime(2026, 11, 9, 16, 0))  # noqa: DTZ001


# -- fetch_snapshot -----------------------------------------------------------------------------


class Server:
    """A fake NSE: records every request, answers the page and the API."""

    def __init__(self, api_response: httpx.Response) -> None:
        self.api_response = api_response
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if str(request.url) == api.PAGE_URL:
            return httpx.Response(
                200, text="<html>etf page</html>", headers={"set-cookie": "a=b; Path=/"}
            )
        return self.api_response


@pytest.fixture
def fixed_clock(monkeypatch):
    monkeypatch.setattr("etfp.http.now_ist", lambda: FETCHED)


def make_client(server: Server) -> PoliteClient:
    return PoliteClient(transport=httpx.MockTransport(server), sleep=lambda s: None)


def test_snapshot_warms_page_then_calls_api(tmp_path, raw, fixed_clock):
    server = Server(httpx.Response(200, content=raw))
    store = RawStore(tmp_path / "raw")
    record = api.fetch_snapshot(make_client(server), store)

    assert [str(r.url) for r in server.requests] == [api.PAGE_URL, api.API_URL]
    page, call = server.requests
    assert page.headers["accept"].startswith("text/html")
    assert call.headers["accept"] == "*/*"
    assert call.headers["referer"] == api.PAGE_URL
    assert call.headers["cookie"] == "a=b"  # the same client carries the cookie over

    assert record.relpath == "nse_etf_api/2026-11-09/etf_160005.json"
    assert record.path.read_bytes() == raw  # exactly the API body
    assert [p.name for p in (tmp_path / "raw").rglob("*") if p.is_file()] == [
        "etf_160005.json",
        "_manifest.jsonl",
    ] or sorted(p.name for p in (tmp_path / "raw").rglob("*") if p.is_file()) == [
        "_manifest.jsonl",
        "etf_160005.json",
    ]  # the warm-up page was not stored


def test_snapshot_without_warming_skips_the_page(tmp_path, raw, fixed_clock):
    server = Server(httpx.Response(200, content=raw))
    api.fetch_snapshot(make_client(server), RawStore(tmp_path / "raw"), warm=False)
    assert [str(r.url) for r in server.requests] == [api.API_URL]


def test_snapshot_then_parse_roundtrip(tmp_path, raw, df, fixed_clock):
    server = Server(httpx.Response(200, content=raw))
    record = api.fetch_snapshot(make_client(server), RawStore(tmp_path / "raw"))
    parsed = api.parse(
        record.path.read_bytes(), source_file=record.relpath, fetched_at=record.fetched_at
    )
    assert len(parsed) == len(df)
    assert (parsed["source_file"] == record.relpath).all()


def test_html_body_raises_and_stores_nothing(tmp_path, fixed_clock):
    html = b"<html><body>Access Denied - bot detected</body></html>"
    server = Server(httpx.Response(200, content=html))
    store = RawStore(tmp_path / "raw")
    with pytest.raises(SourceError, match=r"nse_etf_api for 2026-11-09.*not the expected JSON"):
        api.fetch_snapshot(make_client(server), store)
    assert not store.root.exists()


def test_json_without_data_list_raises_and_stores_nothing(tmp_path, fixed_clock):
    server = Server(httpx.Response(200, content=b'{"message": "Resource not found"}'))
    store = RawStore(tmp_path / "raw")
    with pytest.raises(SourceError, match="data"):
        api.fetch_snapshot(make_client(server), store)
    assert not store.root.exists()


def test_api_404_and_403_propagate(tmp_path, fixed_clock):
    store = RawStore(tmp_path / "raw")
    with pytest.raises(SourceNotFoundError):
        api.fetch_snapshot(make_client(Server(httpx.Response(404))), store)
    with pytest.raises(SourceBlockedError):
        api.fetch_snapshot(make_client(Server(httpx.Response(403))), store)
    assert not store.root.exists()
