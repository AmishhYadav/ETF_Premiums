"""BSE (Bombay Stock Exchange) end-of-day "bhavcopy": one row per security per trading day.

Why we need it (request R9): India's international ETFs trade on BOTH exchanges, NSE and
BSE. If we only count NSE volume, an ETF's "turnover" (how much of its units changed hands)
is understated. This file gives the BSE close, units traded, rupees traded and trade count.

BSE has published two different file formats (live checks on 2026-11-14, DECISIONS D27):

* **UDiFF** (new): ``BhavCopy_BSE_CM_0_0_0_YYYYMMDD_F_0000.CSV`` (plain CSV, not zipped),
  same column names as NSE's UDiFF file. Confirmed for 2024-01-02 up to today; NOT
  published for 2023-12-01 or earlier (the exact first day is between those two dates).
* **Legacy**: ``EQ_ISINCODE_DDMMYY.zip`` holding ``EQ_ISINCODE_DDMMYY.CSV``. Confirmed for
  2021-01-04 and 2024-07-05; no longer published for 2025-01-02 or later.

Both carry the ISIN, so we match BSE rows to our funds by ISIN, never by name. Both
formats agree on the same day (checked 2024-07-05: MON100 54,556 units, Rs 9,013,392,
776 trades in each).

Beware the "soft 404": for a day with no file (holiday, not published yet, or a format
that has been retired) BSE does NOT answer HTTP 404. It answers HTTP 200 with its normal
web page (HTML). ``fetch()`` spots that and raises ``SourceNotFoundError`` so the job
runner treats it as a missing file and nothing HTML is ever stored as "raw data".

Design: ``fetch()`` only downloads and stores the untouched bytes. ``parse()`` only turns
bytes into a table (it works out the format itself) and never touches the network.

Finance notes:
* ``volume`` is the number of ETF units traded, ``value`` the rupees traded.
* ``close`` is BSE's own official close (not the NSE one). The two exchanges can close at
  slightly different prices, so we keep them in separate columns.
"""

from __future__ import annotations

import csv
import io
import logging
import math
import re
import zipfile
from datetime import date

import pandas as pd

from etfp.http import PoliteClient, SourceNotFoundError
from etfp.rawstore import RawRecord, RawStore

SOURCE = "bse_bhavcopy"

_BASE = "https://www.bseindia.com/download/BhavCopy/Equity/"
_UDIFF_URL = _BASE + "BhavCopy_BSE_CM_0_0_0_{stamp}_F_0000.CSV"
_LEGACY_URL = _BASE + "EQ_ISINCODE_{short}.zip"
BSE_HEADERS = {"Accept": "*/*", "Referer": "https://www.bseindia.com/"}

# First day we ask for the UDiFF file. 2024-01-02 is the earliest day confirmed live; the
# legacy file was still published in July 2024, so the two formats overlap and either is
# safe for days around the switch.
UDIFF_FIRST_DAY = date(2024, 1, 2)

OUTPUT_COLUMNS: tuple[str, ...] = (
    "date",
    "isin",
    "scrip_code",
    "ticker",
    "name",
    "series",
    "open",
    "high",
    "low",
    "close",
    "last",
    "prev_close",
    "volume",
    "value",
    "trades",
    "file_format",
    "source_file",
)

FORMAT_UDIFF = "udiff"
FORMAT_LEGACY = "legacy"

# UDiFF column -> our column
logger = logging.getLogger(__name__)

_UDIFF_TEXT = {"ISIN": "isin", "TckrSymb": "ticker", "FinInstrmNm": "name", "SctySrs": "series"}
_UDIFF_FLOAT = {
    "OpnPric": "open",
    "HghPric": "high",
    "LwPric": "low",
    "ClsPric": "close",
    "LastPric": "last",
    "PrvsClsgPric": "prev_close",
    "TtlTrfVal": "value",  # already rupees
}
_UDIFF_INT = {"FinInstrmId": "scrip_code", "TtlTradgVol": "volume", "TtlNbOfTxsExctd": "trades"}
_UDIFF_REQUIRED = ("TradDt", *_UDIFF_TEXT, *_UDIFF_FLOAT, *_UDIFF_INT)

# Legacy column -> our column. The legacy file has no ticker (SC_NAME is a short, padded
# company name), so ``ticker`` is empty for legacy rows.
_LEGACY_TEXT = {"ISIN_CODE": "isin", "SC_NAME": "name", "SC_GROUP": "series"}
_LEGACY_FLOAT = {
    "OPEN": "open",
    "HIGH": "high",
    "LOW": "low",
    "CLOSE": "close",
    "LAST": "last",
    "PREVCLOSE": "prev_close",
    "NET_TURNOV": "value",  # rupees
}
_LEGACY_INT = {"SC_CODE": "scrip_code", "NO_OF_SHRS": "volume", "NO_TRADES": "trades"}
_LEGACY_REQUIRED = ("TRADING_DATE", *_LEGACY_TEXT, *_LEGACY_FLOAT, *_LEGACY_INT)

