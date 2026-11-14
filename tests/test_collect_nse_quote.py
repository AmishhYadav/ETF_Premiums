"""NSE quote capture: what is saved, what is refused, and how it behaves when NSE pushes back.

No browser and no network: a fake browser hands back canned page visits.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from etfp.collect import nse_quote
from etfp.collect.nse_quote import (
    KIND_QUOTE,
    KIND_TRADE_INFO,
    CapturedResponse,
    PageVisit,
    capture_symbols,
    classify_url,
    raw_name,
    run_tick_capture,
)
from etfp.config import NseQuoteSettings
from etfp.http import SourceBlockedError, SourceError
from etfp.rawstore import RawStore
from etfp.timeutil import IST

WHEN = datetime(2026, 11, 16, 10, 30, 15, tzinfo=IST)
BASE = "https://www.nseindia.com/api/quote-equity"


def quote_body(symbol: str = "MONQ50") -> bytes:
    body = {
        "info": {"symbol": symbol},
        "metadata": {"lastUpdateTime": "16-Nov-2026 10:30:10"},
        "priceInfo": {"lastPrice": 240.5, "iNavValue": 112.25},
    }
    return json.dumps(body).encode()


def book_body() -> bytes:
    book = {
        "totalBuyQuantity": 1200,
        "totalSellQuantity": 34500,
        "bid": [{"price": 240.0, "quantity": 100}],
        "ask": [{"price": 241.0, "quantity": 5000}],
    }
    return json.dumps({"marketDeptOrderBook": book}).encode()


def quote_resp(symbol: str = "MONQ50", *, status: int = 200, body: bytes | None = None, at=WHEN):
    return CapturedResponse(
        KIND_QUOTE,
        f"{BASE}?symbol={symbol}",
        status,
        quote_body(symbol) if body is None else body,
        at,
    )


def book_resp(symbol: str = "MONQ50", *, status: int = 200, body: bytes | None = None, at=WHEN):
    return CapturedResponse(
        KIND_TRADE_INFO,
        f"{BASE}?symbol={symbol}&section=trade_info",
        status,
        book_body() if body is None else body,
        at,
    )


class FakeBrowser:
    """Plays back one scripted outcome per symbol and records what was asked."""

    def __init__(self, script: dict[str, object]) -> None:
        self.script = script
        self.visited: list[str] = []
        self.closed = False

    def visit(self, symbol: str, *, wait_s: float) -> PageVisit:
        self.visited.append(symbol)
        item = self.script[symbol]
        if isinstance(item, BaseException):
            raise item
        assert isinstance(item, PageVisit)
        return item

    def close(self) -> None:
        self.closed = True


def run(store, symbols, browser, *, settings=None, sleep=None, clock=None):
    return capture_symbols(
        store,
        symbols,
        settings or NseQuoteSettings(),
        open_browser=lambda _s: browser,
        sleep=sleep or (lambda s: None),
        clock=clock or (lambda: 0.0),
    )


@pytest.fixture
def store(tmp_path: Path) -> RawStore:
    return RawStore(tmp_path / "raw")


def ok_visit(symbol: str = "MONQ50", **kw) -> PageVisit:
    return PageVisit(200, (quote_resp(symbol, **kw), book_resp(symbol, **kw)))


# -- recognising the page's own answers ------------------------------------------


@pytest.mark.parametrize(
    ("url", "kind"),
    [
        (f"{BASE}?symbol=MONQ50", KIND_QUOTE),
        (f"{BASE}?symbol=monq50", KIND_QUOTE),
        (f"{BASE}?symbol=MONQ50&section=trade_info", KIND_TRADE_INFO),
        (f"{BASE}?section=trade_info&symbol=MONQ50", KIND_TRADE_INFO),
        (f"{BASE}?symbol=MON100", None),  # another ETF's answer
        (f"{BASE}?symbol=MONQ50&section=corp_info", None),  # a section we do not record
        ("https://www.nseindia.com/api/chart-databyindex?index=MONQ50", None),
        ("https://evil.example/api/quote-equity?symbol=MONQ50", None),
        ("https://www.nseindia.com.evil.example/api/quote-equity?symbol=MONQ50", None),
        (f"{BASE}", None),
    ],
)
def test_classify_url(url: str, kind: str | None) -> None:
    assert classify_url(url, "MONQ50") == kind


def test_raw_names_pair_by_the_same_ist_time() -> None:
    assert raw_name("MONQ50", KIND_QUOTE, WHEN) == "MONQ50_103015.json"
    assert raw_name("MONQ50", KIND_TRADE_INFO, WHEN) == "MONQ50_trade_info_103015.json"
    utc = WHEN.astimezone(UTC)  # same instant, same IST-based name
    assert raw_name("MONQ50", KIND_QUOTE, utc) == "MONQ50_103015.json"


# -- saving ---------------------------------------------------------------------


def test_saves_both_answers_untouched_under_ist_date_and_time(store: RawStore) -> None:
    report = run(store, ["MONQ50"], FakeBrowser({"MONQ50": ok_visit()}))
    names = sorted(r.relpath for r in report.saved)
    assert names == [
        "nse_quote/2026-11-16/MONQ50_103015.json",
        "nse_quote/2026-11-16/MONQ50_trade_info_103015.json",
    ]
    quote_file = store.path_for("nse_quote", date(2026, 11, 16), "MONQ50_103015.json")
    assert quote_file.read_bytes() == quote_body()  # byte for byte
    book_file = store.path_for("nse_quote", date(2026, 11, 16), "MONQ50_trade_info_103015.json")
    assert book_file.read_bytes() == book_body()
    assert report.captured == ["MONQ50"] and report.problems == []


def test_both_files_share_the_earlier_arrival_time(store: RawStore) -> None:
    later = datetime(2026, 11, 16, 10, 30, 19, tzinfo=IST)
    visit = PageVisit(200, (book_resp(at=later), quote_resp(at=WHEN)))
    report = run(store, ["MONQ50"], FakeBrowser({"MONQ50": visit}))
    assert sorted(r.name for r in report.saved) == [
        "MONQ50_103015.json",
        "MONQ50_trade_info_103015.json",
    ]


def test_missing_order_book_still_saves_the_price_block_and_says_so(store: RawStore) -> None:
    visit = PageVisit(200, (quote_resp(),))
    report = run(store, ["MONQ50"], FakeBrowser({"MONQ50": visit}))
    assert [r.name for r in report.saved] == ["MONQ50_103015.json"]
    assert report.no_depth == ["MONQ50"]
    assert "no order book for MONQ50" in report.problems[0]


def test_wrong_symbol_or_bad_json_is_never_stored(store: RawStore) -> None:
    visit = PageVisit(
        200,
        (quote_resp(body=quote_body("MON100")), book_resp(body=b"<html>Access Denied</html>")),
    )
    report = run(store, ["MONQ50"], FakeBrowser({"MONQ50": visit}))
    assert report.saved == []
    assert "MONQ50" in report.failed
    assert not (store.root / "nse_quote").exists()


def test_non_200_answer_is_not_stored(store: RawStore) -> None:
    visit = PageVisit(200, (quote_resp(status=500), book_resp()))
    report = run(store, ["MONQ50"], FakeBrowser({"MONQ50": visit}))
    assert [r.name for r in report.saved] == ["MONQ50_trade_info_103015.json"]
    assert "MONQ50" in report.failed  # book without price block is called out


def test_page_that_makes_no_quote_request_is_a_failure_with_a_reason(store: RawStore) -> None:
    report = run(store, ["MONQ50"], FakeBrowser({"MONQ50": PageVisit(200, ())}))
    assert "made no quote request" in report.failed["MONQ50"]


def test_identical_second_capture_is_a_duplicate_not_a_new_file(store: RawStore) -> None:
    run(store, ["MONQ50"], FakeBrowser({"MONQ50": ok_visit()}))
    again = run(store, ["MONQ50"], FakeBrowser({"MONQ50": ok_visit()}))
    assert all(r.duplicate for r in again.saved)
    assert len(list((store.root / "nse_quote" / "2026-11-16").iterdir())) == 2


# -- manners: gaps, refusals, budget --------------------------------------------


def test_pauses_between_page_loads_but_not_before_the_first(store: RawStore) -> None:
    pauses: list[float] = []
    script = {s: ok_visit(s) for s in ("MONQ50", "MON100", "MAFANG")}
    run(store, list(script), FakeBrowser(script), sleep=pauses.append)
    assert pauses == [1.5, 1.5]


def test_page_gap_below_the_project_minimum_is_refused() -> None:
    with pytest.raises(ValueError, match="page_gap_s"):
        NseQuoteSettings(page_gap_s=1.0)


@pytest.mark.parametrize("status", [401, 403, 429])
def test_http_refusal_stops_the_rest_of_the_tick(store: RawStore, status: int) -> None:
    script = {"MONQ50": PageVisit(status, ()), "MON100": ok_visit("MON100")}
    browser = FakeBrowser(script)
    report = run(store, ["MONQ50", "MON100"], browser)
    assert browser.visited == ["MONQ50"]  # MON100 was never even asked
    assert report.skipped == ["MON100"]
    assert f"HTTP {status}" in (report.blocked or "")
    assert report.saved == []


def test_protocol_level_cut_counts_as_a_refusal(store: RawStore) -> None:
    err = RuntimeError("Page.goto: net::ERR_HTTP2_PROTOCOL_ERROR at https://www.nseindia.com/x")
    browser = FakeBrowser({"MONQ50": err, "MON100": ok_visit("MON100")})
    report = run(store, ["MONQ50", "MON100"], browser)
    assert browser.visited == ["MONQ50"]
    assert "cut the connection" in (report.blocked or "")


def test_one_broken_page_does_not_stop_the_others(store: RawStore) -> None:
    browser = FakeBrowser({"MONQ50": TimeoutError("page too slow"), "MON100": ok_visit("MON100")})
    report = run(store, ["MONQ50", "MON100"], browser)
    assert report.captured == ["MON100"]
    assert "TimeoutError" in report.failed["MONQ50"]
    assert report.blocked is None


def test_three_errors_in_a_row_stop_the_tick(store: RawStore) -> None:
    syms = ["A1", "A2", "A3", "A4", "A5"]
    browser = FakeBrowser({s: ValueError("boom") for s in syms})
    report = run(store, syms, browser)
    assert browser.visited == ["A1", "A2", "A3"]
    assert report.skipped == ["A4", "A5"]


def test_time_budget_stops_new_pages_and_the_browser_is_still_closed(store: RawStore) -> None:
    ticks = iter([0.0, 10.0, 10.0, 999.0])  # the start, then one look per symbol
    script = {s: ok_visit(s) for s in ("MONQ50", "MON100", "MAFANG")}
    browser = FakeBrowser(script)
    report = run(
        store,
        list(script),
        browser,
        settings=NseQuoteSettings(tick_budget_s=240),
        clock=lambda: next(ticks),
    )
    assert browser.visited == ["MONQ50", "MON100"]
    assert report.skipped == ["MAFANG"]
    assert browser.closed


def test_browser_is_closed_even_when_saving_fails(store: RawStore, monkeypatch) -> None:
    browser = FakeBrowser({"MONQ50": ok_visit()})

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(store, "save", boom)
    with pytest.raises(OSError):
        run(store, ["MONQ50"], browser)
    assert browser.closed


def test_browser_that_cannot_start_raises_a_clear_error(store: RawStore) -> None:
    def no_browser(_s):
        raise nse_quote.BrowserUnavailableError("Executable doesn't exist")

    with pytest.raises(nse_quote.BrowserUnavailableError):
        capture_symbols(store, ["MONQ50"], NseQuoteSettings(), open_browser=no_browser)


# -- tick summary used by the recorder -------------------------------------------


def test_tick_capture_with_nothing_saved_raises_a_source_error(store: RawStore) -> None:
    browser = FakeBrowser({"MONQ50": PageVisit(200, ())})
    with pytest.raises(SourceError, match="nothing captured"):
        run_tick_capture(
            store,
            ["MONQ50"],
            NseQuoteSettings(),
            open_browser=lambda _s: browser,
            sleep=lambda s: None,
        )


def test_tick_capture_when_blocked_raises_the_blocked_error(store: RawStore) -> None:
    browser = FakeBrowser({"MONQ50": PageVisit(403, ())})
    with pytest.raises(SourceBlockedError):
        run_tick_capture(
            store,
            ["MONQ50"],
            NseQuoteSettings(),
            open_browser=lambda _s: browser,
            sleep=lambda s: None,
        )


def test_tick_capture_with_partial_success_returns_the_report(store: RawStore) -> None:
    browser = FakeBrowser({"MONQ50": ok_visit(), "MON100": PageVisit(200, ())})
    report = run_tick_capture(
        store,
        ["MONQ50", "MON100"],
        NseQuoteSettings(),
        open_browser=lambda _s: browser,
        sleep=lambda s: None,
    )
    assert report.captured == ["MONQ50"] and "MON100" in report.failed


# -- the Playwright wrapper, with fake page objects ----------------------------------------


class FakeResponse:
    def __init__(self, url: str, status: int, body: bytes, *, broken: bool = False) -> None:
        self.url, self.status, self._body, self._broken = url, status, body, broken

    def body(self) -> bytes:
        if self._broken:
            raise RuntimeError("response body is gone")
        return self._body


class FakePage:
    def __init__(self, answers: list[FakeResponse], main_status: int = 200) -> None:
        self.answers, self.main_status = answers, main_status
        self.handler = None
        self.closed = False
        self.waits = 0
        self.goto_url = ""

    def on(self, event: str, handler) -> None:
        assert event == "response"
        self.handler = handler

    def goto(self, url: str, **kw):
        self.goto_url = url
        for answer in self.answers:  # the page's own requests fire while it loads
            self.handler(answer)
        return type("Main", (), {"status": self.main_status})()

    def wait_for_timeout(self, ms: int) -> None:
        self.waits += 1

    def close(self) -> None:
        self.closed = True


class FakeContext:
    def __init__(self, page: FakePage) -> None:
        self.page = page

    def new_page(self) -> FakePage:
        return self.page

    def close(self) -> None:
        pass


def wrapper(page: FakePage) -> nse_quote._PlaywrightBrowser:
    return nse_quote._PlaywrightBrowser(None, None, None, FakeContext(page))


def test_wrapper_keeps_only_this_symbols_quote_answers_and_their_exact_bytes() -> None:
    page = FakePage(
        [
            FakeResponse(f"{BASE}?symbol=MONQ50", 200, quote_body()),
            FakeResponse(f"{BASE}?symbol=MONQ50&section=trade_info", 200, book_body()),
            FakeResponse("https://www.nseindia.com/api/chart-databyindex?x=1", 200, b"{}"),
            FakeResponse(f"{BASE}?symbol=MON100", 200, quote_body("MON100")),
        ]
    )
    visit = wrapper(page).visit("MONQ50", wait_s=15)
    assert [(r.kind, r.body) for r in visit.responses] == [
        (KIND_QUOTE, quote_body()),
        (KIND_TRADE_INFO, book_body()),
    ]
    assert visit.page_status == 200 and page.closed
    assert page.goto_url == "https://www.nseindia.com/get-quotes/equity?symbol=MONQ50"
    assert page.waits == 0  # both answers were there already: no needless waiting
    assert all(r.received_at.tzinfo is not None for r in visit.responses)


def test_wrapper_waits_only_as_long_as_needed_then_gives_up() -> None:
    page = FakePage([FakeResponse(f"{BASE}?symbol=MONQ50", 200, quote_body())])
    visit = wrapper(page).visit("MONQ50", wait_s=1)
    assert page.waits == 4  # 1 s in 0.25 s steps, never longer
    assert [r.kind for r in visit.responses] == [KIND_QUOTE]


def test_wrapper_skips_an_answer_whose_body_is_gone() -> None:
    page = FakePage(
        [
            FakeResponse(f"{BASE}?symbol=MONQ50", 200, quote_body()),
            FakeResponse(f"{BASE}?symbol=MONQ50&section=trade_info", 200, b"", broken=True),
        ]
    )
    visit = wrapper(page).visit("MONQ50", wait_s=0.25)
    assert [r.kind for r in visit.responses] == [KIND_QUOTE]


def test_wrapper_reports_a_refused_page_without_waiting() -> None:
    page = FakePage([], main_status=403)
    visit = wrapper(page).visit("MONQ50", wait_s=15)
    assert visit.page_status == 403 and visit.responses == () and page.waits == 0 and page.closed


def test_missing_playwright_package_gives_the_install_hint(monkeypatch) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)  # makes the import fail
    with pytest.raises(nse_quote.BrowserUnavailableError, match="uv sync --extra inav"):
        nse_quote.open_playwright_browser(NseQuoteSettings())


@pytest.mark.skipif(not nse_quote.playwright_available(), reason="Playwright not installed")
def test_missing_browser_program_gives_the_install_hint() -> None:
    cfg = NseQuoteSettings(browser_executable="/nonexistent/chrome", launch_timeout_s=5)
    with pytest.raises(nse_quote.BrowserUnavailableError, match="cannot start the browser"):
        nse_quote.open_playwright_browser(cfg)


# -- the manual command -------------------------------------------------------------------


def test_cli_nse_quote_prints_saved_files_and_problems(monkeypatch, tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from etfp import cli

    monkeypatch.setattr(cli.paths, "raw_dir", lambda: tmp_path / "raw")
    browser = FakeBrowser({"MONQ50": ok_visit(), "MON100": PageVisit(200, ())})
    real = nse_quote.capture_symbols
    seen = {}

    def fake_capture(store, symbols, settings, **kw):
        seen["headless"] = settings.headless
        return real(store, symbols, settings, open_browser=lambda _s: browser, sleep=lambda s: None)

    monkeypatch.setattr(nse_quote, "capture_symbols", fake_capture)
    result = CliRunner().invoke(
        cli.app, ["collect", "nse-quote", "-s", "MONQ50", "-s", "MON100", "--headed"]
    )
    assert result.exit_code == 0, result.output
    assert "OK nse_quote/2026-11-16/MONQ50_103015.json" in result.output
    assert "MON100" in result.output and seen["headless"] is False


def test_cli_nse_quote_missing_browser_exits_1_with_the_fix(monkeypatch, tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from etfp import cli

    monkeypatch.setattr(cli.paths, "raw_dir", lambda: tmp_path / "raw")

    def no_browser(*a, **k):
        raise nse_quote.BrowserUnavailableError(
            "cannot start the browser (x); run playwright install"
        )

    monkeypatch.setattr(nse_quote, "capture_symbols", no_browser)
    result = CliRunner().invoke(cli.app, ["collect", "nse-quote", "-s", "MONQ50"])
    assert result.exit_code == 1 and "playwright install" in result.output
