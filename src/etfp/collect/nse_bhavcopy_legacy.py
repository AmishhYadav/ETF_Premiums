"""NSE's OLD-format bhavcopy (``cmDDMONYYYYbhav.csv.zip``) and the MTO delivery file.

Why this source exists (DECISIONS D54)
--------------------------------------
NSE's ``sec_bhavdata_full`` archive file for 4 Nov 2021 (the Diwali Muhurat
session) is a copy of the 3 Nov file. The older "historical" bhavcopy for the
same day is correct: for MON100 it gives PREVCLOSE 117.57 (the real 3 Nov close)
and CLOSE 120.09, which is exactly the PREV_CLOSE the 8 Nov full file carries.
So this module is a REPAIR source: it is fetched only for sessions where the full
bhavcopy is missing or listed in ``config/raw_exceptions.yaml``; it is not part
of the daily run.

Format (one zip with one CSV)::

    SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,
    TIMESTAMP,TOTALTRADES,ISIN,        <- trailing comma: an empty last column
    20MICRONS,EQ,60,60,58.75,59.75,59.95,58.4,33522,1998734.9,04-NOV-2021,423,INE144J01027,

``TOTTRDVAL`` is in rupees (exact), unlike the full bhavcopy's lacs. There is no
average price and no delivery data in this file; the delivery figures are in the
separate MTO file (``archives/equities/mto/MTO_DDMMYYYY.DAT``), which we save raw
(source ``nse_mto``) but do not parse yet.

``parse()`` returns the same columns as ``nse_bhavcopy.parse`` so the cleaner can
treat both alike (``vwap``, ``deliv_qty`` and ``deliv_pct`` are missing here).
"""

from __future__ import annotations

import csv
import io
import math
import re
import zipfile
from datetime import date

import pandas as pd

from etfp.collect import nse_bhavcopy
from etfp.http import PoliteClient
from etfp.rawstore import RawRecord, RawStore

SOURCE = "nse_bhavcopy_legacy"
SOURCE_MTO = "nse_mto"

_URL_TEMPLATE = (
    "https://nsearchives.nseindia.com/content/historical/EQUITIES/{year}/{mon}/"
    "cm{day:02d}{mon}{year}bhav.csv.zip"
)
_MTO_URL_TEMPLATE = "https://nsearchives.nseindia.com/archives/equities/mto/MTO_{stamp}.DAT"
NSE_HEADERS = nse_bhavcopy.NSE_HEADERS

_REQUIRED = (
    "SYMBOL",
    "SERIES",
    "OPEN",
    "HIGH",
    "LOW",
    "CLOSE",
    "LAST",
    "PREVCLOSE",
    "TOTTRDQTY",
    "TOTTRDVAL",
    "TIMESTAMP",
    "TOTALTRADES",
)
_MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
_NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)\Z")
_INTEGER = re.compile(r"[+-]?\d+\Z")
_STAMP = re.compile(r"(\d{1,2})-([A-Za-z]{3})-(\d{4})\Z")


def url_for(day: date) -> str:
    return _URL_TEMPLATE.format(year=day.year, mon=_MONTHS[day.month - 1], day=day.day)


def raw_name(day: date) -> str:
    """File name exactly as NSE publishes it, e.g. cm04NOV2021bhav.csv.zip."""
    return f"cm{day.day:02d}{_MONTHS[day.month - 1]}{day.year}bhav.csv.zip"


def mto_url_for(day: date) -> str:
    return _MTO_URL_TEMPLATE.format(stamp=day.strftime("%d%m%Y"))


def mto_raw_name(day: date) -> str:
    return f"MTO_{day.strftime('%d%m%Y')}.DAT"


def fetch(client: PoliteClient, store: RawStore, day: date) -> RawRecord:
    """Download the old-format bhavcopy zip and store the untouched bytes."""
    result = client.get(url_for(day), source=SOURCE, day=day, headers=NSE_HEADERS)
    return store.save_result(source=SOURCE, day=day, name=raw_name(day), result=result)


def fetch_mto(client: PoliteClient, store: RawStore, day: date) -> RawRecord:
    """Download the security-wise delivery (MTO) file and store the untouched bytes."""
    result = client.get(mto_url_for(day), source=SOURCE_MTO, day=day, headers=NSE_HEADERS)
    return store.save_result(source=SOURCE_MTO, day=day, name=mto_raw_name(day), result=result)


