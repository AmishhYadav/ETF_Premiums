"""notices: official notices (SEBI, NSE) that might move ETF premiums, with keyword guesses.

Plain-words background: the premium of an international ETF moves when a rule changes:
SEBI's overseas-investment cap for mutual funds, the exchanges' ETF price band, a fund
house stopping or resuming subscriptions. ``etfp.collect.notices`` saves the official
listings untouched; this cleaner turns every raw listing file into one row per notice.

One row per notice (``notice_id``):

* ``sebi:<number>``  SEBI's own id from the page URL. The RSS feed and the Legal listing
  use the same URLs, so a circular seen in both is one row.
* ``nse:<circular number>``  e.g. ``nse:NSE/CMTR/76101``. NSE lists a circular once per
  department it concerns; those rows merge into one and ``section`` lists every department.

Dates: the sources give only a DATE, not a time. ``published_ts`` is that date at 00:00
Asia/Kolkata with ``published_time_known = False``, so nobody reads it as a real time.
``first_seen_ts`` is when our collector first downloaded a file holding the notice (from
the raw manifest): for notices caught by the daily run it is an upper bound on when the
notice became public; for rows found by a backfill it is just the download time.

Keyword guesses (``config/notices.yaml``): ``category``, ``matched_keywords``,
``relevant`` and ``symbols`` come only from the title. They are CANDIDATES for a person to
read. Nothing here writes to ``events``; a notice becomes an event only when someone adds
it to ``data/manual/events.csv`` with its primary document (D23).

Adding an LLM step later: keep this table as it is (it is rebuilt from raw every time) and
put model output in a separate table, e.g. ``notice_labels`` keyed by ``notice_id`` with
model name, run time, category, direction and a short summary. ``rules_version`` says which
keyword rules produced the guess here, so the two can be compared.

Every raw file is read, including older versions (``.v2`` etc.) of the same name: a later
download of a "latest items" feed can drop items an earlier one had, and none may be lost.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import yaml

from etfp import paths
from etfp.clean.common import CleanContext, CleanError, RawFile, _split_version, parse_file
from etfp.collect import notices as src
from etfp.store import SCHEMAS, Column, TableSchema, register_schema
from etfp.timeutil import IST

logger = logging.getLogger("etfp.clean")

TABLE = "notices"
RULES_FILE = "notices.yaml"
CATEGORIES = ("cap", "rule", "caution", "fund_status", "amc_notice")

COLUMNS = [
    "notice_id", "source", "issuer", "published_date", "published_ts", "published_time_known",
    "first_seen_ts", "title", "url", "section", "ref_no", "category", "matched_keywords",
    "relevant", "symbols", "rules_version", "feeds", "source_file", "n_source_files",
]  # fmt: skip

if TABLE not in SCHEMAS:
    register_schema(
        TableSchema(
            name=TABLE,
            primary_key=("notice_id",),
            doc=(
                "Official SEBI/NSE notices with keyword-rule guesses (candidates only; "
                "never verified events)."
            ),
            columns=(
                Column("notice_id", "string", "sebi:<SEBI page id> or nse:<circular number>"),
                Column("source", "string", "sebi or nse", nullable=False),
                Column("issuer", "string", "SEBI, NSE or NCL (NSE Clearing)"),
                Column("published_date", "date", "Date printed by the source (IST)", False),
                Column(
                    "published_ts",
                    "timestamp",
                    "published_date at 00:00 IST (the sources give no time of day)",
                    False,
                ),
                Column("published_time_known", "bool", "False: published_ts holds only a date"),
                Column(
                    "first_seen_ts",
                    "timestamp",
                    "When our collector first downloaded it (IST); empty if not in the manifest",
                ),
                Column("title", "string", "Title / subject as published", False),
                Column("url", "string", "Link to the notice page or PDF"),
                Column("section", "string", "SEBI section or NSE department(s), '; '-joined"),
                Column("ref_no", "string", "NSE circular number, e.g. NSE/CMTR/76101"),
                Column(
                    "category",
                    "string",
                    "Keyword guess: cap, rule, caution, fund_status, amc_notice; empty = none",
                ),
                Column("matched_keywords", "string", "Every keyword that matched, '; '-joined"),
                Column("relevant", "bool", "True if a category rule matched (a candidate only)"),
                Column("symbols", "string", "Guessed ETF symbols, ';'-separated (as in events)"),
                Column("rules_version", "string", "version in config/notices.yaml used"),
                Column("feeds", "string", "Raw sources it was seen in, '; '-joined"),
                Column("source_file", "string", "First raw file (by download time) holding it"),
                Column("n_source_files", "int", "How many raw files hold it"),
            ),
        )
    )


# -- keyword rules ------------------------------------------------------------------


def _pattern(keyword: str) -> re.Pattern[str]:
    """Whole-word, case-insensitive, optional plural s/es; inner spaces match any spacing."""
    words = [re.escape(w) for w in keyword.split()]
    if not words:
        raise ValueError("empty keyword")
    body = r"\s+".join(words)
    return re.compile(rf"(?<![A-Za-z0-9]){body}(?:s|es)?(?![A-Za-z0-9])", re.IGNORECASE)


@dataclass(frozen=True)
class Rule:
    category: str
    groups: tuple[tuple[tuple[str, re.Pattern[str]], ...], ...]  # AND of ORs


@dataclass(frozen=True)
class NoticeRules:
    version: str
    rules: tuple[Rule, ...]
    exclude: tuple[tuple[str, re.Pattern[str]], ...]
    symbols: dict[str, tuple[tuple[str, re.Pattern[str]], ...]]


def _keywords(value: object, where: str) -> tuple[tuple[str, re.Pattern[str]], ...]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{where}: must be a non-empty list of keywords")
    out = []
    for kw in value:
        if not isinstance(kw, str | int) or not str(kw).strip():
            raise ValueError(f"{where}: keyword {kw!r} is not text")
        out.append((str(kw).strip(), _pattern(str(kw).strip())))
    return tuple(out)


def parse_rules(data: object, *, known_symbols: Iterable[str] | None = None) -> NoticeRules:
    """Check and compile the rules file content. Any mistake stops with a clear message."""
    where = f"config/{RULES_FILE}"
    if not isinstance(data, dict):
        raise ValueError(f"{where}: top level must be a mapping")
    if "version" not in data:
        raise ValueError(f"{where}: 'version' is missing")
    rules = []
    for i, item in enumerate(data.get("rules") or [], 1):
        if not isinstance(item, dict):
            raise ValueError(f"{where}: rule {i} must be a mapping")
        cat = item.get("category")
        if cat not in CATEGORIES:
            raise ValueError(f"{where}: rule {i}: category {cat!r} not one of {CATEGORIES}")
        has_any, has_all = "any" in item, "all_of" in item
        if has_any == has_all:
            raise ValueError(f"{where}: rule {i} ({cat}) needs exactly one of 'any' / 'all_of'")
        if has_any:
            groups = (_keywords(item["any"], f"{where}: rule {i} any"),)
        else:
            raw = item["all_of"]
            if not isinstance(raw, list) or not raw:
                raise ValueError(f"{where}: rule {i} all_of must be a list of keyword lists")
            groups = tuple(
                _keywords(g, f"{where}: rule {i} all_of group {j}") for j, g in enumerate(raw, 1)
            )
        rules.append(Rule(cat, groups))
    if not rules:
        raise ValueError(f"{where}: no rules")
    exclude = data.get("exclude") or []
    exclude_kw = _keywords(exclude, f"{where}: exclude") if exclude else ()
    symbols_raw = data.get("symbols") or {}
    if not isinstance(symbols_raw, dict):
        raise ValueError(f"{where}: 'symbols' must map symbol -> list of phrases")
    known = set(known_symbols) if known_symbols is not None else None
    symbols = {}
    for sym, phrases in symbols_raw.items():
        if known is not None and sym not in known:
            raise ValueError(f"{where}: symbol {sym!r} is not in config/etfs.yaml")
        symbols[str(sym)] = _keywords(phrases, f"{where}: symbols.{sym}")
    return NoticeRules(str(data["version"]), tuple(rules), exclude_kw, symbols)


def load_rules(path: Path, *, known_symbols: Iterable[str] | None = None) -> NoticeRules:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise CleanError(f"{TABLE}: cannot read keyword rules {path} ({exc})") from exc
    try:
        return parse_rules(data, known_symbols=known_symbols)
    except ValueError as exc:
        raise CleanError(f"{TABLE}: {exc}") from exc


@dataclass(frozen=True)
class Guess:
    category: str | None
    matched: tuple[str, ...]
    relevant: bool
    symbols: tuple[str, ...]


def classify(title: str, rules: NoticeRules) -> Guess:
    """Keyword guess for one title. The first matching rule gives the category."""
    matched: list[str] = []

    def hits(group: Iterable[tuple[str, re.Pattern[str]]]) -> list[str]:
        return [kw for kw, pat in group if pat.search(title)]

    category = None
    for rule in rules.rules:
        per_group = [hits(g) for g in rule.groups]
        for found in per_group:
            matched.extend(found)
        if category is None and all(per_group):
            category = rule.category
    symbols = []
    for sym, phrases in rules.symbols.items():
        found = hits(phrases)
        if found:
            symbols.append(sym)
            matched.extend(found)
    excluded = hits(rules.exclude)
    matched.extend(excluded)
    unique = tuple(dict.fromkeys(matched))  # keep first-seen order, drop repeats
    return Guess(category, unique, category is not None and not excluded, tuple(sorted(symbols)))


# -- raw files ----------------------------------------------------------------------


def _all_raw_files(raw_root: Path, source: str) -> list[RawFile]:
    """Every file of a source, ALL versions (unlike ``list_raw_files``), oldest first."""
    base = Path(raw_root) / source
    if not base.is_dir():
        return []
    out = []
    for day_dir in sorted(p for p in base.iterdir() if p.is_dir()):
        try:
            day = date.fromisoformat(day_dir.name)
        except ValueError:
            continue  # not a date folder
        for path in sorted(day_dir.iterdir()):
            if path.is_file() and not path.name.startswith("."):
                base_name, version = _split_version(path.name)
                out.append(RawFile(source, day, path.name, base_name, version, path))
    return out


def _first_fetch_times(raw_root: Path) -> dict[str, datetime]:
    """relpath -> earliest download time from the raw manifest (empty if no manifest)."""
    manifest = Path(raw_root) / "_manifest.jsonl"
    if not manifest.exists():
        return {}
    times: dict[str, datetime] = {}
    with manifest.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            entry = json.loads(line)
            if entry.get("source") not in src.SOURCES:
                continue
            ts = datetime.fromisoformat(entry["fetched_at"]).astimezone(IST)
            rel = entry["relpath"]
            if rel not in times or ts < times[rel]:
                times[rel] = ts
    return times


_PAGE = re.compile(r"_p(\d+)\.html\Z")


def _check_listing_complete(files: list[RawFile], frames: dict[str, pd.DataFrame]) -> None:
    """Each SEBI listing window: the pages together must hold the total the page states."""
    windows: dict[str, list[RawFile]] = {}
    for rf in files:
        if _PAGE.search(rf.base_name):
            windows.setdefault(f"{rf.day}/{_PAGE.sub('', rf.base_name)}", []).append(rf)
    for key, group in windows.items():
        meta = None
        for rf in group:
            if _PAGE.search(rf.base_name).group(1) == "1":
                meta = src.listing_meta(rf.path.read_bytes())
        if meta is None:
            logger.warning("%s: SEBI listing window %s has no page 1", TABLE, key)
            continue
        ids = set().union(*(set(frames[rf.relpath]["notice_id"]) for rf in group))
        if len(ids) < meta[2]:
            logger.warning(
                "%s: SEBI listing window %s states %d records but its pages hold %d; "
                "re-run the download for that window",
                TABLE, key, meta[2], len(ids),
            )  # fmt: skip


def _rules_path(ctx: CleanContext) -> Path:
    local = Path(ctx.raw_root).parent.parent / "config" / RULES_FILE
    return local if local.exists() else paths.config_dir() / RULES_FILE


def build(ctx: CleanContext) -> pd.DataFrame:
    """One row per notice from every raw notice file; empty table if none collected yet."""
    rules = load_rules(_rules_path(ctx), known_symbols=ctx.config_symbols())
    fetched = _first_fetch_times(ctx.raw_root)
    frames: list[pd.DataFrame] = []
    for source in src.SOURCES:
        files = _all_raw_files(ctx.raw_root, source)
        parser = src.PARSERS[source]
        parsed: dict[str, pd.DataFrame] = {}
        for rf in files:
            frame = parse_file(TABLE, rf, lambda b, rf=rf, p=parser: p(b, source_file=rf.relpath))
            frame = frame.assign(
                feed=source,
                fetched_at=fetched.get(rf.relpath),
                # files never seen in the manifest sort after those with a time
                _order=(fetched[rf.relpath].timestamp() if rf.relpath in fetched else 1e12),
            )
            parsed[rf.relpath] = frame
            frames.append(frame)
        if source in (src.SOURCE_SEBI_LISTING, src.SOURCE_SEBI_ARCHIVE):
            _check_listing_complete(files, parsed)
    if not frames or all(f.empty for f in frames):
        return pd.DataFrame({c: [] for c in COLUMNS})
    rows = pd.concat([f for f in frames if not f.empty], ignore_index=True)
    rows = rows.sort_values(["_order", "source_file"], kind="stable")
    out = []
    for notice_id, grp in rows.groupby("notice_id", sort=True):
        first = grp.iloc[0]
        dates = sorted(set(grp["published_date"]))
        if len(dates) > 1:
            logger.warning(
                "%s: %s has different dates in different files (%s); keeping the earliest",
                TABLE, notice_id, ", ".join(d.isoformat() for d in dates),
            )  # fmt: skip
        title = str(first["title"])
        guess = classify(title, rules)
        seen = [t for t in grp["fetched_at"] if t is not None and not pd.isna(t)]
        sections = sorted({str(s) for s in grp["section"].dropna()})
        out.append(
            {
                "notice_id": notice_id,
                "source": notice_id.split(":", 1)[0],
                "issuer": first["issuer"],
                "published_date": dates[0],
                "published_ts": datetime(dates[0].year, dates[0].month, dates[0].day, tzinfo=IST),
                "published_time_known": False,
                "first_seen_ts": min(seen) if seen else None,
                "title": title,
                "url": next((u for u in grp["url"] if isinstance(u, str) and u), None),
                "section": "; ".join(sections) or None,
                "ref_no": first["ref_no"] if isinstance(first["ref_no"], str) else None,
                "category": guess.category,
                "matched_keywords": "; ".join(guess.matched) or None,
                "relevant": guess.relevant,
                "symbols": ";".join(guess.symbols) or None,
                "rules_version": rules.version,
                "feeds": "; ".join(sorted(set(grp["feed"]))),
                "source_file": first["source_file"],
                "n_source_files": int(grp["source_file"].nunique()),
            }
        )
    table = pd.DataFrame(out, columns=COLUMNS)
    logger.info(
        "%s: %d notices, %d flagged relevant by keywords (candidates only)",
        TABLE, len(table), int(table["relevant"].sum()),
    )  # fmt: skip
    return table
