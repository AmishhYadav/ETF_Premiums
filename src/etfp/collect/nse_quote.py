"""NSE single-ETF quote capture through a real browser (DECISIONS D12).

Why a browser: NSE's quote API (``/api/quote-equity?symbol=...``) answers HTTP 403
to a plain script. The public quote *page* of an ETF, opened in a real browser,
asks that same API for its own data. So we open the page like a person would and
write down the answers the page received. We never call the hidden API ourselves.

What the page asks for (UNVERIFIED against a live capture, see below):

* ``/api/quote-equity?symbol=SYM``                    -> ``info``, ``metadata``,
  ``priceInfo`` (last price and, for ETFs, the iNAV)
* ``/api/quote-equity?symbol=SYM&section=trade_info`` -> ``marketDeptOrderBook``
  (total buy/sell quantity and the bid/ask depth)

Each answer is saved untouched in the raw store (project rule: raw data is
immutable) as ``nse_quote/<date>/<SYM>_HHMMSS.json`` and
``nse_quote/<date>/<SYM>_trade_info_HHMMSS.json``. Both files of one page visit
carry the same HHMMSS (the moment the first answer arrived), so the clean layer
can pair them.

Manners (CLAUDE.md "be polite to data sources"):

* one browser, one context per tick, one page at a time;
* at least ``page_gap_s`` (1.5 s) between two page loads;
* if NSE refuses us (HTTP 401/403/429, or the connection is cut at the protocol
  level) we stop for the rest of the tick instead of pushing harder;
* we do NOT disguise the browser (no fake user agent, no hiding of automation).
  If NSE refuses it, that is a human decision (DECISIONS D12), not ours.

Design, like the other collectors: ``capture_symbols`` does the saving with an
injected browser object, so every rule above is testable without a network; only
``open_playwright_browser`` touches Playwright, and it imports Playwright lazily
so the recorder still starts when the optional package is missing.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol
from urllib.parse import parse_qs, quote, urlsplit

from etfp.config import NseQuoteSettings
from etfp.http import SourceBlockedError, SourceError
from etfp.rawstore import RawRecord, RawStore
from etfp.timeutil import IST, now_ist

logger = logging.getLogger("etfp.collect")

SOURCE = "nse_quote"
PAGE_URL = "https://www.nseindia.com/get-quotes/equity?symbol={symbol}"

KIND_QUOTE = "quote"  # price block: info, metadata, priceInfo
KIND_TRADE_INFO = "trade_info"  # order book: marketDeptOrderBook
KINDS = (KIND_QUOTE, KIND_TRADE_INFO)

_BLOCK_STATUSES = frozenset({401, 403, 429})
# Chromium's wording when a site cuts the connection at protocol level (what NSE's bot
# protection did to our first plain headless attempt on 14 Nov 2026).
_BLOCK_MARKERS = ("ERR_HTTP2_PROTOCOL_ERROR", "ERR_CONNECTION_RESET", "ERR_ACCESS_DENIED")
_MAX_CONSECUTIVE_ERRORS = 3


class BrowserUnavailableError(RuntimeError):
    """Playwright is not installed, or its browser program is missing or will not start."""


@dataclass(frozen=True)
class CapturedResponse:
    """One answer the page received from NSE's quote API, exactly as received."""

    kind: str  # KIND_QUOTE or KIND_TRADE_INFO
    url: str
    status: int
    body: bytes
    received_at: datetime  # tz-aware


@dataclass(frozen=True)
class PageVisit:
    """What opening one quote page produced."""

    page_status: int | None  # HTTP status of the page itself (None if unknown)
    responses: tuple[CapturedResponse, ...]


class QuoteBrowser(Protocol):
    """The part of a browser session this module needs (faked in tests)."""

    def visit(self, symbol: str, *, wait_s: float) -> PageVisit: ...

    def close(self) -> None: ...


OpenBrowser = Callable[[NseQuoteSettings], QuoteBrowser]


