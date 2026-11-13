"""Shared reader for the hand-filled CSV files in ``data/manual/``.

Some facts cannot be downloaded: dated rule changes, feeder-fund holdings, monthly
AUM from fund-house factsheets. Those are typed (or pasted by the research side)
into CSV files under ``data/manual/``, and the manual cleaners turn them into clean
tables. This module is the common part: read the file, check the header, hand each
row to the cleaner with helper methods that check one value at a time.

Rules every manual file follows
-------------------------------
* A missing file is NOT an error: nothing has been entered yet, so the table is
  empty and an info line is logged.
* A bad value IS an error, and the message names the file and the line number, so
  the person who typed it can find it. Nothing is silently skipped or "fixed".
* Dates are ISO ``YYYY-MM-DD``. Times are IST unless a UTC offset is written.
* Money is in RUPEES (not lakhs or crores), units are plain counts.
* ``source_file`` of every row is ``data/manual/<file>.csv:<line>``, the line
  number counting the header as line 1, so any number traces back to a typed row.
"""

from __future__ import annotations

import csv
import logging
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from etfp.clean.common import CleanContext, CleanError
from etfp.timeutil import IST

logger = logging.getLogger("etfp.clean")

_BOOL_TRUE = {"true", "yes", "y", "1"}
_BOOL_FALSE = {"false", "no", "n", "0"}
# 3,275,018 or 1,234.5 typed with thousands commas (the cell must then be quoted in the CSV).
_THOUSANDS = re.compile(r"\d{1,3}(,\d{3})+(\.\d+)?\Z")


def project_root(ctx: CleanContext) -> Path:
    """Project root, derived from the raw folder (<root>/data/raw)."""
    return ctx.raw_root.parent.parent


def manual_dir(ctx: CleanContext) -> Path:
    """Folder with the hand-filled files (<root>/data/manual)."""
    return ctx.raw_root.parent / "manual"


def relpath(ctx: CleanContext, path: Path) -> str:
    """POSIX path relative to the project root, as stored in ``source_file``."""
    try:
        return path.relative_to(project_root(ctx)).as_posix()
    except ValueError:
        return path.as_posix()


@dataclass(frozen=True)
class Row:
    """One data row of a manual CSV, with checked accessors.

    Every accessor raises ``CleanError`` naming the table, file, line and column.
    The typed accessors return None for an empty cell (an empty cell means "not
    known") unless ``required=True``.
    """

    table: str
    file: str  # path relative to the project root
    line: int  # line number in the file, header = 1
    values: dict[str, str]

    @property
    def source_file(self) -> str:
        return f"{self.file}:{self.line}"

    def fail(self, message: str) -> CleanError:
        """Build (not raise) the error for this row: ``raise row.fail("...")``."""
        return CleanError(f"{self.table}: {self.file} line {self.line}: {message}")

    def get_text(self, col: str, *, required: bool = False) -> str | None:
        value = self.values.get(col, "").strip()
        if not value:
            if required:
                raise self.fail(f"column {col!r} is required but empty")
            return None
        return value

    def get_date(self, col: str, *, required: bool = False) -> date | None:
        value = self.get_text(col, required=required)
        if value is None:
            return None
        try:
            return date.fromisoformat(value)
        except ValueError:
            raise self.fail(f"column {col!r}: {value!r} is not a date YYYY-MM-DD") from None

    def get_datetime_ist(self, col: str) -> datetime | None:
        """A timestamp as an aware datetime in IST. No UTC offset written means IST."""
        value = self.get_text(col)
        if value is None:
            return None
        try:
            stamp = datetime.fromisoformat(value)
        except ValueError:
            raise self.fail(
                f"column {col!r}: {value!r} is not a time like 2026-09-25T16:10 (IST) "
                "or 2026-09-25T16:10+05:30"
            ) from None
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=IST)
        return stamp.astimezone(IST)

    def get_float(
        self,
        col: str,
        *,
        required: bool = False,
        minimum: float | None = None,
        positive: bool = False,
    ) -> float | None:
        value = self.get_text(col, required=required)
        if value is None:
            return None
        cleaned = value.replace(",", "") if _THOUSANDS.match(value) else value
        try:
            number = float(cleaned)
        except ValueError:
            raise self.fail(f"column {col!r}: {value!r} is not a number") from None
        if not math.isfinite(number):
            raise self.fail(f"column {col!r}: {value!r} is not a finite number")
        if positive and number <= 0:
            raise self.fail(f"column {col!r}: {value!r} must be greater than 0")
        if minimum is not None and number < minimum:
            raise self.fail(f"column {col!r}: {value!r} must be at least {minimum:g}")
        return number

    def get_int(self, col: str, *, required: bool = False, positive: bool = False) -> int | None:
        number = self.get_float(col, required=required, positive=positive)
        if number is None:
            return None
        if number != int(number):
            raise self.fail(f"column {col!r}: {self.values[col].strip()!r} must be a whole number")
        return int(number)

    def get_choice(self, col: str, allowed: Sequence[str], *, required: bool = False) -> str | None:
        value = self.get_text(col, required=required)
        if value is None:
            return None
        key = value.lower()
        if key not in allowed:
            raise self.fail(f"column {col!r}: {value!r} must be one of {', '.join(allowed)}")
        return key

    def get_bool(self, col: str, *, default: bool = False) -> bool:
        value = self.get_text(col)
        if value is None:
            return default
        key = value.lower()
        if key in _BOOL_TRUE:
            return True
        if key in _BOOL_FALSE:
            return False
        raise self.fail(f"column {col!r}: {value!r} must be true or false")

    def get_symbols(self, col: str, known: set[str]) -> list[str]:
        """ETF symbols separated by ';' (',' is accepted too). Unknown ones are errors."""
        value = self.get_text(col)
        if value is None:
            return []
        out: list[str] = []
        for part in re.split(r"[;,]", value):
            symbol = part.strip().upper()
            if not symbol:
                continue
            if symbol not in known:
                raise self.fail(
                    f"column {col!r}: unknown ETF symbol {symbol!r} (not in NSE's ETF list "
                    "or config/etfs.yaml)"
                )
            if symbol not in out:
                out.append(symbol)
        return out


