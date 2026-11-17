"""FBIL daily rupee reference rates (USD/INR and a few other currencies).

Why this exists (plain words)
-----------------------------
The rupee value of a US ETF's holdings moves with the dollar. Financial Benchmarks India
(FBIL), the RBI-recognised benchmark administrator, publishes one official "reference
rate" per Indian business day for USD/INR (and GBP, EUR, JPY, later AED, IDR, RUB). The
fund houses' own documents name this rate (or "the RBI reference rate", which since July
2018 is FBIL's) as their NAV exchange rate or its fallback (DECISIONS D36: Nippon uses it
first; Mirae and Motilal as a fallback). So it is the natural free, official covariate for
"what the rupee did" on each day.

What the source gives (checked live 2026-11-18, docs/DECISIONS.md D43)
-------------------------------------------------------------------------
``GET https://www.fbil.org.in/wasdm/refrates/fetchfiltered?fromDate=YYYY-MM-DD&
toDate=YYYY-MM-DD&authenticated=false`` is the JSON call behind the public "Reference Rate"
page of fbil.org.in. No login, no key. One request for a full calendar year (2025) took
about 4 s and returned 972 rows (243 days x 4 currencies). Each row::

    {"processRunDate": "2026-11-09 00:00:00", "subProdName": "INR / 1 USD",
     "displayTime": "2026-11-09 13:00:00", "rate": 93.857900, "comments": ""}

* ``rate`` is rupees per ``N`` units of the foreign currency named in ``subProdName``
  (``"INR / 100 JPY"`` means rupees per 100 yen). We keep the quoted number and also give
  rupees per ONE unit.
* ``displayTime`` is the publication time, Indian time (13:00 or 13:30 IST seen). The rate
  is therefore known before the NSE close (15:30 IST) on the same day: no look-ahead.
* The public answer stopped at 2026-11-09 when asked on 2026-11-18 (a range to 2026-11-18
  returned only up to the 9th): the public feed appears to lag about a week (VERIFY).
  The daily job therefore re-asks a rolling window and newer files win.
* There is no HKD/INR rate (VERIFY on later dates; none in 2025 or Nov 2026).

Design: ``fetch()`` stores the untouched JSON; ``parse()`` reads bytes, never the network.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta

import pandas as pd

from etfp.http import PoliteClient, SourceError
from etfp.rawstore import RawRecord, RawStore
from etfp.timeutil import IST

SOURCE = "fbil_refrates"
HOST = "fbil.org.in"
URL = "https://www.fbil.org.in/wasdm/refrates/fetchfiltered"
HEADERS = {"Accept": "application/json", "Referer": "https://www.fbil.org.in/"}

DAILY_WINDOW_DAYS = 31
"""The daily job asks for the 31 days ending on the run day (the public feed lags ~1 week)."""
MAX_WINDOW_DAYS = 366
"""One request never spans more than this (a full year answered in ~4 s)."""

OUTPUT_COLUMNS: tuple[str, ...] = (
    "date",
    "pair",
    "rate",
    "quote_units",
    "rate_quoted",
    "published_ist",
    "source_file",
)

_PRODUCT = re.compile(r"INR\s*/\s*(\d+)\s*([A-Z]{3})\Z")
_DAY = re.compile(r"(\d{4})-(\d{2})-(\d{2})(?: 00:00:00)?\Z")
_STAMP = "%Y-%m-%d %H:%M:%S"


def raw_name(start: date, end: date) -> str:
    """'fbil_refrates_2025-01-01_2025-12-31.json'."""
    return f"fbil_refrates_{start.isoformat()}_{end.isoformat()}.json"


def windows(start: date, end: date, *, size: int = MAX_WINDOW_DAYS) -> list[tuple[date, date]]:
    """Split [start, end] into calendar-year pieces (none longer than ``size`` days)."""
    if end < start:
        raise ValueError(f"{SOURCE}: end {end} is before start {start}")
    out: list[tuple[date, date]] = []
    lo = start
    while lo <= end:
        hi = min(end, date(lo.year, 12, 31), lo + timedelta(days=size - 1))
        out.append((lo, hi))
        lo = hi + timedelta(days=1)
    return out


def fetch(client: PoliteClient, store: RawStore, day: date, start: date, end: date) -> RawRecord:
    """Download reference rates for [start, end] and store the untouched JSON under ``day``.

    A body that is not a JSON list (an error page) is not stored and raises.
    """
    if (end - start).days + 1 > MAX_WINDOW_DAYS:
        raise ValueError(f"{SOURCE}: window {start}..{end} is longer than {MAX_WINDOW_DAYS} days")
    params = {
        "fromDate": start.isoformat(),
        "toDate": end.isoformat(),
        "authenticated": "false",
    }
    result = client.get(URL, source=SOURCE, day=day, headers=HEADERS, params=params)
    problem = _body_problem(result.content)
    if problem is not None:
        raise SourceError(
            f"{SOURCE} for {start}..{end}: response is not the rate list ({problem}); "
            f"not stored: {URL}",
            source=SOURCE,
            day=day,
            url=URL,
            status_code=result.status_code,
        )
    return store.save_result(source=SOURCE, day=day, name=raw_name(start, end), result=result)


def _body_problem(content: bytes) -> str | None:
    try:
        body = json.loads(content)
    except ValueError:
        return f"not JSON, starts with {content[:30]!r}"
    if not isinstance(body, list):
        return f"JSON {type(body).__name__}, expected a list"
    return None


def _fail(source_file: str, message: str) -> ValueError:
    return ValueError(f"{SOURCE}: {source_file}: {message}")


def parse(content: bytes, *, source_file: str) -> pd.DataFrame:
    """Parse one saved answer into rows (see OUTPUT_COLUMNS).

    * ``pair`` like ``'USDINR'``; ``rate`` = rupees per ONE unit of the foreign currency
      (``rate_quoted / quote_units``); ``published_ist`` tz-aware Asia/Kolkata.
    * An empty list gives an empty frame (a window with no business days).
    * Unknown product names, non-positive rates, or the same (date, pair) twice with
      different rates raise: the source changed and a human must look.
    """
    problem = _body_problem(content)
    if problem is not None:
        raise _fail(source_file, problem)
    rows = []
    for i, item in enumerate(json.loads(content)):
        if not isinstance(item, dict):
            raise _fail(source_file, f"row {i} is not an object")
        product = str(item.get("subProdName", "")).strip()
        m = _PRODUCT.match(product)
        if m is None:
            raise _fail(source_file, f"row {i}: product {product!r} is not like 'INR / 1 USD'")
        units, ccy = int(m.group(1)), m.group(2)
        dm = _DAY.match(str(item.get("processRunDate", "")).strip())
        if dm is None or units <= 0:
            raise _fail(source_file, f"row {i}: bad date {item.get('processRunDate')!r}")
        day = date(int(dm.group(1)), int(dm.group(2)), int(dm.group(3)))
        rate = item.get("rate")
        if not isinstance(rate, int | float) or isinstance(rate, bool) or not rate > 0:
            raise _fail(source_file, f"row {i}: rate {rate!r} is not a positive number")
        shown = item.get("displayTime")
        published = None
        if shown:
            try:
                published = datetime.strptime(str(shown).strip(), _STAMP).replace(tzinfo=IST)
            except ValueError as exc:
                raise _fail(source_file, f"row {i}: displayTime {shown!r} unreadable") from exc
        rows.append(
            {
                "date": day,
                "pair": f"{ccy}INR",
                "rate": float(rate) / units,
                "quote_units": units,
                "rate_quoted": float(rate),
                "published_ist": published,
                "source_file": source_file,
            }
        )
    if not rows:
        return pd.DataFrame({c: [] for c in OUTPUT_COLUMNS})
    frame = pd.DataFrame(rows, columns=list(OUTPUT_COLUMNS))
    clash = frame.groupby(["date", "pair"])["rate_quoted"].nunique()
    if (clash > 1).any():
        first = clash[clash > 1].index[0]
        raise _fail(source_file, f"two different rates for {first}")
    frame = frame.drop_duplicates(["date", "pair"], keep="last")
    frame["published_ist"] = pd.to_datetime(frame["published_ist"]).dt.tz_convert(IST)
    return frame.sort_values(["date", "pair"]).reset_index(drop=True)
