"""Planning and running the BSE (R9) and SLB (R8) downloads through the shared job executor.

Facts used: Mon 2026-11-09 is an NSE session. 2024-01-02 is the first day we ask BSE for the
new file format; 2024-01-01 (a Monday session) still uses the old zip.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import httpx

from etfp.collect import jobs
from etfp.collect.backfill_state import BackfillState
from etfp.http import PoliteClient
from etfp.rawstore import RawStore
from etfp.timeutil import IST

NOW = datetime(2026, 11, 9, 20, 0, tzinfo=IST)
DAY = date(2026, 11, 9)
HTML = b'<!DOCTYPE html><html lang="en"><head><title>LIVE Stock Market | BSE</title></head></html>'


def make_client(handler) -> PoliteClient:
    return PoliteClient(transport=httpx.MockTransport(handler), sleep=lambda s: None, max_retries=0)


# -- the keys the CLI accepts ------------------------------------------------------------


def test_new_sources_are_allowed_in_daily_and_backfill_and_documented():
    for key in (jobs.BSE, jobs.SLB):
        assert key in jobs.DAILY_SOURCES and key in jobs.BACKFILL_SOURCES
        assert jobs.SOURCE_HELP[key]
    assert jobs.parse_sources(["bse,slb"], allowed=jobs.BACKFILL_SOURCES) == ["bse", "slb"]


# -- plan_daily --------------------------------------------------------------------------


def test_plan_daily_bse_is_one_file_on_the_bse_host():
    (unit,) = jobs.plan_daily(DAY, [jobs.BSE], now=NOW).units
    assert unit.source == "bse_bhavcopy" and unit.day == DAY
    assert unit.name == "BhavCopy_BSE_CM_0_0_0_20261109_F_0000.CSV"
    assert unit.host == "bseindia.com"  # its own politeness timer, separate from NSE's


def test_plan_daily_slb_is_two_files_on_the_nse_host():
    units = jobs.plan_daily(DAY, [jobs.SLB], now=NOW).units
    assert [(u.source, u.name, u.host) for u in units] == [
        ("nse_slb_eligible", "SLB_ELG_SEC_09112026.csv", "nseindia.com"),
        ("nse_slb_bhavcopy", "SLBM_BC_09112026.DAT", "nseindia.com"),
    ]


# -- plan_backfill -----------------------------------------------------------------------


def test_backfill_bse_switches_format_on_2_jan_2024():
    plan = jobs.plan_backfill(date(2023, 12, 28), date(2024, 1, 4), [jobs.BSE], now=NOW)
    assert [(u.day.isoformat(), u.name) for u in plan.units] == [
        ("2023-12-28", "EQ_ISINCODE_281223.zip"),
        ("2023-12-29", "EQ_ISINCODE_291223.zip"),
        ("2024-01-01", "EQ_ISINCODE_010124.zip"),
        ("2024-01-02", "BhavCopy_BSE_CM_0_0_0_20240102_F_0000.CSV"),
        ("2024-01-03", "BhavCopy_BSE_CM_0_0_0_20240103_F_0000.CSV"),
        ("2024-01-04", "BhavCopy_BSE_CM_0_0_0_20240104_F_0000.CSV"),
    ]


def test_backfill_slb_plans_both_files_per_session_day_in_order():
    plan = jobs.plan_backfill(date(2026, 11, 6), date(2026, 11, 9), [jobs.SLB], now=NOW)
    assert [(u.day.isoformat(), u.source) for u in plan.units] == [
        ("2026-11-06", "nse_slb_eligible"),
        ("2026-11-06", "nse_slb_bhavcopy"),
        ("2026-11-08", "nse_slb_eligible"),  # Sunday 8 Nov 2026: Diwali Muhurat session (D24)
        ("2026-11-08", "nse_slb_bhavcopy"),
        ("2026-11-09", "nse_slb_eligible"),
        ("2026-11-09", "nse_slb_bhavcopy"),
    ]  # Saturday 7 Nov is not planned


def test_backfill_reaches_back_to_the_confirmed_start_of_both_sources():
    start = date(2021, 1, 4)  # earliest day confirmed live for BSE legacy and for both SLB files
    plan = jobs.plan_backfill(start, start, [jobs.BSE, jobs.SLB], now=NOW)
    assert [u.name for u in plan.units] == [
        "EQ_ISINCODE_040121.zip",
        "SLB_ELG_SEC_04012021.csv",
        "SLBM_BC_04012021.DAT",
    ]


# -- running -----------------------------------------------------------------------------


def test_bse_web_page_in_backfill_is_recorded_as_unavailable_and_not_asked_again(tmp_path: Path):
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, content=HTML, headers={"content-type": "text/html"})

    plan = jobs.plan_backfill(DAY, DAY, [jobs.BSE], now=NOW)
    store, state = RawStore(tmp_path / "raw"), BackfillState(tmp_path / "state.json")
    with make_client(handler) as client:
        (first,) = jobs.run_units(plan.units, client, store, state=state)
        (second,) = jobs.run_units(plan.units, client, store, state=state)
    assert first.status == "unavailable" and "web page" in first.detail
    assert "HTTP 404" not in first.detail  # the honest status (200) is reported, not a fake 404
    assert second.status == "unavailable" and "known not available" in second.detail
    assert len(seen) == 1  # the second run asked nobody
    assert not (tmp_path / "raw").exists()  # no HTML was ever stored as raw data


def test_bse_web_page_in_daily_mode_is_a_loud_failure_with_a_hint(tmp_path: Path):
    plan = jobs.plan_daily(DAY, [jobs.BSE], now=NOW)
    client = make_client(lambda r: httpx.Response(200, content=HTML))
    with client:
        (out,) = jobs.run_units(plan.units, client, RawStore(tmp_path / "raw"))
    assert out.status == "failed"
    assert "bse_bhavcopy for 2026-11-09" in out.detail or "web page" in out.detail
    assert "BSE posts the daily file in the evening" in out.detail


def test_a_real_404_on_bse_still_reads_as_http_404(tmp_path: Path):
    plan = jobs.plan_backfill(DAY, DAY, [jobs.BSE], now=NOW)
    client = make_client(lambda r: httpx.Response(404))
    with client:
        (out,) = jobs.run_units(
            plan.units,
            client,
            RawStore(tmp_path / "raw"),
            state=BackfillState(tmp_path / "s.json"),
        )
    assert out.status == "unavailable" and out.detail.startswith("HTTP 404")


def test_one_slb_file_missing_does_not_stop_the_other_or_bse(tmp_path: Path):
    def handler(request: httpx.Request) -> httpx.Response:
        if "SLBM_BC" in str(request.url):
            return httpx.Response(404)
        return httpx.Response(200, content=b"x,y\n1,2\n")

    plan = jobs.plan_daily(DAY, [jobs.SLB, jobs.BSE], now=NOW)
    with make_client(handler) as client:
        out = jobs.run_units(plan.units, client, RawStore(tmp_path / "raw"))
    by = {o.unit.source: o.status for o in out}
    assert by == {
        "nse_slb_eligible": "fetched",
        "nse_slb_bhavcopy": "failed",
        "bse_bhavcopy": "fetched",
    }
    assert (tmp_path / "raw" / "bse_bhavcopy" / "2026-11-09").is_dir()
