"""IBJA daily gold and silver rates: the domestic (Indian) price of the metal itself.

Why this exists (plain words)
-----------------------------
Gold and silver ETFs (GOLDBEES, SILVERBEES) hold physical metal in a vault in India.
Their NAV is "grams of metal per unit x Indian price of a gram" minus small costs. To
check that NAV, and to see whether an ETF trades above the metal it holds, we need the
Indian metal price on each day. That price is NOT the London price: it includes import
duty, and when metal is scarce in India (October 2025 silver) it can sit well above
London x exchange rate. So we need a domestic source.

IBJA (India Bullion and Jewellers Association) polls bullion dealers, refiners,
importers and jewellers twice a day and publishes the average (highest and lowest quote
dropped). RBI uses these rates for Sovereign Gold Bond prices and bank gold loans.
Per IBJA's methodology note (``ibjarates.com/pdf/spot-polling-mechanism/...``):

* polls run 11:40-12:00 IST (the "AM" rate) and 16:40-17:00 IST (the "PM" rate),
* prices INCLUDE import duty and customs, EXCLUDE GST (3%) and making charges,
* gold is quoted in rupees per 10 grams, silver in rupees per 1 kilogram,
* no rates on central government holidays, Saturdays and Sundays.

What the page gives (checked live 2026-11-14, docs/DECISIONS.md):

* two tables ("AM" and "PM" tabs) with the last ~4 publication days for gold 999 / 995 /
  916 / 750 / 585, silver 999 and platinum 999,
* two hidden form fields (``HdnGold``, ``HdnSilver``) holding the chart data: about 84
  publication days (~4 months) of PM rates for gold 999 and 916 and silver 999.

So one download carries ~4 months of PM history, and a daily download keeps the record
going. Older history is NOT available for free on this site.

Platinum is skipped: the page does not state its quoting unit (VERIFY if ever needed).

Design: ``fetch()`` stores the untouched HTML; ``parse()`` reads bytes and never touches
the network, so it is tested on a saved fixture.
"""

from __future__ import annotations

import html
import json
import re
from datetime import date

import pandas as pd

from etfp.http import PoliteClient
from etfp.rawstore import RawRecord, RawStore

SOURCE = "ibja_rates"
URL = "https://ibjarates.com/"
RAW_NAME = "ibjarates_home.html"
HEADERS = {"Accept": "text/html,application/xhtml+xml"}

# Quoting unit of each metal and how many grams one quote covers.
GRAMS_PER_QUOTE = {"gold": 10.0, "silver": 1000.0}
QUOTE_UNIT = {"gold": "INR per 10 g", "silver": "INR per 1 kg"}

OUTPUT_COLUMNS: tuple[str, ...] = (
    "date",
    "metal",
    "purity",
    "session",
    "quote_inr",
    "quote_unit",
    "price_inr_per_gram",
    "origin",
    "source_file",
)

_TAB = re.compile(r'<div id="tab-(am|pm)"[^>]*>(.*?)</table>', re.S | re.I)
_ROW = re.compile(r"<tr>(.*?)</tr>", re.S | re.I)
_CELL = re.compile(r'<td[^>]*data-label="([^"]+)"[^>]*>(.*?)</td>', re.S | re.I)
_TAG = re.compile(r"<[^>]+>")
_LABEL = re.compile(r"(Gold|Silver|Platinum) (\d{3})\Z", re.I)
_HIDDEN = r'<input[^>]*id="{name}"[^>]*value="([^"]*)"'
_DMY = re.compile(r"(\d{2})/(\d{2})/(\d{4})\Z")
_INT = re.compile(r"\d+\Z")
# Values that mean "no rate published" in a table cell.
_MISSING = {"", "-", "NA", "N/A"}
# Hidden chart field -> (metal, {json key: purity}).
_CHART = {
    "HdnGold": ("gold", {"purity999": "999", "purity916": "916"}),
    "HdnSilver": ("silver", {"silverRate": "999"}),
}


def fetch(client: PoliteClient, store: RawStore, day: date) -> RawRecord:
    """Download the IBJA home page and store the untouched bytes under ``day`` (IST date)."""
    result = client.get(URL, source=SOURCE, day=day, headers=HEADERS)
    return store.save_result(source=SOURCE, day=day, name=RAW_NAME, result=result)


# -- parsing ------------------------------------------------------------------


def _fail(message: str) -> ValueError:
    return ValueError(f"{SOURCE}: {message}")


