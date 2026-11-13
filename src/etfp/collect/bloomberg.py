"""Importer for manually exported Bloomberg CSVs (SPEC 5, DECISIONS D13).

There is no download here. Amish exports data on the college terminal and drops
the CSV into ``data/raw/bloomberg/<YYYY-MM-DD>/`` (the folder date is the export
date). The format is documented in ``docs/bloomberg_template.md``. This module only
READS those files: raw data is immutable, so nothing is moved, rewritten or deleted.

Licence: Bloomberg data may not be redistributed. ``data/raw/`` is git-ignored; never
commit a real export. The fixtures under ``tests/fixtures/bloomberg/`` are
synthetic (made-up numbers).

What "validate loudly" means
----------------------------
Every problem is reported with the file name and line number, and a bad file raises
:class:`BloombergImportError` instead of being half-imported:

* header must contain exactly the template columns (extra or missing = error),
* dates ISO ``YYYY-MM-DD``, times ``HH:MM`` or ``HH:MM:SS``, a known time zone,
* values plain numbers (``#N/A N/A``, blanks, ``1,234.5`` are rejected),
* no duplicate (time, instrument, field) rows within or across files unless the
  values agree and ``on_conflict='latest'`` says to take the newer export.

Coverage (:func:`check_coverage`) then checks that the data needed for fair value
exists for every trading day in a date range.

Parsing is separate from file discovery, so tests parse strings.
"""

from __future__ import annotations

import csv
import hashlib
import io
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

import pandas as pd

from etfp import calendars
from etfp.calendars import Market
from etfp.paths import raw_dir
from etfp.timeutil import HONG_KONG, IST, NEW_YORK

SOURCE = "bloomberg"

REQUIRED_COLUMNS: tuple[str, ...] = ("date", "time", "timezone", "instrument", "field", "value")
OPTIONAL_COLUMNS: tuple[str, ...] = ("note",)
ALLOWED_TIMEZONES: tuple[str, ...] = ("Asia/Kolkata", "America/New_York", "Asia/Hong_Kong", "UTC")

OUTPUT_COLUMNS: tuple[str, ...] = (
    "date",
    "local_time",
    "timezone",
    "ts_utc",
    "ts_ist",
    "instrument",
    "field",
    "value",
    "note",
    "source_file",
    "source_line",
)

# Instrument codes used by this project (the template's naming). Others are allowed
# (for example China ETF codes in R17); this map only drives default coverage checks.
INSTRUMENT_MARKET: dict[str, Market] = {
    "NQ1": "US",  # E-mini Nasdaq-100 front-month future
    "ES1": "US",  # E-mini S&P 500 front-month future
    "NDX": "US",  # Nasdaq-100 index
    "SPX": "US",  # S&P 500 index
    "HSI": "HK",  # Hang Seng index
    "HSTECH": "HK",  # Hang Seng TECH index
    "USDINR": "IN",  # rupees per US dollar
    "HKDINR": "IN",  # rupees per Hong Kong dollar
}
FUTURES_FOR_PROXY = {"NQ": "NQ1", "ES": "ES1"}

# Fields whose value must be strictly positive (prices, NAV, share counts).
_POSITIVE_FIELDS = frozenset(
    {
        "PX_LAST",
        "PX_OPEN",
        "PX_HIGH",
        "PX_LOW",
        "PX_SETTLE",
        "PX_BID",
        "PX_ASK",
        "LAST_PRICE",
        "FUND_NET_ASSET_VAL",
        "EQY_SH_OUT",
    }
)

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME_RE = re.compile(r"^\d{2}:\d{2}(:\d{2})?$")
_INSTRUMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._/\-]*$")
_FIELD_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_MAX_REPORTED_PROBLEMS = 20


class BloombergImportError(ValueError):
    """A Bloomberg file is malformed or inconsistent. The message names file and line."""


