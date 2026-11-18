"""Monthly portfolio files published by the fund houses (SEBI makes every AMC publish them).

Why this exists (plain words)
-----------------------------
Every mutual fund must publish, for each scheme, a "monthly portfolio statement": a list of
everything the scheme owned on the last day of the month, with quantities, values and the
scheme's total net assets. That gives us two numbers the NSE files cannot give for 2021-2023:

* a **feeder fund** (fund of funds, FoF) owns only units of one of our ETFs. Its portfolio file
  says exactly how many ETF units it held at month end. Those units are bought on the exchange
  and then locked up in the feeder, so they drop out of the free-floating supply
  (``fof_monthly.etf_units_held``);
* the ETF's own file gives its total net assets (AUM). AUM divided by the NAV per unit is the
  number of units outstanding at month end (``units_monthly``), for months before NSE's daily
  security master exists (it starts in 2024).

Three sources, all public, no login (checked 18 Nov 2026, docs/DECISIONS.md D46):

``amc_portfolio_motilal``  Motilal Oswal MF "Month End Portfolio" downloads. The page is built
    by JavaScript from a public JSON search (``.../api/search-documents.json``), which we call
    once per month. One big Excel workbook per month with one sheet per scheme.
``amc_portfolio_mirae``    Mirae Asset MF "Portfolio" downloads. The page calls a public JSON
    service (``POST /AjaxService/GetDownloadsData``) that lists every file; one small Excel
    file per scheme per month.
``amc_portfolio_nippon``   Nippon India MF "Factsheet, Portfolio and Other Disclosures" page.
    Plain links to one big Excel workbook per month (one sheet per scheme). Files are named
    ``.xls`` but most are really ``.xlsx``; ``parse`` looks at the first bytes, not the name.

Design (same as the other collectors): ``fetch()`` saves the untouched bytes under
``data/raw/<source>/<download date>/``; ``parse()`` reads bytes only (no network) and is tested
on small saved fixtures. Be polite: at least 2 s between requests to a site, retry with backoff,
stop on a block (HTTP 401/403/429), and name the source and URL in every failure. A file that
is already in the raw store (same URL, from any earlier day) is not downloaded again.

Units: all three AMCs print "Rs. in Lakhs/Lacs". One lakh = 100,000 rupees. ``parse`` returns
rupees (``market_value_inr``, ``aum_inr``); percentages are returned on a 0-100 scale.
"""

from __future__ import annotations

import html
import io
import json
import logging
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from urllib.parse import quote, urljoin, urlsplit

import pandas as pd

from etfp.http import FetchResult, PoliteClient, SourceError
from etfp.rawstore import RawRecord, RawStore

logger = logging.getLogger("etfp.collect")

AMCS: tuple[str, ...] = ("motilal", "mirae", "nippon")
SOURCES: dict[str, str] = {amc: f"amc_portfolio_{amc}" for amc in AMCS}

MOTILAL_HOST = "www.motilaloswalmf.com"
MIRAE_HOST = "www.miraeassetmf.co.in"
NIPPON_HOST = "mf.nipponindiaim.com"
MIN_INTERVAL_S = 2.0
POLITE_INTERVALS: dict[str, float] = {
    "motilaloswalmf.com": MIN_INTERVAL_S,
    "miraeassetmf.co.in": MIN_INTERVAL_S,
    "nipponindiaim.com": MIN_INTERVAL_S,
}

MOTILAL_BASE = f"https://{MOTILAL_HOST}"
MOTILAL_SEARCH = (
    f"{MOTILAL_BASE}/content/aem-cloud-dept-backend-motilal-oswal/api/search-documents.json"
)
MIRAE_BASE = f"https://{MIRAE_HOST}"
MIRAE_LIST = f"{MIRAE_BASE}/AjaxService/GetDownloadsData"
MIRAE_PAGE = f"{MIRAE_BASE}/downloads/portfolio"
NIPPON_BASE = f"https://{NIPPON_HOST}"
NIPPON_PAGE = f"{NIPPON_BASE}/investor-service/downloads/factsheet-portfolio-and-other-disclosures"

