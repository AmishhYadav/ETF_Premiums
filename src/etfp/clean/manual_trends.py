"""trends_daily: Google search interest per day, chained from overlapping windows (R15).

What Google Trends gives (plain words)
--------------------------------------
Trends reports search interest as a number from 0 to 100, where 100 is the
busiest day *inside the window you asked for*. Daily numbers are only available
for windows of up to about 90 days, and every window is rescaled on its own: the
same day can be 40 in one window and 25 in another. So a long daily series cannot
be downloaded in one go, and windows cannot just be pasted together.

The fix used here: ask for windows that overlap (90 days, shifted by 60 days, so
30 days are shared). On the shared days both windows describe the same real
interest, only on different scales, so

    factor = (sum of the chain's values on the shared days)
             / (sum of the new window's values on the shared days)

and the new window is multiplied by that factor before it extends the chain. (A
ratio of sums, not an average of daily ratios, so a day with a tiny value does
not make the factor jump.) Finally the whole chain is rescaled so its maximum is
100, like Trends itself. The result compares days within one term; it does NOT
compare different terms (Trends scales every term separately when pulled alone).

Where the files come from
-------------------------
By hand (v1): trends.google.com, India, "past 90 days" or a custom range, search
term, download the CSV, save it UNCHANGED as
``data/raw/trends/<pull date>/<anything>.csv``. A file looks like::

    Category: All categories

    Day,MONQ50: (India)
    2026-08-01,12
    2026-08-02,<1

``<1`` means "above 0 but under 1"; it is stored as 0.5. A file may hold several
terms side by side (``Day,A: (India),B: (India)``); each term is chained on its own.

A window that shares no day with the series built so far cannot be scaled, so the
build stops with an error naming the file (pull the missing window) instead of
guessing.
"""

from __future__ import annotations

import csv
import io
import logging
import re
from dataclasses import dataclass
from datetime import date

import pandas as pd

from etfp.clean.common import CleanContext, CleanError, RawFile, concat_frames, list_raw_files
from etfp.store import SCHEMAS, Column, TableSchema, register_schema

logger = logging.getLogger("etfp.clean")

TABLE = "trends_daily"
SOURCE = "trends"
COLUMNS = ["term", "geo", "date", "value", "window_value", "n_windows", "source_file"]
LESS_THAN_ONE = 0.5  # what the "<1" cells are stored as

_HEADER_CELL = re.compile(r"^(?P<term>.+?):\s*\((?P<geo>[^)]*)\)\s*$")

if TABLE not in SCHEMAS:
    register_schema(
        TableSchema(
            name=TABLE,
            primary_key=("term", "geo", "date"),
            doc="Google Trends daily search interest, chained from overlapping windows (R15).",
            columns=(
                Column("term", "string", "Search term as typed into Trends"),
                Column("geo", "string", "Region in the Trends header, e.g. India"),
                Column("date", "date", "Day (IST calendar date as Trends reports it)"),
                Column("value", "float", "Chained interest, rescaled so the series maximum is 100"),
                Column("window_value", "float", "Number printed in the window file (0-100)"),
                Column("n_windows", "int", "How many downloaded windows contain this day"),
                Column(
                    "source_file", "string", "Raw path of the window file that supplied the day"
                ),
            ),
        )
    )


@dataclass(frozen=True)
class Window:
    """One downloaded window for one term: a number per day."""

    term: str
    geo: str
    values: dict[date, float]
    file: RawFile

    @property
    def span(self) -> tuple[date, date]:
        return min(self.values), max(self.values)


def parse_trends_csv(raw: bytes) -> dict[tuple[str, str], dict[date, float]]:
    """Read one Trends export. Returns {(term, geo): {day: value}}. Raises ValueError."""
    text = raw.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text)))
    start = next(
        (
            i
            for i, r in enumerate(rows)
            if len(r) >= 2 and r[0].strip().lower() in {"day", "week", "month", "time"}
        ),
        None,
    )
    if start is None:
        raise ValueError("no header line starting with 'Day,' found")
    header = [c.strip() for c in rows[start]]
    if header[0].lower() != "day":
        raise ValueError(
            f"first column is {header[0]!r}, not 'Day': Trends only gives daily numbers for "
            "windows of up to about 90 days; pull a shorter window"
        )
    columns: list[tuple[str, str]] = []
    for cell in header[1:]:
        match = _HEADER_CELL.match(cell)
        if match is None:
            raise ValueError(f"column header {cell!r} is not 'term: (region)'")
        columns.append((match["term"].strip(), match["geo"].strip()))
    out: dict[tuple[str, str], dict[date, float]] = {c: {} for c in columns}
    for r in rows[start + 1 :]:
        if not r or not any(c.strip() for c in r):
            break  # a blank line ends the table
        try:
            day = date.fromisoformat(r[0].strip())
        except ValueError:
            raise ValueError(f"{r[0]!r} is not a date YYYY-MM-DD") from None
        for key, cell in zip(columns, r[1:], strict=False):
            cell = cell.strip()
            if not cell:
                continue  # no number for that day
            if cell == "<1":
                value = LESS_THAN_ONE
            else:
                try:
                    value = float(cell)
                except ValueError:
                    raise ValueError(f"{cell!r} on {day} is not a number") from None
            if day in out[key]:
                raise ValueError(f"{day} appears twice")
            out[key][day] = value
    return out