_MISSING = frozenset({"", "-"})
_NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)\Z")
_INTEGER = re.compile(r"[+-]?\d+\Z")
_ISO_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})\Z")
_LEGACY_DATE = re.compile(r"(\d{1,2})-([A-Za-z]{3})-(\d{2})\Z")
_MONTHS = {
    m: i
    for i, m in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1
    )
}
_MAX_UNZIPPED_BYTES = 200_000_000


# -- names and download ----------------------------------------------------------


def uses_udiff(day: date) -> bool:
    """True if we ask for the UDiFF file for this day (else the legacy zip)."""
    return day >= UDIFF_FIRST_DAY


def url_for(day: date) -> str:
    """Download URL of the day's file, in whichever format BSE publishes for that period."""
    if uses_udiff(day):
        return _UDIFF_URL.format(stamp=day.strftime("%Y%m%d"))
    return _LEGACY_URL.format(short=day.strftime("%d%m%y"))


def raw_name(day: date) -> str:
    """File name exactly as BSE publishes it."""
    if uses_udiff(day):
        return f"BhavCopy_BSE_CM_0_0_0_{day.strftime('%Y%m%d')}_F_0000.CSV"
    return f"EQ_ISINCODE_{day.strftime('%d%m%y')}.zip"


def looks_like_html(content: bytes) -> bool:
    """True if the bytes are a web page (BSE's answer when the file does not exist)."""
    head = content[:512].lstrip().lower()
    return head.startswith((b"<!doctype html", b"<html", b"<head", b"<?xml"))


def fetch(client: PoliteClient, store: RawStore, day: date) -> RawRecord:
    """Download the day's file and store the untouched bytes. No parsing here.

    A web page instead of a data file means "no file for this day": raise
    ``SourceNotFoundError`` and store nothing.
    """
    url = url_for(day)
    result = client.get(url, source=SOURCE, day=day, headers=BSE_HEADERS)
    if looks_like_html(result.content):
        raise SourceNotFoundError(
            f"{SOURCE} for {day.isoformat()}: BSE answered HTTP {result.status_code} with a web "
            f"page instead of the file (its way of saying 'no file for this day'): {url}",
            source=SOURCE,
            day=day,
            url=url,
            status_code=result.status_code,
        )
    return store.save_result(source=SOURCE, day=day, name=raw_name(day), result=result)


# -- parsing ---------------------------------------------------------------------


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


def _to_date_iso(text: str, line: int, column: str) -> date:
    match = _ISO_DATE.match(text)
    if match is None:
        raise _fail(line, f"column {column}: {text!r} is not a date like 2026-11-12")
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError as exc:
        raise _fail(line, f"column {column}: {text!r} is not a real calendar date") from exc


def _to_date_legacy(text: str, line: int, column: str) -> date:
    """'04-Jan-21' -> 2021-01-04. The two-digit year is read as 20xx (the file starts in 2021)."""
    match = _LEGACY_DATE.match(text)
    month = _MONTHS.get(match.group(2).lower()) if match else None
    if match is None or month is None:
        raise _fail(line, f"column {column}: {text!r} is not a date like 04-Jan-21")
    try:
        return date(2000 + int(match.group(3)), month, int(match.group(1)))
    except ValueError as exc:
        raise _fail(line, f"column {column}: {text!r} is not a real calendar date") from exc


def _csv_text(content: bytes) -> str:
    """The CSV text of a raw file: unzip the legacy zip, or decode the UDiFF CSV directly."""
    if looks_like_html(content):
        raise ValueError(
            f"{SOURCE}: file is a web page, not data (BSE's answer for a missing file)"
        )
    if content[:2] == b"PK":
        try:
            archive = zipfile.ZipFile(io.BytesIO(content))
        except zipfile.BadZipFile as exc:
            raise ValueError(f"{SOURCE}: file starts like a zip but cannot be opened") from exc
        with archive:
            members = [i for i in archive.infolist() if not i.is_dir()]
            csvs = [i for i in members if i.filename.lower().endswith(".csv")]
            if len(csvs) != 1:
                raise ValueError(
                    f"{SOURCE}: expected exactly one CSV in the zip, found "
                    f"{[i.filename for i in members]}"
                )
            if csvs[0].file_size > _MAX_UNZIPPED_BYTES:
                raise ValueError(f"{SOURCE}: {csvs[0].filename} is {csvs[0].file_size} bytes")
            content = archive.read(csvs[0])
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(
            f"{SOURCE}: CSV is not UTF-8 text ({exc.reason} at byte {exc.start})"
        ) from exc