_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
LAKH = 100_000.0

HOLDINGS_COLUMNS: tuple[str, ...] = (
    "amc",
    "etf_symbol",
    "role",
    "scheme_name",
    "month_end",
    "holding_name",
    "isin",
    "quantity",
    "market_value_inr",
    "pct_net_assets",
    "aum_inr",
    "sheet",
    "source_file",
)


class PortfolioParseError(ValueError):
    """A portfolio workbook does not look the way we expect. Names the file."""


# -- which schemes we care about ----------------------------------------------------------


@dataclass(frozen=True)
class Target:
    """One scheme we extract: an ETF of the universe, or the feeder fund that holds an ETF."""

    etf_symbol: str
    role: str  # "etf" or "fof"
    pattern: re.Pattern[str]  # matched against the scheme's title line (lower case, tidy spaces)


def _p(text: str) -> re.Pattern[str]:
    return re.compile(text, re.I)


# Order matters: the "fund of fund" patterns come first so an FoF title is never read as the ETF.
TARGETS: tuple[Target, ...] = (
    Target("MON100", "fof", _p(r"nasdaq[- ]?100 (fund of fund|fof)")),
    Target("MAFANG", "fof", _p(r"fang ?\+ ?etf (fund of fund|fof)")),
    Target("MASPTOP50", "fof", _p(r"s&p 500 top 50 etf (fund of fund|fof)")),
    Target("MAHKTECH", "fof", _p(r"hang seng tech etf (fund of fund|fof)")),
    Target("MON100", "etf", _p(r"nasdaq[- ]?100 etf")),
    Target("MONQ50", "etf", _p(r"nasdaq q ?-?50 etf")),
    Target("MAFANG", "etf", _p(r"fang ?\+ ?etf")),
    Target("MASPTOP50", "etf", _p(r"s&p 500 top 50 etf")),
    Target("MAHKTECH", "etf", _p(r"hang seng tech etf")),
    Target("HNGSNGBEES", "etf", _p(r"hang seng bees")),
)

_TITLE_SKIP = re.compile(
    r"asset management company|investment manager|registered office|visit us|"
    r"portfolio statement|^portfolio as on|back to index|^home$|cin:",
    re.I,
)
_AMC_NAME = re.compile(r"^(motilal oswal|mirae asset|nippon india|nippon)", re.I)


def _tidy(text: str) -> str:
    return " ".join(str(text).replace("\xa0", " ").split())


def match_target(title: str) -> Target | None:
    """Which of our schemes a scheme title line names, or None."""
    t = _tidy(title).lower()
    for target in TARGETS:
        if target.pattern.search(t):
            return target
    return None


# -- reading Excel files ------------------------------------------------------------------

Sheet = tuple[str, Callable[[int | None], list[list[Any]]]]


def open_workbook(content: bytes, *, source_file: str) -> list[Sheet]:
    """Sheets of an Excel file as (name, rows loader), whatever its real format is.

    The first bytes decide: ``PK`` = a zip, i.e. .xlsx (openpyxl); the OLE signature
    ``D0 CF 11 E0`` = old .xls (xlrd). The file extension is not trusted: Nippon India
    names .xlsx files ``.xls``.
    """
    if content[:2] == b"PK":
        import openpyxl

        try:
            wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        except Exception as exc:  # corrupt zip, wrong type
            raise PortfolioParseError(f"{source_file}: cannot open as .xlsx ({exc})") from exc

        def xlsx_rows(ws: Any) -> Callable[[int | None], list[list[Any]]]:
            return lambda limit: [list(r) for r in ws.iter_rows(values_only=True, max_row=limit)]

        return [(ws.title, xlsx_rows(ws)) for ws in wb.worksheets]
    if content[:4] == b"\xd0\xcf\x11\xe0":
        import xlrd

        try:
            book = xlrd.open_workbook(file_contents=content)
        except Exception as exc:
            raise PortfolioParseError(f"{source_file}: cannot open as .xls ({exc})") from exc

        def xls_rows(sh: Any) -> Callable[[int | None], list[list[Any]]]:
            return lambda limit: [
                sh.row_values(i) for i in range(sh.nrows if limit is None else min(limit, sh.nrows))
            ]

        return [(sh.name, xls_rows(sh)) for sh in book.sheets()]
    raise PortfolioParseError(
        f"{source_file}: not an Excel file (first bytes {content[:8]!r}); the site may have "
        "sent an error or login page"
    )


