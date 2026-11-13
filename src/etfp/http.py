"""Polite HTTP client for downloading from NSE, AMFI and friends.

Why this exists: every data source is somebody else's server. The project rules
say we must not hammer them, must retry gently, and must fail loudly (naming the
source and the date) so a broken download is never silently skipped.

What the client does:

* waits between requests to the same site (NSE: at least 1.5 s),
* keeps cookies between calls (NSE only answers API calls after a page visit
  has handed out cookies),
* retries only *temporary* trouble (server errors, timeouts, dropped
  connections) with exponentially growing waits,
* never retries a *block* (HTTP 401/403/429). Retrying a block is exactly what
  gets an IP address banned, so we stop at once and tell the human.

Error messages never contain cookies or header values.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from types import TracebackType
from typing import Any
from urllib.parse import urlsplit

import httpx

from etfp.timeutil import now_ist

logger = logging.getLogger("etfp.http")

# A generic desktop Chrome string. NSE rejects clients with no browser-like UA.
# Deliberately contains no names, emails or project identifiers.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# host-suffix -> minimum seconds between requests. "nseindia.com" covers
# www.nseindia.com and nsearchives.nseindia.com, which share one timer.
DEFAULT_MIN_INTERVAL_S: dict[str, float] = {"nseindia.com": 1.5}


class SourceError(RuntimeError):
    """A download from a data source failed. Always says which source and which date."""

    def __init__(
        self,
        message: str,
        *,
        source: str,
        day: date | None,
        url: str,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.source = source
        self.day = day
        self.url = url
        self.status_code = status_code


class SourceBlockedError(SourceError):
    """HTTP 401, 403 or 429: the site is refusing us. Do not retry; a human must look."""


class SourceNotFoundError(SourceError):
    """HTTP 404. Normal on market holidays (no file is published), so callers may expect it."""


class SourceUnavailableError(SourceError):
    """Retries exhausted on 5xx, timeouts or connection errors."""


@dataclass(frozen=True)
class FetchResult:
    """What a successful download returned. `content` is the body untouched."""

    url: str  # final URL after redirects
    status_code: int
    content: bytes  # exactly as received (after transport decompression)
    headers: dict[str, str]
    fetched_at: datetime  # timezone-aware, Asia/Kolkata


_BLOCKED_STATUS = frozenset({401, 403, 429})


def _host_key(host: str, intervals: Mapping[str, float]) -> str:
    """Pick the timer key for a host: the longest matching suffix, else the host itself."""
    host = host.lower()
    best: str | None = None
    for suffix in intervals:
        if (host == suffix or host.endswith("." + suffix)) and (
            best is None or len(suffix) > len(best)
        ):
            best = suffix
    return best if best is not None else host


class PoliteClient:
    """A thin wrapper around one `httpx.Client` that is gentle with servers."""

    def __init__(
        self,
        *,
        min_interval_s: Mapping[str, float] | None = None,
        default_interval_s: float = 0.5,
        max_retries: int = 3,
        backoff_base_s: float = 2.0,
        timeout_s: float = 30.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        rng: Callable[[], float] = random.random,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        self._intervals: dict[str, float] = {
            k.lower(): v for k, v in {**DEFAULT_MIN_INTERVAL_S, **(min_interval_s or {})}.items()
        }
        self._default_interval_s = default_interval_s
        self._max_retries = max_retries
        self._backoff_base_s = backoff_base_s
        self._sleep = sleep
        self._monotonic = monotonic
        self._rng = rng
        # timer key -> monotonic time at which the last request to that site finished
        self._last_finished: dict[str, float] = {}
        self._client = httpx.Client(
            follow_redirects=True,  # cookies persist on the client automatically
            timeout=timeout_s,
            transport=transport,
            headers={"User-Agent": user_agent, "Accept-Language": "en-US,en;q=0.9"},
        )

    # -- context manager -------------------------------------------------

    def __enter__(self) -> PoliteClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # -- rate limiting ---------------------------------------------------

    def _throttle(self, key: str) -> None:
        """Sleep until the site's minimum gap since its previous request has passed."""
        last = self._last_finished.get(key)
        if last is None:
            return
        interval = self._intervals.get(key, self._default_interval_s)
        wait = interval - (self._monotonic() - last)
        if wait > 0:
            self._sleep(wait)

    def _mark_finished(self, key: str) -> None:
        self._last_finished[key] = self._monotonic()

    # -- main entry point ------------------------------------------------

    def get(
        self,
        url: str,
        *,
        source: str,
        day: date | None = None,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, str] | None = None,
    ) -> FetchResult:
        """GET a URL politely. Returns the response or raises a SourceError subclass."""
        return self._request("GET", url, source=source, day=day, headers=headers, params=params)

    def post(
        self,
        url: str,
        *,
        source: str,
        day: date | None = None,
        headers: Mapping[str, str] | None = None,
        json: Any | None = None,
        content: bytes | None = None,
    ) -> FetchResult:
        """POST to a URL politely, with the same rules as ``get``.

        Give a JSON body (``json=``) or raw bytes (``content=``), not both. With
        neither, an empty body is sent with ``Content-Length: 0`` (some servers
        answer HTTP 411 "length required" to a POST with no length header).

        Retrying a POST after a 5xx or a timeout is acceptable here only because
        every POST this project sends is a read-only query ("give me the current
        values"); repeating it cannot change anything on the server.
        """
        if json is not None and content is not None:
            raise ValueError("post(): give either json= or content=, not both")
        if json is None and content is None:
            content = b""
        return self._request(
            "POST", url, source=source, day=day, headers=headers, json=json, content=content
        )

    def _request(
        self,
        method: str,
        url: str,
        *,
        source: str,
        day: date | None,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, str] | None = None,
        json: Any | None = None,
        content: bytes | None = None,
    ) -> FetchResult:
        """The one code path behind get() and post(): throttle, retry, classify errors."""
        key = _host_key(urlsplit(url).hostname or "", self._intervals)
        last_exc: Exception | None = None
        last_status: int | None = None

        for attempt in range(self._max_retries + 1):
            if attempt > 0:
                self._backoff(attempt - 1, source, day, url, last_status, last_exc)
            self._throttle(key)
            try:
                response = self._client.request(
                    method, url, headers=headers, params=params, json=json, content=content
                )
            except httpx.TransportError as exc:  # timeouts, connection errors
                self._mark_finished(key)
                last_exc, last_status = exc, None
                continue
            except httpx.HTTPError as exc:  # e.g. too many redirects: retrying won't help
                self._mark_finished(key)
                raise self._error(
                    SourceError, source, day, url, None, f"request failed ({type(exc).__name__})"
                ) from exc
            self._mark_finished(key)

            status = response.status_code
            if 200 <= status < 300:
                return self._success(response, source, day)
            if status in _BLOCKED_STATUS:
                raise self._error(SourceBlockedError, source, day, url, status, "blocked")
            if status == 404:
                raise self._error(SourceNotFoundError, source, day, url, status, "not found")
            if status >= 500:
                last_exc, last_status = None, status
                continue
            raise self._error(SourceError, source, day, url, status, "unexpected response")

        what = (
            f"unavailable after {self._max_retries + 1} attempts ({type(last_exc).__name__})"
            if last_exc is not None
            else f"unavailable after {self._max_retries + 1} attempts"
        )
        raise self._error(SourceUnavailableError, source, day, url, last_status, what) from last_exc

    # -- helpers ---------------------------------------------------------

    def _backoff(
        self,
        retry_index: int,
        source: str,
        day: date | None,
        url: str,
        status: int | None,
        exc: Exception | None,
    ) -> None:
        """Wait base * 2**n plus random jitter before the next try, and log it."""
        wait = self._backoff_base_s * 2**retry_index + self._rng() * self._backoff_base_s
        reason = f"HTTP {status}" if status is not None else type(exc).__name__
        logger.warning(
            "%s for %s: %s, retry %d/%d in %.1fs",
            source,
            _day_text(day),
            reason,
            retry_index + 1,
            self._max_retries,
            wait,
        )
        self._sleep(wait)

    def _success(self, response: httpx.Response, source: str, day: date | None) -> FetchResult:
        content = response.content
        logger.info(
            "%s for %s: HTTP %d, %d bytes",
            source,
            _day_text(day),
            response.status_code,
            len(content),
        )
        return FetchResult(
            url=str(response.url),
            status_code=response.status_code,
            content=content,
            headers=dict(response.headers),
            fetched_at=now_ist(),
        )

    @staticmethod
    def _error(
        cls: type[SourceError],
        source: str,
        day: date | None,
        url: str,
        status: int | None,
        what: str,
    ) -> SourceError:
        """Build (and log) an error whose text names source, date, status and URL."""
        status_text = f" (HTTP {status})" if status is not None else ""
        message = f"{source} for {_day_text(day)} {what}{status_text}: {url}"
        logger.error(message)
        return cls(message, source=source, day=day, url=url, status_code=status)


def _day_text(day: date | None) -> str:
    return day.isoformat() if day is not None else "no date"
