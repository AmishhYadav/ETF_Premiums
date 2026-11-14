"""Notice reader, part 1 (no LLM): log official notices that can move ETF premiums.

Why we need it (SPEC section 3, v2 "notice reader"): the premiums of the international ETFs
jump or fall when a regulator or exchange changes a rule. Examples: SEBI caps how much
Indian mutual funds may invest overseas (the cap is why the ETFs cannot create new units),
the exchanges set the ETF price band, and fund houses suspend or resume subscriptions.
The research needs a dated list of every such notice. This module downloads the official
listings untouched; ``etfp.clean.notices`` turns them into the ``notices`` table and tags
likely-relevant rows with keyword rules. A keyword match is only a CANDIDATE: a human (or a
later LLM step) decides what becomes a verified row in ``events``.

Sources (live checks on 2026-11-14, see docs/DECISIONS.md):

* ``sebi_rss``  https://www.sebi.gov.in/sebirss.xml. Everything SEBI posts (circulars,
  press releases, orders), last ~30 items (about 3 days). Item dates have no time of day.
  Plain GET, no cookies.
* ``sebi_listing``  SEBI's "Legal" listing (circulars, master circulars, regulations,
  gazette notifications) as served to the listing page by
  ``POST /sebiweb/ajax/home/getnewslistinfo.jsp`` with a date range. 25 rows per page;
  ``totalpage`` says how many pages. Gives history (2,821 circulars listed). The listing
  shows SEBI's "Active" view; items moved to "Archive" are NOT in it (see ``sebi_archive``).
* ``sebi_archive``  the same page's "Archive" tab: ``getArchiveCircularlistinfo.jsp``, the
  same form with ``ssid=7`` (circulars only). Holds circulars no longer in the Active view
  (none of them appear there; 200 at the 2026-11-14 check, Jan 2020 - Apr 2025).
* ``nse_circulars``  NSE's circulars API ``/api/circulars?fromDate=&toDate=`` (JSON), the
  same call the nseindia.com circulars page makes. Like the other NSE API, it answers only
  after the web page has handed out cookies, so we load the page first ("warm"). About
  700 circulars a month in one answer; includes NSE Clearing (NCL) and the "Mutual Fund"
  department's subscription suspension/resumption notices for funds on NSE's MF platform.

Not used: NSE's circulars RSS (works, but only the last day and fewer fields than the
API); AMFI (its new website renders notices in the browser; no plain listing or notice
feed was found, only NAV feeds); AMC websites (each different, mostly PDFs).

Design: ``fetch_*()`` only download and store untouched bytes; ``parse_*()`` only turn
bytes into tables and never touch the network.
"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import re
import xml.etree.ElementTree as ET
from datetime import date
from urllib.parse import urlencode

import pandas as pd

from etfp.http import PoliteClient, SourceError
from etfp.rawstore import RawRecord, RawStore

logger = logging.getLogger(__name__)

SOURCE_SEBI_RSS = "sebi_rss"
SOURCE_SEBI_LISTING = "sebi_listing"
SOURCE_SEBI_ARCHIVE = "sebi_archive"
SOURCE_NSE_CIRCULARS = "nse_circulars"
SOURCES = (SOURCE_SEBI_RSS, SOURCE_SEBI_LISTING, SOURCE_SEBI_ARCHIVE, SOURCE_NSE_CIRCULARS)

SEBI_HOST = "sebi.gov.in"
SEBI_RSS_URL = "https://www.sebi.gov.in/sebirss.xml"
SEBI_RSS_NAME = "sebirss.xml"
SEBI_LISTING_URL = "https://www.sebi.gov.in/sebiweb/ajax/home/getnewslistinfo.jsp"
SEBI_LISTING_REFERER = (
    "https://www.sebi.gov.in/sebiweb/home/HomeAction.do?doListing=yes&sid=1&ssid=7&smid=0"
)
# The "Archive" tab of the circulars page (checked 2026-11-14, DECISIONS D38): circulars SEBI
# has moved out of the Active view (200 of them, Jan 2020 - Apr 2025 at that date) are NOT
# in the Active listing above. Same form, other endpoint, circulars only (ssid=7).
SEBI_ARCHIVE_URL = "https://www.sebi.gov.in/sebiweb/ajax/home/getArchiveCircularlistinfo.jsp"
SEBI_ARCHIVE_REFERER = (
    "https://www.sebi.gov.in/sebiweb/home/HomeAction.do?doListingCirArchive=yes&sid=1&ssid=7&smid=0"
)
NSE_PAGE_URL = "https://www.nseindia.com/resources/exchange-communication-circulars"
NSE_API_URL = "https://www.nseindia.com/api/circulars"
NSE_PAGE_HEADERS = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
NSE_API_HEADERS = {"Accept": "*/*", "Referer": NSE_PAGE_URL}

# Every parser returns these columns (one row per listing row, duplicates kept; the
# cleaner de-duplicates by notice_id).
COLUMNS: tuple[str, ...] = (
    "notice_id",
    "issuer",
    "published_date",
    "title",
    "url",
    "section",
    "ref_no",
    "source_file",
)

_MONTHS = {
    m: i
    for i, m in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1
    )
}
_SEBI_DATE = re.compile(r"(\d{1,2}) ([A-Za-z]{3}),? (\d{4})")  # "13 Nov, 2026 +0530"
_LISTING_DATE = re.compile(r"([A-Za-z]{3}) (\d{1,2}), (\d{4})\Z")  # "Nov 11, 2026"
_SEBI_ID = re.compile(r"_(\d+)\.html\Z")
_LISTING_ROW = re.compile(
    r"<td>\s*([A-Z][a-z]{2} \d{1,2}, \d{4})\s*</td>\s*<td>\s*<a\s+href=(['\"])(.*?)\2"
    r"[^>]*?title=\"(.*?)\"",
    re.S,
)
_LISTING_TOTAL = re.compile(r"(\d+) to (\d+) of (\d+) records")
_LISTING_PAGES = re.compile(r"name='totalpage'\s+value=(\d+)")
_NSE_DATE = re.compile(r"(\d{4})(\d{2})(\d{2})\Z")


# -- names -----------------------------------------------------------------------


def _stamp(day: date) -> str:
    return day.strftime("%Y%m%d")


def sebi_listing_name(start: date, end: date, page: int = 1) -> str:
    return f"sebi_legal_{_stamp(start)}_{_stamp(end)}_p{page}.html"


def sebi_archive_name(start: date, end: date, page: int = 1) -> str:
    return f"sebi_archive_{_stamp(start)}_{_stamp(end)}_p{page}.html"


def nse_circulars_name(start: date, end: date) -> str:
    return f"nse_circulars_{_stamp(start)}_{_stamp(end)}.json"


def _check_window(start: date, end: date) -> None:
    if start > end:
        raise ValueError(f"window start {start} is after its end {end}")


def _looks_like_html(content: bytes) -> bool:
    head = content[:512].lstrip().lower()
    return head.startswith((b"<!doctype html", b"<html", b"<head"))


def _bad(source: str, day: date, url: str, why: str, status: int) -> SourceError:
    return SourceError(
        f"{source} for {day.isoformat()}: {why}; not stored: {url}",
        source=source,
        day=day,
        url=url,
        status_code=status,
    )


# -- download --------------------------------------------------------------------


def fetch_sebi_rss(client: PoliteClient, store: RawStore, day: date) -> RawRecord:
    """Download SEBI's RSS feed (latest ~30 items) and store it under ``day``."""
    result = client.get(SEBI_RSS_URL, source=SOURCE_SEBI_RSS, day=day)
    if b"<rss" not in result.content[:1024]:
        raise _bad(SOURCE_SEBI_RSS, day, SEBI_RSS_URL, "answer is not an RSS feed", 200)
    return store.save_result(source=SOURCE_SEBI_RSS, day=day, name=SEBI_RSS_NAME, result=result)