def _day(text: str) -> date:
    match = _DMY.match(text.strip())
    if match is None:
        raise _fail(f"{text!r} is not a date like 13/11/2026")
    dd, mm, yyyy = (int(g) for g in match.groups())
    try:
        return date(yyyy, mm, dd)
    except ValueError as exc:
        raise _fail(f"{text!r} is not a real calendar date") from exc


def _quote(text: str, where: str) -> int | None:
    """A price cell: whole rupees, or None when IBJA shows '-' / 'NA'."""
    value = str(text).strip().replace(",", "")
    if value.upper() in _MISSING:
        return None
    if not _INT.match(value) or int(value) <= 0:
        raise _fail(f"{where}: {text!r} is not a positive whole-rupee price")
    return int(value)


def _row(day: date, metal: str, purity: str, session: str, quote: int, origin: str) -> dict:
    return {
        "date": day,
        "metal": metal,
        "purity": purity,
        "session": session,
        "quote_inr": float(quote),
        "quote_unit": QUOTE_UNIT[metal],
        # Unit conversion: gold is quoted per 10 g, silver per 1,000 g.
        "price_inr_per_gram": quote / GRAMS_PER_QUOTE[metal],
        "origin": origin,
    }


def _table_rows(text: str) -> list[dict]:
    rows: list[dict] = []
    for tab, body in _TAB.findall(text):
        session = tab.upper()
        for tr in _ROW.findall(body):
            cells = [
                (label.strip(), _TAG.sub("", value).strip()) for label, value in _CELL.findall(tr)
            ]
            if not cells:
                continue  # header row
            first_label, first_value = cells[0]
            if first_label.upper() != session:
                raise _fail(
                    f"{session} table: first cell label {first_label!r}, expected {session}"
                )
            day = _day(html.unescape(first_value))
            for label, value in cells[1:]:
                match = _LABEL.match(label)
                if match is None:
                    raise _fail(f"{session} table {day}: unknown column {label!r}")
                metal, purity = match.group(1).lower(), match.group(2)
                if metal not in GRAMS_PER_QUOTE:
                    continue  # platinum: quoting unit not stated on the page
                quote = _quote(value, f"{session} table {day} {label}")
                if quote is not None:
                    rows.append(_row(day, metal, purity, session, quote, "table"))
    return rows


def _chart_rows(text: str) -> list[dict]:
    rows: list[dict] = []
    for field, (metal, series) in _CHART.items():
        match = re.search(_HIDDEN.format(name=field), text, re.I)
        if match is None:
            continue
        try:
            data = json.loads(html.unescape(match.group(1)))
        except json.JSONDecodeError as exc:
            raise _fail(f"hidden field {field} is not JSON ({exc.msg})") from exc
        labels = data.get("labels")
        if not isinstance(labels, list):
            raise _fail(f"hidden field {field} has no 'labels' list")
        for key, purity in series.items():
            values = data.get(key)
            if not isinstance(values, list) or len(values) != len(labels):
                raise _fail(f"hidden field {field}: '{key}' missing or not as long as 'labels'")
            for label, value in zip(labels, values, strict=True):
                quote = _quote(str(value), f"{field} {label} {key}")
                if quote is not None:
                    # The chart legend says these are PM rates ("999 Purity Gold Rates (PM)").
                    rows.append(_row(_day(label), metal, purity, "PM", quote, "chart"))
    return rows


def parse(content: bytes, *, source_file: str) -> pd.DataFrame:
    """Turn the saved IBJA page into one row per (date, metal, purity, session).

    Table rows win over chart rows for the same key, but only if both give the same
    price; a disagreement inside one page is raised, never silently resolved.
    """
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _fail(f"page is not UTF-8 ({exc.reason} at byte {exc.start})") from exc
    table, chart = _table_rows(text), _chart_rows(text)
    if not table and not chart:
        raise _fail("no rate table and no chart data found: the page layout has changed")
    frame = pd.DataFrame(table + chart, columns=[c for c in OUTPUT_COLUMNS if c != "source_file"])
    key = ["date", "metal", "purity", "session"]
    spread = frame.groupby(key)["quote_inr"].agg(["min", "max"])
    bad = spread[spread["min"] != spread["max"]]
    if not bad.empty:
        first = bad.index[0]
        raise _fail(
            f"table and chart disagree for {len(bad)} rate(s), first {first}: "
            f"{bad.iloc[0]['min']:.0f} vs {bad.iloc[0]['max']:.0f}"
        )
    frame["_rank"] = (frame["origin"] != "table").astype(int)  # table first
    frame = frame.sort_values([*key, "_rank"]).drop_duplicates(key, keep="first")
    frame = frame.drop(columns="_rank").reset_index(drop=True)
    frame["source_file"] = source_file
    return frame[list(OUTPUT_COLUMNS)]
