"""NSE securities lending and borrowing (SLB): who is allowed to lend/borrow, and what traded.

Why we need it (request R8): the paper claims that people could not short these ETFs to
push their price down. Shorting an ETF means borrowing units, selling them, and buying
them back later. NSE runs a lending market (SLB) where a unit can be borrowed for a fee.
Two daily files tell us (a) whether an ETF was *eligible* for SLB at all, and (b) whether
anyone actually *borrowed* any units, and at what fee.

Both files come from NSE's archive server (live checks on 2026-11-14, DECISIONS D28), no
cookies needed, both available back to at least 2021-01-04:

* **Eligible list** ``archives/slbs/seclist/SLB_ELG_SEC_DDMMYYYY.csv``. One row per
  (symbol, SLB series) that is eligible. Columns: Sr.No., Symbol, Series, Normal
  Eligibility, Recall Eligibility, Repay Eligibility, Market Type. In every file seen,
  every row has Normal Eligibility = "E"; a symbol that is NOT in the file is not
  eligible that day. The file has no date inside, only in its name.
* **SLB bhavcopy** ``archives/slbs/bhavcopy/SLBM_BC_DDMMYYYY.DAT``. Despite ".DAT" it is a
  comma-separated text file with NO header and 17 fields. Only series that traded that
  day appear (about 30 to 300 rows a day). Field layout (0-based):

  ====  =====================================================================
  0     security name (padded), e.g. "MIRAEAMC - MAFANG"
  1     symbol
  2     SLB series, e.g. XD (see below)
  3     contract maturity date, e.g. 01-DEC-2026
  4     market type, N
  5-9   lending fee: previous close, open, high, low, close
  10    always blank
  11    quantity lent (units), the "volume"
  12    fee value in rupees = quantity x average fee (NOT the value of the shares)
  13-14 two more fee figures (meaning unconfirmed, not used)
  15    trade date, e.g. 12-NOV-2026
  16    number of trades
  ====  =====================================================================

  Fields 5-9 order was worked out from the data: only the order (previous close, open,
  high, low, close) keeps high >= open/close and low <= open/close on all 288 rows of
  three files, and the "previous close" of 12 Nov equals the "close" of 11 Nov on all 138
  series traded on both days. The column meaning is therefore data-checked, but NSE's own
  file description was not found (VERIFY, docs/DECISIONS.md).

Finance note: in SLB the "price" is the fee for borrowing, in rupees per unit for the whole
contract period (not per year), so a fee of 0.30 on 8,000 units is Rs 2,400. The series
code (01-12, X1-X9, XD, XN, XO, D1, D2, DF, DJ, R3) names the contract (its tenure and
maturity); we keep it exactly as published and also give the maturity date.

Design: ``fetch_*()`` only download and store untouched bytes; ``parse_*()`` only turn bytes
into tables and never touch the network.
"""

from __future__ import annotations

import csv
import io
import math
import re
from datetime import date

import pandas as pd

from etfp.http import PoliteClient, SourceNotFoundError
from etfp.rawstore import RawRecord, RawStore

SOURCE_ELIGIBLE = "nse_slb_eligible"
SOURCE_BHAVCOPY = "nse_slb_bhavcopy"

_BASE = "https://nsearchives.nseindia.com/archives/slbs/"
_ELIGIBLE_URL = _BASE + "seclist/SLB_ELG_SEC_{stamp}.csv"
_BHAVCOPY_URL = _BASE + "bhavcopy/SLBM_BC_{stamp}.DAT"
NSE_HEADERS = {"Accept": "*/*", "Referer": "https://www.nseindia.com/"}

ELIGIBLE_COLUMNS: tuple[str, ...] = (
    "symbol",
    "series",
    "normal_eligibility",
    "recall_eligibility",
    "repay_eligibility",
    "market_type",
    "source_file",
)
BHAVCOPY_COLUMNS: tuple[str, ...] = (
    "date",
    "symbol",
    "name",
    "series",
    "maturity",
    "market_type",
    "fee_prev_close",
    "fee_open",
    "fee_high",
    "fee_low",
    "fee_close",
    "volume",
    "value",
    "trades",
    "source_file",
)