# --------------------------------------------------------------------------
# Parsing one file
# --------------------------------------------------------------------------
def _parse_value(text: str) -> float:
    raw = text.strip()
    if not raw:
        raise ValueError("value is blank")
    if not re.fullmatch(r"[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?", raw):
        raise ValueError(f"value {text!r} is not a plain number (no commas, no '#N/A')")
    num = float(raw)
    if not math.isfinite(num):
        raise ValueError(f"value {text!r} is not finite")
    return num


def parse(content: bytes | str, source_file: str) -> pd.DataFrame:
    """Parse and validate the text of one export; ``source_file`` is used for lineage and errors.

    Returns a frame with ``OUTPUT_COLUMNS``. Raises :class:`BloombergImportError` listing up to
    20 problems with line numbers.
    """
    text = content.decode("utf-8-sig") if isinstance(content, bytes) else content.lstrip("﻿")
    reader = csv.reader(io.StringIO(text))
    try:
        header_raw = next(reader)
    except StopIteration:
        raise BloombergImportError(f"{source_file}: file is empty (no header row)") from None
    header = [h.strip().lower() for h in header_raw]
    missing = [c for c in REQUIRED_COLUMNS if c not in header]
    unknown = [c for c in header if c not in REQUIRED_COLUMNS + OPTIONAL_COLUMNS]
    if missing or unknown or len(set(header)) != len(header):
        raise BloombergImportError(
            f"{source_file}: bad header {header_raw!r}; missing={missing}, unknown={unknown}, "
            f"duplicates={sorted({h for h in header if header.count(h) > 1})}. "
            f"Expected columns: {list(REQUIRED_COLUMNS)} (+ optional {list(OPTIONAL_COLUMNS)}). "
            "See docs/bloomberg_template.md."
        )
    col = {name: header.index(name) for name in header}

    problems: list[str] = []
    rows: list[dict[str, Any]] = []
    seen: dict[tuple[Any, ...], int] = {}
    for line_no, rec in enumerate(reader, start=2):
        if not any(cell.strip() for cell in rec):
            continue  # blank line
        if len(rec) != len(header):
            problems.append(f"line {line_no}: expected {len(header)} fields, found {len(rec)}")
            continue

        def get(name: str, rec: list[str] = rec) -> str:
            return rec[col[name]].strip() if name in col else ""

        errs: list[str] = []
        day: date | None = None
        clock: time | None = None
        tz_name = get("timezone")
        if not _DATE_RE.match(get("date")):
            errs.append(f"date {get('date')!r} is not YYYY-MM-DD")
        else:
            try:
                day = date.fromisoformat(get("date"))
            except ValueError:
                errs.append(f"date {get('date')!r} is not a real calendar date")
        if not _TIME_RE.match(get("time")):
            errs.append(f"time {get('time')!r} is not HH:MM or HH:MM:SS")
        else:
            try:
                clock = time.fromisoformat(get("time"))
            except ValueError:
                errs.append(f"time {get('time')!r} is not a real clock time")
        if tz_name not in ALLOWED_TIMEZONES:
            errs.append(f"timezone {tz_name!r} not in {list(ALLOWED_TIMEZONES)}")
        instrument, field_name = get("instrument"), get("field")
        if not _INSTRUMENT_RE.match(instrument):
            errs.append(f"instrument {instrument!r} is empty or has odd characters")
        if not _FIELD_RE.match(field_name):
            errs.append(f"field {field_name!r} must be UPPER_SNAKE_CASE like PX_LAST")
        value = math.nan
        try:
            value = _parse_value(get("value"))
            if field_name in _POSITIVE_FIELDS and value <= 0:
                errs.append(f"{field_name} must be > 0, got {value}")
        except ValueError as exc:
            errs.append(str(exc))
        if errs:
            problems.append(f"line {line_no}: " + "; ".join(errs))
            continue

        assert day is not None and clock is not None  # for the type checker
        ts = datetime.combine(day, clock, tzinfo=ZoneInfo(tz_name))
        ts_utc = pd.Timestamp(ts).tz_convert("UTC")
        key = (ts_utc, instrument, field_name)
        if key in seen:
            problems.append(
                f"line {line_no}: duplicate of line {seen[key]} "
                f"({instrument} {field_name} at {day} {clock} {tz_name})"
            )
            continue
        seen[key] = line_no
        rows.append(
            {
                "date": day,
                "local_time": clock,
                "timezone": tz_name,
                "ts_utc": ts_utc,
                "ts_ist": ts_utc.tz_convert(IST),
                "instrument": instrument,
                "field": field_name,
                "value": value,
                "note": get("note") or None,
                "source_file": source_file,
                "source_line": line_no,
            }
        )
    if problems:
        shown = problems[:_MAX_REPORTED_PROBLEMS]
        more = len(problems) - len(shown)
        tail = f"\n... and {more} more" if more > 0 else ""
        raise BloombergImportError(
            f"{source_file}: {len(problems)} problem(s):\n" + "\n".join(shown) + tail
        )
    frame = pd.DataFrame(rows, columns=list(OUTPUT_COLUMNS))
    if frame.empty:
        raise BloombergImportError(f"{source_file}: header found but no data rows")
    frame["ts_utc"] = pd.to_datetime(frame["ts_utc"], utc=True)
    frame["ts_ist"] = frame["ts_utc"].dt.tz_convert(IST)
    return frame