# -- parsing one sheet --------------------------------------------------------------------

_ISIN = re.compile(r"[A-Z]{2}[A-Z0-9]{9}[0-9]\Z")
_AS_ON = re.compile(
    r"as\s+on\s+(?:the\s+)?([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?\s*,?\s*(\d{4})", re.I
)
_AS_ON_DMY = re.compile(
    r"as\s+on\s+(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})", re.I
)
_HEAD_ROWS = 14


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return float(value) if value == value else None  # NaN -> None
    return None


def _text(value: Any) -> str:
    return _tidy(value) if isinstance(value, str) else ""


def _month_number(name: str) -> int | None:
    key = name.strip().lower()[:3]
    return _MONTHS.index(key) + 1 if key in _MONTHS else None


def _month_end_from_text(text: str) -> date | None:
    """'as on September 30, 2026' / 'as on 30th September 2026' -> date(2026, 9, 30)."""
    for match in _AS_ON.finditer(text):
        month = _month_number(match.group(1))
        if month is not None:
            try:
                return date(int(match.group(3)), month, int(match.group(2)))
            except ValueError:
                continue
    for match in _AS_ON_DMY.finditer(text):
        month = _month_number(match.group(2))
        if month is not None:
            try:
                return date(int(match.group(3)), month, int(match.group(1)))
            except ValueError:
                continue
    return None


def _title_line(rows: Sequence[Sequence[Any]]) -> str:
    """The scheme's own title: the first head cell naming the AMC or fund, not boilerplate."""
    for row in rows[:_HEAD_ROWS]:
        for cell in row:
            t = _text(cell)
            if not t or t.startswith("(") or _TITLE_SKIP.search(t):
                continue
            if _AMC_NAME.match(t) or "etf" in t.lower() or "fund" in t.lower():
                return t
    return ""