_ELIGIBLE_HEADER = (
    "Sr.No.",
    "Symbol",
    "Series",
    "Normal Eligibility",
    "Recall Eligibility",
    "Repay Eligibility",
    "Market Type",
)
_BHAVCOPY_FIELDS = 17

_NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)\Z")
_INTEGER = re.compile(r"[+-]?\d+\Z")
_DATE = re.compile(r"(\d{1,2})-([A-Za-z]{3})-(\d{4})\Z")
_MONTHS = {
    m: i
    for i, m in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1
    )
}


# -- names and download ----------------------------------------------------------


def eligible_url(day: date) -> str:
    return _ELIGIBLE_URL.format(stamp=day.strftime("%d%m%Y"))


def eligible_raw_name(day: date) -> str:
    return f"SLB_ELG_SEC_{day.strftime('%d%m%Y')}.csv"


def bhavcopy_url(day: date) -> str:
    return _BHAVCOPY_URL.format(stamp=day.strftime("%d%m%Y"))


def bhavcopy_raw_name(day: date) -> str:
    return f"SLBM_BC_{day.strftime('%d%m%Y')}.DAT"


def _looks_like_html(content: bytes) -> bool:
    head = content[:512].lstrip().lower()
    return head.startswith((b"<!doctype html", b"<html", b"<head"))


def _fetch(
    client: PoliteClient, store: RawStore, day: date, *, source: str, url: str, name: str
) -> RawRecord:
    result = client.get(url, source=source, day=day, headers=NSE_HEADERS)
    if _looks_like_html(result.content):  # a web page where a data file should be
        raise SourceNotFoundError(
            f"{source} for {day.isoformat()}: got a web page instead of the file: {url}",
            source=source,
            day=day,
            url=url,
            status_code=result.status_code,
        )
    return store.save_result(source=source, day=day, name=name, result=result)


def fetch_eligible(client: PoliteClient, store: RawStore, day: date) -> RawRecord:
    """Download the day's SLB-eligible list and store the untouched bytes."""
    return _fetch(
        client,
        store,
        day,
        source=SOURCE_ELIGIBLE,
        url=eligible_url(day),
        name=eligible_raw_name(day),
    )


def fetch_bhavcopy(client: PoliteClient, store: RawStore, day: date) -> RawRecord:
    """Download the day's SLB bhavcopy and store the untouched bytes."""
    return _fetch(
        client,
        store,
        day,
        source=SOURCE_BHAVCOPY,
        url=bhavcopy_url(day),
        name=bhavcopy_raw_name(day),
    )


# -- parsing ---------------------------------------------------------------------


def _fail(source: str, line: int, message: str) -> ValueError:
    return ValueError(f"{source}: line {line}: {message}")


def _text(content: bytes, source: str) -> str:
    if _looks_like_html(content):
        raise ValueError(f"{source}: file is a web page, not data")
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(
            f"{source}: file is not UTF-8 text ({exc.reason} at byte {exc.start})"
        ) from exc


def _rows(text: str, source: str) -> list[tuple[int, list[str]]]:
    reader = csv.reader(io.StringIO(text, newline=""))
    rows: list[tuple[int, list[str]]] = []
    try:
        for fields in reader:
            if any(f.strip() for f in fields):
                rows.append((reader.line_num, [f.strip() for f in fields]))
    except csv.Error as exc:
        raise ValueError(f"{source}: line {reader.line_num}: unreadable CSV ({exc})") from exc
    return rows


