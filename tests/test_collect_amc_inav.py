"""Tests for the fund-house iNAV collector. No network: httpx.MockTransport and saved samples."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import httpx
import pandas as pd
import pytest

from etfp.collect import amc_inav
from etfp.http import PoliteClient, SourceError
from etfp.rawstore import RawStore
from etfp.timeutil import IST, UTC

FIXTURES = Path(__file__).parent / "fixtures" / "amc_inav"
FILES = {
    "motilal": FIXTURES / "motilal_GetINAVandPrice.json",
    "mirae": FIXTURES / "mirae_ticker.json",
    "nippon": FIXTURES / "nippon_DetailsFill.json",
}
FETCHED = datetime(2026, 11, 10, 15, 50, 3, tzinfo=IST)


def load(amc: str) -> pd.DataFrame:
    return amc_inav.parse(
        FILES[amc].read_bytes(), amc=amc, source_file=f"src/{amc}.json", fetched_at=FETCHED
    )


def row(df: pd.DataFrame, symbol: str) -> pd.Series:
    hits = df[df["symbol"] == symbol]
    assert len(hits) == 1, f"{symbol}: {len(hits)} rows"
    return hits.iloc[0]


# -- parse ---------------------------------------------------------------


@pytest.mark.parametrize("amc", amc_inav.AMCS)
def test_columns_dtypes_and_lineage(amc: str) -> None:
    df = load(amc)
    assert list(df.columns) == amc_inav.OUTPUT_COLUMNS
    for col in ("amc", "symbol", "amc_name", "amc_ts_raw", "source_file"):
        assert isinstance(df[col].dtype, pd.StringDtype), col
    assert df["inav"].dtype == "float64"
    assert df["prev_nav"].dtype == "float64"
    assert isinstance(df["fetched_at"].dtype, pd.DatetimeTZDtype)
    assert (df["fetched_at"] == pd.Timestamp(FETCHED)).all()
    assert df["fetched_at"].iloc[0].utcoffset().total_seconds() == 5.5 * 3600
    assert (df["amc"] == amc).all()
    assert (df["source_file"] == f"src/{amc}.json").all()


def test_motilal_known_values_and_non_inav_rows_dropped() -> None:
    df = load("motilal")
    mq = row(df, "MONQ50")
    assert mq["inav"] == 110.67813237368985
    assert mq["prev_nav"] == 110.8852
    assert mq["amc_name"] == "MONQ50 iNAV"
    assert mq["amc_ts_raw"] == "11/10/2026 15:47:00"
    assert row(df, "MON100")["inav"] == 247.1797156700835
    # The two plain end-of-day rows (229.67 / 298.59) are not iNAV and are gone.
    assert df["amc_name"].str.endswith(" iNAV").all()
    assert 229.67 not in set(df["inav"])
    assert 298.59 not in set(df["inav"])
    assert len(df) == 34  # 32 in m50M100Data + 2 iNAV rows from n100Data


def test_mirae_known_values_and_mixed_number_types() -> None:
    df = load("mirae")
    assert len(df) == 41
    fang = row(df, "MAFANG")
    assert fang["inav"] == 155.0547  # a JSON string in the response
    assert fang["prev_nav"] == 159.5331
    assert row(df, "MAHKTECH")["inav"] == 16.2961
    assert row(df, "MASPTOP50")["inav"] == 62.8733
    assert row(df, "CONSUMER")["inav"] == pytest.approx(12.089038583333)  # a JSON number
    assert row(df, "LIQUID")["inav"] == 1000.0  # a JSON integer
    # both timestamp formats are kept as raw text
    stamps = set(df["amc_ts_raw"])
    assert "10-Nov-2026 15:55:15" in stamps
    assert "2026-11-09T10:29:59.052Z" in stamps


def test_nippon_known_values_and_symbol_mapping() -> None:
    df = load("nippon")
    assert len(df) == 23
    nifty = row(df, "NIFTYBEES")
    assert nifty["inav"] == 250.7151
    assert nifty["prev_nav"] == 252.2389
    assert nifty["amc_name"] == "Nippon India ETF Nifty 50 BeES"
    assert row(df, "HNGSNGBEES")["inav"] == 325.2129
    assert row(df, "GOLDBEES")["inav"] == 76.0340
    assert row(df, "BANKBEES")["inav"] == 515.1710
    assert df["symbol"].notna().sum() == 4  # everything else stays NA, no guessing
    assert df.loc[df["amc_name"] == "CPSE ETF", "symbol"].isna().all()


def test_non_numeric_and_missing_values_become_na() -> None:
    body = {
        "RVDetailsList": [
            {"SchName": "Nippon India ETF Nifty 50 BeES", "CNav": "-", "PNav": None},
            {"SchName": "Nippon India ETF Gold BeES", "CNav": " 76.5 ", "PNav": "NaN"},
            {"SchName": "Other"},
        ]
    }
    df = amc_inav.parse(
        json.dumps(body).encode(), amc="nippon", source_file="f", fetched_at=FETCHED
    )
    assert df["inav"].isna().tolist() == [True, False, True]
    assert df["inav"].iloc[1] == 76.5
    assert df["prev_nav"].isna().all()
    assert df["amc_ts_raw"].isna().all()


def test_parse_empty_and_unknown_amc_and_naive_time() -> None:
    empty = amc_inav.parse(
        b'{"RVDetailsList": []}', amc="nippon", source_file="f", fetched_at=FETCHED
    )
    assert len(empty) == 0
    assert list(empty.columns) == amc_inav.OUTPUT_COLUMNS
    assert isinstance(empty["fetched_at"].dtype, pd.DatetimeTZDtype)
    with pytest.raises(ValueError, match="unknown fund house"):
        amc_inav.parse(b"[]", amc="nope", source_file="f", fetched_at=FETCHED)
    with pytest.raises(ValueError, match="timezone-aware"):
        amc_inav.parse(
            b"[]",
            amc="mirae",
            source_file="f",
            fetched_at=datetime(2026, 11, 10, 15, 50),  # noqa: DTZ001 (testing the guard)
        )


# -- names ---------------------------------------------------------------


def test_source_names() -> None:
    assert [amc_inav.source_name(a) for a in amc_inav.AMCS] == [
        "amc_inav_motilal",
        "amc_inav_mirae",
        "amc_inav_nippon",
    ]
    with pytest.raises(ValueError):
        amc_inav.source_name("other")


def test_raw_name_uses_ist_clock_and_rejects_naive() -> None:
    assert amc_inav.raw_name(datetime(2026, 11, 11, 9, 15, 3, tzinfo=IST)) == "inav_091503.json"
    # 03:45:03 UTC is 09:15:03 IST
    assert amc_inav.raw_name(datetime(2026, 11, 11, 3, 45, 3, tzinfo=UTC)) == "inav_091503.json"
    with pytest.raises(ValueError, match="timezone-aware"):
        amc_inav.raw_name(datetime(2026, 11, 11, 9, 15, 3))  # noqa: DTZ001


# -- fetch ---------------------------------------------------------------


def make_client(handler) -> PoliteClient:
    return PoliteClient(
        transport=httpx.MockTransport(handler),
        sleep=lambda _s: None,
        default_interval_s=0.0,
        max_retries=0,
    )


def serve(amc: str, requests: list[httpx.Request]):
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=FILES[amc].read_bytes())

    return handler


def test_motilal_request_and_exact_bytes_stored(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []
    store = RawStore(tmp_path / "raw")
    with make_client(serve("motilal", requests)) as client:
        rec = amc_inav.fetch_snapshot(client, store, "motilal")
    (req,) = requests
    assert req.method == "POST"
    assert str(req.url) == "https://www.motilaloswalmf.com/mutualfund/api/v1/someFunc"
    assert json.loads(req.content) == {"apiName": "GetINAVandPrice"}
    assert req.headers["content-type"] == "application/json"
    assert req.headers["accept"] == "application/json, text/plain, */*"
    assert req.headers["origin"] == "https://www.motilaloswalmf.com"
    assert req.headers["referer"] == "https://www.motilaloswalmf.com/inav"
    assert_stored(rec, tmp_path, "motilal")


def test_mirae_request_and_exact_bytes_stored(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []
    store = RawStore(tmp_path / "raw")
    with make_client(serve("mirae", requests)) as client:
        rec = amc_inav.fetch_snapshot(client, store, "mirae")
    (req,) = requests
    assert req.method == "GET"
    assert str(req.url) == "https://miraeassetetf.co.in/api/ticker"
    assert req.headers["accept"] == "application/json, text/plain, */*"
    assert req.headers["referer"] == "https://miraeassetetf.co.in/inav-baskets?tab=inav"
    assert_stored(rec, tmp_path, "mirae")


def test_nippon_request_has_empty_body_with_content_length_zero(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []
    store = RawStore(tmp_path / "raw")
    with make_client(serve("nippon", requests)) as client:
        rec = amc_inav.fetch_snapshot(client, store, "nippon")
    (req,) = requests
    assert req.method == "POST"
    assert str(req.url) == "https://investeasy.nipponindiaim.com/Online/Realtime/DetailsFill"
    assert req.content == b""
    assert req.headers["content-length"] == "0"
    assert req.headers["content-type"] == "application/json"
    assert req.headers["origin"] == "https://investeasy.nipponindiaim.com"
    assert req.headers["referer"] == "https://investeasy.nipponindiaim.com/online/realtime/nav"
    assert req.headers["x-requested-with"] == "XMLHttpRequest"
    assert_stored(rec, tmp_path, "nippon")


def assert_stored(rec, tmp_path: Path, amc: str) -> None:
    source = f"amc_inav_{amc}"
    assert rec.source == source
    ist_day = rec.fetched_at.astimezone(IST).date().isoformat()
    assert rec.relpath == f"{source}/{ist_day}/{amc_inav.raw_name(rec.fetched_at)}"
    assert rec.name.startswith("inav_")
    assert rec.name.endswith(".json")
    assert rec.path == tmp_path / "raw" / rec.relpath
    assert rec.path.read_bytes() == FILES[amc].read_bytes()  # byte for byte
    # and it parses straight from the stored file
    df = amc_inav.parse(
        rec.path.read_bytes(), amc=amc, source_file=rec.relpath, fetched_at=rec.fetched_at
    )
    assert len(df) > 0


@pytest.mark.parametrize(
    ("amc", "body", "expected_type"),
    [
        ("motilal", b"<html><body>Access denied</body></html>", "not JSON"),
        ("motilal", b'{"data": {"data": {"m50M100Data": []}}}', "object"),
        ("motilal", b"[1, 2]", "array"),
        ("mirae", b"<html>Just a moment...</html>", "not JSON"),
        ("mirae", b'{"error": "nope"}', "object"),
        ("mirae", b'[{"ETF": "x"}]', "array"),
        ("nippon", b"<html>blocked</html>", "not JSON"),
        ("nippon", b'{"RVDetailsList": null}', "object"),
        ("nippon", b"[]", "array"),
    ],
)
def test_bad_body_raises_source_error_and_stores_nothing(
    tmp_path: Path, amc: str, body: bytes, expected_type: str
) -> None:
    store = RawStore(tmp_path / "raw")
    handler = lambda request: httpx.Response(200, content=body)  # noqa: E731
    with make_client(handler) as client, pytest.raises(SourceError) as info:
        amc_inav.fetch_snapshot(client, store, amc)
    message = str(info.value)
    assert f"amc_inav_{amc}" in message
    assert "format changed" in message
    assert f"content length {len(body)} bytes" in message
    assert f"first JSON type seen: {expected_type}" in message
    assert body.decode() not in message  # never echo the body
    assert info.value.source == f"amc_inav_{amc}"
    assert info.value.day is not None
    assert info.value.day.isoformat() in message
    assert not (tmp_path / "raw").exists() or not list((tmp_path / "raw").rglob("*.json"))
    assert list(store.iter_manifest()) == []


def test_unknown_amc_in_fetch(tmp_path: Path) -> None:
    with make_client(lambda r: httpx.Response(200)) as client, pytest.raises(ValueError):
        amc_inav.fetch_snapshot(client, RawStore(tmp_path), "other")
