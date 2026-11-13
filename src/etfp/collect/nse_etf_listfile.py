"""NSE list of all exchange-traded funds (ETFs): symbol, ISIN, underlying and listing date.

This is the project's ETF universe source (docs/DECISIONS.md D8). The
"underlying" is what the ETF holds or tracks: an index (Nifty 50), a commodity
(Gold) or a debt basket. This file is not dated: NSE overwrites it in place, so
we save a copy under the day we downloaded it (the raw store keeps older copies).

Design: `fetch()` stores the untouched file; `parse()` reads it and never
touches the network.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import date

import pandas as pd

from etfp.http import PoliteClient
from etfp.rawstore import RawRecord, RawStore

SOURCE = "nse_etf_seclist"

URL = "https://nsearchives.nseindia.com/content/equities/eq_etfseclist.csv"
NSE_HEADERS = {"Accept": "*/*", "Referer": "https://www.nseindia.com/"}

OUTPUT_COLUMNS: tuple[str, ...] = (
    "symbol",
    "underlying_asset",
    "name",
    "listing_date",
    "market_lot",
    "isin",
    "face_value",
    "underlying_type",
    "underlying_key",
    "source_file",
)

# raw column -> output column
_COLUMNS = {
    "Symbol": "symbol",
    "Underlying Asset": "underlying_asset",
    "SecurityName": "name",
    "DateofListing": "listing_date",
    "MarketLot": "market_lot",
    "ISINNumber": "isin",
    "FaceValue": "face_value",
    "ETF Underlying": "underlying_type",
    "Underlying Key": "underlying_key",
}
_TEXT = ("symbol", "underlying_asset", "name", "isin", "underlying_type", "underlying_key")

_NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)\Z")
_INTEGER = re.compile(r"[+-]?\d+\Z")
_LISTING = re.compile(r"(\d{1,2})-([A-Za-z]{3})-(\d{2}|\d{4})\Z")
_MONTHS = {
    m: i
    for i, m in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1
    )
}


def url_for(day: date) -> str:
    """The list has one fixed URL; `day` is accepted only to match the other collectors."""
    del day
    return URL


def raw_name(day: date) -> str:
    """Same file name every day; `day` is accepted only to match the other collectors."""
    del day
    return "eq_etfseclist.csv"


def fetch(client: PoliteClient, store: RawStore, day: date) -> RawRecord:
    """Download the list and store the untouched bytes under `day`. No parsing here."""
    result = client.get(url_for(day), source=SOURCE, day=day, headers=NSE_HEADERS)
    return store.save_result(source=SOURCE, day=day, name=raw_name(day), result=result)


# -- parsing ------------------------------------------------------------------


def _fail(line: int, message: str) -> ValueError:
    return ValueError(f"{SOURCE}: line {line}: {message}")


def _listing_date(text: str, line: int) -> date:
    """Parse '08-Jan-02'. Two-digit years: this list starts in 2002, so 00-69 means 2000-2069."""
    match = _LISTING.match(text)
    month = _MONTHS.get(match.group(2).lower()) if match else None
    if match is None or month is None:
        raise _fail(line, f"column DateofListing: {text!r} is not a date like 08-Jan-02")
    year = int(match.group(3))
    if len(match.group(3)) == 2:
        year += 2000 if year < 70 else 1900
    try:
        return date(year, month, int(match.group(1)))
    except ValueError as exc:
        raise _fail(line, f"column DateofListing: {text!r} is not a real calendar date") from exc


def parse(content: bytes, *, source_file: str) -> pd.DataFrame:
    """Turn raw bytes into a typed table with every row of the list.

    `source_file` is the raw file's relative path in the raw store; it is copied
    onto every row so each value can be traced back to its raw file.
    """
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(
            f"{SOURCE}: file is not UTF-8 text ({exc.reason} at byte {exc.start})"
        ) from exc

    reader = csv.reader(io.StringIO(text, newline=""))
    header: list[str] | None = None
    index: dict[str, int] = {}
    out: dict[str, list] = {name: [] for name in OUTPUT_COLUMNS}
    try:
        for fields in reader:
            line = reader.line_num
            if not any(f.strip() for f in fields):
                continue  # blank line: not a data row
            fields = [f.strip() for f in fields]
            if header is None:
                header = fields
                missing = [c for c in _COLUMNS if c not in header]
                if missing:
                    raise ValueError(
                        f"{SOURCE}: header is missing column(s) {missing}; got {header}. "
                        "Has NSE changed the file format?"
                    )
                index = {name: header.index(name) for name in _COLUMNS}
                continue
            if len(fields) != len(header):
                raise _fail(line, f"expected {len(header)} fields but found {len(fields)}")
            _append_row(out, fields, index, line, source_file)
    except csv.Error as exc:
        raise ValueError(f"{SOURCE}: line {reader.line_num}: unreadable CSV ({exc})") from exc

    if header is None:
        raise ValueError(f"{SOURCE}: the file is empty")
    if not out["symbol"]:
        raise ValueError(f"{SOURCE}: header found but the file has no data rows")
    return _frame(out)


def _append_row(
    out: dict[str, list],
    fields: list[str],
    index: dict[str, int],
    line: int,
    source_file: str,
) -> None:
    def get(raw: str) -> str:
        return fields[index[raw]]

    if not get("Symbol"):
        raise _fail(line, "column Symbol is empty")
    for raw, col in _COLUMNS.items():
        if col in _TEXT:
            out[col].append(get(raw) or None)
    out["listing_date"].append(_listing_date(get("DateofListing"), line))
    lot = get("MarketLot")
    if not _INTEGER.match(lot):
        raise _fail(line, f"column MarketLot: {lot!r} is not a whole number")
    out["market_lot"].append(int(lot))
    face = get("FaceValue")
    if not _NUMBER.match(face):
        raise _fail(line, f"column FaceValue: {face!r} is not a number")
    out["face_value"].append(float(face))
    out["source_file"].append(source_file)


def _frame(out: dict[str, list]) -> pd.DataFrame:
    """Apply the output dtypes: dates as date objects, counts as Int64, text as string."""
    data: dict[str, pd.Series] = {}
    for name in OUTPUT_COLUMNS:
        values = out[name]
        if name == "listing_date":
            data[name] = pd.Series(values, dtype=object)
        elif name == "market_lot":
            data[name] = pd.Series(pd.array(values, dtype="Int64"))
        elif name == "face_value":
            data[name] = pd.Series(values, dtype="float64")
        else:
            data[name] = pd.Series(pd.array(values, dtype="string"))
    return pd.DataFrame(data, columns=list(OUTPUT_COLUMNS))
