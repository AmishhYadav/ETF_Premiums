"""AMC monthly portfolio files in the daily run (D50): when is a look due, and what does it fetch.

No network: ``amc_portfolio.fetch`` is replaced by a fake that records its arguments and saves
files into a temporary raw store, the same way the real collector does (listing + new files).
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import httpx
import pytest

from etfp.collect import amc_portfolio as ap
from etfp.collect import jobs
from etfp.http import PoliteClient
from etfp.rawstore import RawRecord, RawStore
from etfp.timeutil import IST


def now_on(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 20, 0, tzinfo=IST)


def save(store: RawStore, amc: str, day: date, name: str, body: bytes = b"x") -> RawRecord:
    return store.save(
        source=ap.SOURCES[amc],
        day=day,
        name=name,
        content=body,
        url=f"https://example.invalid/{name}",
        status_code=200,
        fetched_at=now_on(day),
    )


@pytest.fixture
def store(tmp_path: Path) -> RawStore:
    return RawStore(tmp_path / "raw")


def test_not_due_before_the_10th(store: RawStore) -> None:
    assert not jobs.amc_portfolio_due(date(2026, 11, 9), store)
    assert jobs.amc_portfolio_due(date(2026, 11, 10), store)


def test_empty_store_is_due_on_the_first_run_on_or_after_the_10th(store: RawStore) -> None:
    for day in (date(2026, 11, 10), date(2026, 11, 18), date(2026, 11, 30)):
        assert jobs.amc_portfolio_due(day, store)


def test_not_due_once_a_file_was_saved_since_the_10th(store: RawStore) -> None:
    save(store, "mirae", date(2026, 11, 11), "listing_monthly.json")
    save(store, "mirae", date(2026, 11, 11), "2026-10-31_MAFANG_etf_x.xlsx")
    assert not jobs.amc_portfolio_due(date(2026, 11, 11), store)
    assert not jobs.amc_portfolio_due(date(2026, 11, 28), store)
    assert jobs.amc_portfolio_due(date(2026, 12, 10), store)  # next month: due again


def test_a_file_saved_before_the_10th_does_not_count_for_this_month(store: RawStore) -> None:
    save(store, "nippon", date(2026, 11, 5), "NIMF-Monthly-Portfolio-Oct-2026.xls")
    assert jobs.amc_portfolio_due(date(2026, 11, 10), store)


def test_late_amc_is_looked_at_again_only_every_three_days(store: RawStore) -> None:
    # A look on the 10th found nothing new: only listings were saved.
    save(store, "motilal", date(2026, 11, 10), "listing_2026-nov.json")
    save(store, "nippon", date(2026, 11, 10), "portfolio_page.html")
    assert not jobs.amc_portfolio_due(date(2026, 11, 10), store)  # second run the same day
    assert not jobs.amc_portfolio_due(date(2026, 11, 12), store)
    assert jobs.amc_portfolio_due(date(2026, 11, 13), store)


def test_plan_daily_adds_three_units_only_when_due_and_today(store: RawStore) -> None:
    day = date(2026, 11, 18)
    plan = jobs.plan_daily(day, [jobs.AMC_PORTFOLIO], now=now_on(day), store=store)
    assert [u.source for u in plan.units] == [
        "amc_portfolio_motilal",
        "amc_portfolio_mirae",
        "amc_portfolio_nippon",
    ]
    assert {u.day for u in plan.units} == {day}
    assert plan.notes == []
    # Before the 10th: nothing planned, no noise.
    early = date(2026, 11, 9)
    quiet = jobs.plan_daily(early, [jobs.AMC_PORTFOLIO], now=now_on(early), store=store)
    assert quiet.units == [] and quiet.notes == []
    # Without a store the planner cannot know what is stored: nothing planned.
    assert jobs.plan_daily(day, [jobs.AMC_PORTFOLIO], now=now_on(day)).units == []
    # A past --date is skipped with a note (the pages only show the latest files).
    past = jobs.plan_daily(date(2026, 11, 12), [jobs.AMC_PORTFOLIO], now=now_on(day), store=store)
    assert past.units == [] and "skipped" in past.notes[0]


def test_amc_portfolio_is_in_the_daily_sources_but_not_in_catch_up() -> None:
    assert jobs.AMC_PORTFOLIO in jobs.DAILY_SOURCES
    assert jobs.AMC_PORTFOLIO not in jobs.CATCH_UP_SOURCES
    assert jobs.AMC_PORTFOLIO not in jobs.BACKFILL_SOURCES


def test_catch_up_never_plans_portfolio_files(store: RawStore) -> None:
    day = date(2026, 11, 18)
    plan = jobs.plan_catch_up(day, store, now=now_on(day), sources=list(jobs.DAILY_SOURCES))
    assert not any(u.source.startswith("amc_portfolio_") for u in plan.units)


def _client() -> PoliteClient:
    return PoliteClient(transport=httpx.MockTransport(lambda r: httpx.Response(500)))


def test_run_fetches_only_new_files_last_and_this_month_and_reports_the_new_one(
    store: RawStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    day = date(2026, 11, 12)
    calls: list[tuple[str, date, date]] = []

    def fake_fetch(client, st, d, amc, *, first, last, on_outcome=None):
        calls.append((amc, first, last))
        save(st, amc, d, "listing_monthly.json")
        if amc != "mirae":  # the other two have the October files; Mirae is not out yet
            rec = save(st, amc, d, f"{amc}_2026-10.xlsx")
            ref = ap.FileRef(amc, "https://example.invalid/f", rec.name, "t", None)
            return [ap.FetchOutcome(ref, "fetched", "1 bytes", rec)]
        ref = ap.FileRef(amc, "https://example.invalid/old", "old.xlsx", "t", None)
        return [ap.FetchOutcome(ref, "exists")]

    monkeypatch.setattr(ap, "fetch", fake_fetch)
    plan = jobs.plan_daily(day, [jobs.AMC_PORTFOLIO], now=now_on(day), store=store)
    outcomes = jobs.run_units(plan.units, _client(), store)
    assert [o.status for o in outcomes] == ["fetched", "fetched", "fetched"]
    # Window: the 1st of last month up to the run day (Motilal files by publication month).
    assert {(first, last) for _, first, last in calls} == {(date(2026, 10, 1), day)}
    # Mirae found nothing new, so its outcome is the listing saved by the look.
    mirae = next(o for o in outcomes if o.unit.source == "amc_portfolio_mirae")
    assert mirae.record is not None and mirae.record.name == "listing_monthly.json"
    # And a second run the same day plans nothing more from the files already saved.
    assert not jobs.amc_portfolio_due(day, store)


def test_a_failed_download_fails_the_unit_and_names_the_url(
    store: RawStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    day = date(2026, 11, 12)

    def fake_fetch(client, st, d, amc, *, first, last, on_outcome=None):
        save(st, amc, d, "listing_monthly.json")
        ref = ap.FileRef(amc, f"https://example.invalid/{amc}/bad.xlsx", "bad.xlsx", "t", None)
        return [ap.FetchOutcome(ref, "failed", "SourceError: HTTP 500")]

    monkeypatch.setattr(ap, "fetch", fake_fetch)
    plan = jobs.plan_daily(day, [jobs.AMC_PORTFOLIO], now=now_on(day), store=store)
    outcomes = jobs.run_units(plan.units[:1], _client(), store)
    assert outcomes[0].status == "failed"
    assert "bad.xlsx" in outcomes[0].detail and "amc_portfolio_motilal" in outcomes[0].detail