def _parse_sheet(
    sheet: str, rows: list[list[Any]], *, amc: str, source_file: str
) -> pd.DataFrame | None:
    title = _title_line(rows)
    target = match_target(title) if title else None
    if target is None:
        return None
    where = f"{source_file} sheet {sheet!r}"
    head_text = " ".join(_tidy(c) for r in rows[:_HEAD_ROWS] for c in r if isinstance(c, str))
    month_end = _month_end_from_text(head_text)
    if month_end is None:
        raise PortfolioParseError(f"{where}: no 'as on <date>' line found for {title!r}")

    # Header row: has both an ISIN and a quantity column.
    header_at = None
    for i, row in enumerate(rows[:30]):
        cells = [_text(c).lower() for c in row]
        if any(c.startswith("isin") for c in cells) and any(
            c.startswith("quantity") for c in cells
        ):
            header_at = i
            break
    if header_at is None:
        raise PortfolioParseError(f"{where}: no header row with ISIN and Quantity")
    header = [_text(c).lower() for c in rows[header_at]]
    header_text = " ".join(header)
    if not re.search(r"lakh|lac", header_text):
        raise PortfolioParseError(f"{where}: header does not say the values are in lakhs")
    isin_col = next(j for j, c in enumerate(header) if c.startswith("isin"))
    name_col = next((j for j, c in enumerate(header) if c.startswith("name")), None)
    name_before_isin = name_col is None or name_col < isin_col

    # Total net assets: the GRAND TOTAL line.
    aum_lakh = None
    total_pct = None
    total_at = None
    for i in range(header_at + 1, len(rows)):
        if any(_text(c).upper() == "GRAND TOTAL" for c in rows[i]):
            nums = [n for n in (_num(c) for c in rows[i]) if n is not None]
            if nums:
                aum_lakh = nums[0]
                total_pct = nums[1] if len(nums) > 1 else None
            total_at = i
            break
    if aum_lakh is None or total_at is None:
        raise PortfolioParseError(f"{where}: no GRAND TOTAL line with a number")
    # Older files print % as a fraction (1 = 100%); newer ones as percent (100).
    pct_scale = 100.0 if total_pct is not None and total_pct <= 1.5 else 1.0

    out: list[dict[str, Any]] = []
    for row in rows[header_at + 1 : total_at]:
        isin_at = next((j for j, c in enumerate(row) if _ISIN.match(_text(c))), None)
        if isin_at is None:
            continue
        nums = [n for n in (_num(c) for c in row[isin_at + 1 :]) if n is not None]
        if name_before_isin:
            names = [_text(c) for c in row[:isin_at] if _text(c)]
        else:
            names = [_text(c) for c in row[isin_at + 1 :] if _text(c)]
        holding = (names[-1] if name_before_isin else names[0]) if names else ""
        qty = nums[0] if len(nums) > 0 else None
        value = nums[1] if len(nums) > 1 else None
        pct = nums[2] if len(nums) > 2 else None
        out.append(
            {
                "holding_name": holding,
                "isin": _text(row[isin_at]),
                "quantity": qty,
                "market_value_inr": None if value is None else value * LAKH,
                "pct_net_assets": None if pct is None else pct * pct_scale,
            }
        )
    if not out:
        out.append(
            {
                "holding_name": None,
                "isin": None,
                "quantity": None,
                "market_value_inr": None,
                "pct_net_assets": None,
            }
        )
    frame = pd.DataFrame(out)
    frame.insert(0, "amc", amc)
    frame.insert(1, "etf_symbol", target.etf_symbol)
    frame.insert(2, "role", target.role)
    frame.insert(3, "scheme_name", title)
    frame.insert(4, "month_end", month_end)
    frame["aum_inr"] = aum_lakh * LAKH
    frame["sheet"] = sheet
    frame["source_file"] = source_file
    return frame[list(HOLDINGS_COLUMNS)]


def parse(content: bytes, *, amc: str, source_file: str) -> pd.DataFrame:
    """Holdings of our target schemes in one portfolio workbook (no network).

    One row per holding of each target scheme found in the workbook (ETF or feeder fund, see
    ``TARGETS``), with the scheme's total net assets repeated on every row (``aum_inr``,
    rupees). A workbook that contains none of our schemes gives an empty frame. Anything that
    looks wrong (no 'as on' date, no GRAND TOTAL, values not in lakhs) raises
    ``PortfolioParseError`` naming the file and sheet; nothing is guessed.
    """
    if amc not in AMCS:
        raise ValueError(f"unknown AMC {amc!r}; expected one of {AMCS}")
    frames: list[pd.DataFrame] = []
    for name, load in open_workbook(content, source_file=source_file):
        # Cheap pre-check on the first rows only, so a 100-sheet workbook is not read in full.
        head = load(_HEAD_ROWS)
        title = _title_line(head)
        if not title or match_target(title) is None:
            continue
        frame = _parse_sheet(name, load(None), amc=amc, source_file=source_file)
        if frame is not None:
            frames.append(frame)
    if not frames:
        return pd.DataFrame({c: [] for c in HOLDINGS_COLUMNS})
    return pd.concat(frames, ignore_index=True)


# -- finding the files --------------------------------------------------------------------


@dataclass(frozen=True)
class FileRef:
    """One downloadable portfolio file found on an AMC's site."""

    amc: str
    url: str
    name: str  # name to store under data/raw/<source>/<day>/
    title: str
    published: date | None


def _safe_name(text: str) -> str:
    """A file name safe for the raw store: no slashes, '..', or odd characters."""
    out = re.sub(r"[^A-Za-z0-9._+()&-]+", "_", text).strip("._")
    return re.sub(r"\.{2,}", ".", out) or "file"