def _sebi_form(start: date, end: date, page_index: int, *, archive: bool = False) -> dict[str, str]:
    # The listing page sends these fields. sid=1 is the "Legal" section; ssid=-1 means
    # all of its sub-sections. next=s is the first page, next=n with nextValue=k is page k+1.
    return {
        "nextValue": str(page_index),
        "next": "s" if page_index == 0 else "n",
        "search": "",
        "fromDate": start.strftime("%d-%m-%Y"),
        "toDate": end.strftime("%d-%m-%Y"),
        "fromYear": "",
        "toYear": "",
        "deptId": "-1",
        "sid": "1",
        "ssid": "7" if archive else "-1",
        "smid": "0",
        "ssidhidden": "7",
        "intmid": "-1",
        "sText": "Legal",
        "ssText": "Circulars" if archive else "-- All Sub Section --",
        "smText": "",
        "doDirect": "-1",
    }


def fetch_sebi_listing(
    client: PoliteClient, store: RawStore, start: date, end: date, *, day: date | None = None
) -> RawRecord:
    """Download every page of SEBI's Legal listing for [start, end]; store under ``day``.

    ``day`` (the raw folder) defaults to ``end``. All pages are downloaded before any is
    stored, and page 1 is stored last: page 1 is the file the job runner checks, so a
    failure half-way leaves page 1 missing and the next run tries the whole window again.
    """
    return _fetch_sebi_pages(client, store, start, end, day, archive=False)


