"""Fund-house iNAV pages (Motilal Oswal, Mirae Asset, Nippon India).

Why this exists: an ETF's iNAV ("indicative NAV") is the fund house's running
estimate, during trading hours, of what one unit's holdings are worth. Nobody
publishes an archive of it, so the only way to have iNAV history is to write
down what the fund house shows, every 15 minutes, as it happens. Each fund
house has one public JSON address that returns the iNAV of all its ETFs, so one
request per fund house per tick is enough.

Two steps, kept apart on purpose (see CLAUDE.md):

* ``fetch_snapshot`` does the network request, checks that the answer still has
  the shape we expect, and saves the body untouched in the raw store.
* ``parse`` turns saved bytes into a table. It never touches the network, so it
  can be tested from the files in ``tests/fixtures/amc_inav/``.

Two warnings that matter for the research (details in docs/DECISIONS.md D15):

1. The fund-house timestamps look like the server's clock at the moment it
   answered, not the moment the iNAV was computed. On a market holiday the
   values stayed frozen while the stamp kept moving. So ``fetched_at`` (our own
   clock, IST) is the observation time, and ``amc_ts_raw`` is kept only as
   evidence, never parsed or trusted.
2. On 10 Nov 2026 Nippon's numbers were far from the official NAVs (Nifty 50
   BeES 250.72 against an official 285.81). They may be stale. They are
   recorded but must not be trusted until checked in a live trading session.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pandas as pd

from etfp.http import FetchResult, PoliteClient, SourceError
from etfp.rawstore import RawRecord, RawStore
from etfp.timeutil import IST, require_aware, today_ist

AMCS: tuple[str, ...] = ("motilal", "mirae", "nippon")

OUTPUT_COLUMNS: list[str] = [
    "amc",
    "symbol",
    "amc_name",
    "inav",
    "prev_nav",
    "amc_ts_raw",
    "fetched_at",
    "source_file",
]

_ACCEPT = "application/json, text/plain, */*"

# Nippon's response has no NSE symbol, only the scheme name. Only names we are
# certain about are listed; every other row keeps symbol = NA rather than a guess.
NIPPON_SYMBOLS: dict[str, str] = {
    "Nippon India ETF Nifty 50 BeES": "NIFTYBEES",
    "Nippon India ETF Hang Seng BeES": "HNGSNGBEES",
    "Nippon India ETF Gold BeES": "GOLDBEES",
    "Nippon India ETF Nifty Bank BeES": "BANKBEES",
}

# Motilal puts the live iNAV in rows whose name ends with this suffix.
_MOTILAL_SUFFIX = " iNAV"
_MOTILAL_LISTS = ("m50M100Data", "n100Data")


# ---------------------------------------------------------------------------
# Request definitions and body-shape checks
# ---------------------------------------------------------------------------
def _json_type(value: Any) -> str:
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    return type(value).__name__


def _motilal_problem(body: Any) -> str | None:
    if not isinstance(body, dict):
        return "expected a JSON object"
    outer = body.get("data")
    inner = outer.get("data") if isinstance(outer, dict) else None
    if not isinstance(inner, dict):
        return 'expected an object at ["data"]["data"]'
    if not isinstance(inner.get("n100Data"), list):
        return 'expected a list at ["data"]["data"]["n100Data"]'
    return None


def _mirae_problem(body: Any) -> str | None:
    if not isinstance(body, list):
        return "expected a JSON array"
    if not any(isinstance(row, dict) and "NSE_Symbol" in row for row in body):
        return 'expected at least one row with key "NSE_Symbol"'
    return None


def _nippon_problem(body: Any) -> str | None:
    if not isinstance(body, dict):
        return "expected a JSON object"
    if not isinstance(body.get("RVDetailsList"), list):
        return 'expected a list under "RVDetailsList"'
    return None


@dataclass(frozen=True)
class _Spec:
    """How to ask one fund house for its iNAV table."""

    method: str
    url: str
    headers: Mapping[str, str]
    problem: Callable[[Any], str | None]  # returns what is wrong with a parsed body, or None
    json_body: Any | None = None  # None on a POST means "empty body, Content-Length: 0"


_SPECS: dict[str, _Spec] = {
    "motilal": _Spec(
        method="POST",
        url="https://www.motilaloswalmf.com/mutualfund/api/v1/someFunc",
        headers={
            "Accept": _ACCEPT,
            "Origin": "https://www.motilaloswalmf.com",
            "Referer": "https://www.motilaloswalmf.com/inav",
        },
        json_body={"apiName": "GetINAVandPrice"},
        problem=_motilal_problem,
    ),
    "mirae": _Spec(
        method="GET",
        url="https://miraeassetetf.co.in/api/ticker",
        headers={
            "Accept": _ACCEPT,
            "Referer": "https://miraeassetetf.co.in/inav-baskets?tab=inav",
        },
        problem=_mirae_problem,
    ),
    "nippon": _Spec(
        method="POST",
        url="https://investeasy.nipponindiaim.com/Online/Realtime/DetailsFill",
        headers={
            "Content-Type": "application/json",
            "Accept": _ACCEPT,
            "Origin": "https://investeasy.nipponindiaim.com",
            "Referer": "https://investeasy.nipponindiaim.com/online/realtime/nav",
            "X-Requested-With": "XMLHttpRequest",
        },
        problem=_nippon_problem,
    ),
}


def _spec(amc: str) -> _Spec:
    try:
        return _SPECS[amc]
    except KeyError:
        raise ValueError(f"unknown fund house {amc!r}; expected one of {', '.join(AMCS)}") from None


def source_name(amc: str) -> str:
    """Raw-store source name for one fund house, e.g. ``amc_inav_motilal``."""
    _spec(amc)
    return f"amc_inav_{amc}"


def raw_name(ts: datetime) -> str:
    """File name for a snapshot taken at ``ts``: ``inav_HHMMSS.json`` in IST."""
    require_aware(ts, "ts")
    return ts.astimezone(IST).strftime("inav_%H%M%S.json")


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------
def _check_shape(amc: str, content: bytes) -> tuple[str | None, str]:
    """Return (problem or None, first JSON type seen). Never includes body text."""
    try:
        body = json.loads(content)
    except ValueError:  # includes UnicodeDecodeError and JSONDecodeError
        return "body is not JSON (bot-protection or error page?)", "not JSON"
    return _spec(amc).problem(body), _json_type(body)


def fetch_snapshot(client: PoliteClient, store: RawStore, amc: str) -> RawRecord:
    """Ask one fund house for its iNAV table and save the answer untouched.

    The body is validated first. A page that is not what we expect (for
    example an HTML "are you a robot?" page that came back with HTTP 200) is
    never stored as if it were data: it raises ``SourceError`` instead.
    """
    spec = _spec(amc)
    source = source_name(amc)
    if spec.method == "GET":
        result = client.get(spec.url, source=source, day=today_ist(), headers=spec.headers)
    else:
        result = client.post(
            spec.url, source=source, day=today_ist(), headers=spec.headers, json=spec.json_body
        )

    day = result.fetched_at.astimezone(IST).date()
    problem, first_type = _check_shape(amc, result.content)
    if problem is not None:
        raise _format_error(source, day, result, problem, first_type)
    return store.save_result(
        source=source, day=day, name=raw_name(result.fetched_at), result=result
    )


def _format_error(
    source: str, day: Any, result: FetchResult, problem: str, first_type: str
) -> SourceError:
    message = (
        f"{source} for {day.isoformat()} format changed: {problem} "
        f"(content length {len(result.content)} bytes, first JSON type seen: {first_type}); "
        f"nothing was stored: {result.url}"
    )
    return SourceError(
        message, source=source, day=day, url=result.url, status_code=result.status_code
    )


# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------
def _num(value: Any) -> float:
    """Number from a JSON number or numeric string; NaN when missing or not a number ("-")."""
    if value is None or isinstance(value, bool):
        return math.nan
    try:
        number = float(value.strip()) if isinstance(value, str) else float(value)
    except (TypeError, ValueError):
        return math.nan
    return number if math.isfinite(number) else math.nan


def _text(value: Any) -> str | None:
    return None if value is None else str(value)


def _rows(content: bytes, amc: str) -> list[dict[str, Any]]:
    """The fund house's own rows, reduced to our names. Pure: no network."""
    body = json.loads(content)
    if amc == "motilal":
        return _motilal_rows(body)
    if amc == "mirae":
        return _mirae_rows(body)
    if amc == "nippon":
        return _nippon_rows(body)
    raise ValueError(f"unknown fund house {amc!r}; expected one of {', '.join(AMCS)}")