def parse_eligible(content: bytes, *, source_file: str) -> pd.DataFrame:
    """The eligible list as a table: one row per (symbol, series) in the file.

    The file does not say which day it is for; the caller takes the day from the raw
    folder. ``source_file`` is copied onto every row for lineage.
    """
    rows = _rows(_text(content, SOURCE_ELIGIBLE), SOURCE_ELIGIBLE)
    if not rows:
        raise ValueError(f"{SOURCE_ELIGIBLE}: file is empty")
    header = tuple(rows[0][1])
    if header != _ELIGIBLE_HEADER:
        raise ValueError(
            f"{SOURCE_ELIGIBLE}: unexpected header {list(header)}; expected "
            f"{list(_ELIGIBLE_HEADER)}. Has NSE changed the file format?"
        )
    body = rows[1:]
    if not body:
        raise ValueError(f"{SOURCE_ELIGIBLE}: header found but the file has no data rows")
    out: dict[str, list] = {c: [] for c in ELIGIBLE_COLUMNS}
    for line, f in body:
        if len(f) != len(_ELIGIBLE_HEADER):
            raise _fail(
                SOURCE_ELIGIBLE, line, f"expected {len(_ELIGIBLE_HEADER)} fields, found {len(f)}"
            )
        if not f[1] or not f[2]:
            raise _fail(SOURCE_ELIGIBLE, line, "symbol or series is empty")
        out["symbol"].append(f[1])
        out["series"].append(f[2])
        out["normal_eligibility"].append(f[3] or None)
        out["recall_eligibility"].append(f[4] or None)
        out["repay_eligibility"].append(f[5] or None)
        out["market_type"].append(f[6] or None)
        out["source_file"].append(source_file)
    return pd.DataFrame(
        {c: pd.Series(pd.array(v, dtype="string")) for c, v in out.items()},
        columns=list(ELIGIBLE_COLUMNS),
    )


def _to_date(text: str, line: int) -> date:
    match = _DATE.match(text)
    month = _MONTHS.get(match.group(2).lower()) if match else None
    if match is None or month is None:
        raise _fail(SOURCE_BHAVCOPY, line, f"{text!r} is not a date like 12-NOV-2026")
    try:
        return date(int(match.group(3)), month, int(match.group(1)))
    except ValueError as exc:
        raise _fail(SOURCE_BHAVCOPY, line, f"{text!r} is not a real calendar date") from exc


def _to_float(text: str, line: int, what: str) -> float:
    if text == "":
        return math.nan
    if not _NUMBER.match(text):
        raise _fail(SOURCE_BHAVCOPY, line, f"{what}: {text!r} is not a number")
    return float(text)


def _to_int(text: str, line: int, what: str) -> int:
    if not _INTEGER.match(text):
        raise _fail(SOURCE_BHAVCOPY, line, f"{what}: {text!r} is not a whole number")
    return int(text)


def parse_bhavcopy(content: bytes, *, source_file: str) -> pd.DataFrame:
    """The SLB bhavcopy as a table: one row per (symbol, series) that traded that day.

    The layout is the one in the module notes. A file with no rows gives an empty table
    (a day on which nothing was lent is possible); the cleaner logs it as a warning.
    """
    rows = _rows(_text(content, SOURCE_BHAVCOPY), SOURCE_BHAVCOPY)
    out: dict[str, list] = {c: [] for c in BHAVCOPY_COLUMNS}
    for line, f in rows:
        if len(f) != _BHAVCOPY_FIELDS:
            raise _fail(
                SOURCE_BHAVCOPY,
                line,
                f"expected {_BHAVCOPY_FIELDS} fields but found {len(f)}; has NSE changed the "
                "file format?",
            )
        if not f[1] or not f[2]:
            raise _fail(SOURCE_BHAVCOPY, line, "symbol or series is empty")
        out["date"].append(_to_date(f[15], line))
        out["symbol"].append(f[1])
        out["name"].append(f[0] or None)
        out["series"].append(f[2])
        out["maturity"].append(_to_date(f[3], line))
        out["market_type"].append(f[4] or None)
        for col, i in (
            ("fee_prev_close", 5),
            ("fee_open", 6),
            ("fee_high", 7),
            ("fee_low", 8),
            ("fee_close", 9),
        ):
            out[col].append(_to_float(f[i], line, col))
        out["volume"].append(_to_int(f[11], line, "volume"))
        out["value"].append(_to_float(f[12], line, "value"))
        out["trades"].append(_to_int(f[16], line, "trades"))
        out["source_file"].append(source_file)

    text_cols = {"symbol", "name", "series", "market_type", "source_file"}
    data: dict[str, pd.Series] = {}
    for name in BHAVCOPY_COLUMNS:
        values = out[name]
        if name in text_cols:
            data[name] = pd.Series(pd.array(values, dtype="string"))
        elif name in ("volume", "trades"):
            data[name] = pd.Series(pd.array(values, dtype="Int64"))
        elif name in ("date", "maturity"):
            data[name] = pd.Series(values, dtype=object)
        else:
            data[name] = pd.Series(values, dtype="float64")
    return pd.DataFrame(data, columns=list(BHAVCOPY_COLUMNS))
