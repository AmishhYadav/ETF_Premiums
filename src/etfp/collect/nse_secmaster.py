"""NSE "security master": the exchange's own reference file for every listed security.

Two columns matter to this project:

* `PricRg` is the *initial price band* for the next trading session, written as
  "low-high" (for example "206.71-252.63"). A price band is the range outside
  which the exchange will not accept orders. For ETFs the centre of this band
  is what SEBI's rule changes moved (docs/DECISIONS.md D4, D5).
* `IssdCptl` ("issued capital") is the number of ETF units outstanding. For an
  ETF that cannot create new units this number stays flat, which is the whole
  point of the research. NSE writes it in scientific notation
  (1.46750180000000E+07 means 14,675,018 units).

Timing: the file is published in the evening and describes the NEXT trading
session. So the file dated 9 Nov 2026 holds the band that applies on 10 Nov
(it is 229.67, the 9 Nov close, times 0.90 and 1.10). `file_date` records the
date in the file name; it is not the date the band applies to.

Design: `fetch()` stores the untouched .csv.gz; `parse()` reads it and never
touches the network.
"""

from __future__ import annotations

import csv
import gzip
import io
import re
import zlib
from collections.abc import Callable
from datetime import date
from decimal import Decimal, InvalidOperation

import pandas as pd

from etfp.http import PoliteClient
from etfp.rawstore import RawRecord, RawStore

SOURCE = "nse_security_master"

_URL_TEMPLATE = "https://nsearchives.nseindia.com/content/cm/NSE_CM_security_{stamp}.csv.gz"
NSE_HEADERS = {"Accept": "*/*", "Referer": "https://www.nseindia.com/"}

OUTPUT_COLUMNS: tuple[str, ...] = (
    "file_date",
    "symbol",
    "series",
    "name",
    "isin",
    "issued_units",
    "par_value_raw",
    "band_low",
    "band_high",
    "price_range_raw",
    "call_auction_ind",
    "listing_date_raw",
    "delete_flag",
    "slb_eligible_raw",
    "source_file",
)

# The header changes a little between years (some unrelated columns were
# renamed), so only the columns we use are required.
_REQUIRED = (
    "TckrSymb",
    "SctySrs",
    "FinInstrmNm",
    "ISIN",
    "ParVal",
    "IssdCptl",
    "PricRg",
    "CallAuctnInd",
    "ListgDt",
    "DelFlg",
    "SLBMElgblty",
)

# Safety limit for the decompressed CSV (real file: about 11 MB).
_MAX_UNZIPPED_BYTES = 500_000_000

_NAME = re.compile(r"NSE_CM_security_(\d{2})(\d{2})(\d{4})(?:\.v\d+)?\.csv\.gz\Z")
_BAND = re.compile(r"(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)\Z")
_INTEGER = re.compile(r"[+-]?\d+\Z")


def url_for(day: date) -> str:
    """Download URL of the security master dated `day` (the date in the file name)."""
    return _URL_TEMPLATE.format(stamp=day.strftime("%d%m%Y"))


def raw_name(day: date) -> str:
    """File name exactly as NSE publishes it."""
    return f"NSE_CM_security_{day.strftime('%d%m%Y')}.csv.gz"


def file_date_from_name(name: str) -> date:
    """Date in a security-master file name, e.g. 'NSE_CM_security_09112026.v2.csv.gz'.

    The optional '.vN' is the raw store's version suffix for a re-downloaded,
    changed file. A path is accepted too; only the last part is looked at.
    """
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    match = _NAME.fullmatch(base)
    if match is None:
        raise ValueError(f"{SOURCE}: cannot read a date from file name {name!r}")
    try:
        return date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
    except ValueError as exc:
        raise ValueError(f"{SOURCE}: file name {name!r} holds an impossible date") from exc


def fetch(client: PoliteClient, store: RawStore, day: date) -> RawRecord:
    """Download the file dated `day` and store the untouched bytes. No parsing here."""
    result = client.get(url_for(day), source=SOURCE, day=day, headers=NSE_HEADERS)
    return store.save_result(source=SOURCE, day=day, name=raw_name(day), result=result)


# -- parsing ------------------------------------------------------------------


def _fail(line: int, message: str) -> ValueError:
    return ValueError(f"{SOURCE}: line {line}: {message}")


def _gunzip(content: bytes) -> bytes:
    if content[:2] != b"\x1f\x8b":
        hint = " (looks like an HTML page)" if content.lstrip()[:1] == b"<" else ""
        raise ValueError(f"{SOURCE}: not a gzip file{hint}; starts with {content[:40]!r}")
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(content)) as handle:
            data = handle.read(_MAX_UNZIPPED_BYTES + 1)
    except (gzip.BadGzipFile, EOFError, OSError, zlib.error) as exc:
        raise ValueError(f"{SOURCE}: gzip data is damaged ({exc})") from exc
    if len(data) > _MAX_UNZIPPED_BYTES:
        raise ValueError(f"{SOURCE}: decompressed file is larger than {_MAX_UNZIPPED_BYTES} bytes")
    return data


def _units(text: str, line: int) -> int | None:
    """Units outstanding as an exact integer from text like '1.46750180000000E+07'."""
    if text == "":
        return None
    try:
        number = Decimal(text)
    except InvalidOperation as exc:
        raise _fail(line, f"column IssdCptl: {text!r} is not a number") from exc
    if not number.is_finite() or number != number.to_integral_value():
        raise _fail(line, f"column IssdCptl: {text!r} is not a whole number of units")
    return int(number)