@dataclass
class CaptureReport:
    """Outcome of one tick's capture, symbol by symbol."""

    saved: list[RawRecord] = field(default_factory=list)
    captured: list[str] = field(default_factory=list)  # symbols with a saved price block
    no_depth: list[str] = field(default_factory=list)  # saved a price block but no order book
    failed: dict[str, str] = field(default_factory=dict)  # symbol -> why nothing was saved
    skipped: list[str] = field(default_factory=list)  # not tried (blocked or out of time)
    blocked: str | None = None  # message when NSE refused us

    @property
    def problems(self) -> list[str]:
        """Plain-text list of everything that was not fully captured."""
        out = [f"{s}: {why}" for s, why in self.failed.items()]
        if self.no_depth:
            out.append("no order book for " + ", ".join(self.no_depth))
        if self.skipped:
            out.append("not tried: " + ", ".join(self.skipped))
        if self.blocked:
            out.append(self.blocked)
        return out


# ---------------------------------------------------------------------------
# Recognising the answers we want
# ---------------------------------------------------------------------------
def classify_url(url: str, symbol: str) -> str | None:
    """Which kind of quote answer this URL is for ``symbol``, or None to ignore it.

    Only the two quote-equity answers of this very symbol qualify. Everything else
    the page loads (charts, announcements, other sections) is ignored.
    """
    parts = urlsplit(url)
    host = parts.hostname or ""
    if not (host == "nseindia.com" or host.endswith(".nseindia.com")):
        return None
    if parts.path.rstrip("/") != "/api/quote-equity":
        return None
    query = parse_qs(parts.query)
    if [s.upper() for s in query.get("symbol", [])] != [symbol.upper()]:
        return None
    sections = query.get("section", [])
    if not sections:
        return KIND_QUOTE
    if sections == ["trade_info"]:
        return KIND_TRADE_INFO
    return None


def raw_name(symbol: str, kind: str, fetched_at: datetime) -> str:
    """``MONQ50_103015.json`` or ``MONQ50_trade_info_103015.json`` (IST clock time)."""
    stamp = fetched_at.astimezone(IST).strftime("%H%M%S")
    middle = "" if kind == KIND_QUOTE else f"_{kind}"
    return f"{symbol}{middle}_{stamp}.json"


def _body_problem(kind: str, symbol: str, content: bytes) -> str | None:
    """Why this answer is not the expected JSON, or None. Bad answers are never stored."""
    try:
        body = json.loads(content)
    except ValueError:
        return f"not JSON, starts with {content[:30]!r}"
    if not isinstance(body, dict):
        return "JSON is not an object"
    if kind == KIND_QUOTE:
        info = body.get("info")
        found = info.get("symbol") if isinstance(info, dict) else None
        if not isinstance(found, str) or found.strip().upper() != symbol.upper():
            return f'"info.symbol" is {found!r}, expected {symbol!r}'
    elif not isinstance(body.get("marketDeptOrderBook"), dict):
        return 'JSON without a "marketDeptOrderBook" object'
    return None


def _looks_blocked(message: str) -> bool:
    return any(marker in message for marker in _BLOCK_MARKERS)


