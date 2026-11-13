"""NSE "UDiFF" bhavcopy: the newer end-of-day price file, delivered as a zip.

Compared with the older full bhavcopy (nse_bhavcopy.py) this file adds three
things we want: the ISIN (the 12-character international security code, which
lets us join NSE prices to AMFI NAVs), the exact rupee turnover, and the
settlement price. The earliest available date is still unconfirmed
(docs/DECISIONS.md D2); expected mid-2024.

Design: `fetch()` stores the untouched zip; `parse()` reads the one CSV inside
it. `parse()` never touches the network.

Finance note: ClsPric is the exchange's official close (a volume-weighted average
of the last 30 minutes), LastPric is the final trade. SttlmPric is the price used
to settle the day's trades; for these equity-type rows it normally equals the
close or is within a paisa of it.
"""

from __future__ import annotations

import csv
import io
import math
import re
import zipfile
from datetime import date

import pandas as pd

from etfp.http import PoliteClient
from etfp.rawstore import RawRecord, RawStore

SOURCE = "nse_udiff_bhavcopy"

_URL_TEMPLATE = (
    "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{stamp}_F_0000.csv.zip"
)
NSE_HEADERS = {"Accept": "*/*", "Referer": "https://www.nseindia.com/"}

OUTPUT_COLUMNS: tuple[str, ...] = (
    "date",
    "symbol",
    "series",
    "isin",
    "name",
    "fin_instrm_id",
    "open",
    "high",
    "low",
    "close",
    "last",
    "prev_close",
    "settle_price",
    "volume",
    "value",
    "trades",
    "source_file",
)

# raw column -> output column, grouped by type
_TEXT_COLUMNS = {"TckrSymb": "symbol", "SctySrs": "series", "ISIN": "isin", "FinInstrmNm": "name"}
_FLOAT_COLUMNS = {
    "OpnPric": "open",
    "HghPric": "high",
    "LwPric": "low",
    "ClsPric": "close",
    "LastPric": "last",
    "PrvsClsgPric": "prev_close",
    "SttlmPric": "settle_price",
    "TtlTrfVal": "value",  # total traded value, already in rupees
}
_INT_COLUMNS = {
    "FinInstrmId": "fin_instrm_id",  # NSE's internal numeric id of the security
    "TtlTradgVol": "volume",
    "TtlNbOfTxsExctd": "trades",
}
_REQUIRED = ("TradDt", *_TEXT_COLUMNS, *_FLOAT_COLUMNS, *_INT_COLUMNS)

# An empty value means "missing". There is no "-" convention in this file, but
# accepting it keeps the rule the same as the other NSE files.
_MISSING = frozenset({"", "-"})

# Safety limit for the unzipped CSV (real file: about 0.6 MB).
_MAX_UNZIPPED_BYTES = 200_000_000

_NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)\Z")
_INTEGER = re.compile(r"[+-]?\d+\Z")
_ISO_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})\Z")


def url_for(day: date) -> str:
    """Download URL for the UDiFF bhavcopy of a trading day."""
    return _URL_TEMPLATE.format(stamp=day.strftime("%Y%m%d"))


def raw_name(day: date) -> str:
    """File name exactly as NSE publishes it (the zip's name)."""
    return f"BhavCopy_NSE_CM_0_0_0_{day.strftime('%Y%m%d')}_F_0000.csv.zip"


def fetch(client: PoliteClient, store: RawStore, day: date) -> RawRecord:
    """Download the day's zip and store the untouched bytes. No parsing here."""
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
    match = _ISO_DATE.match(text)
    if match is None:
        raise _fail(line, f"column {column}: {text!r} is not a date like 2026-11-09")
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError as exc:
        raise _fail(line, f"column {column}: {text!r} is not a real calendar date") from exc


def _unzip_single_csv(content: bytes) -> bytes:
    """Return the bytes of the one CSV inside the zip; anything else is an error."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        head = content[:40]
        raise ValueError(f"{SOURCE}: not a zip file (starts with {head!r})") from exc
    with archive:
        members = [i for i in archive.infolist() if not i.is_dir()]
        csvs = [i for i in members if i.filename.lower().endswith(".csv")]
        if not members:
            raise ValueError(f"{SOURCE}: the zip is empty")
        if len(csvs) != 1:
            names = [i.filename for i in members]
            raise ValueError(f"{SOURCE}: expected exactly one CSV in the zip, found {names}")
        member = csvs[0]
        if member.file_size > _MAX_UNZIPPED_BYTES:
            raise ValueError(f"{SOURCE}: {member.filename} is {member.file_size} bytes unzipped")
        try:
            return archive.read(member)
        except (zipfile.BadZipFile, OSError, EOFError, RuntimeError) as exc:
            raise ValueError(
                f"{SOURCE}: cannot read {member.filename} from the zip ({exc})"
            ) from exc


def parse(content: bytes, *, source_file: str) -> pd.DataFrame:
    """Turn the raw zip bytes into a typed table with every row of the CSV inside.

    `source_file` is the raw zip's relative path in the raw store; it is copied
    onto every row so each number can be traced back to its raw file.
    """
    csv_bytes = _unzip_single_csv(content)
    try:
        text = csv_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(
            f"{SOURCE}: CSV is not UTF-8 text ({exc.reason} at byte {exc.start})"
        ) from exc

    reader = csv.reader(io.StringIO(text, newline=""))
    header: list[str] | None = None
    out: dict[str, list] = {name: [] for name in OUTPUT_COLUMNS}
    index: dict[str, int] = {}
    try:
        for fields in reader:
            line = reader.line_num
            if not any(f.strip() for f in fields):
                continue  # blank line: not a data row
            fields = [f.strip() for f in fields]
            if header is None:
                # Early-2024 files (seen on 2024-01-01 and 2024-01-02) end the
                # header with a trailing comma that the data rows lack, which
                # makes one empty column name. Drop trailing empty names so
                # those files parse; any other width mismatch still fails.
                while fields and fields[-1] == "":
                    fields.pop()
                header = fields
                missing = [c for c in _REQUIRED if c not in header]
                if missing:
                    raise ValueError(
                        f"{SOURCE}: header is missing column(s) {missing}; got {header[:12]}... "
                        "Has NSE changed the file format?"
                    )
                index = {name: header.index(name) for name in _REQUIRED}
                continue
            if len(fields) != len(header):
                raise _fail(line, f"expected {len(header)} fields but found {len(fields)}")
            out["date"].append(_to_date(fields[index["TradDt"]], line, "TradDt"))
            for raw, col in _TEXT_COLUMNS.items():
                value = fields[index[raw]]
                if raw in ("TckrSymb", "SctySrs") and not value:
                    raise _fail(line, f"column {raw} is empty")
                out[col].append(value or None)
            for raw, col in _FLOAT_COLUMNS.items():
                out[col].append(_to_float(fields[index[raw]], line, raw))
            for raw, col in _INT_COLUMNS.items():
                out[col].append(_to_int(fields[index[raw]], line, raw))
            out["source_file"].append(source_file)
    except csv.Error as exc:
        raise ValueError(f"{SOURCE}: line {reader.line_num}: unreadable CSV ({exc})") from exc

    if header is None:
        raise ValueError(f"{SOURCE}: the CSV inside the zip is empty")
    if not out["symbol"]:
        raise ValueError(f"{SOURCE}: header found but the CSV has no data rows")
    return _frame(out)


def _frame(out: dict[str, list]) -> pd.DataFrame:
    """Apply the output dtypes: dates as date objects, counts as Int64, text as string."""
    text = {*_TEXT_COLUMNS.values(), "source_file"}
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
