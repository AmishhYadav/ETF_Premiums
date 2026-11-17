"""Tests for the Yahoo intraday collector (interim free fair value, D13).

The fixtures are synthetic (see tests/fixtures/yahoo_intraday/README.md). Fetching uses
httpx.MockTransport: no network.

Time facts used below (November 2026, New York on standard time, UTC-5):
  16:00 New York = 21:00 UTC.   15:30 India (UTC+5:30) = 10:00 UTC.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pandas as pd
import pytest

from etfp.collect import yahoo_intraday as yi
from etfp.http import PoliteClient, SourceBlockedError, SourceError
from etfp.rawstore import RawStore
from etfp.timeutil import IST, NEW_YORK

FIXTURES = Path(__file__).parent / "fixtures" / "yahoo_intraday"
NQ = (FIXTURES / "NQ_F_5m.json").read_bytes()
FX = (FIXTURES / "USDINR_X_5m.json").read_bytes()
ERR = (FIXTURES / "error_not_found.json").read_bytes()
DAY = date(2026, 11, 13)  # Friday, an NSE and NYSE session
NOW = datetime(2026, 11, 13, 20, 0, tzinfo=IST)


@pytest.fixture(scope="module")
def nq():
    return yi.parse(NQ, source_file="yahoo_intraday/2026-11-13/NQ_F_5m.json")


@pytest.fixture(scope="module")
def fx():
    return yi.parse(FX, source_file="yahoo_intraday/2026-11-13/USDINR_X_5m.json")


def utc(day: int, hh: int, mm: int) -> datetime:
    return datetime(2026, 11, day, hh, mm, tzinfo=UTC)


# -- names / window -------------------------------------------------------------------


def test_raw_names_and_symbol_list():
    assert yi.raw_name("NQ=F") == "NQ_F_5m.json"
    assert yi.raw_name("USDINR=X", "1m") == "USDINR_X_1m.json"
    assert yi.SYMBOLS == ("NQ=F", "ES=F", "USDINR=X", "HKDINR=X")
    assert yi.raw_names(DAY) == [
        "NQ_F_5m.json",
        "ES_F_5m.json",
        "USDINR_X_5m.json",
        "HKDINR_X_5m.json",
    ]


def test_window_reaches_back_over_a_long_weekend_and_forward_a_day():
    start, end = yi.window(DAY)
    assert start == utc(8, 0, 0)  # Sunday 8 Nov, 5 days before Friday 13 Nov
    assert end == utc(15, 0, 0)  # Sunday 15 Nov 00:00 UTC
    assert start.tzinfo is not None and end.tzinfo is not None


# -- parsing --------------------------------------------------------------------------


def test_parse_columns_dtypes_and_lineage(nq):
    assert tuple(nq.columns) == yi.OUTPUT_COLUMNS
    assert set(nq["symbol"]) == {"NQ=F"}
    assert set(nq["interval_minutes"]) == {5}
    assert set(nq["source_file"]) == {"yahoo_intraday/2026-11-13/NQ_F_5m.json"}
    assert isinstance(nq["ts"].dtype, pd.DatetimeTZDtype)  # timezone-aware, never naive
    assert str(nq["ts"].dt.tz) == "UTC"
    assert nq["ts"].is_monotonic_increasing and nq["ts"].is_unique


def test_parse_drops_null_bars_and_counts_them(nq, fx):
    assert nq.attrs["null_bars"] == 1 and fx.attrs["null_bars"] == 1
    assert utc(13, 3, 15) not in set(nq["ts"].dt.to_pydatetime())
    assert utc(13, 5, 0) not in set(fx["ts"].dt.to_pydatetime())
    assert nq["close"].notna().all()


def test_parse_keeps_the_cme_break_as_a_gap(nq):
    times = set(nq["ts"].dt.to_pydatetime())
    assert utc(12, 21, 55) in times and utc(12, 23, 0) in times
    assert utc(12, 22, 0) not in times and utc(12, 22, 55) not in times


def test_parse_bar_count_matches_hand_count(nq):
    # 14:30 UTC on 12 Nov to 10:30 UTC on 13 Nov inclusive = 20h x 12 + 1 = 241 slots,
    # minus 12 slots of the 22:00-23:00 break, minus 1 null bar = 228.
    assert len(nq) == 241 - 12 - 1


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"<html>blocked</html>", "not JSON"),
        (b"[]", 'without a "chart"'),
        (ERR, "Not Found"),
        (b'{"chart":{"result":[],"error":null}}', 'no "result"'),
    ],
)
def test_parse_rejects_non_chart_bodies(content: bytes, message: str):
    with pytest.raises(ValueError, match=message):
        yi.parse(content, source_file="f.json")


def _edit(mutator) -> bytes:
    doc = json.loads(NQ)
    mutator(doc["chart"]["result"][0])
    return json.dumps(doc).encode()


def test_parse_rejects_misaligned_arrays():
    bad = _edit(lambda r: r["indicators"]["quote"][0]["close"].pop())
    with pytest.raises(ValueError, match="'close' is missing or not as long"):
        yi.parse(bad, source_file="f.json")


def test_parse_rejects_duplicate_timestamps():
    def dup(r):
        r["timestamp"][1] = r["timestamp"][0]

    with pytest.raises(ValueError, match="repeated bar timestamps"):
        yi.parse(_edit(dup), source_file="f.json")


def test_parse_sorts_out_of_order_bars():
    def swap(r):
        r["timestamp"][0], r["timestamp"][1] = r["timestamp"][1], r["timestamp"][0]
        for col in r["indicators"]["quote"][0].values():
            col[0], col[1] = col[1], col[0]

    frame = yi.parse(_edit(swap), source_file="f.json")
    assert frame["ts"].is_monotonic_increasing


def test_parse_requires_symbol_and_granularity():
    bad = _edit(lambda r: r["meta"].pop("dataGranularity"))
    with pytest.raises(ValueError, match="dataGranularity"):
        yi.parse(bad, source_file="f.json")


# -- value_at: the heart of the module ------------------------------------------------


def test_indian_close_picks_the_bar_that_ENDED_at_1530_not_the_one_that_starts_then(nq):
    at = yi.indian_close(DAY)
    assert at == datetime(2026, 11, 13, 15, 30, tzinfo=IST) == utc(13, 10, 0)
    got = yi.value_at(nq, at)
    assert got is not None
    # Bar stamped 09:55 UTC (15:25 IST) covers 15:25-15:30 and has close 21000.50.
    assert got.close == 21000.50
    assert got.bar_start == utc(13, 9, 55) and got.bar_end == utc(13, 10, 0)
    assert got.stale == timedelta(0)
    # The bar stamped 10:00 UTC (21010.25) lies after 15:30 and must NOT be used.
    assert got.close != 21010.25


def test_us_close_is_1600_new_york_and_picks_the_last_bar_before_it(nq):
    at = yi.us_close(date(2026, 11, 12))
    assert at == datetime(2026, 11, 12, 16, 0, tzinfo=NEW_YORK) == utc(12, 21, 0)
    got = yi.value_at(nq, at)
    assert got is not None and got.close == 21100.00  # bar 20:55 UTC; 21101.75 is after
    assert got.bar_end == utc(12, 21, 0)


def test_fx_values_at_the_same_two_moments(fx):
    assert yi.value_at(fx, yi.us_close(date(2026, 11, 12))).close == 83.4125
    assert yi.value_at(fx, yi.indian_close(DAY)).close == 83.4550


def test_a_hand_computed_fair_value_move_from_the_fixture_values(nq, fx):
    """The fair-value ingredients for 13 Nov, worked by hand.

    Future moved 21000.50 / 21100.00 - 1 = -0.47156% from the US close to the Indian close;
    the dollar moved 83.4550 / 83.4125 - 1 = +0.05095%. Combined rupee move of the
    holdings: (1 - 0.0047156) x (1 + 0.0005095) - 1 = -0.4209% (about).
    """
    f0 = yi.value_at(nq, yi.us_close(date(2026, 11, 12))).close
    f1 = yi.value_at(nq, yi.indian_close(DAY)).close
    s0 = yi.value_at(fx, yi.us_close(date(2026, 11, 12))).close
    s1 = yi.value_at(fx, yi.indian_close(DAY)).close
    assert f1 / f0 - 1 == pytest.approx(-0.0047156, abs=1e-7)
    assert s1 / s0 - 1 == pytest.approx(0.0005095, abs=1e-7)
    assert (f1 / f0) * (s1 / s0) - 1 == pytest.approx(-0.0042085, abs=1e-6)


def test_moment_inside_a_null_bar_slot_falls_back_to_the_previous_bar(nq):
    # The bar stamped 03:15 UTC is null (dropped). At 03:20 UTC it would have been the newest
    # ended bar (staleness 0); instead the 03:10 bar, which ended at 03:15, is used.
    got = yi.value_at(nq, utc(13, 3, 20))
    assert got is not None and got.bar_end == utc(13, 3, 15)
    assert got.stale == timedelta(minutes=5)


def test_inside_the_futures_break_returns_none_when_data_is_too_old(nq):
    # Last bar before the break ends 22:00 UTC; at 22:45 UTC that is 45 min old > 30 min.
    assert yi.value_at(nq, utc(12, 22, 45)) is None
    # With a looser limit the stale value is returned, and says how stale it is.
    got = yi.value_at(nq, utc(12, 22, 45), max_stale=timedelta(hours=1))
    assert got is not None and got.stale == timedelta(minutes=45)


def test_before_the_first_bar_and_empty_frame_return_none(nq):
    assert yi.value_at(nq, utc(12, 14, 0)) is None
    assert yi.value_at(nq.iloc[0:0], utc(13, 10, 0)) is None


def test_naive_moment_is_rejected(nq):
    with pytest.raises(ValueError, match="timezone-aware"):
        yi.value_at(nq, datetime(2026, 11, 13, 15, 30))  # noqa: DTZ001


def test_value_at_filters_by_symbol_and_refuses_ambiguity(nq, fx):
    both = pd.concat([nq, fx], ignore_index=True)
    with pytest.raises(ValueError, match="several symbols"):
        yi.value_at(both, utc(13, 10, 0))
    assert yi.value_at(both, utc(13, 10, 0), symbol="USDINR=X").close == 83.4550


def test_value_at_works_with_a_one_minute_bar_size():
    doc = json.loads(NQ)
    result = doc["chart"]["result"][0]
    result["meta"]["dataGranularity"] = "1m"
    frame = yi.parse(json.dumps(doc).encode(), source_file="f.json")
    # A "1m" bar stamped 09:55 ends at 09:56, so at 10:00 it is 4 minutes old (not 0).
    got = yi.value_at(frame, utc(13, 10, 0))
    assert got is not None and got.bar_end == utc(13, 9, 56)
    assert got.stale == timedelta(minutes=4)


# -- fetching (mock transport) --------------------------------------------------------


def make_client(handler) -> PoliteClient:
    return PoliteClient(transport=httpx.MockTransport(handler), sleep=lambda s: None, max_retries=0)


def test_fetch_requests_the_window_and_saves_untouched_bytes(tmp_path: Path):
    seen: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        return httpx.Response(200, content=NQ)

    store = RawStore(tmp_path)
    with make_client(handler) as client:
        rec = yi.fetch(client, store, DAY, "NQ=F", now=NOW)
    assert rec.relpath == "yahoo_intraday/2026-11-13/NQ_F_5m.json"
    assert rec.path.read_bytes() == NQ
    url = seen[0]
    assert url.path.endswith("/v8/finance/chart/NQ=F")
    assert url.params["interval"] == "5m"
    assert int(url.params["period1"]) == int(utc(8, 0, 0).timestamp())
    assert int(url.params["period2"]) == int(utc(15, 0, 0).timestamp())


def test_fetch_refuses_days_older_than_yahoo_history_without_calling_out(tmp_path: Path):
    def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail("must not call Yahoo")

    store = RawStore(tmp_path)
    old = date(2026, 8, 1)  # > 60 days before NOW
    with (
        make_client(handler) as client,
        pytest.raises(yi.IntradayHistoryUnavailable, match="2026-08-01") as exc,
    ):
        yi.fetch(client, store, old, "NQ=F", now=NOW)
    assert exc.value.source == "yahoo_intraday"
    assert not list(tmp_path.rglob("*.json"))


def test_fetch_edge_of_the_history_limit(tmp_path: Path):
    ok_day = (NOW - timedelta(days=50)).date()
    with make_client(lambda r: httpx.Response(200, content=NQ)) as client:
        yi.fetch(client, RawStore(tmp_path), ok_day, "NQ=F", now=NOW)  # 50+5 days < 59: fine
        with pytest.raises(yi.IntradayHistoryUnavailable):
            yi.fetch(client, RawStore(tmp_path), (NOW - timedelta(days=55)).date(), "NQ=F", now=NOW)


def test_fetch_never_stores_a_json_error_body(tmp_path: Path):
    store = RawStore(tmp_path)
    with (
        make_client(lambda r: httpx.Response(200, content=ERR)) as client,
        pytest.raises(SourceError, match="Not Found") as exc,
    ):
        yi.fetch(client, store, DAY, "NOPE=F", now=NOW)
    assert exc.value.day == DAY and "not stored" in str(exc.value)
    assert not (tmp_path / "yahoo_intraday").exists()


def test_fetch_never_stores_an_html_block_page(tmp_path: Path):
    html = httpx.Response(200, content=b"<html>Too Many Requests</html>")
    with make_client(lambda r: html) as c, pytest.raises(SourceError, match="not JSON"):
        yi.fetch(c, RawStore(tmp_path), DAY, "NQ=F", now=NOW)


def test_http_429_is_a_block_and_is_not_retried(tmp_path: Path):
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(429)

    client = PoliteClient(
        transport=httpx.MockTransport(handler), sleep=lambda s: None, max_retries=3
    )
    with client, pytest.raises(SourceBlockedError, match="yahoo_intraday for 2026-11-13"):
        yi.fetch(client, RawStore(tmp_path), DAY, "NQ=F", now=NOW)
    assert len(calls) == 1


def test_fetch_rejects_naive_now_and_bad_interval(tmp_path: Path):
    with make_client(lambda r: httpx.Response(200, content=NQ)) as client:
        store = RawStore(tmp_path)
        with pytest.raises(ValueError, match="timezone-aware"):
            yi.fetch(client, store, DAY, "NQ=F", now=datetime(2026, 11, 13, 20, 0))  # noqa: DTZ001
        with pytest.raises(ValueError, match="interval"):
            yi.fetch(client, store, DAY, "NQ=F", interval="5min", now=NOW)


# -- history (60m backfill) mode and the value-at-a-moment table (D43) --------------


def test_history_request_ends_on_a_whole_hour_and_is_stored_under_the_ist_day(tmp_path: Path):
    """now = 18 Nov 2026 01:31 IST = 17 Nov 20:01 UTC. End = 20:00 UTC (the hour still in
    progress is not asked for); start = end - 728 days = 19 Nov 2024 20:00 UTC
    (17 Nov 2024 -> 17 Nov 2026 is 365 + 365 = 730 days, so 728 back is 19 Nov 2024)."""
    seen: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        return httpx.Response(200, content=NQ)

    now = datetime(2026, 11, 18, 1, 31, tzinfo=IST)
    with make_client(handler) as client:
        rec = yi.fetch_history(client, RawStore(tmp_path), "NQ=F", now=now)
    assert rec.relpath == "yahoo_intraday/2026-11-18/NQ_F_60m_history.json"
    assert rec.path.read_bytes() == NQ
    params = seen[0].params
    assert params["interval"] == "60m"
    assert int(params["period2"]) == int(datetime(2026, 11, 17, 20, 0, tzinfo=UTC).timestamp())
    assert int(params["period1"]) == int(datetime(2024, 11, 19, 20, 0, tzinfo=UTC).timestamp())
    assert len(seen) == 1


def test_history_429_is_a_block_and_nothing_is_stored(tmp_path: Path):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(429, content=b"Too Many Requests")

    with make_client(handler) as client, pytest.raises(SourceBlockedError):
        yi.fetch_history(client, RawStore(tmp_path), "NQ=F", now=NOW)
    assert len(calls) == 1 and not list(tmp_path.rglob("*.json"))


def test_values_at_india_close_and_us_close_with_staleness(nq, fx):
    """Fixture bars (README): NQ 5m bar 09:55-10:00 UTC on 13 Nov closes 21000.50 -> the value
    at 15:30 IST 13 Nov, stale 0 min; the bar 20:55-21:00 UTC on 12 Nov closes 21100.00 -> the
    US close of 12 Nov. USD/INR at 15:30 IST: 83.4550."""
    bars = pd.concat([nq, fx], ignore_index=True)
    ind = yi.values_at(bars, [date(2026, 11, 13)], moment="IN_1530").set_index("symbol")
    assert ind.loc["NQ=F", "close"] == 21000.50 and ind.loc["NQ=F", "stale_minutes"] == 0.0
    assert ind.loc["NQ=F", "bar_start_utc"] == pd.Timestamp("2026-11-13 09:55", tz="UTC")
    assert ind.loc["USDINR=X", "close"] == 83.4550
    assert ind.loc["NQ=F", "source_file"].endswith("NQ_F_5m.json")
    us = yi.values_at(bars, [date(2026, 11, 12)], moment="US_CLOSE").set_index("symbol")
    assert us.loc["NQ=F", "close"] == 21100.00
    # a day the bars do not cover gives no row (never a value from further back)
    assert yi.values_at(bars, [date(2026, 11, 2)]).empty
    # a stale bar: at 15:30 IST on 13 Nov with only bars up to 09:00 UTC -> 60 min stale -> none
    early = nq[nq["ts"] < pd.Timestamp("2026-11-13 09:00", tz="UTC")]
    assert yi.values_at(early, [date(2026, 11, 13)]).empty
    got = yi.values_at(early, [date(2026, 11, 13)], max_stale=timedelta(minutes=65))
    assert got.iloc[0]["stale_minutes"] == pytest.approx(60.0)
