"""Tests for the download planner and executor behind `collect daily` and `backfill`.

No network: httpx.MockTransport answers every request, and a fake clock replaces sleeping.
Facts used (checked in docs/DECISIONS.md and tests/test_calendars.py):
  Mon 2026-11-09 is an NSE session; Tue 2026-11-10 is an NSE holiday; Sat/Sun 7-8 Nov are closed.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from itertools import pairwise
from pathlib import Path

import httpx
import pytest

from etfp.collect import jobs
from etfp.collect.backfill_state import BackfillState
from etfp.http import PoliteClient
from etfp.rawstore import RawStore
from etfp.timeutil import IST

NOW = datetime(2026, 11, 9, 20, 0, tzinfo=IST)  # Monday evening
DAY = date(2026, 11, 9)


class Server:
    """A scripted fake of every website. Records requests; answers by URL pattern."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.status: dict[str, int] = {}  # substring of URL -> forced status code

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        for needle, code in self.status.items():
            if needle in url:
                return httpx.Response(code, content=b"nope")
        if "finance.yahoo.com" in url:
            return httpx.Response(200, content=b'{"chart":{"result":[{"meta":{}}],"error":null}}')
        return httpx.Response(200, content=f"body of {url}".encode())

    def count(self, needle: str) -> int:
        return sum(needle in str(r.url) for r in self.requests)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.stamps: dict[str, list[float]] = {}

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def make_client(server: Server, clock: Clock | None = None, retries: int = 0) -> PoliteClient:
    clock = clock or Clock()
    return PoliteClient(
        min_interval_s={"nseindia.com": 1.5},
        transport=httpx.MockTransport(server),
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        rng=lambda: 0.0,
        max_retries=retries,
    )


@pytest.fixture
def server() -> Server:
    return Server()


# -- parse_sources --------------------------------------------------------------------


def test_parse_sources_default_is_everything_allowed():
    assert jobs.parse_sources(None, allowed=jobs.DAILY_SOURCES) == list(jobs.DAILY_SOURCES)
    assert jobs.parse_sources([], allowed=jobs.BACKFILL_SOURCES) == list(jobs.BACKFILL_SOURCES)


def test_parse_sources_accepts_commas_repeats_case_and_dedupes_in_order():
    got = jobs.parse_sources(["UDIFF, bhavcopy", "udiff", "yahoo"], allowed=jobs.DAILY_SOURCES)
    assert got == ["udiff", "bhavcopy", "yahoo"]


def test_parse_sources_rejects_unknown_and_empty_and_non_backfillable():
    with pytest.raises(ValueError, match="unknown source 'nse'"):
        jobs.parse_sources(["nse"], allowed=jobs.DAILY_SOURCES)
    with pytest.raises(ValueError, match="no source given"):
        jobs.parse_sources([" , "], allowed=jobs.DAILY_SOURCES)
    with pytest.raises(ValueError, match="navall"):  # snapshot-only sources can't be backfilled
        jobs.parse_sources(["navall"], allowed=jobs.BACKFILL_SOURCES)


# -- amfi_windows ---------------------------------------------------------------------


def test_amfi_windows_cover_the_range_exactly_in_chunks_of_at_most_seven_days():
    start, end = date(2021, 1, 1), date(2021, 3, 20)
    windows = jobs.amfi_windows(start, end)
    assert windows[0][0] == start and windows[-1][1] == end
    for (_, prev_end), (next_start, _) in pairwise(windows):
        assert next_start == prev_end + timedelta(days=1)  # no gap, no overlap
    assert all(0 <= (e - s).days <= 6 for s, e in windows)
    assert sum((e - s).days + 1 for s, e in windows) == (end - start).days + 1  # 79 days


def test_amfi_windows_use_a_fixed_grid_so_a_different_start_reuses_file_names():
    a = jobs.amfi_windows(date(2026, 9, 1), date(2026, 10, 31))
    b = jobs.amfi_windows(date(2026, 9, 20), date(2026, 10, 31))
    assert set(b[1:]) <= set(a)  # everything after the (clipped) first window is identical


def test_amfi_windows_single_day_and_bad_size():
    assert jobs.amfi_windows(DAY, DAY) == [(DAY, DAY)]
    assert jobs.amfi_windows(DAY, DAY - timedelta(days=1)) == []
    with pytest.raises(ValueError):
        jobs.amfi_windows(DAY, DAY, days=0)


