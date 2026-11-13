"""AMFI NAV files: the daily net asset value of every Indian mutual fund scheme and ETF.

NAV (net asset value) is what one unit's holdings are worth: the fund house adds
up the market value of everything the fund holds, subtracts costs, and divides by
the number of units. Fund houses report it once a day to AMFI (the industry body).
The ETF "premium" in this project is the exchange price compared with this NAV.

Two files, both semicolon-separated text:

* `NAVAll.txt`: the latest NAV of every scheme (one row per scheme).
* The history download: every NAV between two dates, for all schemes (one row per
  scheme per date). An unfiltered 10-day window is about 9 MB. Filtering and
  chunking are not implemented because the filter parameters are unverified
  (docs/DECISIONS.md D9).

The two files have different column orders and slightly different header names,
so each header is read and columns are found BY NAME, never by position.

Layout quirk: between data rows there are blank lines, a *section header* line
naming the scheme category ("Open Ended Schemes(Debt Scheme - Gilt Fund)") and an
*AMC line* naming the fund house ("Axis Mutual Fund"). A data row is a line whose
first field is an integer scheme code; it belongs to the most recent AMC line and
category line above it.

Design: `fetch_*()` stores the untouched bytes; `parse_*()` reads bytes and never
touches the network.
"""

from __future__ import annotations

import re
from datetime import date

import pandas as pd

from etfp.http import PoliteClient
from etfp.rawstore import RawRecord, RawStore

SOURCE_NAVALL = "amfi_navall"
SOURCE_HISTORY = "amfi_nav_history"
SOURCES: tuple[str, str] = (SOURCE_NAVALL, SOURCE_HISTORY)

NAVALL_URL = "https://www.amfiindia.com/spages/NAVAll.txt"
NAVALL_NAME = "NAVAll.txt"
HISTORY_URL = "https://portal.amfiindia.com/DownloadNAVHistoryReport_Po.aspx"
AMFI_HEADERS = {"Accept": "*/*"}

OUTPUT_COLUMNS: tuple[str, ...] = (
    "scheme_code",
    "scheme_name",
    "isin_growth",
    "isin_reinvest",
    "plan",
    "option",
    "nav",
    "nav_date",
    "amc",
    "category",
    "source_file",
)

# Header names (after tidying whitespace) the two files use for each of our columns.
_NAV_COLUMN = "Net Asset Value"
_DATE_COLUMN = "Date"
_COMMON_HEADER = {
    "scheme_code": "Scheme Code",
    # In a growth scheme this ISIN is the growth ISIN; in a payout-IDCW scheme it is
    # the dividend-payout ISIN. AMFI uses one column for both, so we keep its meaning.
    "isin_growth": "ISIN Div Payout/ISIN Growth",
    "isin_reinvest": "ISIN Div Reinvestment",
    "plan": "Plan",
    "option": "Option",
    "nav": _NAV_COLUMN,
    "nav_date": _DATE_COLUMN,
}
_NAME_HEADER = {SOURCE_NAVALL: "Scheme Name", SOURCE_HISTORY: "NAV Name"}

_MONTH_ABBR = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_MONTHS = {m.lower(): i for i, m in enumerate(_MONTH_ABBR, 1)}

_INTEGER = re.compile(r"\d+\Z")
_NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)\Z")
_DATE = re.compile(r"(\d{1,2})-([A-Za-z]{3})-(\d{4})\Z")
_SECTION = re.compile(
    r"(?:(?:open|close|closed)\s*ended\s+schemes|interval\s+fund\s+schemes)", re.I
)
_LINE_BREAK = re.compile(r"\r\n|\r|\n")
_MISSING_ISIN = frozenset({"", "-"})


def _stamp(day: date) -> str:
    """Date as AMFI writes it in URLs: 01-Sep-2026 (month names do not depend on locale)."""
    return f"{day.day:02d}-{_MONTH_ABBR[day.month - 1]}-{day.year}"


def history_url(start: date, end: date) -> str:
    """Download URL of the NAV history between two dates, both included."""
    return f"{HISTORY_URL}?frmdt={_stamp(start)}&todt={_stamp(end)}"


def history_raw_name(start: date, end: date) -> str:
    """Raw file name we use for a history download."""
    return f"navhistory_{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}.txt"


def fetch_navall(client: PoliteClient, store: RawStore, day: date) -> RawRecord:
    """Download today's NAVAll.txt and store it under `day` (the day it was fetched)."""
    result = client.get(NAVALL_URL, source=SOURCE_NAVALL, day=day, headers=AMFI_HEADERS)
    return store.save_result(source=SOURCE_NAVALL, day=day, name=NAVALL_NAME, result=result)


def fetch_history(client: PoliteClient, store: RawStore, start: date, end: date) -> RawRecord:
    """Download the NAV history for start..end and store it under day = `end`."""
    if start > end:
        raise ValueError(f"{SOURCE_HISTORY}: start {start} is after end {end}")
    result = client.get(
        history_url(start, end), source=SOURCE_HISTORY, day=end, headers=AMFI_HEADERS
    )
    return store.save_result(
        source=SOURCE_HISTORY, day=end, name=history_raw_name(start, end), result=result
    )