def fetch_sebi_archive(
    client: PoliteClient, store: RawStore, start: date, end: date, *, day: date | None = None
) -> RawRecord:
    """Same as ``fetch_sebi_listing`` but for the circulars "Archive" view (see top of file).

    The whole archive is only about 8 pages, so use one wide window, not monthly ones.
    """
    return _fetch_sebi_pages(client, store, start, end, day, archive=True)


def _fetch_sebi_pages(
    client: PoliteClient,
    store: RawStore,
    start: date,
    end: date,
    day: date | None,
    *,
    archive: bool,
) -> RawRecord:
    _check_window(start, end)
    day = day or end
    source = SOURCE_SEBI_ARCHIVE if archive else SOURCE_SEBI_LISTING
    url = SEBI_ARCHIVE_URL if archive else SEBI_LISTING_URL
    referer = SEBI_ARCHIVE_REFERER if archive else SEBI_LISTING_REFERER
    name = sebi_archive_name if archive else sebi_listing_name
    pages = []
    total_pages = 1
    index = 0
    while index < total_pages:
        result = client.post(
            url,
            source=source,
            day=day,
            content=urlencode(_sebi_form(start, end, index, archive=archive)).encode("ascii"),
            headers={
                "Referer": referer,
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            },
        )
        meta = listing_meta(result.content)
        if meta is None:
            raise _bad(
                source,
                day,
                url,
                f"page {index + 1} has no 'N to M of T records' line (format changed?)",
                result.status_code,
            )
        if index == 0:
            total_pages = meta[3]
            if total_pages > 200:
                raise _bad(
                    source, day, url,
                    f"{total_pages} pages for one window; use a shorter window", 200,
                )  # fmt: skip
        pages.append(result)
        index += 1
    for page in range(len(pages), 1, -1):
        store.save_result(
            source=source, day=day, name=name(start, end, page), result=pages[page - 1]
        )
    return store.save_result(source=source, day=day, name=name(start, end), result=pages[0])


def fetch_nse_circulars(
    client: PoliteClient,
    store: RawStore,
    start: date,
    end: date,
    *,
    day: date | None = None,
    warm: bool = True,
) -> RawRecord:
    """Download NSE's circular list for [start, end] (JSON); store under ``day`` (default end).

    With ``warm`` the circulars web page is loaded first so NSE hands out cookies (the
    client keeps them and spaces the calls at least 1.5 s apart).
    """
    _check_window(start, end)
    day = day or end
    if warm:
        client.get(NSE_PAGE_URL, source=SOURCE_NSE_CIRCULARS, day=day, headers=NSE_PAGE_HEADERS)
    url = f"{NSE_API_URL}?fromDate={start.strftime('%d-%m-%Y')}&toDate={end.strftime('%d-%m-%Y')}"
    result = client.get(url, source=SOURCE_NSE_CIRCULARS, day=day, headers=NSE_API_HEADERS)
    problem = _nse_body_problem(result.content)
    if problem:
        raise _bad(SOURCE_NSE_CIRCULARS, day, url, problem, result.status_code)
    return store.save_result(
        source=SOURCE_NSE_CIRCULARS, day=day, name=nse_circulars_name(start, end), result=result
    )