_EXCEL = (".xls", ".xlsx")


def month_range(first: date, last: date) -> list[tuple[int, str]]:
    """(year, 'jan'..'dec') for every month from ``first`` to ``last`` inclusive."""
    out: list[tuple[int, str]] = []
    y, m = first.year, first.month
    while (y, m) <= (last.year, last.month):
        out.append((y, _MONTHS[m - 1]))
        y, m = (y, m + 1) if m < 12 else (y + 1, 1)
    return out


_MOTILAL_SKIP = re.compile(r"fortnight|forthnight|half|performance|overlap", re.I)


def motilal_refs(
    client: PoliteClient, store: RawStore, day: date, first: date, last: date
) -> list[FileRef]:
    """List Motilal Oswal's month-end portfolio files published between two months.

    Motilal files are filed under the PUBLICATION month (the October folder holds the
    September portfolio, published about the 8th). One JSON request per month; each answer
    is saved raw as ``listing_<year>-<mon>.json``.
    """
    source = SOURCES["motilal"]
    refs: list[FileRef] = []
    for year, mon in month_range(first, last):
        url = (
            f"{MOTILAL_SEARCH}?year={year}&category={quote('month end portfolio')}"
            f"&month={mon}&type=mf"
        )
        result = client.get(url, source=source, day=day, headers={"Accept": "application/json"})
        store.save_result(source=source, day=day, name=f"listing_{year}-{mon}.json", result=result)
        try:
            body = json.loads(result.content)
            results = body["results"]
        except (ValueError, KeyError, TypeError) as exc:
            raise SourceError(
                f"{source}: listing for {year}-{mon} is not the expected JSON ({exc})",
                source=source,
                day=day,
                url=url,
            ) from exc
        for item in results:
            path = str(item.get("path", ""))
            title = str(item.get("title", ""))
            if not path.lower().endswith(_EXCEL) or _MOTILAL_SKIP.search(title + " " + path):
                continue
            published = _dmy(item.get("publishDate"))
            base = path.rsplit("/", 1)[-1]
            refs.append(
                FileRef(
                    "motilal",
                    MOTILAL_BASE + quote(path),
                    _safe_name(f"{year}-{mon}__{base}"),
                    title.strip(),
                    published,
                )
            )
    return refs


def _dmy(text: Any) -> date | None:
    match = re.fullmatch(r"(\d{2})-(\d{2})-(\d{4})", str(text).strip())
    if match is None:
        return None
    try:
        return date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
    except ValueError:
        return None


_MIRAE_SCHEMES = (
    ("fang", "MAFANG"),
    ("s&p 500 top 50", "MASPTOP50"),
    ("hang seng tech", "MAHKTECH"),
)


def mirae_scheme(title: str) -> tuple[str, str] | None:
    """(etf symbol, 'etf'|'fof') of a Mirae listing title, or None if not one of ours."""
    t = _tidy(title).lower().replace(" +", "+").replace("+ ", "+")
    for key, symbol in _MIRAE_SCHEMES:
        if key in t:
            role = "fof" if ("fund of fund" in t or "fof" in t) else "etf"
            return symbol, role
    return None


