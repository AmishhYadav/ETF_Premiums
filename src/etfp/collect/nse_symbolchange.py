"""NSE list of symbol changes: which ticker became which, and from what date.

Plain words: NSE sometimes renames a listed security's trading symbol (an ETF called
SETFNIF50 one day is SBINIFTY50 the next). Each NSE daily file uses the symbol of its own
day, so a rename splits one fund's history in two unless the project maps old to new
(``former_symbols`` in config/etfs.yaml, DECISIONS D42). This file is NSE's own list of
renames, so the build can check that every rename touching a configured ETF is mapped.

The file is one fixed URL that NSE overwrites in place (about 1,080 rows, 1999 onwards,
~50 KB), so like the ETF list it is a "latest" snapshot: saved under the day it was
downloaded; an unchanged list is stored once (identical copies are not duplicated).

Format (checked live 2026-11-18, DECISIONS D45)::

    NAME OF THE COMPANY,OLD SYMBOL,NEW SYMBOL,EFFECTIVE DATE
    3M India Limited,BIRLA3M,3MINDIA,15-JUN-2004
    ICICI Prudential Nifty ETF,NIFTYIWIN,ICICINIFTY,10-APR-2018

``EFFECTIVE DATE`` is the first day the NEW symbol trades (example: N100 -> MON100
effective 16-JUN-2021, last N100 session 15-JUN-2021). The name can be empty on a row.

Design: ``fetch()`` stores the untouched file; ``parse()`` reads bytes, never the network.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import date

import pandas as pd

from etfp.http import PoliteClient
from etfp.rawstore import RawRecord, RawStore

SOURCE = "nse_symbolchange"
HOST = "nseindia.com"
URL = "https://nsearchives.nseindia.com/content/equities/symbolchange.csv"
HEADERS = {"Accept": "*/*", "Referer": "https://www.nseindia.com/"}
RAW_NAME = "symbolchange.csv"

OUTPUT_COLUMNS: tuple[str, ...] = (
    "company_name",
    "old_symbol",
    "new_symbol",
    "change_date",
    "source_file",
)

_COLUMNS = {
    "NAME OF THE COMPANY": "company_name",
    "OLD SYMBOL": "old_symbol",
    "NEW SYMBOL": "new_symbol",
    "EFFECTIVE DATE": "change_date",
}
_DATE = re.compile(r"(\d{1,2})-([A-Za-z]{3})-(\d{4})\Z")
_MONTHS = {
    m: i
    for i, m in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1
    )
}


def raw_name(day: date) -> str:
    """Same file name every day; `day` is accepted only to match the other collectors."""
    del day
    return RAW_NAME


def fetch(client: PoliteClient, store: RawStore, day: date) -> RawRecord:
    """Download the list and store the untouched bytes under `day`. No parsing here."""
    result = client.get(URL, source=SOURCE, day=day, headers=HEADERS)
    return store.save_result(source=SOURCE, day=day, name=RAW_NAME, result=result)


def _fail(line: int, message: str) -> ValueError:
    return ValueError(f"{SOURCE}: line {line}: {message}")


def _change_date(text: str, line: int) -> date:
    """Parse '16-JUN-2021' to a date."""
    match = _DATE.match(text)
    month = _MONTHS.get(match.group(2).lower()) if match else None
    if match is None or month is None:
        raise _fail(line, f"column EFFECTIVE DATE: {text!r} is not a date like 16-JUN-2021")
    try:
        return date(int(match.group(3)), month, int(match.group(1)))
    except ValueError as exc:
        raise _fail(line, f"column EFFECTIVE DATE: {text!r} is not a real calendar date") from exc


def parse(content: bytes, *, source_file: str) -> pd.DataFrame:
    """Turn raw bytes into a typed table, one row per rename (dates as ISO-able dates).

    `source_file` is the raw file's relative path; it is copied onto every row.
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
    out: dict[str, list] = {c: [] for c in OUTPUT_COLUMNS}
    try:
        for fields in reader:
            line = reader.line_num
            if not any(f.strip() for f in fields):
                continue
            fields = [f.strip() for f in fields]
            if header is None:
                header = [f.upper() for f in fields]
                missing = [c for c in _COLUMNS if c not in header]
                if missing:
                    raise ValueError(
                        f"{SOURCE}: header is missing column(s) {missing}; got {header}. "
                        "Has NSE changed the file format?"
                    )
                index = {c: header.index(c) for c in _COLUMNS}
                continue
            if len(fields) != len(header):
                raise _fail(line, f"expected {len(header)} fields but found {len(fields)}")
            old, new = fields[index["OLD SYMBOL"]], fields[index["NEW SYMBOL"]]
            if not old or not new:
                raise _fail(line, "OLD SYMBOL or NEW SYMBOL is empty")
            out["company_name"].append(fields[index["NAME OF THE COMPANY"]] or None)
            out["old_symbol"].append(old.upper())
            out["new_symbol"].append(new.upper())
            out["change_date"].append(_change_date(fields[index["EFFECTIVE DATE"]], line))
            out["source_file"].append(source_file)
    except csv.Error as exc:
        raise ValueError(f"{SOURCE}: line {reader.line_num}: unreadable CSV ({exc})") from exc
    if header is None:
        raise ValueError(f"{SOURCE}: the file is empty")
    if not out["old_symbol"]:
        raise ValueError(f"{SOURCE}: header found but the file has no data rows")
    data: dict[str, pd.Series] = {}
    for name in OUTPUT_COLUMNS:
        if name == "change_date":
            data[name] = pd.Series(out[name], dtype=object)
        else:
            data[name] = pd.Series(pd.array(out[name], dtype="string"))
    return pd.DataFrame(data, columns=list(OUTPUT_COLUMNS))