def _nse_body_problem(content: bytes) -> str | None:
    if _looks_like_html(content):
        return "got a web page instead of JSON (blocked or cookies missing?)"
    try:
        body = json.loads(content)
    except ValueError:
        return "answer is not JSON"
    if not isinstance(body, dict) or not isinstance(body.get("data"), list):
        return "JSON has no 'data' list (format changed?)"
    return None


# -- parsing ---------------------------------------------------------------------


def _frame(rows: list[dict]) -> pd.DataFrame:
    data = {}
    for col in COLUMNS:
        values = [r[col] for r in rows]
        if col == "published_date":
            data[col] = pd.Series(values, dtype=object)
        else:
            data[col] = pd.Series(pd.array(values, dtype="string"))
    return pd.DataFrame(data, columns=list(COLUMNS))


def _real_date(y: int, m: int | None, d: int, where: str, text: str) -> date:
    if m is None:
        raise ValueError(f"{where}: {text!r} has an unknown month")
    try:
        return date(y, m, d)
    except ValueError as exc:
        raise ValueError(f"{where}: {text!r} is not a real calendar date") from exc


def sebi_notice_id(url: str) -> str:
    """SEBI pages end in ``_<number>.html``; that number is SEBI's own id for the item.

    The RSS feed and the listing use the same URLs, so the same notice gets the same id
    from both.
    """
    match = _SEBI_ID.search(url.strip())
    if match is None:
        raise ValueError(f"SEBI URL without a numeric id: {url!r}")
    return f"sebi:{match.group(1)}"


def sebi_section(url: str) -> str | None:
    """'https://www.sebi.gov.in/legal/circulars/nov-2026/x_1.html' -> 'legal/circulars'."""
    parts = url.split("sebi.gov.in/", 1)
    if len(parts) != 2:
        return None
    bits = parts[1].split("/")
    return "/".join(bits[:2]) if len(bits) >= 3 else None


def parse_sebi_rss(content: bytes, *, source_file: str) -> pd.DataFrame:
    """SEBI RSS items as notices. Item dates carry no time of day."""
    where = SOURCE_SEBI_RSS
    if _looks_like_html(content):
        raise ValueError(f"{where}: file is a web page, not RSS")
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise ValueError(f"{where}: unreadable XML ({exc})") from exc
    channel = root.find("channel")
    if root.tag != "rss" or channel is None:
        raise ValueError(f"{where}: not an RSS 2.0 feed (root <{root.tag}>)")
    rows = []
    for n, item in enumerate(channel.findall("item"), 1):
        title = (item.findtext("title") or "").strip()
        url = (item.findtext("link") or "").strip()
        when = (item.findtext("pubDate") or "").strip()
        if not title or not url:
            raise ValueError(f"{where}: item {n} has no title or link")
        m = _SEBI_DATE.search(when)
        if m is None:
            raise ValueError(f"{where}: item {n}: date {when!r} not like '13 Nov, 2026'")
        published = _real_date(
            int(m.group(3)), _MONTHS.get(m.group(2).lower()), int(m.group(1)), where, when
        )
        rows.append(
            {
                "notice_id": sebi_notice_id(url),
                "issuer": "SEBI",
                "published_date": published,
                "title": " ".join(html.unescape(title).split()),
                "url": url,
                "section": sebi_section(url),
                "ref_no": None,
                "source_file": source_file,
            }
        )
    return _frame(rows)


def listing_meta(content: bytes) -> tuple[int, int, int, int] | None:
    """(first row, last row, total rows, total pages) of a SEBI listing page, or None.

    An empty window says "0 to 0 of 0 records" or similar; no line at all means the
    answer is not a listing page.
    """
    text = content.decode("utf-8", errors="replace")
    m = _LISTING_TOTAL.search(text)
    if m is None:
        if re.search(r"No [Rr]ecords? [Ff]ound|of 0 records", text):
            return (0, 0, 0, 1)
        return None
    pages = _LISTING_PAGES.search(text)
    first, last, total = (int(g) for g in m.groups())
    return (first, last, total, int(pages.group(1)) if pages else 1)