# --------------------------------------------------------------------------
# Many files
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class FileInfo:
    """One imported file, for lineage and the export manifest."""

    source_file: str  # path relative to the bloomberg folder, POSIX style
    sha256: str
    rows: int
    first_date: date
    last_date: date


@dataclass(frozen=True)
class BloombergImport:
    """Combined result of importing a folder."""

    frame: pd.DataFrame
    files: tuple[FileInfo, ...]
    superseded_rows: int  # rows replaced by a newer export (only with on_conflict='latest')


def bloomberg_root(root: Path | None = None) -> Path:
    """Default drop folder: ``data/raw/bloomberg``."""
    return root if root is not None else raw_dir() / SOURCE


def list_csv_files(root: Path | None = None) -> list[Path]:
    """CSV files below the drop folder in a stable order (hidden and ``_``-prefixed skipped)."""
    base = bloomberg_root(root)
    if not base.exists():
        return []
    files = [p for p in base.rglob("*.csv") if p.is_file() and not p.name.startswith((".", "_"))]
    return sorted(files, key=lambda p: p.relative_to(base).as_posix())


def import_directory(
    root: Path | None = None,
    *,
    on_conflict: Literal["error", "latest"] = "error",
    require_files: bool = False,
) -> BloombergImport:
    """Read every CSV under the drop folder and combine them.

    ``on_conflict`` decides what happens when two files give a different value for the same
    (time, instrument, field): ``'error'`` (default) raises naming both files; ``'latest'``
    keeps the file that sorts last (later export folder) and counts the replaced rows.
    Identical repeats across files are always collapsed to the first.

    With no files the result is empty (Bloomberg exports are not available yet, D13) unless
    ``require_files`` is True.
    """
    base = bloomberg_root(root)
    paths = list_csv_files(base)
    if not paths:
        if require_files:
            raise BloombergImportError(f"no Bloomberg CSV files found under {base}")
        return BloombergImport(pd.DataFrame(columns=list(OUTPUT_COLUMNS)), (), 0)

    frames: list[pd.DataFrame] = []
    infos: list[FileInfo] = []
    for path in paths:
        rel = path.relative_to(base).as_posix()
        data = path.read_bytes()
        frame = parse(data, f"{SOURCE}/{rel}")
        frames.append(frame)
        infos.append(
            FileInfo(
                source_file=f"{SOURCE}/{rel}",
                sha256=hashlib.sha256(data).hexdigest(),
                rows=len(frame),
                first_date=frame["date"].min(),
                last_date=frame["date"].max(),
            )
        )
    allrows = pd.concat(frames, ignore_index=True)
    key = ["ts_utc", "instrument", "field"]
    dup = allrows.duplicated(key, keep=False)
    superseded = 0
    if dup.any():
        keep: list[int] = []
        for _, grp in allrows[dup].groupby(key, sort=False):
            first, last = grp.iloc[0], grp.iloc[-1]
            if grp["value"].nunique() > 1 and on_conflict == "error":
                raise BloombergImportError(
                    f"conflicting values for {first['instrument']} {first['field']} at "
                    f"{first['ts_utc']}: "
                    + "; ".join(
                        f"{r.source_file} line {r.source_line} = {r.value}"
                        for r in grp.itertuples()
                    )
                    + ". Resolve by hand or import with on_conflict='latest'."
                )
            chosen = last if on_conflict == "latest" else first
            keep.append(int(chosen.name))
            superseded += int(grp["value"].nunique() > 1) * (len(grp) - 1)
        drop_mask = dup.copy()
        drop_mask.loc[keep] = False
        allrows = allrows[~drop_mask]
    allrows = allrows.sort_values(["ts_utc", "instrument", "field"]).reset_index(drop=True)
    return BloombergImport(allrows, tuple(infos), superseded)