def parse(content: bytes, *, source_file: str) -> pd.DataFrame:
    """Turn a raw file (either format) into a typed table with every row of the file.

    The format is recognised from the header: a ``TradDt`` column means UDiFF, a
    ``SC_CODE`` column means legacy. Anything else is an error that says so. ``source_file``
    is the raw file's relative path in the raw store; it is copied onto every row.
    """
    text = _csv_text(content)
    reader = csv.reader(io.StringIO(text, newline=""))
    header: list[str] | None = None
    rows: list[tuple[int, list[str]]] = []
    try:
        for fields in reader:
            if not any(f.strip() for f in fields):
                continue
            fields = [f.strip() for f in fields]
            if header is None:
                while fields and fields[-1] == "":  # a trailing comma makes an empty name
                    fields.pop()
                header = fields
                continue
            rows.append((reader.line_num, fields))
    except csv.Error as exc:
        raise ValueError(f"{SOURCE}: line {reader.line_num}: unreadable CSV ({exc})") from exc
    if header is None:
        raise ValueError(f"{SOURCE}: file is empty")

    if "TradDt" in header:
        fmt, required = FORMAT_UDIFF, _UDIFF_REQUIRED
    elif "SC_CODE" in header:
        fmt, required = FORMAT_LEGACY, _LEGACY_REQUIRED
    else:
        raise ValueError(
            f"{SOURCE}: header is neither the UDiFF nor the legacy layout; got {header[:12]}. "
            "Has BSE changed the file format?"
        )
    missing = [c for c in required if c not in header]
    if missing:
        raise ValueError(
            f"{SOURCE}: {fmt} header is missing column(s) {missing}; got {header[:12]}. "
            "Has BSE changed the file format?"
        )
    if not rows:
        raise ValueError(f"{SOURCE}: header found but the file has no data rows")
    index = {name: header.index(name) for name in required}

    out: dict[str, list] = {name: [] for name in OUTPUT_COLUMNS}
    isin_col = index["ISIN" if fmt == FORMAT_UDIFF else "ISIN_CODE"]
    no_isin = 0
    malformed: list[tuple[int, str]] = []
    for line, fields in rows:
        # A row may be longer than the header only by empty fields (a header with a trailing
        # comma loses its last empty name above). Anything else is a real mismatch.
        if len(fields) < len(header) or any(fields[len(header) :]):
            if fmt == FORMAT_LEGACY:
                # Seen 7 Feb 2022: two legacy rows glued into one line (a missing line
                # break, one ISIN cut short). BSE's own file is corrupt there, so the
                # row is kept aside, not guessed at. The cleaner fails loudly if a kept-
                # aside line mentions one of OUR funds' ISINs (see clean/bse_prices.py).
                malformed.append((line, ",".join(fields)))
                continue
            raise _fail(line, f"expected {len(header)} fields but found {len(fields)}")
        if not fields[isin_col].strip():
            # Some legacy rows have no ISIN (seen 14 Jan 2022: an XT-group stock). We match
            # funds by ISIN only, so such a row can never be one of ours: skip and count it.
            no_isin += 1
            continue
        if fmt == FORMAT_UDIFF:
            _read_udiff_row(out, fields, index, line)
        else:
            _read_legacy_row(out, fields, index, line)
        out["file_format"].append(fmt)
        out["source_file"].append(source_file)
    if no_isin:
        logger.info("%s: %s: skipped %d row(s) with no ISIN", SOURCE, source_file, no_isin)
    frame = _frame(out)
    if malformed:
        logger.warning(
            "%s: %s: %d malformed legacy line(s) set aside (first: line %d)",
            SOURCE,
            source_file,
            len(malformed),
            malformed[0][0],
        )
    frame.attrs["malformed_lines"] = malformed
    return frame


def _read_udiff_row(out: dict[str, list], f: list[str], index: dict[str, int], line: int) -> None:
    out["date"].append(_to_date_iso(f[index["TradDt"]], line, "TradDt"))
    for raw, col in _UDIFF_TEXT.items():
        out[col].append(f[index[raw]] or None)
    for raw, col in _UDIFF_FLOAT.items():
        out[col].append(_to_float(f[index[raw]], line, raw))
    for raw, col in _UDIFF_INT.items():
        out[col].append(_to_int(f[index[raw]], line, raw))
    _require_isin(out, line)


def _read_legacy_row(out: dict[str, list], f: list[str], index: dict[str, int], line: int) -> None:
    out["date"].append(_to_date_legacy(f[index["TRADING_DATE"]], line, "TRADING_DATE"))
    for raw, col in _LEGACY_TEXT.items():
        out[col].append(f[index[raw]] or None)
    out["ticker"].append(None)  # the legacy file has no ticker column
    for raw, col in _LEGACY_FLOAT.items():
        out[col].append(_to_float(f[index[raw]], line, raw))
    for raw, col in _LEGACY_INT.items():
        out[col].append(_to_int(f[index[raw]], line, raw))
    _require_isin(out, line)


def _require_isin(out: dict[str, list], line: int) -> None:
    """Every row must have an ISIN: it is the only key we match funds by."""
    if not out["isin"][-1]:
        raise _fail(line, "column ISIN is empty")


def _frame(out: dict[str, list]) -> pd.DataFrame:
    """Apply the output dtypes: dates as date objects, counts as Int64, text as string."""
    text = {"isin", "ticker", "name", "series", "file_format", "source_file"}
    ints = {"scrip_code", "volume", "trades"}
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