# -- plan_daily -----------------------------------------------------------------------


def test_plan_daily_on_the_day_itself_lists_all_ten_files():
    plan = jobs.plan_daily(DAY, jobs.DAILY_SOURCES, now=NOW)
    names = [(u.source, u.name) for u in plan.units]
    assert names == [
        ("nse_bhavcopy_full", "sec_bhavdata_full_09112026.csv"),
        ("nse_udiff_bhavcopy", "BhavCopy_NSE_CM_0_0_0_20261109_F_0000.csv.zip"),
        ("nse_security_master", "NSE_CM_security_09112026.csv.gz"),
        ("nse_etf_seclist", "eq_etfseclist.csv"),
        ("amfi_navall", "NAVAll.txt"),
        # window = previous NSE session (Fri 6 Nov) .. the day
        ("amfi_nav_history", "navhistory_20261106_20261109.txt"),
        ("yahoo_intraday", "NQ_F_5m.json"),
        ("yahoo_intraday", "ES_F_5m.json"),
        ("yahoo_intraday", "USDINR_X_5m.json"),
        ("yahoo_intraday", "HKDINR_X_5m.json"),
    ]
    assert plan.notes == []
    assert all(u.day == DAY for u in plan.units)


def test_plan_daily_amfi_window_skips_the_holiday_when_looking_back():
    # Wed 11 Nov: the previous session is Mon 9 Nov because Tue 10 Nov was a holiday.
    plan = jobs.plan_daily(date(2026, 11, 11), [jobs.AMFI_HISTORY], now=NOW + timedelta(days=2))
    assert [u.name for u in plan.units] == ["navhistory_20261109_20261111.txt"]


def test_plan_daily_for_a_past_day_skips_snapshot_only_sources_with_a_note():
    plan = jobs.plan_daily(date(2026, 11, 6), [jobs.ETF_LIST, jobs.NAVALL, jobs.BHAVCOPY], now=NOW)
    assert [u.source for u in plan.units] == ["nse_bhavcopy_full"]
    assert len(plan.notes) == 2
    assert "latest" in plan.notes[0] and "2026-11-06" in plan.notes[0]
    assert "NAVAll" in plan.notes[1]


def test_plan_daily_yahoo_too_old_is_a_loud_note_not_a_unit():
    old = date(2026, 6, 1)  # window start May 27 is > 59 days before Nov 9
    plan = jobs.plan_daily(old, [jobs.YAHOO], now=NOW)
    assert plan.units == []
    assert "NOT recoverable" in plan.notes[0] and "2026-06-01" in plan.notes[0]


def test_plan_daily_yahoo_edge_exactly_inside_the_limit():
    inside = (NOW - timedelta(days=40)).date()  # window start = 45 days back: kept
    assert len(jobs.plan_daily(inside, [jobs.YAHOO], now=NOW).units) == 4
    outside = (NOW - timedelta(days=56)).date()  # window start = 61 days back: dropped
    assert jobs.plan_daily(outside, [jobs.YAHOO], now=NOW).units == []


def test_plan_daily_requires_aware_now():
    with pytest.raises(ValueError, match="timezone-aware"):
        jobs.plan_daily(DAY, [jobs.BHAVCOPY], now=datetime(2026, 11, 9, 20, 0))  # noqa: DTZ001


# -- plan_backfill --------------------------------------------------------------------


def test_plan_backfill_nse_files_only_for_trading_days():
    plan = jobs.plan_backfill(
        date(2026, 11, 6), date(2026, 11, 11), [jobs.BHAVCOPY], now=NOW + timedelta(days=3)
    )
    # Fri 6, (weekend), Mon 9, (Tue 10 holiday), Wed 11
    # Sun 8 Nov 2026 is the Diwali Muhurat session (D24).
    assert [u.day for u in plan.units] == [
        date(2026, 11, 6),
        date(2026, 11, 8),
        date(2026, 11, 9),
        date(2026, 11, 11),
    ]


def test_plan_backfill_stops_at_today_and_says_so():
    plan = jobs.plan_backfill(date(2026, 11, 6), date(2026, 12, 31), [jobs.BHAVCOPY], now=NOW)
    assert [u.day for u in plan.units] == [date(2026, 11, 6), date(2026, 11, 8), date(2026, 11, 9)]
    assert any("future" in n for n in plan.notes)