# --------------------------------------------------------------------------
# Using the data
# --------------------------------------------------------------------------
_ZONES = {"Asia/Kolkata": IST, "America/New_York": NEW_YORK, "Asia/Hong_Kong": HONG_KONG}


def series_at(
    frame: pd.DataFrame,
    instrument: str,
    field: str,
    *,
    tz: str,
    local_time: time | None = None,
    not_before: time | None = None,
) -> pd.Series:
    """One value per local date for an instrument/field, indexed by python ``date`` in ``tz``.

    * ``local_time`` given: the observation stamped exactly that clock time in ``tz``
      (for example ``time(15, 30)`` in ``Asia/Kolkata`` for "price at the Indian close").
    * ``local_time`` None: the LAST observation of each local date, considering only rows
      stamped at or after ``not_before`` when that is given. Beware: a row exported in another
      zone is converted, so a 15:30 IST row lands on the same New York date at 06:00. Use
      ``local_time`` (the real close, 16:00 New York) when you need THE close.

    These Series feed ``fair_value_frame``: ``futures_t`` = ``series_at(NQ1, tz=IST, 15:30)``
    indexed by Indian trading day; ``futures_a`` = ``series_at(NQ1, tz=New_York)`` looked up
    at ``nav_known_underlying_date``.
    """
    zone = _ZONES.get(tz) or ZoneInfo(tz)
    sub = frame[(frame["instrument"] == instrument) & (frame["field"] == field)]
    if sub.empty:
        return pd.Series(dtype="float64", name=f"{instrument}.{field}")
    local = sub["ts_utc"].dt.tz_convert(zone)
    work = pd.DataFrame({"ts": local, "value": sub["value"].to_numpy()})
    work["d"] = [t.date() for t in work["ts"]]
    if local_time is not None:
        want = local_time.replace(second=0, microsecond=0)
        work = work[[t.time().replace(second=0, microsecond=0) == want for t in work["ts"]]]
    else:
        if not_before is not None:
            work = work[[t.time() >= not_before for t in work["ts"]]]
        work = work.sort_values("ts").groupby("d", sort=True).tail(1)
    out = pd.Series(work["value"].to_numpy(), index=pd.Index(work["d"], name="date"))
    out.name = f"{instrument}.{field}"
    return out.sort_index()


@dataclass(frozen=True)
class Requirement:
    """One series that must exist on every trading day of ``market`` in the checked range.

    ``local_time`` set: a row stamped exactly that time in ``tz``. Otherwise a row dated that
    day in ``tz`` and, if ``not_before`` is set, stamped at or after it (use this for "the close"
    where early-close days differ: 13:00 New York, 12:00 Hong Kong). Presence is checked, not
    that the value is right.
    """

    instrument: str
    field: str
    market: Market
    tz: str
    local_time: time | None = None
    not_before: time | None = None


