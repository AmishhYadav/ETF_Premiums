"""NSE "full bhavcopy with delivery": one row per security per series per trading day.

A *bhavcopy* is the exchange's end-of-day price summary. This one is the main
daily price source for the project (see docs/DECISIONS.md D1). It also carries
the *delivery* figures: how many of the traded units were actually taken into a
demat account (instead of being squared off the same day).

Design: `fetch()` only downloads and stores the untouched bytes. `parse()` only
turns bytes into a table and never touches the network, so it can be tested from
saved fixtures. If NSE changes the file format, `parse()` raises a ValueError
that names this source and says what was wrong.

Finance note on prices: CLOSE_PRICE is the exchange's official close. It is a
volume-weighted average price (VWAP) of the trades in the last 30 minutes of the
session, which is why it can differ from LAST_PRICE (the very last trade). Both
are kept. AVG_PRICE is the VWAP over the whole day.
"""

from __future__ import annotations

import csv
import io
import math
import re
from datetime import date
from decimal import Decimal, InvalidOperation

import pandas as pd

from etfp.http import PoliteClient
from etfp.rawstore import RawRecord, RawStore

SOURCE = "nse_bhavcopy_full"

_URL_TEMPLATE = "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{stamp}.csv"
NSE_HEADERS = {"Accept": "*/*", "Referer": "https://www.nseindia.com/"}

# Parsed column names, in order. Everything is one row per (symbol, series).
OUTPUT_COLUMNS: tuple[str, ...] = (
    "symbol",
    "series",
    "date",
    "prev_close",
    "open",
    "high",
    "low",
    "last",
    "close",
    "vwap",
    "volume",
    "value",
    "trades",
    "deliv_qty",
    "deliv_pct",
    "source_file",
)

# Raw column name -> what we call it. All of these must be present in the header.
_REQUIRED = (
    "SYMBOL",
    "SERIES",
    "DATE1",
    "PREV_CLOSE",
    "OPEN_PRICE",
    "HIGH_PRICE",
    "LOW_PRICE",
    "LAST_PRICE",
    "CLOSE_PRICE",
    "AVG_PRICE",
    "TTL_TRD_QNTY",
    "TURNOVER_LACS",
    "NO_OF_TRADES",
    "DELIV_QTY",
    "DELIV_PER",
)

_FLOAT_COLUMNS = {
    "PREV_CLOSE": "prev_close",
    "OPEN_PRICE": "open",
    "HIGH_PRICE": "high",
    "LOW_PRICE": "low",
    "LAST_PRICE": "last",
    "CLOSE_PRICE": "close",
    "AVG_PRICE": "vwap",
    "DELIV_PER": "deliv_pct",
}
_INT_COLUMNS = {
    "TTL_TRD_QNTY": "volume",
    "NO_OF_TRADES": "trades",
    "DELIV_QTY": "deliv_qty",
}

# NSE writes "-" where a number does not exist (typically delivery data for
# series that have no delivery). That means "missing", never zero.
_MISSING = frozenset({"", "-"})

# One "lac" is 100,000 rupees. TURNOVER_LACS = 636.28 means Rs 63,628,000.
_RUPEES_PER_LAC = Decimal(100_000)

_NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)\Z")
_INTEGER = re.compile(r"[+-]?\d+\Z")
_DATE1 = re.compile(r"(\d{1,2})-([A-Za-z]{3})-(\d{4})\Z")
_MONTHS = {
    m: i
    for i, m in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1
    )
}


def url_for(day: date) -> str:
    """Download URL for the bhavcopy of a trading day."""
    return _URL_TEMPLATE.format(stamp=day.strftime("%d%m%Y"))


def raw_name(day: date) -> str:
    """File name exactly as NSE publishes it."""
    return f"sec_bhavdata_full_{day.strftime('%d%m%Y')}.csv"


def fetch(client: PoliteClient, store: RawStore, day: date) -> RawRecord:
    """Download the day's file and store the untouched bytes. No parsing here."""
    result = client.get(url_for(day), source=SOURCE, day=day, headers=NSE_HEADERS)
    return store.save_result(source=SOURCE, day=day, name=raw_name(day), result=result)


# -- parsing ------------------------------------------------------------------


def _fail(line: int, message: str) -> ValueError:
    return ValueError(f"{SOURCE}: line {line}: {message}")


def _to_float(text: str, line: int, column: str) -> float:
    if text in _MISSING:
        return math.nan
    if not _NUMBER.match(text):
        raise _fail(line, f"column {column}: {text!r} is not a number")
    return float(text)


