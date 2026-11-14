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
import zipfile
from datetime import date
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree

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


_XLSX_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def _column_index(cell_ref: str) -> int:
    """'A1' -> 0, 'O7' -> 14, 'AA3' -> 26 (Excel column letters to a 0-based index)."""
    letters = re.match(r"[A-Z]+", cell_ref)
    if letters is None:
        raise ValueError(f"{SOURCE}: bad cell reference {cell_ref!r} in Excel file")
    n = 0
    for ch in letters.group(0):
        n = n * 26 + (ord(ch) - ord("A") + 1)
    return n - 1


def _xlsx_rows(content: bytes) -> list[tuple[int, list[str]]]:
    """Read the first sheet of an .xlsx file into (row number, cell texts).

    NSE occasionally serves the "csv" bhavcopy as an Excel workbook (seen for
    2022-08-08). An .xlsx file is a zip of XML parts, so the standard library is
    enough: shared strings live in xl/sharedStrings.xml and cells in
    xl/worksheets/sheet1.xml. Numbers come back as the text Excel stored, which
    the normal CSV field parsers then read, so both formats share one code path.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            names = set(z.namelist())
            if "xl/worksheets/sheet1.xml" not in names:
                raise ValueError(
                    f"{SOURCE}: zip file is not an Excel workbook ({sorted(names)[:5]})"
                )
            shared: list[str] = []
            if "xl/sharedStrings.xml" in names:
                root = ElementTree.fromstring(z.read("xl/sharedStrings.xml"))
                for si in root.findall("m:si", _XLSX_NS):
                    shared.append("".join(t.text or "" for t in si.iter(f"{{{_XLSX_NS['m']}}}t")))
            sheet = ElementTree.fromstring(z.read("xl/worksheets/sheet1.xml"))
    except zipfile.BadZipFile as exc:
        raise ValueError(f"{SOURCE}: file starts like a zip but cannot be opened ({exc})") from exc
    rows: list[tuple[int, list[str]]] = []
    for row in sheet.iter(f"{{{_XLSX_NS['m']}}}row"):
        cells: dict[int, str] = {}
        for c in row.findall("m:c", _XLSX_NS):
            kind = c.get("t")
            if kind == "inlineStr":
                text = "".join(t.text or "" for t in c.iter(f"{{{_XLSX_NS['m']}}}t"))
            else:
                v = c.find("m:v", _XLSX_NS)
                text = "" if v is None or v.text is None else v.text
                if kind == "s":
                    text = shared[int(text)]
            cells[_column_index(c.get("r", ""))] = text
        if cells:
            width = max(cells) + 1
            rows.append(
                (int(row.get("r", len(rows) + 1)), [cells.get(i, "") for i in range(width)])
            )
    return rows


def _read_rows(content: bytes) -> tuple[list[str], list[tuple[int, list[str]]]]:
    """Split the file into a stripped header and (line number, stripped fields) rows."""
    if content[:2] == b"PK":  # a zip: NSE sent an Excel workbook instead of CSV text
        xrows = [(n, [f.strip() for f in fields]) for n, fields in _xlsx_rows(content)]
        xrows = [(n, f) for n, f in xrows if any(f)]
        if not xrows:
            raise ValueError(f"{SOURCE}: Excel file has no rows")
        return xrows[0][1], xrows[1:]
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