def parse_sebi_listing(content: bytes, *, source_file: str) -> pd.DataFrame:
    """Rows of one SEBI listing page as notices. Fails if the page says it has rows but
    none can be read (the layout has changed)."""
    where = SOURCE_SEBI_LISTING
    meta = listing_meta(content)
    if meta is None:
        raise ValueError(f"{where}: no 'N to M of T records' line; not a listing page?")
    text = content.decode("utf-8", errors="replace")
    rows = []
    for when, _q, url, title in _LISTING_ROW.findall(text):
        m = _LISTING_DATE.match(when.strip())
        if m is None:
            raise ValueError(f"{where}: date {when!r} not like 'Nov 11, 2026'")
        published = _real_date(
            int(m.group(3)), _MONTHS.get(m.group(1).lower()), int(m.group(2)), where, when
        )
        url = html.unescape(url).strip()
        rows.append(
            {
                "notice_id": sebi_notice_id(url),
                "issuer": "SEBI",
                "published_date": published,
                "title": " ".join(html.unescape(title).split()),
                "url": url,
                "section": sebi_section(url),
                "ref_no": None,
                "source_file": source_file,
            }
        )
    first, last, _total, _pages = meta
    expected = last - first + 1 if last >= first > 0 else 0
    if len(rows) != expected:
        raise ValueError(
            f"{where}: page says rows {first} to {last} but {len(rows)} could be read; "
            "has SEBI changed the page layout?"
        )
    return _frame(rows)


def parse_nse_circulars(content: bytes, *, source_file: str) -> pd.DataFrame:
    """NSE circular list as notices: one row per listing row.

    NSE lists a circular once per department it concerns, so the same circular can
    appear more than once with a different ``section``; the cleaner merges them.
    """
    where = SOURCE_NSE_CIRCULARS
    problem = _nse_body_problem(content)
    if problem:
        raise ValueError(f"{where}: {problem}")
    rows = []
    set_aside = 0
    for n, rec in enumerate(json.loads(content)["data"], 1):
        if not isinstance(rec, dict):
            raise ValueError(f"{where}: entry {n} is not an object")
        # Some old entries (12 of ~190,000 in 2020-2025) have an empty display number but
        # keep NSE's plain circular number: use that. With neither number, a subject keeps
        # the row under a stable id nse:<date>:<hash of subject+department>; with no
        # subject either, nothing identifies the notice, so it is set aside (counted and
        # warned about after the loop, never silent).
        ref = str(rec.get("circDisplayNo") or rec.get("circNumber") or "").strip()
        title = " ".join(str(rec.get("sub") or "").split())
        stamp = str(rec.get("cirDate") or "").strip()
        dept = str(rec.get("circDepartment") or "").strip() or None
        if not title:
            set_aside += 1
            continue
        notice_ref = ref
        if not ref:
            digest = hashlib.sha1(f"{title}|{dept or ''}".encode()).hexdigest()[:10]
            notice_ref = f"{stamp}:{digest}"
        m = _NSE_DATE.match(stamp)
        if m is None:
            raise ValueError(f"{where}: entry {n} ({ref}): date {stamp!r} not like 20260901")
        published = _real_date(int(m.group(1)), int(m.group(2)), int(m.group(3)), where, stamp)
        company = str(rec.get("circCompany") or "NSE").strip() or "NSE"
        rows.append(
            {
                "notice_id": f"nse:{notice_ref}",
                "issuer": company,
                "published_date": published,
                "title": title,
                "url": (str(rec.get("circFilelink") or "").strip() or None),
                "section": (str(rec.get("circDepartment") or "").strip() or None),
                "ref_no": ref or None,
                "source_file": source_file,
            }
        )
    if set_aside:
        logger.warning(
            "%s: %s: %d entries with no subject (and so nothing to identify them) set aside",
            where,
            source_file,
            set_aside,
        )
    return _frame(rows)


PARSERS = {
    SOURCE_SEBI_RSS: parse_sebi_rss,
    SOURCE_SEBI_LISTING: parse_sebi_listing,
    SOURCE_SEBI_ARCHIVE: parse_sebi_listing,
    SOURCE_NSE_CIRCULARS: parse_nse_circulars,
}
