"""Shared helpers for the cleaners: walking the raw store, error messages, gap logging.

How the cleaners work, in plain words
-------------------------------------
Raw files (data/raw/<source>/<YYYY-MM-DD>/<name>) are what the sources sent us,
untouched. Each cleaner walks every date folder of its source(s), runs the
matching ``parse`` function from ``etfp.collect`` and reshapes the result into
the registered table (see ``etfp.store``). Every cleaned row keeps the raw
file's path in ``source_file`` so any number can be traced back.

If a source ever re-sent a changed file, the raw store keeps both
(``name.csv`` and ``name.v2.csv``). The cleaners use the highest version, since
that is the corrected one; the version is visible in ``source_file``.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, TypeVar

import pandas as pd

from etfp import calendars
from etfp.config import EtfUniverse

logger = logging.getLogger("etfp.clean")

T = TypeVar("T")

_ISO_DAY = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
_VERSIONED = re.compile(r"\.v(\d+)(\..*)?\Z")
_MAX_GAPS_SHOWN = 10


class CleanError(RuntimeError):
    """A raw file could not be cleaned. The message names the table and the raw file."""


@dataclass(frozen=True)
class RawFile:
    """One raw file chosen for cleaning (the highest stored version of its name)."""

    source: str
    day: date  # the folder date
    name: str  # name on disk, e.g. NSE_CM_security_09112026.v2.csv.gz
    base_name: str  # name without the version tag, e.g. NSE_CM_security_09112026.csv.gz
    version: int
    path: Path

    @property
    def relpath(self) -> str:
        """Path relative to the raw root, POSIX style. This is what ``source_file`` holds."""
        return f"{self.source}/{self.day.isoformat()}/{self.name}"


def _split_version(name: str) -> tuple[str, int]:
    """'a.v2.csv' -> ('a.csv', 2); 'a.csv' -> ('a.csv', 1) (tag after the first dot)."""
    stem, dot, rest = name.partition(".")
    match = _VERSIONED.match(dot + rest)
    if match is None:
        return name, 1
    return stem + (match.group(2) or ""), int(match.group(1))


def list_raw_files(raw_root: Path, source: str) -> list[RawFile]:
    """Every raw file of a source, one per (date, name), highest version only, oldest first.

    A missing source folder is not an error (nothing collected yet): it gives [].
    """
    base = Path(raw_root) / source
    if not base.is_dir():
        return []
    best: dict[tuple[date, str], RawFile] = {}
    for day_dir in sorted(p for p in base.iterdir() if p.is_dir()):
        if not _ISO_DAY.match(day_dir.name):
            continue  # not a date folder; ignore
        try:
            day = date.fromisoformat(day_dir.name)
        except ValueError:
            raise CleanError(f"{source}: folder {day_dir} is not a real calendar date") from None
        for path in sorted(day_dir.iterdir()):
            if not path.is_file() or path.name.startswith("."):
                continue  # temp files from an interrupted save, hidden files
            base_name, version = _split_version(path.name)
            rf = RawFile(source, day, path.name, base_name, version, path)
            key = (day, base_name)
            if key not in best or version > best[key].version:
                best[key] = rf
    files = sorted(best.values(), key=lambda f: (f.day, f.base_name))
    revised = [f.relpath for f in files if f.version > 1]
    if revised:
        logger.info("%s: using revised raw file versions: %s", source, ", ".join(revised[:5]))
    return files


def parse_file(table: str, rf: RawFile, parser: Callable[[bytes], T]) -> T:
    """Read a raw file and run ``parser`` on its bytes. Any failure names table and file."""
    try:
        return parser(rf.path.read_bytes())
    except CleanError:
        raise
    except Exception as exc:
        raise CleanError(
            f"{table}: cannot clean raw file {rf.relpath} ({type(exc).__name__}: {exc})"
        ) from exc


def concat_frames(frames: list[pd.DataFrame], columns: Iterable[str]) -> pd.DataFrame:
    """Stack frames; an empty list gives an empty frame with the given columns."""
    columns = list(columns)
    if not frames:
        return pd.DataFrame({c: [] for c in columns})
    return pd.concat(frames, ignore_index=True)[columns]


def apply_corporate_actions(frame: pd.DataFrame, *, table: str) -> pd.DataFrame:
    """HOOK for split/bonus adjustment of prices and NAVs. Today it changes nothing.

    Background: some ETFs split their units (MON100 had a 10:1 split on
    2021-06-17, so its NAV drops from about 920 to about 92 overnight with no
    change in value). The clean tables keep the numbers exactly as published,
    unadjusted. When the corporate-actions loader exists (config/
    corporate_actions.yaml), adjustment belongs HERE, applied to the price and
    NAV columns, with the adjustment factor stored beside the raw value so
    nothing is lost.
    """
    del table
    return frame


def find_gaps(present: Iterable[date], *, market: str = "IN") -> list[date]:
    """Trading days between the first and last date in ``present`` that are not in it.

    Days outside the supported calendar range give no answer (an empty list).
    """
    days = sorted(set(present))
    if len(days) < 2:
        return []
    try:
        expected = calendars.sessions(market, days[0], days[-1])  # type: ignore[arg-type]
    except ValueError:
        return []
    have = set(days)
    return [d for d in expected if d not in have]


def log_gaps(table: str, present: Iterable[date], *, market: str = "IN") -> list[date]:
    """Log (never raise) the trading days with no data between the first and last date present.

    Returns the missing days. A gap is expected while the collectors have only
    run for a few days, or when a raw file was never downloaded; the log makes
    it visible.
    """
    days = sorted(set(present))
    missing = find_gaps(days, market=market)
    if missing:
        shown = ", ".join(d.isoformat() for d in missing[:_MAX_GAPS_SHOWN])
        more = (
            f" (+{len(missing) - _MAX_GAPS_SHOWN} more)" if len(missing) > _MAX_GAPS_SHOWN else ""
        )
        logger.warning(
            "%s: %d trading day(s) between %s and %s have no raw file: %s%s",
            table,
            len(missing),
            days[0],
            days[-1],
            shown,
            more,
        )
    return missing


@dataclass
class CleanContext:
    """What every cleaner needs: where the raw files are and which ETFs exist.

    ``master`` (the cleaned ``etf_master`` table) is filled in by ``build_clean``
    after the first cleaner has run; until then the universe is just config.
    ``cache`` lets two cleaners share one expensive read (the security master).
    """

    raw_root: Path
    universe: EtfUniverse
    config_file: str = "config/etfs.yaml"
    master: pd.DataFrame | None = None
    cache: dict[str, Any] = field(default_factory=dict)

    def config_symbols(self) -> set[str]:
        return set(self.universe.symbols())

    def etf_symbols(self) -> set[str]:
        """Every ETF symbol we know: NSE's ETF list plus config."""
        symbols = self.config_symbols()
        if self.master is not None:
            symbols |= set(self.master["symbol"].dropna().astype(str))
        return symbols