def _fail(line: int, message: str) -> ValueError:
    return ValueError(f"{SOURCE}: line {line}: {message}")


def _float(text: str, line: int, column: str) -> float:
    if text in ("", "-"):
        return math.nan
    if not _NUMBER.match(text):
        raise _fail(line, f"column {column}: {text!r} is not a number")
    return float(text)


def _int(text: str, line: int, column: str) -> int | None:
    if text in ("", "-"):
        return None
    if not _INTEGER.match(text):
        raise _fail(line, f"column {column}: {text!r} is not a whole number")
    return int(text)


def _date(text: str, line: int) -> date:
    m = _STAMP.match(text)
    if m is None or m.group(2).upper() not in _MONTHS:
        raise _fail(line, f"column TIMESTAMP: {text!r} is not a date like 04-NOV-2021")
    try:
        return date(int(m.group(3)), _MONTHS.index(m.group(2).upper()) + 1, int(m.group(1)))
    except ValueError as exc:
        raise _fail(line, f"column TIMESTAMP: {text!r} is not a real calendar date") from exc


def _csv_text(content: bytes) -> str:
    """The CSV text, from the zip (NSE's file) or, for tests, plain CSV bytes."""
    if content[:2] == b"PK":
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as z:
                names = [n for n in z.namelist() if n.lower().endswith(".csv")]
                if len(names) != 1:
                    raise ValueError(f"{SOURCE}: expected one CSV in the zip, found {z.namelist()}")
                data = z.read(names[0])
        except zipfile.BadZipFile as exc:
            raise ValueError(f"{SOURCE}: cannot open zip ({exc})") from exc
    else:
        data = content
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{SOURCE}: file is not UTF-8 text ({exc.reason})") from exc


def parse(content: bytes, *, source_file: str) -> pd.DataFrame:
    """Typed table with the columns of ``nse_bhavcopy.parse`` (vwap and delivery are missing)."""
    reader = csv.reader(io.StringIO(_csv_text(content), newline=""))
    header: list[str] | None = None
    rows: list[tuple[int, list[str]]] = []
    for fields in reader:
        if not any(f.strip() for f in fields):
            continue
        clean = [f.strip() for f in fields]
        if header is None:
            while clean and clean[-1] == "":  # trailing comma in the header
                clean.pop()
            header = clean
            continue
        rows.append((reader.line_num, clean))
    if header is None:
        raise ValueError(f"{SOURCE}: file is empty")
    missing = [c for c in _REQUIRED if c not in header]
    if missing:
        raise ValueError(
            f"{SOURCE}: header is missing column(s) {missing}; got {header}. "
            "Has NSE changed the file format?"
        )
    if not rows:
        raise ValueError(f"{SOURCE}: header found but the file has no data rows")
    idx = {name: header.index(name) for name in _REQUIRED}

    out: dict[str, list] = {c: [] for c in nse_bhavcopy.OUTPUT_COLUMNS}
    for line, f in rows:
        if len(f) < len(header):
            raise _fail(line, f"expected {len(header)} fields but found {len(f)}")
        if not f[idx["SYMBOL"]]:
            raise _fail(line, "column SYMBOL is empty")
        out["symbol"].append(f[idx["SYMBOL"]])
        out["series"].append(f[idx["SERIES"]] or None)
        out["date"].append(_date(f[idx["TIMESTAMP"]], line))
        out["prev_close"].append(_float(f[idx["PREVCLOSE"]], line, "PREVCLOSE"))
        out["open"].append(_float(f[idx["OPEN"]], line, "OPEN"))
        out["high"].append(_float(f[idx["HIGH"]], line, "HIGH"))
        out["low"].append(_float(f[idx["LOW"]], line, "LOW"))
        out["last"].append(_float(f[idx["LAST"]], line, "LAST"))
        out["close"].append(_float(f[idx["CLOSE"]], line, "CLOSE"))
        out["vwap"].append(math.nan)
        out["volume"].append(_int(f[idx["TOTTRDQTY"]], line, "TOTTRDQTY"))
        out["value"].append(_float(f[idx["TOTTRDVAL"]], line, "TOTTRDVAL"))
        out["trades"].append(_int(f[idx["TOTALTRADES"]], line, "TOTALTRADES"))
        out["deliv_qty"].append(None)
        out["deliv_pct"].append(math.nan)
        out["source_file"].append(source_file)
    return nse_bhavcopy._frame(out)
