"""NSE live ETF list API: an intraday snapshot of every ETF's price, volume and last NAV.

The page behind https://www.nseindia.com/market-data/exchange-traded-funds-etf
loads its table from https://www.nseindia.com/api/etf. We record that response
every 15 minutes while the market is open (docs/DECISIONS.md D12).

Important finance caveat: the `nav` field in this response is the PREVIOUSLY
PUBLISHED end-of-day NAV (net asset value: what one unit's holdings were worth
when the fund house last calculated it). It is NOT a live iNAV (the "indicative
NAV" the fund house updates through the day). So it goes into the column
`nav_prev`, and must not be compared with the live price as if it were current.

NSE only answers this API after a visit to a normal page has handed out
cookies, so `fetch_snapshot()` first loads the page ("warming"), then calls the
API with the same client (the client keeps the cookies and spaces the calls).

Design: `fetch_snapshot()` downloads and stores the exact body; `parse()` turns
bytes into a table and never touches the network.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime

import pandas as pd

from etfp.http import PoliteClient, SourceError
from etfp.rawstore import RawRecord, RawStore
from etfp.timeutil import IST, require_aware, today_ist

SOURCE = "nse_etf_api"

PAGE_URL = "https://www.nseindia.com/market-data/exchange-traded-funds-etf"
API_URL = "https://www.nseindia.com/api/etf"
PAGE_HEADERS = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
API_HEADERS = {"Accept": "*/*", "Referer": PAGE_URL}

OUTPUT_COLUMNS: tuple[str, ...] = (
    "api_ts",
    "fetched_at",
    "nav_date",
    "market_status",
    "symbol",
    "series",
    "underlying",
    "open",
    "high",
    "low",
    "last_price",
    "prev_close",
    "change",
    "pct_change",
    "volume",
    "value",
    "nav_prev",
    "source_file",
)

# JSON field of each item -> output column. Numbers arrive as text.
_FLOAT_FIELDS = {
    "open": "open",
    "high": "high",
    "low": "low",
    "ltP": "last_price",  # last traded price
    "prevClose": "prev_close",
    "chn": "change",  # change in rupees since the previous close
    "per": "pct_change",  # change in percent since the previous close
    "trdVal": "value",  # traded value so far today, in rupees
    "nav": "nav_prev",  # the PREVIOUS published NAV, not a live iNAV
}
_INT_FIELDS = {"qty": "volume"}
_TEXT_FIELDS = {"symbol": "symbol", "series": "series", "assets": "underlying"}
_REQUIRED_ITEM_KEYS = (*_FLOAT_FIELDS, *_INT_FIELDS, *_TEXT_FIELDS)

_MISSING = frozenset({"", "-"})
_NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)\Z")
_INTEGER = re.compile(r"[+-]?\d+\Z")
_STAMP = re.compile(r"(\d{1,2})-([A-Za-z]{3})-(\d{4})(?: (\d{2}):(\d{2}):(\d{2}))?\Z")
_MONTHS = {
    m: i
    for i, m in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1
    )
}


def _timestamps(value: datetime | None, count: int) -> pd.Series:
    """A column of `count` copies of one Indian-time timestamp (NaT when value is None)."""
    if value is None:
        index = pd.DatetimeIndex([pd.NaT] * count).tz_localize(IST)
    else:
        index = pd.DatetimeIndex([value.astimezone(IST)] * count)
    return pd.Series(index.as_unit("ns"))


def raw_name(ts: datetime) -> str:
    """File name for a snapshot taken at `ts`: 'etf_HHMMSS.json' in Indian time."""
    require_aware(ts, "ts")
    return ts.astimezone(IST).strftime("etf_%H%M%S.json")


def fetch_snapshot(client: PoliteClient, store: RawStore, *, warm: bool = True) -> RawRecord:
    """Record one snapshot of the ETF API. Raises SourceError if the body is not the data.

    With `warm=True` the ETF web page is loaded first so NSE hands out cookies;
    that response is discarded and not stored. The API body is stored exactly
    as received, under the Indian date and time it arrived. A body that is not
    JSON with a "data" list (for example an HTML block page) is never stored.
    """
    if warm:
        client.get(PAGE_URL, source=SOURCE, day=today_ist(), headers=PAGE_HEADERS)
    result = client.get(API_URL, source=SOURCE, day=today_ist(), headers=API_HEADERS)
    day: date = result.fetched_at.astimezone(IST).date()
    problem = _body_problem(result.content)
    if problem is not None:
        raise SourceError(
            f"{SOURCE} for {day.isoformat()}: response is not the expected JSON ({problem}); "
            f"not stored: {API_URL}",
            source=SOURCE,
            day=day,
            url=API_URL,
            status_code=result.status_code,
        )
    return store.save_result(
        source=SOURCE, day=day, name=raw_name(result.fetched_at), result=result
    )


def _body_problem(content: bytes) -> str | None:
    """Why this body is not an ETF snapshot, or None if it looks right."""
    try:
        body = json.loads(content)
    except ValueError:  # includes JSONDecodeError and UnicodeDecodeError
        return f"not JSON, starts with {content[:30]!r}"
    if not isinstance(body, dict) or not isinstance(body.get("data"), list):
        return 'JSON without a "data" list'
    return None


# -- parsing ------------------------------------------------------------------


def _stamp(text: object, what: str) -> tuple[date, tuple[int, int, int] | None]:
    """Parse '09-Nov-2026' or '09-Nov-2026 16:00:00' into a date and optional (h, m, s)."""
    match = _STAMP.match(text.strip()) if isinstance(text, str) else None
    month = _MONTHS.get(match.group(2).lower()) if match else None
    if match is None or month is None:
        raise ValueError(f"{SOURCE}: {what}: {text!r} is not a date like 09-Nov-2026")
    try:
        day = date(int(match.group(3)), month, int(match.group(1)))
    except ValueError as exc:
        raise ValueError(f"{SOURCE}: {what}: {text!r} is not a real calendar date") from exc
    clock = None
    if match.group(4) is not None:
        clock = (int(match.group(4)), int(match.group(5)), int(match.group(6)))
    return day, clock


def _number_text(value: object, where: str) -> str:
    """Normalise a JSON number or numeric text to a plain string; '' means missing."""
    if value is None:
        return ""
    if isinstance(value, bool):
        raise ValueError(f"{SOURCE}: {where}: {value!r} is not a number")
    if isinstance(value, int | float):
        return repr(value)
    if isinstance(value, str):
        text = value.strip().replace(",", "")
        return "" if text in _MISSING else text
    raise ValueError(f"{SOURCE}: {where}: {value!r} is not a number")


def _float(value: object, where: str) -> float:
    text = _number_text(value, where)
    if text == "":
        return float("nan")
    if not _NUMBER.match(text):
        raise ValueError(f"{SOURCE}: {where}: {value!r} is not a number")
    return float(text)


def _int(value: object, where: str) -> int | None:
    text = _number_text(value, where)
    if text == "":
        return None
    if not _INTEGER.match(text):
        raise ValueError(f"{SOURCE}: {where}: {value!r} is not a whole number")
    return int(text)


def parse(content: bytes, *, source_file: str, fetched_at: datetime | None = None) -> pd.DataFrame:
    """Turn a stored API body into a table with one row per ETF entry in the response.

    `api_ts` is NSE's own timestamp of the data (Indian time). `fetched_at` is
    when we downloaded it (pass the raw record's `fetched_at`); NA if not given.
    The same symbol can appear twice in NSE's response (seen for NXT100CASE, one
    entry with all-zero prices); both entries are kept as received.
    """
    try:
        body = json.loads(content)
    except ValueError as exc:
        raise ValueError(f"{SOURCE}: body is not JSON (starts with {content[:40]!r})") from exc
    if not isinstance(body, dict):
        raise ValueError(f"{SOURCE}: top level of the JSON is not an object")
    missing = [k for k in ("timestamp", "data", "navDate", "marketStatus") if k not in body]
    if missing:
        raise ValueError(f"{SOURCE}: JSON is missing key(s) {missing}; has {sorted(body)}")
    items = body["data"]
    if not isinstance(items, list):
        raise ValueError(f'{SOURCE}: "data" is not a list')
    if not items:
        raise ValueError(f'{SOURCE}: "data" list is empty')
    status = body["marketStatus"]
    if not isinstance(status, dict) or "marketStatus" not in status:
        raise ValueError(f'{SOURCE}: "marketStatus" has no "marketStatus" field')
    if fetched_at is not None:
        require_aware(fetched_at, "fetched_at")

    day, clock = _stamp(body["timestamp"], "timestamp")
    if clock is None:
        raise ValueError(f"{SOURCE}: timestamp: {body['timestamp']!r} has no time of day")
    api_ts = datetime(day.year, day.month, day.day, *clock, tzinfo=IST)
    nav_date = None if body["navDate"] in (None, "", "-") else _stamp(body["navDate"], "navDate")[0]
    market_status = None if status["marketStatus"] in (None, "") else str(status["marketStatus"])

    out: dict[str, list] = {name: [] for name in OUTPUT_COLUMNS}
    for n, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(f"{SOURCE}: data[{n}] is not an object")
        absent = [k for k in _REQUIRED_ITEM_KEYS if k not in item]
        if absent:
            raise ValueError(f"{SOURCE}: data[{n}] is missing field(s) {absent}")
        for raw, col in _FLOAT_FIELDS.items():
            out[col].append(_float(item[raw], f"data[{n}].{raw}"))
        for raw, col in _INT_FIELDS.items():
            out[col].append(_int(item[raw], f"data[{n}].{raw}"))
        for raw, col in _TEXT_FIELDS.items():
            value = item[raw]
            text = value.strip() if isinstance(value, str) else ""
            if raw == "symbol" and not text:
                raise ValueError(f"{SOURCE}: data[{n}] has an empty symbol")
            out[col].append(text or None)

    count = len(items)
    data: dict[str, pd.Series] = {}
    for name in OUTPUT_COLUMNS:
        if name == "api_ts":
            data[name] = _timestamps(api_ts, count)
        elif name == "fetched_at":
            data[name] = _timestamps(fetched_at, count)
        elif name == "nav_date":
            data[name] = pd.Series([nav_date] * count, dtype=object)
        elif name == "market_status":
            data[name] = pd.Series(pd.array([market_status] * count, dtype="string"))
        elif name == "source_file":
            data[name] = pd.Series(pd.array([source_file] * count, dtype="string"))
        elif name in _INT_FIELDS.values():
            data[name] = pd.Series(pd.array(out[name], dtype="Int64"))
        elif name in _TEXT_FIELDS.values():
            data[name] = pd.Series(pd.array(out[name], dtype="string"))
        else:
            data[name] = pd.Series(out[name], dtype="float64")
    return pd.DataFrame(data, columns=list(OUTPUT_COLUMNS))