def test_plan_backfill_amfi_windows_end_no_later_than_today():
    plan = jobs.plan_backfill(date(2026, 11, 1), date(2026, 12, 31), [jobs.AMFI_HISTORY], now=NOW)
    assert plan.units[-1].day == DAY
    assert all(u.source == "amfi_nav_history" for u in plan.units)


def test_plan_backfill_yahoo_only_where_yahoo_still_has_data():
    plan = jobs.plan_backfill(date(2026, 6, 1), date(2026, 11, 9), [jobs.YAHOO], now=NOW)
    days = {u.day for u in plan.units}
    assert days and min(days) >= (NOW - timedelta(days=64)).date()
    assert any("NOT recoverable" in n for n in plan.notes)


def test_plan_backfill_ignores_snapshot_sources_and_rejects_reversed_range():
    plan = jobs.plan_backfill(DAY, DAY, [jobs.NAVALL, jobs.ETF_LIST], now=NOW)
    assert plan.units == [] and len(plan.notes) == 2
    with pytest.raises(ValueError, match="after --to"):
        jobs.plan_backfill(DAY, DAY - timedelta(days=1), [jobs.BHAVCOPY], now=NOW)


def test_plan_backfill_before_calendar_start_is_a_clear_error():
    with pytest.raises(ValueError, match="before the project sample start"):
        jobs.plan_backfill(date(2020, 1, 1), date(2020, 1, 5), [jobs.BHAVCOPY], now=NOW)


# -- run_units: idempotence and failure isolation --------------------------------------


def test_second_run_makes_zero_requests(tmp_path: Path, server: Server):
    plan = jobs.plan_daily(DAY, jobs.DAILY_SOURCES, now=NOW)
    store = RawStore(tmp_path)
    with make_client(server) as client:
        first = jobs.run_units(plan.units, client, store)
        n = len(server.requests)
        second = jobs.run_units(plan.units, client, store)
    assert jobs.summarize(first) == {"fetched": 10, "exists": 0, "unavailable": 0, "failed": 0}
    assert jobs.summarize(second) == {"fetched": 0, "exists": 10, "unavailable": 0, "failed": 0}
    assert len(server.requests) == n == 10


def test_force_refetches_but_identical_bytes_are_not_stored_twice(tmp_path: Path, server: Server):
    plan = jobs.plan_daily(DAY, [jobs.BHAVCOPY], now=NOW)
    store = RawStore(tmp_path)
    with make_client(server) as client:
        jobs.run_units(plan.units, client, store)
        again = jobs.run_units(plan.units, client, store, force=True)
    assert again[0].status == "fetched" and "identical" in again[0].detail
    assert len(server.requests) == 2
    assert [p.name for p in (tmp_path / "nse_bhavcopy_full" / "2026-11-09").iterdir()] == [
        "sec_bhavdata_full_09112026.csv"
    ]


def test_one_failing_source_does_not_stop_the_others(tmp_path: Path, server: Server):
    server.status["BhavCopy_NSE_CM"] = 500  # UDiFF is down
    plan = jobs.plan_daily(DAY, jobs.DAILY_SOURCES, now=NOW)
    with make_client(server) as client:
        out = jobs.run_units(plan.units, client, RawStore(tmp_path))
    by = {o.unit.source: o for o in out}
    assert by["nse_udiff_bhavcopy"].status == "failed"
    assert (
        "nse_udiff_bhavcopy for 2026-11-09" in by["nse_udiff_bhavcopy"].detail
    )  # names source+date
    assert "HTTP 500" in by["nse_udiff_bhavcopy"].detail
    assert by["nse_bhavcopy_full"].status == by["nse_security_master"].status == "fetched"
    assert by["amfi_nav_history"].status == "fetched"
    assert jobs.summarize(out)["failed"] == 1


def test_daily_mode_treats_404_as_a_failure_with_a_hint(tmp_path: Path, server: Server):
    server.status["sec_bhavdata_full"] = 404
    plan = jobs.plan_daily(DAY, [jobs.BHAVCOPY], now=NOW)
    with make_client(server) as client:
        (out,) = jobs.run_units(plan.units, client, RawStore(tmp_path))
    assert out.status == "failed" and "after ~19:00 IST" in out.detail


