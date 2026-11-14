"""The browser quote capture inside the recorder tick: it must never break the other sources.

The existing AMC / NSE-ETF-table paths are checked to be unchanged; the quote step is
tested with fakes for: Playwright missing, browser missing, NSE blocking, a crash.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path

import httpx
import pytest

from etfp import recorder
from etfp.collect import nse_quote
from etfp.config import NseQuoteSettings, load_collect_settings
from etfp.http import PoliteClient, SourceBlockedError
from etfp.rawstore import RawStore
from etfp.recorder import SourceOutcome, run_loop, run_tick
from etfp.timeutil import IST
from test_collect_nse_quote import FakeBrowser, PageVisit, ok_visit
from test_recorder import FakeClock, FakeSources, at, failure_lines

SESSION_DAY = date(2026, 11, 11)


@pytest.fixture
def client() -> PoliteClient:
    def no_network(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not make requests")

    return PoliteClient(transport=httpx.MockTransport(no_network))


@pytest.fixture
def store(tmp_path: Path) -> RawStore:
    return RawStore(tmp_path / "raw")


@pytest.fixture
def state(tmp_path: Path) -> Path:
    return tmp_path / "state"


@pytest.fixture
def two_etfs(monkeypatch) -> None:
    """The step reads the tracked ETFs from config; pin it to two symbols."""
    from etfp.config import load_etfs

    universe = load_etfs()
    tracked = [e.symbol for e in universe.tracked()][:2]
    monkeypatch.setattr(recorder, "load_etfs", lambda: _Fake(tracked))


class _Fake:
    def __init__(self, symbols: list[str]) -> None:
        self._symbols = symbols

    def tracked(self):
        return [type("E", (), {"symbol": s})() for s in self._symbols]


def make_step(monkeypatch, cfg: NseQuoteSettings, browser=None, *, installed: bool = True):
    monkeypatch.setattr(nse_quote, "playwright_available", lambda: installed)
    if browser is not None:
        real = nse_quote.run_tick_capture

        def with_fake(store, symbols, settings, **kw):
            return real(
                store, symbols, settings, open_browser=lambda _s: browser, sleep=lambda s: None
            )

        monkeypatch.setattr(nse_quote, "run_tick_capture", with_fake)
    return recorder._nse_quote_step(cfg)


def run_with(client, store, state, step, fake=None, **kw):
    fake = fake or FakeSources(["a"])
    result = run_tick(
        client,
        store,
        sources=fake.registry(),
        extra_steps={"nse_quote": step},
        state_dir=state,
        now=FakeClock(at(10, 30)).now,
        **kw,
    )
    return result, fake


def tracked_two() -> list[str]:
    return [e.symbol for e in recorder.load_etfs().tracked()][:2]


# -- defaults keep everything else unchanged --------------------------------------


def test_default_sources_are_unchanged_and_quote_capture_is_not_among_them() -> None:
    assert list(recorder.default_sources()) == [
        "nse_etf_api",
        "amc_inav_motilal",
        "amc_inav_mirae",
        "amc_inav_nippon",
    ]
    assert list(recorder.default_extra_steps()) == ["nse_quote"]


def test_custom_sources_get_no_extra_step_unless_asked(client, store, state, monkeypatch) -> None:
    def must_not_run(*a, **k):
        raise AssertionError("a browser must not start in a plain test tick")

    monkeypatch.setattr(recorder, "default_extra_steps", must_not_run)
    fake = FakeSources(["a"])
    result = run_tick(
        client, store, sources=fake.registry(), state_dir=state, now=FakeClock(at(9, 15)).now
    )
    assert [o.source for o in result.outcomes] == ["a"]


def test_quote_config_defaults_and_shipped_yaml() -> None:
    cfg = load_collect_settings().nse_quote
    # Shipped OFF until a supervised capture against NSE works (D31).
    assert cfg.enabled is False and cfg.headless is True and cfg.page_gap_s >= 1.5


# -- happy path ---------------------------------------------------------------------


def test_step_runs_after_the_plain_sources_and_reports_ok(
    client, store, state, monkeypatch, two_etfs
) -> None:
    syms = tracked_two()
    browser = FakeBrowser(
        {s: ok_visit(s, at=datetime(2026, 11, 11, 10, 30, 5, tzinfo=IST)) for s in syms}
    )
    step = make_step(monkeypatch, NseQuoteSettings(), browser)
    result, fake = run_with(client, store, state, step)
    assert [o.source for o in result.outcomes] == ["a", "nse_quote"]
    quote = result.outcomes[1]
    assert quote.ok and quote.relpath.startswith("nse_quote/2026-11-11/")
    assert quote.size_bytes > 0 and browser.closed
    assert fake.calls == ["a"]  # the plain source ran as before


# -- isolation: every way the step can go wrong ---------------------------------------


def test_playwright_missing_in_auto_mode_skips_with_one_warning(
    client, store, state, monkeypatch, two_etfs, caplog
) -> None:
    step = make_step(monkeypatch, NseQuoteSettings(enabled="auto"), installed=False)
    with caplog.at_level(logging.WARNING, logger="etfp.recorder"):
        result, _ = run_with(client, store, state, step)
    assert [o.source for o in result.outcomes] == ["a"]  # not an outcome, so no alert
    assert result.failed_count == 0
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1 and "Playwright is not installed" in warnings[0].getMessage()
    assert failure_lines(state) == []


def test_playwright_missing_when_explicitly_enabled_is_a_failed_source(
    client, store, state, monkeypatch, two_etfs
) -> None:
    step = make_step(monkeypatch, NseQuoteSettings(enabled=True), installed=False)
    result, _ = run_with(client, store, state, step)
    assert result.outcomes[1].source == "nse_quote" and not result.outcomes[1].ok
    assert result.outcomes[1].error_type == "BrowserUnavailableError"
    assert result.outcomes[0].ok  # the plain source is untouched


def test_switched_off_in_config_does_nothing_at_all(
    client, store, state, monkeypatch, two_etfs, caplog
) -> None:
    step = make_step(monkeypatch, NseQuoteSettings(enabled=False), installed=True)
    with caplog.at_level(logging.WARNING, logger="etfp.recorder"):
        result, _ = run_with(client, store, state, step)
    assert [o.source for o in result.outcomes] == ["a"]
    assert caplog.records == []


def test_browser_missing_is_a_failed_outcome_and_the_other_sources_still_run(
    client, store, state, monkeypatch, two_etfs, caplog
) -> None:
    def no_browser(_settings):
        raise nse_quote.BrowserUnavailableError("Executable doesn't exist at /x/chrome")

    monkeypatch.setattr(nse_quote, "playwright_available", lambda: True)
    monkeypatch.setattr(nse_quote, "open_playwright_browser", no_browser)
    step = recorder._nse_quote_step(NseQuoteSettings())
    with caplog.at_level(logging.WARNING, logger="etfp.recorder"):
        result, fake = run_with(client, store, state, step)
    assert fake.calls == ["a"] and result.outcomes[0].ok
    q = result.outcomes[1]
    assert not q.ok and q.error_type == "BrowserUnavailableError"
    assert "Executable doesn't exist" in (q.error or "")
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1
    assert not warnings[0].exc_info  # expected trouble: no traceback noise
    assert [f["source"] for f in failure_lines(state)] == ["nse_quote"]


def test_nse_blocking_is_a_failed_outcome_not_a_crash(
    client, store, state, monkeypatch, two_etfs
) -> None:
    syms = tracked_two()
    browser = FakeBrowser({s: PageVisit(403, ()) for s in syms})
    step = make_step(monkeypatch, NseQuoteSettings(), browser)
    result, _ = run_with(client, store, state, step)
    q = result.outcomes[1]
    assert not q.ok and q.error_type == "SourceBlockedError"
    assert browser.visited == syms[:1] and browser.closed  # stopped after the first refusal


def test_unexpected_exception_in_the_step_is_contained_with_a_traceback(
    client, store, state, monkeypatch, two_etfs, caplog
) -> None:
    def explode(store):
        raise ZeroDivisionError("bug in the step")

    with caplog.at_level(logging.WARNING, logger="etfp.recorder"):
        result, fake = run_with(client, store, state, explode)
    assert result.outcomes[0].ok and fake.calls == ["a"]
    q = result.outcomes[1]
    assert not q.ok and q.error_type == "ZeroDivisionError"
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1 and warnings[0].exc_info
    # and the heartbeat still got written
    beat = recorder.read_heartbeat(state)
    assert beat["sources"]["nse_quote"]["ok"] is False and beat["sources"]["a"]["ok"] is True


def test_partial_capture_keeps_the_data_and_logs_one_warning(
    client, store, state, monkeypatch, two_etfs, caplog
) -> None:
    first, second = tracked_two()
    browser = FakeBrowser({first: ok_visit(first), second: PageVisit(200, ())})
    step = make_step(monkeypatch, NseQuoteSettings(), browser)
    with caplog.at_level(logging.WARNING, logger="etfp.recorder"):
        result, _ = run_with(client, store, state, step)
    assert result.outcomes[1].ok  # real data was saved
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1 and second in warnings[0].getMessage()


def test_a_quote_failure_never_changes_the_plain_sources_results(
    client, store, state, monkeypatch, two_etfs
) -> None:
    fake = FakeSources(["a", "b"])
    fake.fail["a"] = RuntimeError("source a is down")

    def blocked(store):
        raise SourceBlockedError("403", source="nse_quote", day=None, url="u")

    result, _ = run_with(client, store, state, blocked, fake)
    assert [(o.source, o.ok) for o in result.outcomes] == [
        ("a", False),
        ("b", True),
        ("nse_quote", False),
    ]


def test_step_returning_none_is_not_recorded_in_the_heartbeat(client, store, state) -> None:
    result, _ = run_with(client, store, state, lambda s: None)
    assert [o.source for o in result.outcomes] == ["a"]
    assert "nse_quote" not in recorder.read_heartbeat(state)["sources"]


# -- the loop passes the step through ---------------------------------------------------


def test_run_loop_hands_the_extra_steps_to_every_tick(client, store, state) -> None:
    settings = load_collect_settings()
    seen: list[str] = []

    def step(store: RawStore) -> SourceOutcome | None:
        seen.append("tick")
        return None

    clock = FakeClock(at(15, 40))  # 15:30 (10 min late, still taken) and 15:45 remain
    results = run_loop(
        client=client,
        store=store,
        settings=settings,
        state_dir=state,
        now=clock.now,
        sleep=clock.sleep,
        sources=FakeSources(["a"]).registry(),
        extra_steps={"nse_quote": step},
    )
    assert len(results) == len(seen) >= 1  # the step ran exactly once per tick