def _to_int(text: str, line: int, column: str) -> int | None:
    if text in _MISSING:
        return None
    if not _INTEGER.match(text):
        raise _fail(line, f"column {column}: {text!r} is not a whole number")
    return int(text)


def _to_date(text: str, line: int, column: str) -> date:
    match = _DATE1.match(text)
    month = _MONTHS.get(match.group(2).lower()) if match else None
    if match is None or month is None:
        raise _fail(line, f"column {column}: {text!r} is not a date like 09-Nov-2026")
    try:
        return date(int(match.group(3)), month, int(match.group(1)))
    except ValueError as exc:
        raise _fail(line, f"column {column}: {text!r} is not a real calendar date") from exc


def _to_rupees(lacs_text: str, line: int) -> float:
    """Turnover in rupees from a figure in lacs. Decimal arithmetic keeps it exact."""
    if lacs_text in _MISSING:
        return math.nan
    try:
        return float(Decimal(lacs_text) * _RUPEES_PER_LAC)
    except InvalidOperation as exc:
        raise _fail(line, f"column TURNOVER_LACS: {lacs_text!r} is not a number") from exc


def _read_rows(content: bytes) -> tuple[list[str], list[tuple[int, list[str]]]]:
    """Split the file into a stripped header and (line number, stripped fields) rows."""
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(
            f"{SOURCE}: file is not UTF-8 text ({exc.reason} at byte {exc.start})"
        ) from exc
    reader = csv.reader(io.StringIO(text, newline=""))
    header: list[str] | None = None
    rows: list[tuple[int, list[str]]] = []
    try:
        for fields in reader:
            if not any(f.strip() for f in fields):
                continue  # blank line: not a data row
            if header is None:
                header = [f.strip() for f in fields]
                continue
            rows.append((reader.line_num, [f.strip() for f in fields]))
    except csv.Error as exc:
        raise ValueError(f"{SOURCE}: line {reader.line_num}: unreadable CSV ({exc})") from exc
    if header is None:
        raise ValueError(f"{SOURCE}: file is empty")
    return header, rows


def parse(content: bytes, *, source_file: str) -> pd.DataFrame:
    """Turn raw bytes into a typed table with every row of the file.

    `source_file` is the raw file's relative path in the raw store; it is copied
    onto every row so each number can be traced back to its raw file.
    """
    header, rows = _read_rows(content)
    missing = [c for c in _REQUIRED if c not in header]
    if missing:
        raise ValueError(
            f"{SOURCE}: header is missing column(s) {missing}; got {header[:20]}. "
            "Has NSE changed the file format?"
        )
    if not rows:
        raise ValueError(f"{SOURCE}: header found but the file has no data rows")
    index = {name: header.index(name) for name in _REQUIRED}

    out: dict[str, list] = {name: [] for name in OUTPUT_COLUMNS}
    for line, fields in rows:
        if len(fields) != len(header):
            raise _fail(line, f"expected {len(header)} fields but found {len(fields)}")

        def get(name: str, fields: list[str] = fields) -> str:
            return fields[index[name]]

        for raw, col in _FLOAT_COLUMNS.items():
            out[col].append(_to_float(get(raw), line, raw))
        for raw, col in _INT_COLUMNS.items():
            out[col].append(_to_int(get(raw), line, raw))
        out["symbol"].append(get("SYMBOL") or None)
        out["series"].append(get("SERIES") or None)
        out["date"].append(_to_date(get("DATE1"), line, "DATE1"))
        out["value"].append(_to_rupees(get("TURNOVER_LACS"), line))
        out["source_file"].append(source_file)
        if not get("SYMBOL"):
            raise _fail(line, "column SYMBOL is empty")

    return _frame(out)


def _frame(out: dict[str, list]) -> pd.DataFrame:
    """Apply the output dtypes: dates as date objects, counts as Int64, text as string."""
    text = {"symbol", "series", "source_file"}
    ints = set(_INT_COLUMNS.values())
    data: dict[str, pd.Series] = {}
    for name in OUTPUT_COLUMNS:
        values = out[name]
        if name in text:
            data[name] = pd.Series(pd.array(values, dtype="string"))
        elif name in ints:
            data[name] = pd.Series(pd.array(values, dtype="Int64"))
        elif name == "date":
            data[name] = pd.Series(values, dtype=object)
        else:
            data[name] = pd.Series(values, dtype="float64")
    return pd.DataFrame(data, columns=list(OUTPUT_COLUMNS))
