"""A quick "are we online at all?" check, run before the daily downloads.

Why this exists: on 2026-11-17 the evening run started while the laptop had no network.
Every download failed with a connection error, and because each one is retried with
growing waits, the run spent 50 minutes failing 17 sources one by one. This probe answers
the question in a few seconds instead, so the run can say "offline (no network)" at once.

How it works, in plain words:
  1. Ask DNS for the address of a well-known site we already download from (no new third
     party is contacted). No DNS answer usually means no network.
  2. Send ONE small HEAD request there with a short timeout. ANY HTTP answer counts as
     online, even an error code such as 403: it proves packets go out and come back.
  3. Stop at the first site that answers (so normally this costs exactly one request).
     Only if every site fails do we call the machine offline.

NSE is deliberately not probed: it is the site most sensitive to automated traffic, and
the real downloads talk to it soon after anyway.
"""

from __future__ import annotations

import socket
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from etfp.http import DEFAULT_USER_AGENT

# Three independent organisations (different servers, different DNS zones), all of which
# the daily run downloads from anyway.
PROBE_URLS: tuple[str, ...] = (
    "https://www.amfiindia.com/",
    "https://www.bseindia.com/",
    "https://query1.finance.yahoo.com/",
)
PROBE_TIMEOUT_S = 5.0

Resolver = Callable[..., Any]


@dataclass(frozen=True)
class ProbeCheck:
    """The result for one site."""

    url: str
    ok: bool
    detail: str  # e.g. "HTTP 200" or "DNS lookup failed (gaierror: ...)"


@dataclass(frozen=True)
class ProbeResult:
    online: bool
    checks: tuple[ProbeCheck, ...]

    def summary(self) -> str:
        """One line naming every site tried and what happened."""
        return "; ".join(f"{urlsplit(c.url).hostname}: {c.detail}" for c in self.checks)

    def as_data(self) -> list[dict[str, Any]]:
        """For the JSON run record."""
        return [{"url": c.url, "ok": c.ok, "detail": c.detail} for c in self.checks]


def probe(
    urls: Sequence[str] = PROBE_URLS,
    *,
    timeout_s: float = PROBE_TIMEOUT_S,
    transport: httpx.BaseTransport | None = None,
    resolve: Resolver = socket.getaddrinfo,
) -> ProbeResult:
    """Check whether any of ``urls`` can be reached. Never raises for a network problem.

    ``transport`` and ``resolve`` exist so tests can pretend to be online or offline
    without touching the real network.
    """
    checks: list[ProbeCheck] = []
    with httpx.Client(
        timeout=timeout_s,
        transport=transport,
        follow_redirects=False,  # any answer will do; no need to chase redirects
        headers={"User-Agent": DEFAULT_USER_AGENT},
    ) as client:
        for url in urls:
            host = urlsplit(url).hostname or ""
            try:
                resolve(host, 443)
            except OSError as exc:  # socket.gaierror is a subclass of OSError
                checks.append(
                    ProbeCheck(url, False, f"DNS lookup failed ({type(exc).__name__}: {exc})")
                )
                continue
            try:
                response = client.head(url)
            except httpx.HTTPError as exc:
                checks.append(ProbeCheck(url, False, f"no answer ({type(exc).__name__})"))
                continue
            checks.append(ProbeCheck(url, True, f"HTTP {response.status_code}"))
            return ProbeResult(True, tuple(checks))
    return ProbeResult(False, tuple(checks))