def read_rows(
    ctx: CleanContext,
    filename: str,
    table: str,
    *,
    required: Sequence[str],
    optional: Sequence[str],
) -> list[Row] | None:
    """Read ``data/manual/<filename>``. None if the file does not exist.

    The header must contain every ``required`` column and may contain the
    ``optional`` ones, in any order; any other column name is an error (a typo in
    a header would otherwise silently drop a whole column). Completely blank lines
    are skipped. A header-only file gives an empty list.
    """
    path = manual_dir(ctx) / filename
    rel = relpath(ctx, path)
    if not path.is_file():
        logger.info("%s: %s not found; table left empty", table, rel)
        return None

    allowed = [*required, *optional]
    rows: list[Row] = []
    try:
        handle = path.open(encoding="utf-8-sig", newline="")
    except OSError as exc:
        raise CleanError(f"{table}: cannot open {rel}: {exc}") from exc
    with handle:
        reader = csv.reader(handle)
        try:
            header_raw = next(reader)
        except StopIteration:
            raise CleanError(
                f"{table}: {rel} is empty; it needs a header line: {','.join(allowed)}"
            ) from None
        except (csv.Error, UnicodeDecodeError) as exc:
            raise CleanError(f"{table}: {rel} line 1: {exc}") from exc
        header = [h.strip().lower() for h in header_raw]
        dupes = sorted({h for h in header if header.count(h) > 1})
        if dupes:
            raise CleanError(f"{table}: {rel} line 1: duplicate column(s) {', '.join(dupes)}")
        unknown = [h for h in header if h not in allowed]
        if unknown:
            raise CleanError(
                f"{table}: {rel} line 1: unknown column(s) {', '.join(unknown)}; "
                f"allowed: {', '.join(allowed)}"
            )
        absent = [c for c in required if c not in header]
        if absent:
            raise CleanError(
                f"{table}: {rel} line 1: missing required column(s) {', '.join(absent)}"
            )
        while True:
            try:
                fields = next(reader)
            except StopIteration:
                break
            except (csv.Error, UnicodeDecodeError) as exc:
                raise CleanError(f"{table}: {rel} line {reader.line_num}: {exc}") from exc
            if not any(f.strip() for f in fields):
                continue
            line = reader.line_num  # last line of the record
            if len(fields) > len(header):
                raise CleanError(
                    f"{table}: {rel} line {line}: {len(fields)} fields but the header has "
                    f"{len(header)} (a comma inside a value needs the value in double quotes)"
                )
            padded = fields + [""] * (len(header) - len(fields))
            rows.append(Row(table, rel, line, dict(zip(header, padded, strict=True))))
    logger.info("%s: read %d row(s) from %s", table, len(rows), rel)
    return rows


def is_month_end(day: date) -> bool:
    return (day + timedelta(days=1)).day == 1