def test_unexpected_exception_is_reported_with_its_type_and_run_continues(
    tmp_path: Path, server: Server
):
    def boom(client, store):
        raise KeyError("oops")

    bad = jobs.Unit("nse_x", DAY, "x.csv", jobs.NSE_HOST, boom)
    good = jobs.plan_daily(DAY, [jobs.BHAVCOPY], now=NOW).units
    with make_client(server) as client:
        out = jobs.run_units([bad, *good], client, RawStore(tmp_path))
    assert out[0].status == "failed" and "KeyError" in out[0].detail
    assert out[1].status == "fetched"


def test_a_block_stops_that_website_but_not_the_others(tmp_path: Path, server: Server):
    server.status["nsearchives"] = 403
    plan = jobs.plan_daily(
        DAY, [jobs.BHAVCOPY, jobs.UDIFF, jobs.SECMASTER, jobs.AMFI_HISTORY], now=NOW
    )
    with make_client(server) as client:
        out = jobs.run_units(plan.units, client, RawStore(tmp_path))
    assert server.count("nsearchives") == 1  # one 403, then no more NSE requests at all
    assert [o.status for o in out] == ["failed", "failed", "failed", "fetched"]
    assert "blocked" in out[0].detail
    assert "not tried" in out[1].detail and "blocked" in out[1].detail


def test_five_failures_in_a_row_stop_that_website(tmp_path: Path, server: Server):
    server.status["nsearchives"] = 500
    units = jobs.plan_backfill(
        date(2026, 10, 1), date(2026, 10, 30), [jobs.BHAVCOPY], now=NOW
    ).units
    assert len(units) >= 6
    with make_client(server) as client:
        out = jobs.run_units(units, client, RawStore(tmp_path))
    assert server.count("nsearchives") == jobs.MAX_CONSECUTIVE_FAILURES
    assert all(o.status == "failed" for o in out)
    assert "failed 5 times in a row" in out[-1].detail


def test_a_success_resets_the_failure_streak(tmp_path: Path, server: Server):
    units = jobs.plan_backfill(
        date(2026, 10, 1), date(2026, 10, 30), [jobs.BHAVCOPY], now=NOW
    ).units
    # Four failures, one success, four failures ... never five in a row, so nothing is skipped.
    codes = iter([500, 500, 500, 500, 200, 500, 500, 500, 500, 200, 500])
    units = units[:11]

    def handler(request: httpx.Request) -> httpx.Response:
        server.requests.append(request)
        return httpx.Response(next(codes), content=b"x")

    with PoliteClient(
        transport=httpx.MockTransport(handler), sleep=lambda s: None, max_retries=0
    ) as client:
        out = jobs.run_units(units, client, RawStore(tmp_path))
    assert len(server.requests) == 11  # no unit was skipped by the circuit breaker
    assert [o.status for o in out].count("fetched") == 2


# -- run_units: backfill mode (state) --------------------------------------------------


def test_backfill_404_is_remembered_and_never_asked_again(tmp_path: Path, server: Server):
    server.status["20261109"] = 404  # UDiFF has no file for that day
    units = jobs.plan_backfill(DAY, DAY, [jobs.UDIFF], now=NOW).units
    state = BackfillState.load(tmp_path / "state" / "s.json")
    store = RawStore(tmp_path / "raw")
    with make_client(server) as client:
        first = jobs.run_units(units, client, store, state=state)
        second = jobs.run_units(units, client, store, state=state)
        third = jobs.run_units(units, client, store, state=state, retry_unavailable=True)
    assert first[0].status == "unavailable" and "recorded" in first[0].detail
    assert second[0].status == "unavailable" and "known not available" in second[0].detail
    assert third[0].status == "unavailable"
    assert len(server.requests) == 2  # first run + the explicit retry; the second run asked nobody
    reloaded = BackfillState.load(tmp_path / "state" / "s.json")  # survives a restart
    assert reloaded.is_unavailable("nse_udiff_bhavcopy", DAY, units[0].name)
    assert not (tmp_path / "raw" / "nse_udiff_bhavcopy").exists()


def test_a_file_that_appears_later_clears_its_unavailable_mark(tmp_path: Path, server: Server):
    units = jobs.plan_backfill(DAY, DAY, [jobs.BHAVCOPY], now=NOW).units
    state = BackfillState(tmp_path / "s.json")
    state.mark_unavailable(units[0].source, DAY, units[0].name, status_code=404, url="u")
    with make_client(server) as client:
        out = jobs.run_units(
            units, client, RawStore(tmp_path / "raw"), state=state, retry_unavailable=True
        )
    assert out[0].status == "fetched"
    assert not state.is_unavailable(units[0].source, DAY, units[0].name)