def chain_windows(windows: list[Window]) -> pd.DataFrame:
    """Join the windows of ONE term into a single series (see the module docstring).

    Windows are taken in order of start day (then end day, then pull date). The
    earliest window sets the starting scale; every later window is multiplied by
    the factor that makes it agree with the series so far on the shared days, and
    only adds the days the series does not have yet. Days already present keep the
    value from the earlier window (``n_windows`` counts how many windows saw them).
    """
    if not windows:
        return pd.DataFrame({c: [] for c in COLUMNS})
    ordered = sorted(windows, key=lambda w: (*w.span, w.file.day, w.file.name))
    term, geo = ordered[0].term, ordered[0].geo
    chained: dict[date, float] = {}
    window_value: dict[date, float] = {}
    source: dict[date, str] = {}
    counts: dict[date, int] = {}
    for w in ordered:
        if not chained:
            factor = 1.0
        else:
            shared = sorted(set(chained) & set(w.values))
            if not shared:
                raise CleanError(
                    f"{TABLE}: window {w.file.relpath} for {term!r} covers {w.span[0]} to "
                    f"{w.span[1]} and shares no day with the windows before it "
                    f"({min(chained)} to {max(chained)}); pull a window that overlaps"
                )
            old = sum(chained[d] for d in shared)
            new = sum(w.values[d] for d in shared)
            if old <= 0 or new <= 0:
                raise CleanError(
                    f"{TABLE}: window {w.file.relpath} for {term!r} cannot be scaled: its "
                    f"{len(shared)} shared day(s) ({shared[0]} to {shared[-1]}) add up to "
                    f"{new:g} here and {old:g} in the earlier windows; a zero total gives no ratio "
                    "(pull a window whose overlap has some search interest)"
                )
            factor = old / new
        for day, value in w.values.items():
            counts[day] = counts.get(day, 0) + 1
            if day not in chained:
                chained[day] = value * factor
                window_value[day] = value
                source[day] = w.file.relpath
    top = max(chained.values())
    rescale = 100.0 / top if top > 0 else 1.0
    days = sorted(chained)
    return pd.DataFrame(
        {
            "term": term,
            "geo": geo,
            "date": days,
            "value": [chained[d] * rescale for d in days],
            "window_value": [window_value[d] for d in days],
            "n_windows": [counts[d] for d in days],
            "source_file": [source[d] for d in days],
        }
    )[COLUMNS]


def build(ctx: CleanContext) -> pd.DataFrame:
    """Chain every Trends file under data/raw/trends/ (empty table if there are none)."""
    files = [f for f in list_raw_files(ctx.raw_root, SOURCE) if f.name.lower().endswith(".csv")]
    if not files:
        logger.info("%s: no files under data/raw/%s/; table left empty", TABLE, SOURCE)
        return concat_frames([], COLUMNS)
    by_term: dict[tuple[str, str], list[Window]] = {}
    for rf in files:
        try:
            parsed = parse_trends_csv(rf.path.read_bytes())
        except (ValueError, UnicodeDecodeError) as exc:
            raise CleanError(f"{TABLE}: cannot read Trends file {rf.relpath}: {exc}") from exc
        for (term, geo), values in parsed.items():
            if values:
                by_term.setdefault((term, geo), []).append(Window(term, geo, values, rf))
    frames = [chain_windows(ws) for _, ws in sorted(by_term.items())]
    out = concat_frames(frames, COLUMNS)
    logger.info(
        "%s: %d day(s) for %d term(s) from %d file(s)", TABLE, len(out), len(by_term), len(files)
    )
    return out