def _motilal_rows(body: Any) -> list[dict[str, Any]]:
    data = body["data"]["data"]
    out: list[dict[str, Any]] = []
    for list_name in _MOTILAL_LISTS:
        for row in data.get(list_name) or []:
            name = row.get("secname")
            # The n100Data list also holds two plain end-of-day rows (names without
            # the " iNAV" suffix, dated midnight). They are last night's official
            # NAV, not a live estimate, so they are not iNAV and are dropped.
            if not isinstance(name, str) or not name.endswith(_MOTILAL_SUFFIX):
                continue
            out.append(
                {
                    "symbol": _text(row.get("nseSymbol")),
                    "amc_name": name,
                    "inav": _num(row.get("currNav")),
                    "prev_nav": _num(row.get("prevNAV")),
                    "amc_ts_raw": _text(row.get("currNavDate")),
                }
            )
    return out


def _mirae_rows(body: Any) -> list[dict[str, Any]]:
    return [
        {
            "symbol": _text(row.get("NSE_Symbol")),
            "amc_name": _text(row.get("ETF")),
            # INAV is sometimes a JSON string and sometimes a number; _num handles both.
            "inav": _num(row.get("INAV")),
            "prev_nav": _num(row.get("NAV")),
            # Two different timestamp formats appear in one response; kept as text.
            "amc_ts_raw": _text(row.get("timestamp")),
        }
        for row in body
        if isinstance(row, dict)
    ]