def _whole(text: str, line: int, column: str) -> int | None:
    if text == "":
        return None
    if not _INTEGER.match(text):
        raise _fail(line, f"column {column}: {text!r} is not a whole number")
    return int(text)


def _band(text: str) -> tuple[float, float] | None:
    """Split 'low-high' into two floats. Empty or any other shape gives None (missing)."""
    match = _BAND.match(text)
    if match is None:
        return None
    return float(match.group(1)), float(match.group(2))


def parse(content: bytes, *, file_date: date, source_file: str) -> pd.DataFrame:
    """Turn raw .csv.gz bytes into a typed table with every row (all securities, all series).

    `file_date` is the date in the file name (see `file_date_from_name`).
    `source_file` is the raw file's relative path in the raw store.

    Left as given on purpose: `par_value_raw` (face value in rupees),
    `listing_date_raw` (NSE's own integer date code; see docs/DECISIONS.md for
    what was found about its epoch) and `slb_eligible_raw`.
    """
    data = _gunzip(content)
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(
            f"{SOURCE}: CSV is not UTF-8 text ({exc.reason} at byte {exc.start})"
        ) from exc

    reader = csv.reader(io.StringIO(text, newline=""))
    header: list[str] | None = None
    out: dict[str, list] = {name: [] for name in OUTPUT_COLUMNS}
    add_row: Callable[[list[str], int], None] | None = None
    try:
        for fields in reader:
            line = reader.line_num
            if not any(f.strip() for f in fields):
                continue  # blank line: not a data row
            if header is None:
                header = [f.strip() for f in fields]
                missing = [c for c in _REQUIRED if c not in header]
                if missing:
                    raise ValueError(
                        f"{SOURCE}: header is missing column(s) {missing}; got {header[:12]}... "
                        "Has NSE changed the file format?"
                    )
                add_row = _row_adder(out, header, file_date, source_file)
                continue
            if len(fields) != len(header):
                raise _fail(line, f"expected {len(header)} fields but found {len(fields)}")
            assert add_row is not None
            add_row(fields, line)
    except csv.Error as exc:
        raise ValueError(f"{SOURCE}: line {reader.line_num}: unreadable CSV ({exc})") from exc

    if header is None:
        raise ValueError(f"{SOURCE}: the file is empty")
    if not out["symbol"]:
        raise ValueError(f"{SOURCE}: header found but the file has no data rows")
    return _frame(out)


def _row_adder(
    out: dict[str, list], header: list[str], file_date: date, source_file: str
) -> Callable[[list[str], int], None]:
    """A function that checks one data row and appends its values to ``out``.

    Speed matters here: there are 25,000-40,000 rows per file and about 700 files, and each
    row has ~120 fields of which we use 11. So the column positions are looked up once,
    only the 11 fields we use are stripped of spaces, and the lists are bound to local
    names. The checks and the values are exactly those of a plain row-by-row read.
    """
    (
        i_sym, i_ser, i_name, i_isin, i_par, i_units, i_range, i_call, i_listed, i_del, i_slb,
    ) = (header.index(name) for name in _REQUIRED)  # fmt: skip
    add_date, add_sym, add_ser = (out[c].append for c in ("file_date", "symbol", "series"))
    add_name, add_isin, add_units = (out[c].append for c in ("name", "isin", "issued_units"))
    add_par, add_low, add_high = (out[c].append for c in ("par_value_raw", "band_low", "band_high"))
    add_range, add_call = out["price_range_raw"].append, out["call_auction_ind"].append
    add_listed, add_del = out["listing_date_raw"].append, out["delete_flag"].append
    add_slb, add_file = out["slb_eligible_raw"].append, out["source_file"].append
    nan = float("nan")

    def add(fields: list[str], line: int) -> None:
        symbol, series = fields[i_sym].strip(), fields[i_ser].strip()
        if not symbol or not series:
            raise _fail(line, "TckrSymb or SctySrs is empty")
        price_range = fields[i_range].strip()
        band = _band(price_range)
        add_date(file_date)
        add_sym(symbol)
        add_ser(series)
        add_name(fields[i_name].strip() or None)
        add_isin(fields[i_isin].strip() or None)
        add_units(_units(fields[i_units].strip(), line))
        add_par(_whole(fields[i_par].strip(), line, "ParVal"))
        add_low(band[0] if band else nan)
        add_high(band[1] if band else nan)
        add_range(price_range or None)
        add_call(fields[i_call].strip() or None)
        add_listed(_whole(fields[i_listed].strip(), line, "ListgDt"))
        add_del(fields[i_del].strip() or None)
        add_slb(fields[i_slb].strip() or None)
        add_file(source_file)

    return add


_INT_OUTPUT = {"issued_units", "par_value_raw", "listing_date_raw"}
_FLOAT_OUTPUT = {"band_low", "band_high"}


def _frame(out: dict[str, list]) -> pd.DataFrame:
    """Apply the output dtypes: dates as date objects, counts as Int64, text as string."""
    data: dict[str, pd.Series] = {}
    for name in OUTPUT_COLUMNS:
        values = out[name]
        if name in _INT_OUTPUT:
            data[name] = pd.Series(pd.array(values, dtype="Int64"))
        elif name in _FLOAT_OUTPUT:
            data[name] = pd.Series(values, dtype="float64")
        elif name == "file_date":
            data[name] = pd.Series(values, dtype=object)
        else:
            data[name] = pd.Series(pd.array(values, dtype="string"))
    return pd.DataFrame(data, columns=list(OUTPUT_COLUMNS))