@dataclass(frozen=True)
class CoverageGap:
    requirement: Requirement
    missing_dates: tuple[date, ...]

    def describe(self) -> str:
        r = self.requirement
        if r.local_time:
            when = f"at {r.local_time:%H:%M} {r.tz}"
        elif r.not_before:
            when = f"on the {r.tz} date, at or after {r.not_before:%H:%M}"
        else:
            when = f"on the {r.tz} date"
        shown = ", ".join(d.isoformat() for d in self.missing_dates[:5])
        extra = f" (+{len(self.missing_dates) - 5} more)" if len(self.missing_dates) > 5 else ""
        return (
            f"{r.instrument} {r.field} {when} missing for {len(self.missing_dates)} "
            f"{r.market} trading day(s): {shown}{extra}"
        )


def default_requirements(universe: Any) -> list[Requirement]:
    """What fair value needs, for the tracked ETFs of an ``EtfUniverse``.

    * US ETF with a futures proxy: the future at 15:30 IST on each Indian trading day, the
      future on each US trading day (close of ``a``), USD/INR at 15:30 IST and on each US day.
    * HK ETF: HKD/INR at 15:30 IST on each Indian trading day and on each HK day.
    * Domestic ETFs: nothing.
    """
    ist_1530 = time(15, 30)
    us_close = time(13, 0)  # earliest close of any US session (half days)
    reqs: dict[Requirement, None] = {}
    for etf in universe.tracked():
        if etf.underlying_market == "US":
            if etf.futures_proxy is not None:
                fut = FUTURES_FOR_PROXY[etf.futures_proxy]
                reqs[Requirement(fut, "PX_LAST", "IN", "Asia/Kolkata", ist_1530)] = None
                reqs[Requirement(fut, "PX_LAST", "US", "America/New_York", None, us_close)] = None
            reqs[Requirement("USDINR", "PX_LAST", "IN", "Asia/Kolkata", ist_1530)] = None
            # NAV-time rate: the time each fund house uses is not verified yet (D10), so this
            # only checks that SOME USD/INR row is dated on each US session. Tighten later.
            reqs[Requirement("USDINR", "PX_LAST", "US", "America/New_York")] = None
        elif etf.underlying_market == "HK":
            reqs[Requirement("HKDINR", "PX_LAST", "IN", "Asia/Kolkata", ist_1530)] = None
            reqs[Requirement("HKDINR", "PX_LAST", "HK", "Asia/Hong_Kong")] = None
    return list(reqs)


def check_coverage(
    frame: pd.DataFrame,
    requirements: Sequence[Requirement],
    start: date,
    end: date,
    *,
    cal: Any = calendars,
) -> list[CoverageGap]:
    """Trading days in ``[start, end]`` (inclusive) with no matching row, per requirement."""
    gaps: list[CoverageGap] = []
    for req in requirements:
        have = set(
            series_at(
                frame,
                req.instrument,
                req.field,
                tz=req.tz,
                local_time=req.local_time,
                not_before=req.not_before,
            ).index
        )
        expected = cal.sessions(req.market, start, end)
        missing = tuple(d for d in expected if d not in have)
        if missing:
            gaps.append(CoverageGap(req, missing))
    return gaps


def assert_coverage(
    frame: pd.DataFrame,
    requirements: Sequence[Requirement],
    start: date,
    end: date,
    *,
    cal: Any = calendars,
) -> None:
    """Raise :class:`BloombergImportError` listing every coverage gap (nothing if complete)."""
    gaps = check_coverage(frame, requirements, start, end, cal=cal)
    if gaps:
        raise BloombergImportError(
            f"Bloomberg data does not cover {start}..{end}:\n"
            + "\n".join(f"- {g.describe()}" for g in gaps)
        )