def _nippon_rows(body: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in body.get("RVDetailsList") or []:
        name = _text(row.get("SchName"))
        out.append(
            {
                "symbol": NIPPON_SYMBOLS.get(name.strip()) if name is not None else None,
                "amc_name": name,
                "inav": _num(row.get("CNav")),
                "prev_nav": _num(row.get("PNav")),
                "amc_ts_raw": _text(row.get("Realdt")),
            }
        )
    return out


def parse(content: bytes, *, amc: str, source_file: str, fetched_at: datetime) -> pd.DataFrame:
    """One row per ETF in a saved response.

    ``fetched_at`` (our clock, timezone-aware) is stamped on every row;
    ``source_file`` is the raw-store path the numbers came from.
    """
    require_aware(fetched_at, "fetched_at")
    rows = _rows(content, amc)
    n = len(rows)
    tz_index = (
        pd.DatetimeIndex([fetched_at] * n) if n else pd.DatetimeIndex([], tz=fetched_at.tzinfo)
    )
    frame = pd.DataFrame(
        {
            "amc": pd.array([amc] * n, dtype="string"),
            "symbol": pd.array([r["symbol"] for r in rows], dtype="string"),
            "amc_name": pd.array([r["amc_name"] for r in rows], dtype="string"),
            "inav": pd.array([r["inav"] for r in rows], dtype="float64"),
            "prev_nav": pd.array([r["prev_nav"] for r in rows], dtype="float64"),
            "amc_ts_raw": pd.array([r["amc_ts_raw"] for r in rows], dtype="string"),
            "fetched_at": pd.Series(tz_index),
            "source_file": pd.array([source_file] * n, dtype="string"),
        }
    )
    return frame[OUTPUT_COLUMNS]