# -- parsing ------------------------------------------------------------------


def _decode(content: bytes, source: str) -> str:
    """AMFI files are UTF-8 (names contain a typographic apostrophe, bytes E2 80 99).

    If a file is ever not valid UTF-8 we fall back to Windows-1252, the usual
    older Windows encoding, rather than guessing silently-wrong characters.
    """
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    try:
        return content.decode("cp1252")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{source}: file is neither UTF-8 nor Windows-1252 text") from exc


def _tidy(name: str) -> str:
    """Normalise a header name: collapse spaces, and drop spaces around '/'."""
    return re.sub(r"\s*/\s*", "/", re.sub(r"\s+", " ", name.strip()))


def _nav_value(text: str) -> float | None:
    return float(text) if _NUMBER.match(text) else None


def _date_value(text: str, source: str, line: int) -> date:
    match = _DATE.match(text)
    month = _MONTHS.get(match.group(2).lower()) if match else None
    if match is None or month is None:
        raise ValueError(f"{source}: line {line}: {text!r} is not a date like 09-Nov-2026")
    try:
        return date(int(match.group(3)), month, int(match.group(1)))
    except ValueError as exc:
        raise ValueError(f"{source}: line {line}: {text!r} is not a real calendar date") from exc


def _parse(content: bytes, *, source: str, source_file: str) -> pd.DataFrame:
    lines = _LINE_BREAK.split(_decode(content, source))
    header_at = next((i for i, text in enumerate(lines) if text.strip()), None)
    if header_at is None:
        raise ValueError(f"{source}: file is empty")
    header = [_tidy(h) for h in lines[header_at].split(";")]
    wanted = {**_COMMON_HEADER, "scheme_name": _NAME_HEADER[source]}
    absent = [name for name in wanted.values() if name not in header]
    if absent:
        raise ValueError(
            f"{source}: header is missing column(s) {absent}; first line is "
            f"{lines[header_at][:120]!r}. Has AMFI changed the file format?"
        )
    at = {col: header.index(name) for col, name in wanted.items()}

    out: dict[str, list] = {name: [] for name in OUTPUT_COLUMNS}
    amc: str | None = None
    category: str | None = None
    nav_missing = 0
    for n, text in enumerate(lines[header_at + 1 :], start=header_at + 2):
        if not text.strip():
            continue
        fields = [f.strip() for f in text.split(";")]
        if _INTEGER.match(fields[0]):
            if len(fields) != len(header):
                raise ValueError(
                    f"{source}: line {n}: expected {len(header)} fields but found "
                    f"{len(fields)}: {text[:100]!r}"
                )
            nav = _nav_value(fields[at["nav"]])
            nav_missing += nav is None  # "N.A.", "-" or empty: counted, never turned into 0
            out["scheme_code"].append(int(fields[at["scheme_code"]]))
            out["scheme_name"].append(fields[at["scheme_name"]] or None)
            for col in ("isin_growth", "isin_reinvest"):
                value = fields[at[col]]
                out[col].append(None if value in _MISSING_ISIN else value)
            out["plan"].append(fields[at["plan"]] or None)
            out["option"].append(fields[at["option"]] or None)
            out["nav"].append(float("nan") if nav is None else nav)
            out["nav_date"].append(_date_value(fields[at["nav_date"]], source, n))
            out["amc"].append(amc)
            out["category"].append(category)
            out["source_file"].append(source_file)
        elif ";" in text:
            raise ValueError(
                f"{source}: line {n}: has ';' separators but the first field is not a "
                f"scheme code: {text[:100]!r}"
            )
        elif _SECTION.match(text.strip()):
            # NOT resetting `amc` here: in the real files a new category sometimes
            # continues the same fund house without repeating its AMC line.
            category = text.strip()
        else:
            amc = text.strip()

    if not out["scheme_code"]:
        raise ValueError(f"{source}: header found but the file has no data rows")
    frame = _frame(out)
    frame.attrs["nav_missing"] = nav_missing
    return frame


def _frame(out: dict[str, list]) -> pd.DataFrame:
    """Apply the output dtypes: dates as date objects, codes as Int64, text as string."""
    data: dict[str, pd.Series] = {}
    for name in OUTPUT_COLUMNS:
        values = out[name]
        if name == "scheme_code":
            data[name] = pd.Series(pd.array(values, dtype="Int64"))
        elif name == "nav":
            data[name] = pd.Series(values, dtype="float64")
        elif name == "nav_date":
            data[name] = pd.Series(values, dtype=object)
        else:
            data[name] = pd.Series(pd.array(values, dtype="string"))
    return pd.DataFrame(data, columns=list(OUTPUT_COLUMNS))


def parse_navall(content: bytes, *, source_file: str) -> pd.DataFrame:
    """Parse NAVAll.txt. `df.attrs["nav_missing"]` counts rows whose NAV is not a number."""
    return _parse(content, source=SOURCE_NAVALL, source_file=source_file)


def parse_history(content: bytes, *, source_file: str) -> pd.DataFrame:
    """Parse a NAV history download. `df.attrs["nav_missing"]` counts non-numeric NAVs."""
    return _parse(content, source=SOURCE_HISTORY, source_file=source_file)