def test_404_does_not_count_toward_the_failure_streak(tmp_path: Path, server: Server):
    server.status["nsearchives"] = 404
    units = jobs.plan_backfill(date(2026, 10, 1), date(2026, 10, 30), [jobs.UDIFF], now=NOW).units
    assert len(units) > jobs.MAX_CONSECUTIVE_FAILURES
    with make_client(server) as client:
        out = jobs.run_units(
            units, client, RawStore(tmp_path / "r"), state=BackfillState(tmp_path / "s.json")
        )
    assert {o.status for o in out} == {"unavailable"}
    assert server.count("nsearchives") == len(units)


def test_backfill_resumes_after_interruption(tmp_path: Path, server: Server):
    units = jobs.plan_backfill(date(2026, 11, 2), date(2026, 11, 9), [jobs.BHAVCOPY], now=NOW).units
    store = RawStore(tmp_path)
    with make_client(server) as client:
        jobs.run_units(units[:3], client, store)  # "crash" after three days
        out = jobs.run_units(units, client, store)
    assert [o.status for o in out] == ["exists"] * 3 + ["fetched"] * (len(units) - 3)
    assert len(server.requests) == len(units)


def test_on_outcome_callback_sees_every_unit_in_order(tmp_path: Path, server: Server):
    seen: list[str] = []
    units = jobs.plan_daily(DAY, [jobs.BHAVCOPY, jobs.UDIFF], now=NOW).units
    with make_client(server) as client:
        jobs.run_units(
            units, client, RawStore(tmp_path), on_outcome=lambda o: seen.append(o.unit.source)
        )
    assert seen == ["nse_bhavcopy_full", "nse_udiff_bhavcopy"]


# -- politeness -------------------------------------------------------------------------


def test_nse_requests_are_at_least_one_and_a_half_seconds_apart(tmp_path: Path):
    clock = Clock()
    times: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        times.append(clock.monotonic())
        return httpx.Response(200, content=b"x")

    client = PoliteClient(
        min_interval_s={"nseindia.com": 1.5},
        transport=httpx.MockTransport(handler),
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        max_retries=0,
    )
    units = jobs.plan_backfill(
        date(2026, 11, 2), date(2026, 11, 9), [jobs.BHAVCOPY, jobs.SECMASTER], now=NOW
    ).units
    with client:
        jobs.run_units(units, client, RawStore(tmp_path))
    assert len(times) == len(units) >= 8
    gaps = [b - a for a, b in pairwise(times)]
    assert min(gaps) >= 1.5 - 1e-9


# -- BackfillState ----------------------------------------------------------------------


def test_state_roundtrip_and_aware_timestamps(tmp_path: Path):
    path = tmp_path / "x" / "state.json"
    state = BackfillState.load(path)
    assert len(state) == 0 and not path.exists()
    state.mark_unavailable(
        "src", DAY, "f.csv", status_code=404, url="http://u", note="n", checked_at=NOW
    )
    again = BackfillState.load(path)
    item = again.get("src", DAY, "f.csv")
    assert item is not None and item.checked_at == NOW and item.checked_at.tzinfo is not None
    assert item.status_code == 404 and item.url == "http://u" and item.note == "n"
    doc = json.loads(path.read_text())
    assert doc["version"] == 1 and "src|2026-11-09|f.csv" in doc["unavailable"]
    assert [p.name for p in path.parent.iterdir()] == ["state.json"]  # no temp files left behind


def test_state_forget_is_idempotent(tmp_path: Path):
    state = BackfillState(tmp_path / "s.json")
    state.forget("a", DAY, "b")  # nothing to forget: no error, no file
    assert not (tmp_path / "s.json").exists()


@pytest.mark.parametrize(
    "text", ["not json", "[]", '{"version": 2, "unavailable": {}}', '{"version": 1}']
)
def test_damaged_state_file_is_an_error_not_a_silent_reset(tmp_path: Path, text: str):
    path = tmp_path / "s.json"
    path.write_text(text)
    with pytest.raises(ValueError, match="damaged"):
        BackfillState.load(path)