def mirae_refs(client: PoliteClient, store: RawStore, day: date) -> list[FileRef]:
    """List Mirae Asset's monthly portfolio files for our six schemes.

    The portfolio page asks ``POST /AjaxService/GetDownloadsData`` for the tab
    ``portfolio_tab1`` ("Monthly Portfolio"); the answer lists every file of every scheme back
    to 2009 (3,900 entries). We ask for all in one page and keep our schemes. The listing is
    saved raw as ``listing_monthly.json``.
    """
    source = SOURCES["mirae"]
    body = {"request": {"modulename": "portfolio_tab1", "pgno": 1, "pgsize": 5000}}
    result = client.post(
        MIRAE_LIST,
        source=source,
        day=day,
        json=body,
        headers={"Referer": MIRAE_PAGE, "Accept": "application/json"},
    )
    store.save_result(source=source, day=day, name="listing_monthly.json", result=result)
    try:
        data = json.loads(result.content)
        if data["ReturnCode"] != "0":
            raise ValueError(f"ReturnCode {data['ReturnCode']}: {data.get('ReturnMsg')}")
        items = data["Data"]
    except (ValueError, KeyError, TypeError) as exc:
        raise SourceError(
            f"{source}: listing is not the expected JSON ({exc})",
            source=source,
            day=day,
            url=MIRAE_LIST,
        ) from exc
    refs: list[FileRef] = []
    for item in items:
        title = _tidy(item.get("Title", ""))
        hit = mirae_scheme(title)
        url_path = str(item.get("URL", ""))
        if hit is None or url_path.split("?")[0].lower().endswith(_EXCEL) is False:
            continue
        symbol, role = hit
        published = _mirae_date(item.get("PublishDate"))
        base = urlsplit(url_path).path.rsplit("/", 1)[-1]
        stamp = published.isoformat() if published else "undated"
        refs.append(
            FileRef(
                "mirae",
                urljoin(MIRAE_BASE, url_path),
                _safe_name(f"{stamp}_{symbol}_{role}_{base}"),
                title,
                published,
            )
        )
    return refs


def _mirae_date(text: Any) -> date | None:
    match = re.search(r"/Date\((-?\d+)\)/", str(text))
    if not match:
        return None
    from datetime import UTC

    return datetime.fromtimestamp(int(match.group(1)) / 1000, tz=UTC).date()


_NIPPON_HREF = re.compile(r'href="([^"]+\.xlsx?)"', re.I)
_NIPPON_SKIP = re.compile(
    r"debt|fort|fornight|reliance|nipponindia-monthly|risk-?parameter|riskparameter|"
    r"jan-13|in_mf_rlmf|nimf-jan-|portfolio-final|(?:jul|aug|sep|oct|nov)[a-z]*[-_](?:20|2020)\b",
    re.I,
)


def nippon_refs(client: PoliteClient, store: RawStore, day: date) -> list[FileRef]:
    """List Nippon India's monthly portfolio workbooks (links on one static page).

    The page lists debt-scheme-only files, fortnightly files, risk files and pre-2021 files as
    well; names that say so are skipped. Month and year are read from the workbook itself
    later, not from the file name (the names are inconsistent). Page saved raw as
    ``portfolio_page.html``.
    """
    source = SOURCES["nippon"]
    result = client.get(NIPPON_PAGE, source=source, day=day, headers={"Accept": "text/html"})
    store.save_result(source=source, day=day, name="portfolio_page.html", result=result)
    page = result.content.decode("utf-8", errors="replace")
    seen: set[str] = set()
    refs: list[FileRef] = []
    for href in _NIPPON_HREF.findall(page):
        href = html.unescape(href)
        base = urlsplit(href).path.rsplit("/", 1)[-1]
        if "portfolio" not in base.lower() or _NIPPON_SKIP.search(base) or base in seen:
            continue
        seen.add(base)
        refs.append(FileRef("nippon", urljoin(NIPPON_BASE, href), _safe_name(base), base, None))
    return refs


# -- downloading --------------------------------------------------------------------------


@dataclass(frozen=True)
class FetchOutcome:
    ref: FileRef
    status: str  # "fetched", "exists" (already in the raw store), or "failed"
    detail: str = ""
    record: RawRecord | None = None


def already_fetched(store: RawStore, source: str) -> set[str]:
    """URLs of this source that an earlier run saved with HTTP 200 (from the manifest)."""
    return {
        str(e["url"])
        for e in store.iter_manifest()
        if e.get("source") == source
        and e.get("status_code") == 200
        and not e["name"].startswith("listing_")
    }