# ---------------------------------------------------------------------------
# One tick
# ---------------------------------------------------------------------------
def capture_symbols(
    store: RawStore,
    symbols: list[str],
    settings: NseQuoteSettings,
    *,
    open_browser: OpenBrowser | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> CaptureReport:
    """Open each symbol's quote page once and save the answers the page received.

    Raises BrowserUnavailableError when no browser can be started. Everything that
    goes wrong for one symbol is recorded in the report and never stops the others,
    except a refusal by NSE, which stops the whole tick (politeness).
    """
    opener = open_browser if open_browser is not None else open_playwright_browser
    report = CaptureReport()
    if not symbols:
        return report
    browser = opener(settings)
    started = clock()
    errors_in_a_row = 0
    try:
        for index, symbol in enumerate(symbols):
            if report.blocked is not None or errors_in_a_row >= _MAX_CONSECUTIVE_ERRORS:
                report.skipped.append(symbol)
                continue
            if clock() - started >= settings.tick_budget_s:
                report.skipped.append(symbol)  # out of time: leave the rest for the next tick
                continue
            if index > 0:
                sleep(settings.page_gap_s)
            try:
                visit = browser.visit(symbol, wait_s=settings.wait_s)
            except SourceBlockedError as exc:
                report.blocked = str(exc)
                report.failed[symbol] = "blocked"
                continue
            except Exception as exc:  # one broken page must not stop the others
                text = f"{type(exc).__name__}: {str(exc)[:200]}"
                if _looks_blocked(text):
                    report.blocked = f"NSE cut the connection ({text})"
                    report.failed[symbol] = "blocked"
                else:
                    report.failed[symbol] = text
                    errors_in_a_row += 1
                continue
            errors_in_a_row = 0
            _save_visit(store, symbol, visit, report)
    finally:
        try:
            browser.close()
        except Exception:  # closing must never hide the real result
            logger.debug("closing the quote browser failed", exc_info=True)
    return report


def _save_visit(store: RawStore, symbol: str, visit: PageVisit, report: CaptureReport) -> None:
    """Validate and store the answers of one page visit, pairing them by one timestamp."""
    if visit.page_status in _BLOCK_STATUSES or any(
        r.status in _BLOCK_STATUSES for r in visit.responses
    ):
        status = visit.page_status if visit.page_status in _BLOCK_STATUSES else next(
            r.status for r in visit.responses if r.status in _BLOCK_STATUSES
        )  # fmt: skip
        report.blocked = f"NSE answered HTTP {status} for the {symbol} quote page"
        report.failed[symbol] = f"HTTP {status}"
        return

    good: dict[str, CapturedResponse] = {}
    for response in visit.responses:  # the first good answer of each kind wins
        if response.kind not in KINDS or response.kind in good:
            continue
        if response.status != 200:
            continue
        if _body_problem(response.kind, symbol, response.body) is None:
            good[response.kind] = response
    if KIND_QUOTE not in good and KIND_TRADE_INFO not in good:
        report.failed[symbol] = _why_nothing(symbol, visit)
        return

    fetched_at = min(r.received_at for r in good.values())
    day = fetched_at.astimezone(IST).date()
    for kind in KINDS:
        response = good.get(kind)
        if response is None:
            continue
        record = store.save(
            source=SOURCE,
            day=day,
            name=raw_name(symbol, kind, fetched_at),
            content=response.body,
            url=response.url,
            status_code=response.status,
            fetched_at=response.received_at,
        )
        report.saved.append(record)
    if KIND_QUOTE in good:
        report.captured.append(symbol)
    if KIND_TRADE_INFO not in good:
        report.no_depth.append(symbol)
    elif KIND_QUOTE not in good:
        report.failed[symbol] = "saved the order book but no price block"
        report.captured.append(symbol)


def _why_nothing(symbol: str, visit: PageVisit) -> str:
    if not visit.responses:
        return f"the {symbol} quote page made no quote request (page status {visit.page_status})"
    first = visit.responses[0]
    if first.status != 200:
        return f"quote request answered HTTP {first.status}"
    problem = _body_problem(first.kind, symbol, first.body)
    return f"unexpected quote answer ({problem})"


# ---------------------------------------------------------------------------
# The real browser (Playwright). Imported lazily: it is an optional extra.
# ---------------------------------------------------------------------------
def playwright_available() -> bool:
    """True if the optional ``playwright`` package can be imported."""
    try:
        return importlib.util.find_spec("playwright") is not None
    except (ImportError, ValueError):
        return False


class _PlaywrightBrowser:
    """One browser and one context, reused for every page of a tick."""

    def __init__(self, playwright_cm: Any, playwright: Any, browser: Any, context: Any) -> None:
        self._cm = playwright_cm
        self._pw = playwright
        self._browser = browser
        self._context = context

    def visit(self, symbol: str, *, wait_s: float) -> PageVisit:
        url = PAGE_URL.format(symbol=quote(symbol, safe=""))
        seen: list[tuple[str, Any, datetime]] = []  # (kind, playwright response, arrival time)

        def on_response(response: Any) -> None:
            kind = classify_url(response.url, symbol)
            if kind is not None:
                seen.append((kind, response, now_ist()))

        page = self._context.new_page()
        page.on("response", on_response)
        try:
            main = page.goto(url, wait_until="domcontentloaded", timeout=int(wait_s * 1000) * 2)
            page_status = main.status if main is not None else None
            if page_status in _BLOCK_STATUSES:
                return PageVisit(page_status, ())
            # Wait (in short steps, so Playwright keeps delivering events) until the
            # page has asked for both answers, or the time is up.
            waited = 0.0
            while waited < wait_s and {k for k, _, _ in seen} != set(KINDS):
                page.wait_for_timeout(250)
                waited += 0.25
            captured = []
            for kind, response, arrived in seen:
                try:
                    body = response.body()
                except Exception:  # the page may have dropped it; treat as not received
                    continue
                captured.append(
                    CapturedResponse(kind, response.url, response.status, body, arrived)
                )
            return PageVisit(page_status, tuple(captured))
        finally:
            page.close()

    def close(self) -> None:
        """Close everything; a failure in one step must not stop the next."""
        steps: list[Callable[[], Any]] = [
            self._context.close,
            self._browser.close,
            lambda: self._cm.__exit__(None, None, None),
        ]
        for step in steps:
            try:
                step()
            except Exception:
                logger.debug("error while closing the quote browser", exc_info=True)


def open_playwright_browser(settings: NseQuoteSettings) -> QuoteBrowser:
    """Start Chromium through Playwright. Raises BrowserUnavailableError if that is impossible."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise BrowserUnavailableError(
            "Playwright is not installed; run `uv sync --extra inav` "
            "and `uv run playwright install chromium`"
        ) from exc
    cm = sync_playwright()
    try:
        pw = cm.__enter__()
        launch: dict[str, Any] = {
            "headless": settings.headless,
            "timeout": int(settings.launch_timeout_s * 1000),
        }
        if settings.channel:
            launch["channel"] = settings.channel
        if settings.browser_executable:
            launch["executable_path"] = settings.browser_executable
        browser = pw.chromium.launch(**launch)
        context = browser.new_context(locale="en-IN", timezone_id="Asia/Kolkata")
    except Exception as exc:
        try:
            cm.__exit__(None, None, None)
        except Exception:
            logger.debug("error while stopping Playwright", exc_info=True)
        first_line = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
        raise BrowserUnavailableError(
            f"cannot start the browser ({first_line[:200]}); "
            "if the browser is missing run `uv run playwright install chromium`"
        ) from exc
    return _PlaywrightBrowser(cm, pw, browser, context)


# ---------------------------------------------------------------------------
# Used by the recorder: one tick, summarised as success or one clear error
# ---------------------------------------------------------------------------
def run_tick_capture(
    store: RawStore,
    symbols: list[str],
    settings: NseQuoteSettings,
    *,
    open_browser: OpenBrowser | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> CaptureReport:
    """Capture all symbols; raise SourceError / SourceBlockedError if nothing was saved.

    Partly successful ticks return normally (the report lists the gaps), so the
    recorder keeps the data it got and still tells the owner what is missing.
    """
    report = capture_symbols(
        store, symbols, settings, open_browser=open_browser, sleep=sleep, clock=clock
    )
    if report.captured or report.saved:
        return report
    day = now_ist().date()
    detail = "; ".join(report.problems) or "no ETF symbols to capture"
    cls = SourceBlockedError if report.blocked else SourceError
    raise cls(
        f"{SOURCE} for {day.isoformat()}: nothing captured ({detail})",
        source=SOURCE,
        day=day,
        url=PAGE_URL.format(symbol=symbols[0] if symbols else ""),
        status_code=None,
    )