def _check_excel(result: FetchResult, ref: FileRef) -> None:
    head = result.content[:4]
    if not (head[:2] == b"PK" or head == b"\xd0\xcf\x11\xe0"):
        raise SourceError(
            f"{SOURCES[ref.amc]}: {ref.url} is not an Excel file (starts {head!r})",
            source=SOURCES[ref.amc],
            day=None,
            url=ref.url,
            status_code=result.status_code,
        )


def download(
    client: PoliteClient,
    store: RawStore,
    day: date,
    refs: Iterable[FileRef],
    *,
    skip_urls: set[str] | None = None,
    on_outcome: Callable[[FetchOutcome], None] | None = None,
) -> list[FetchOutcome]:
    """Download every ref not stored before. One failure does not stop the others.

    A block (HTTP 401/403/429) is the exception: it stops the whole run at once, because
    retrying a block is how an IP address gets banned (``SourceBlockedError`` propagates).
    """
    from etfp.http import SourceBlockedError

    known = skip_urls or set()
    outcomes: list[FetchOutcome] = []
    for ref in refs:
        source = SOURCES[ref.amc]
        if ref.url in known:
            outcome = FetchOutcome(ref, "exists")
        else:
            try:
                result = client.get(ref.url, source=source, day=day)
                _check_excel(result, ref)
                record = store.save_result(source=source, day=day, name=ref.name, result=result)
                outcome = FetchOutcome(ref, "fetched", f"{record.size_bytes} bytes", record)
            except SourceBlockedError:
                raise
            except SourceError as exc:
                outcome = FetchOutcome(ref, "failed", f"{type(exc).__name__}: {exc}")
        outcomes.append(outcome)
        if on_outcome is not None:
            on_outcome(outcome)
    return outcomes


def list_refs(
    amc: str, client: PoliteClient, store: RawStore, day: date, first: date, last: date
) -> list[FileRef]:
    """Dispatch to the AMC's lister. ``first``/``last`` only matter for Motilal (JSON per month)."""
    if amc == "motilal":
        return motilal_refs(client, store, day, first, last)
    if amc == "mirae":
        return mirae_refs(client, store, day)
    if amc == "nippon":
        return nippon_refs(client, store, day)
    raise ValueError(f"unknown AMC {amc!r}; expected one of {AMCS}")


def fetch(
    client: PoliteClient,
    store: RawStore,
    day: date,
    amc: str,
    *,
    first: date,
    last: date,
    on_outcome: Callable[[FetchOutcome], None] | None = None,
) -> list[FetchOutcome]:
    """List and download one AMC's portfolio files into ``data/raw/amc_portfolio_<amc>/<day>/``."""
    refs = list_refs(amc, client, store, day, first, last)
    logger.info("%s: %d candidate file(s) listed", SOURCES[amc], len(refs))
    return download(
        client,
        store,
        day,
        refs,
        skip_urls=already_fetched(store, SOURCES[amc]),
        on_outcome=on_outcome,
    )


def motilal_predicted_ref(period_year: int, period_month: int) -> FileRef:
    """The file name Motilal used for a month's "Scheme Portfolio Detail" workbook (D54).

    The May and June 2026 workbooks are on Motilal's server but missing from its search
    listing (D46 gap). They sit in the publication month's folder, named
    ``Scheme Portfolio Detail <Month> <Year>.xlsx`` (checked 18 Nov 2026: HTTP 200 for May and
    June 2026; the plural ``Details`` 404s). A guess can 404 for other months; the caller
    treats that as "not found", not as an error in the data.
    """
    import calendar

    pub_year, pub_month = (
        (period_year, period_month + 1)
        if period_month < 12
        else (
            period_year + 1,
            1,
        )
    )
    mon = calendar.month_abbr[pub_month].lower()
    stem = f"Scheme Portfolio Detail {calendar.month_name[period_month]} {period_year}.xlsx"
    return FileRef(
        "motilal",
        f"{MOTILAL_BASE}/content/dam/motilal-mf/downloads/mf/month-end-portfolio/"
        f"{pub_year}/{mon}/{quote(stem)}",
        _safe_name(f"{pub_year}-{mon}__{stem}"),
        stem,
        None,
    )
