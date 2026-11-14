"""Open dataset export (project output 3): ``etfp export dataset``.

Writes ``exports/dataset/<YYYY-MM-DD>/`` (the build date in India) with, for every public
table, a CSV and a Parquet file, plus ``DATA_DICTIONARY.md``, ``README.md``,
``LICENSE-DATA`` (a placeholder: the licence is the owner's decision) and ``manifest.json``
(git commit, build time, date ranges, row counts, schema version and the sha256 of every
other file). Nothing is uploaded anywhere; publishing is Amish's call.

Licence filtering (the part that must never go wrong)
-----------------------------------------------------
``config/dataset.yaml`` lists the tables and columns that may be published and a deny list.
Two guards enforce it:

1. **Unclassified column guard** (``select_table``). Every column of an exported table must be
   named in the config: exported (``columns``), dropped for a non-licence reason (``drop``) or
   denied. A column the config does not know stops the export, so a column added to the
   pipeline later is never published by default.
2. **Deny guard** (``assert_no_denied``). Runs on the selected tables and again on the files
   read back from disk. Fails if a denied table, a denied column (by name or pattern), a denied
   row (e.g. a limit day measured on Bloomberg minute bars) or a lineage path into a denied
   raw folder (``bloomberg/...``, ``yahoo_intraday/...``) is present.

Why the fair value is not in the dataset: ``fv`` moves the last NAV forward with index futures
and FX from Bloomberg (else Yahoo), and both licences forbid redistributing their data or
numbers derived from it. Every column built on ``fv`` (``prem_fv``, ``pi``, ``cap_pi``,
``rationed``, ...) goes with it. The NAV-based premiums (``prem_raw``, ``prem_nav_known``,
``prem_band_ref``, ``x_april``) use only exchange prices and AMFI NAVs and stay.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from etfp import __version__, paths
from etfp.store import Store
from etfp.timeutil import IST, now_ist

logger = logging.getLogger("etfp.export_dataset")

DERIVED_SOURCES = ("clean", "derived_units_daily", "derived_unit_events", "derived_events")
LICENCE_PLACEHOLDER = "TO BE DECIDED BY OWNER"
DISCLAIMER = (
    "For research and information only. Not investment advice. Figures may contain errors; "
    "check official NSE and AMFI data before acting."
)  # SPEC section 9, word for word


class DatasetLicenceError(RuntimeError):
    """The dataset export refused to write: config problem or a licence guard failed."""


# ==========================================================================
# Config
# ==========================================================================
@dataclass(frozen=True)
class TableSpec:
    name: str
    origin: str
    columns: tuple[str, ...]
    drop: dict[str, str]
    sources: tuple[str, ...]
    primary_key: tuple[str, ...]
    date_column: str | None
    description: str


@dataclass(frozen=True)
class DatasetConfig:
    format_version: str
    title: str
    sources: dict[str, dict[str, str]]
    tables: dict[str, TableSpec]
    deny_sources: dict[str, str]
    deny_tables: dict[str, str]
    deny_table_patterns: tuple[str, ...]
    deny_columns: dict[str, dict[str, str]]
    deny_column_patterns: tuple[str, ...]
    deny_rows: dict[str, dict[str, list[Any]]]
    lineage_substrings: tuple[str, ...]
    path: Path | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def column_denied(self, table: str, column: str) -> str | None:
        """Why ``column`` of ``table`` may not be published, or None if it is not denied."""
        if column in self.deny_columns.get(table, {}):
            return self.deny_columns[table][column]
        for pat in self.deny_column_patterns:
            if re.search(pat, column, flags=re.IGNORECASE):
                return f"matches deny pattern {pat!r}"
        return None

    def table_denied(self, table: str) -> str | None:
        if table in self.deny_tables:
            return self.deny_tables[table]
        for pat in self.deny_table_patterns:
            if re.search(pat, table, flags=re.IGNORECASE):
                return f"matches deny pattern {pat!r}"
        return None


def default_config_path() -> Path:
    return paths.config_dir() / "dataset.yaml"


def parse_dataset_config(raw: dict[str, Any], path: Path | None = None) -> DatasetConfig:
    """Build and validate the config. Any contradiction is an error, never a warning."""
    where = str(path) if path else "dataset config"

    def need(d: dict[str, Any], key: str, ctx: str) -> Any:
        if key not in d:
            raise DatasetLicenceError(f"{where}: {ctx} is missing {key!r}")
        return d[key]

    allowed_status = set(raw.get("licence_status_values") or ["own_work", "unverified"])
    sources = need(raw, "sources", "top level") or {}
    for name, s in sources.items():
        status = (s or {}).get("licence_status")
        if status not in allowed_status:
            raise DatasetLicenceError(
                f"{where}: source {name!r} has licence_status {status!r}; "
                f"allowed {sorted(allowed_status)}"
            )
    deny = need(raw, "deny", "top level") or {}
    deny_sources = dict(deny.get("sources") or {})
    for name in deny_sources:
        if name in sources:
            raise DatasetLicenceError(f"{where}: source {name!r} is both allowed and denied")

    tables: dict[str, TableSpec] = {}
    for name, t in (need(raw, "tables", "top level") or {}).items():
        t = t or {}
        origin = t.get("from", "clean")
        if origin not in DERIVED_SOURCES:
            raise DatasetLicenceError(f"{where}: table {name!r} has unknown from: {origin!r}")
        cols = tuple(need(t, "columns", f"table {name!r}") or ())
        if len(set(cols)) != len(cols):
            raise DatasetLicenceError(f"{where}: table {name!r} lists a column twice")
        drop = dict(t.get("drop") or {})
        if set(cols) & set(drop):
            raise DatasetLicenceError(
                f"{where}: table {name!r} both exports and drops {sorted(set(cols) & set(drop))}"
            )
        srcs = tuple(need(t, "sources", f"table {name!r}") or ())
        for s in srcs:
            if s in deny_sources:
                raise DatasetLicenceError(f"{where}: table {name!r} uses denied source {s!r}")
            if s not in sources:
                raise DatasetLicenceError(f"{where}: table {name!r} uses unknown source {s!r}")
        tables[name] = TableSpec(
            name=name,
            origin=origin,
            columns=cols,
            drop=drop,
            sources=srcs,
            primary_key=tuple(t.get("primary_key") or ()),
            date_column=t.get("date_column"),
            description=str(t.get("description", "")),
        )

    cfg = DatasetConfig(
        format_version=str(raw.get("dataset_format_version", "0")),
        title=str(raw.get("title", "Dataset")),
        sources=sources,
        tables=tables,
        deny_sources=deny_sources,
        deny_tables=dict(deny.get("tables") or {}),
        deny_table_patterns=tuple(deny.get("table_patterns") or ()),
        deny_columns={k: dict(v or {}) for k, v in (deny.get("columns") or {}).items()},
        deny_column_patterns=tuple(deny.get("column_patterns") or ()),
        deny_rows={k: dict(v or {}) for k, v in (deny.get("rows") or {}).items()},
        lineage_substrings=tuple(s.lower() for s in (deny.get("lineage_substrings") or ())),
        path=path,
        raw=raw,
    )
    # An exported table or column must not also be denied (a typo here would be a leak).
    for name, spec in tables.items():
        why = cfg.table_denied(name)
        if why:
            raise DatasetLicenceError(f"{where}: table {name!r} is listed but denied ({why})")
        for col in spec.columns:
            why = cfg.column_denied(name, col)
            if why:
                raise DatasetLicenceError(
                    f"{where}: column {name}.{col} is listed for export but denied ({why})"
                )
        for pk in spec.primary_key:
            if pk not in spec.columns:
                raise DatasetLicenceError(f"{where}: primary key {name}.{pk} is not exported")
    return cfg


def load_dataset_config(path: Path | None = None) -> DatasetConfig:
    path = Path(path) if path else default_config_path()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return parse_dataset_config(raw, path)


# ==========================================================================
# The two guards
# ==========================================================================
def select_table(name: str, df: pd.DataFrame, cfg: DatasetConfig) -> tuple[pd.DataFrame, int]:
    """Public part of one table: (frame with only exported columns and allowed rows, rows removed).

    Guard 1: a column of ``df`` that the config does not classify (export, drop or deny)
    raises. A listed column missing from ``df`` also raises (the dictionary would be wrong).
    """
    spec = cfg.tables[name]
    unknown = [
        c
        for c in df.columns
        if c not in spec.columns and c not in spec.drop and cfg.column_denied(name, c) is None
    ]
    if unknown:
        raise DatasetLicenceError(
            f"table {name!r} has column(s) not classified in config/dataset.yaml: {unknown}. "
            "Add each to the table's `columns` (publish), `drop` (leave out, with reason) or "
            "`deny.columns` (licence) before exporting."
        )
    missing = [c for c in spec.columns if c not in df.columns]
    if missing:
        raise DatasetLicenceError(
            f"table {name!r} is missing column(s) listed in config/dataset.yaml: {missing}"
        )
    out = df
    removed = 0
    for col, values in cfg.deny_rows.get(name, {}).items():
        if col not in out.columns:
            raise DatasetLicenceError(
                f"table {name!r} has no column {col!r} needed for its deny.rows filter"
            )
        bad = out[col].isin(values)
        removed += int(bad.sum())
        out = out[~bad]
    return out.loc[:, list(spec.columns)].reset_index(drop=True), removed


def _is_lineage(column: str) -> bool:
    return "source" in column.lower() or column.lower().endswith("_files")


def assert_no_denied(frames: dict[str, pd.DataFrame], cfg: DatasetConfig) -> None:
    """Guard 2: fail if anything denied is in ``frames`` (table name -> output frame)."""
    problems: list[str] = []
    for name, df in frames.items():
        why = cfg.table_denied(name)
        if why:
            problems.append(f"denied table {name!r} ({why})")
        if name not in cfg.tables:
            problems.append(f"table {name!r} is not listed in config/dataset.yaml")
        for col in df.columns:
            why = cfg.column_denied(name, col)
            if why:
                problems.append(f"denied column {name}.{col} ({why})")
        for col, values in cfg.deny_rows.get(name, {}).items():
            if col in df.columns:
                n = int(df[col].isin(values).sum())
                if n:
                    problems.append(f"{n} denied row(s) in {name} ({col} in {values})")
        for col in df.columns:
            if not _is_lineage(col) or not cfg.lineage_substrings:
                continue
            text = df[col].dropna().astype(str).str.lower()
            for sub in cfg.lineage_substrings:
                n = int(text.str.contains(sub, regex=False).sum())
                if n:
                    problems.append(f"{n} row(s) of {name}.{col} name a {sub!r} raw file")
    if problems:
        raise DatasetLicenceError("dataset export refused (licence guard): " + "; ".join(problems))


# ==========================================================================
# Writing
# ==========================================================================
def _date_range(df: pd.DataFrame, col: str | None) -> dict[str, str | None]:
    if not col or col not in df.columns or df.empty:
        return {"first": None, "last": None}
    values = df[col].dropna()
    if values.empty:
        return {"first": None, "last": None}
    if pd.api.types.is_datetime64_any_dtype(values):
        values = values.dt.tz_convert(IST).dt.date if values.dt.tz else values.dt.date
    return {"first": min(values).isoformat(), "last": max(values).isoformat()}


def _dictionary(frames: dict[str, pd.DataFrame], cfg: DatasetConfig, now: datetime) -> str:
    """DATA_DICTIONARY.md from the research export's generator, limited to published columns."""
    from etfp import export  # the research export owns the column definitions

    tables: dict[str, Any] = {}
    for name, df in frames.items():
        rows = {r["name"]: r for r in export._rows(name, df)}
        cols = []
        for c in df.columns:
            if c not in rows:
                raise DatasetLicenceError(f"no dictionary entry for {name}.{c}")
            cols.append(rows[c])
        tables[name] = {"primary_key": list(cfg.tables[name].primary_key), "columns": cols}
    schema = {
        "schema_version": export.SCHEMA_VERSION,
        "generated_at_ist": now.isoformat(timespec="seconds"),
        "changelog": export.SCHEMA_CHANGELOG,
        "conventions": (
            "Dates are ISO calendar dates (YYYY-MM-DD) in India. Timestamps are timezone-aware "
            "(IST, +05:30). Prices, NAV and values are rupees, on today's unit scale where the "
            "column says adjusted. Premiums are fractions (1.0 = 100%); log columns are natural "
            "logs. Empty = unknown, never zero. dq_* flags mean 'look at this row'; no row is "
            "dropped or corrected. The changelog below covers the project's full research "
            "schema; columns built on the futures-based fair value are not in this dataset "
            "(see README.md)."
        ),
        "tables": tables,
    }
    return export._dictionary_md(schema)


def _licence_text() -> str:
    return (
        f"LICENCE FOR THE DATA IN THIS FOLDER: {LICENCE_PLACEHOLDER}\n"
        "\n"
        "No licence has been chosen yet. Until the owner decides and replaces this file, no\n"
        "permission to copy, share or reuse this data is granted.\n"
        "\n"
        "Before choosing, the owner must settle the redistribution questions listed in\n"
        "README.md (NSE, BSE, AMFI, AMC and Google Trends terms). Options often used for\n"
        "research datasets: CC BY 4.0, CC0, ODbL. Upstream terms may restrict the choice.\n"
    )


def _readme(
    cfg: DatasetConfig,
    frames: dict[str, pd.DataFrame],
    skipped: dict[str, str],
    removed_rows: dict[str, int],
    manifest: dict[str, Any],
) -> str:
    from etfp import export

    git = manifest["code"]
    etf_day = frames.get("etf_day")
    lines = [
        f"# {cfg.title}",
        "",
        f"> {DISCLAIMER}",
        "",
        "## What this is",
        "",
        "A cleaned daily panel of India-listed ETFs that hold foreign shares (Nasdaq 100, "
        "S&P 500 Top 50, NYSE FANG+, Hang Seng TECH and others) plus domestic comparison "
        "ETFs (Nifty, bank, gold, silver). "
        "Indian mutual funds have reached their limits on overseas investment, so these ETFs "
        "cannot issue new units and their market price can drift far above the value of what "
        "they hold (the premium over NAV). In September 2026 SEBI changed how ETF price bands "
        "are anchored and premiums rose from about 20% to over 200%; on 1 April 2027 the band "
        "anchor moves back to NAV. The panel records prices, NAVs, NAV-based premiums, price "
        "bands, limit hits and units outstanding so that this episode can be studied.",
        "",
        "## Coverage",
        "",
    ]
    if etf_day is not None and len(etf_day):
        rng = manifest["date_range"].get("etf_day", {})
        lines.append(
            f"`etf_day`: {len(etf_day):,} ETF-days, {etf_day['symbol'].nunique()} ETFs, "
            f"{rng.get('first')} to {rng.get('last')} (NSE trading days)."
        )
        lines.append("")
    lines += [
        "| table | rows | first | last | sources | description |",
        "|---|---|---|---|---|---|",
    ]
    for name, df in frames.items():
        spec = cfg.tables[name]
        rng = manifest["date_range"].get(name, {})
        lines.append(
            f"| `{name}` | {len(df):,} | {rng.get('first') or ''} | {rng.get('last') or ''} | "
            f"{', '.join(spec.sources)} | {spec.description} |"
        )
    if skipped:
        lines += ["", "Listed in the dataset config but not in this release:", ""]
        lines += [f"- `{t}`: {why}" for t, why in skipped.items()]
    cut = {t: n for t, n in removed_rows.items() if n}
    if cut:
        lines += ["", "Rows removed by the licence filter (`deny.rows`):", ""]
        lines += [f"- `{t}`: {n} row(s)" for t, n in cut.items()]
    lines += [
        "",
        "Each table is given twice with the same content: `<table>.parquet` (exact types and "
        "values) and `<table>.csv` (decimals printed to 15 significant digits; timestamps as "
        "`YYYY-MM-DD HH:MM:SS+05:30`). Column definitions, units and formulas are in "
        "`DATA_DICTIONARY.md`.",
        "",
        "## What is not included, and why",
        "",
        "The project's own fair value (`fv`) moves the last published NAV forward to the Indian "
        "close with index futures and FX rates from Bloomberg (or Yahoo Finance as a free "
        "fallback). Neither licence allows redistributing their data or numbers derived from "
        "it, so `fv` and every column built on it are left out: "
        + ", ".join(f"`{c}`" for c in sorted(cfg.deny_columns.get("etf_day", {})))
        + ". Also left out: "
        + ", ".join(f"`{t}` ({why.rstrip('.')})" for t, why in cfg.deny_tables.items())
        + ", and limit days measured on Bloomberg minute bars. "
        "The NAV-based premiums (`prem_raw`, `prem_nav_known`, `prem_band_ref`, `x_april`) use "
        "only exchange prices and AMFI NAVs and are included.",
        "",
        "## How it was built",
        "",
        "Every number traces to an untouched raw download (`source_file` and the other "
        "`*_source_file` columns give the path under the project's `data/raw/`). The raw files "
        "are not in this folder. To rebuild from source:",
        "",
        "```",
        "uv run etfp backfill --from 2021-01-01   # download raw files (rate-limited)",
        "uv run etfp build                       # raw -> clean tables -> etf_day",
        "uv run etfp export dataset              # this folder",
        "```",
        "",
        f"- Code: git commit `{git['git_commit']}`"
        + (" (built with uncommitted changes)" if git["git_dirty"] else "")
        + f", package etfp {git['package_version']}.",
        f"- Column schema version {export.SCHEMA_VERSION}; dataset format {cfg.format_version}.",
        f"- Built {manifest['build_time_ist']} (India time).",
        "- `manifest.json` lists the sha256 checksum of every other file in this folder.",
        "",
        "## How to cite",
        "",
        "TO BE COMPLETED BY OWNER (author, title, version, DOI once published):",
        "",
        "```",
        f"[Author]. ({manifest['build_time_ist'][:4]}). {cfg.title} "
        f"(version {manifest['release']}) [Data set]. [Repository]. https://doi.org/[DOI]",
        "```",
        "",
        "## Licence",
        "",
        f"{LICENCE_PLACEHOLDER}. See `LICENSE-DATA`. No permission to reuse is granted until the "
        "owner chooses a licence.",
        "",
        "## Redistribution questions for the owner",
        "",
        "These sources are included in this local build but their terms for republishing have "
        "not been checked (`licence_status: unverified` in `config/dataset.yaml`). Settle each "
        "before publishing; any table can be dropped by removing it from that file.",
        "",
    ]
    for key, s in cfg.sources.items():
        if s.get("licence_status") != "unverified":
            continue
        used = [t for t, spec in cfg.tables.items() if key in spec.sources and t in frames]
        lines.append(
            f"- **{s.get('name', key)}** (`{key}`), used in "
            f"{', '.join(f'`{t}`' for t in used) or 'no table in this release'}. "
            f"{s.get('note', '')}"
        )
    lines += ["", "## Disclaimer", "", DISCLAIMER, ""]
    return "\n".join(lines)


def _atomic_text(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def _source_frames(store: Store, root: Path, cfg: DatasetConfig) -> dict[str, pd.DataFrame | None]:
    """Full (unfiltered) frames for every configured table; None when the store lacks it."""
    from etfp import build, export  # lazy: heavy imports, and build registers the schemas

    bcfg = build.load_config(root)
    out: dict[str, pd.DataFrame | None] = {}
    unit_tables: tuple[pd.DataFrame, pd.DataFrame] | None = None
    for name, spec in cfg.tables.items():
        if spec.origin == "clean":
            if not store.has_table(name):
                out[name] = None
            elif name == "etf_day":
                with export.etf_day_schema_registered():
                    out[name] = store.read_table("etf_day")
            else:
                out[name] = store.read_table(name)
        elif spec.origin in ("derived_units_daily", "derived_unit_events"):
            if unit_tables is None:
                unit_tables = build.build_unit_tables(store, bcfg.universe)
            out[name] = unit_tables[0 if spec.origin == "derived_units_daily" else 1]
        elif spec.origin == "derived_events":
            out[name] = build.build_events(store, universe=bcfg.universe)
    return out


def export_dataset(
    store: Store,
    out_root: Path | None = None,
    *,
    root: Path | None = None,
    now: datetime | None = None,
    repo_dir: Path | None = None,
    config_path: Path | None = None,
    source_frames: dict[str, pd.DataFrame | None] | None = None,
) -> Path:
    """Write ``<out_root>/<YYYY-MM-DD>/`` and point ``<out_root>/LATEST.txt`` at it.

    The folder is assembled under a temporary name, checked, and only then swapped in, so a
    guard failure leaves no folder behind. ``source_frames`` replaces reading the store
    (tests).
    """
    from etfp import build, export

    root = build.resolve_root(store, root)
    now = now.astimezone(IST) if now is not None else now_ist()
    out_root = Path(out_root) if out_root is not None else root / "exports" / "dataset"
    repo_dir = repo_dir if repo_dir is not None else paths.project_root()
    cfg = load_dataset_config(config_path)

    if source_frames is None:
        if not store.has_table("etf_day"):
            raise FileNotFoundError("etf_day has not been built; run `etfp build` first")
        source_frames = _source_frames(store, root, cfg)

    frames: dict[str, pd.DataFrame] = {}
    skipped: dict[str, str] = {}
    removed: dict[str, int] = {}
    for name in cfg.tables:
        df = source_frames.get(name)
        if df is None:
            skipped[name] = "table not built"
            continue
        selected, n_removed = select_table(name, df, cfg)  # guard 1 (runs even if empty)
        removed[name] = n_removed
        if selected.empty:
            skipped[name] = "no rows yet"
            continue
        frames[name] = selected
    if "etf_day" not in frames:
        raise DatasetLicenceError("etf_day has no publishable rows; nothing to export")
    assert_no_denied(frames, cfg)  # guard 2, in memory

    out_root.mkdir(parents=True, exist_ok=True)
    folder = out_root / now.date().isoformat()
    tmp = Path(tempfile.mkdtemp(prefix=f".{folder.name}.", dir=out_root))
    try:
        for name, df in frames.items():
            df.to_parquet(tmp / f"{name}.parquet", index=False, compression="zstd")
            # 15 significant digits: prints 0.2393 rather than 0.23929999999999998 (the
            # same double); Parquet keeps the exact binary value.
            df.to_csv(tmp / f"{name}.csv", index=False, float_format="%.15g")

        # Guard 2 again, on what is actually on disk.
        back = {n: pd.read_parquet(tmp / f"{n}.parquet") for n in frames}
        assert_no_denied(back, cfg)
        for n in frames:
            header = pd.read_csv(tmp / f"{n}.csv", nrows=0).columns.tolist()
            if header != list(frames[n].columns):
                raise DatasetLicenceError(f"{n}.csv header differs from the selected columns")
        csv_back = {n: pd.read_csv(tmp / f"{n}.csv", dtype=str) for n in frames}
        assert_no_denied(csv_back, cfg)

        (tmp / "DATA_DICTIONARY.md").write_text(_dictionary(frames, cfg, now), encoding="utf-8")
        (tmp / "LICENSE-DATA").write_text(_licence_text(), encoding="utf-8")

        git = export._git_info(repo_dir)
        manifest: dict[str, Any] = {
            "dataset_title": cfg.title,
            "release": now.date().isoformat(),
            "dataset_format_version": cfg.format_version,
            "schema_version": export.SCHEMA_VERSION,
            "build_time_ist": now.isoformat(timespec="seconds"),
            "code": {
                "git_commit": git["commit"],
                "git_dirty": git["dirty"],
                "package_version": __version__,
                "dataset_config_sha256": export._sha256(cfg.path) if cfg.path else None,
            },
            "licence": LICENCE_PLACEHOLDER,
            "date_range": {
                n: _date_range(df, cfg.tables[n].date_column) for n, df in frames.items()
            },
            "row_counts": {
                "tables": {n: len(df) for n, df in frames.items()},
                "etf_day_by_symbol": {
                    str(k): int(v)
                    for k, v in frames["etf_day"]["symbol"].value_counts().sort_index().items()
                },
            },
            "tables": {
                n: {
                    "sources": list(cfg.tables[n].sources),
                    "licence_status": {
                        s: cfg.sources[s]["licence_status"] for s in cfg.tables[n].sources
                    },
                    "columns": len(df.columns),
                    "rows_removed_by_licence_filter": removed.get(n, 0),
                }
                for n, df in frames.items()
            },
            "tables_skipped": skipped,
            "excluded": {
                "tables": cfg.deny_tables,
                "columns": cfg.deny_columns,
                "column_patterns": list(cfg.deny_column_patterns),
                "rows": cfg.deny_rows,
                "sources": cfg.deny_sources,
            },
            "unverified_sources": sorted(
                k for k, s in cfg.sources.items() if s.get("licence_status") == "unverified"
            ),
            "files": {},
        }
        (tmp / "README.md").write_text(
            _readme(cfg, frames, skipped, removed, manifest), encoding="utf-8"
        )
        for path in sorted(tmp.iterdir()):
            manifest["files"][path.name] = {
                "sha256": export._sha256(path),
                "bytes": path.stat().st_size,
            }
        (tmp / "manifest.json").write_text(
            json.dumps(manifest, indent=2, default=str) + "\n", encoding="utf-8"
        )

        old = None
        if folder.exists():
            old = out_root / f".{folder.name}.old.{os.getpid()}"
            folder.rename(old)
        tmp.rename(folder)
        if old is not None:
            shutil.rmtree(old, ignore_errors=True)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    _atomic_text(out_root / "LATEST.txt", str(folder.resolve()) + "\n")
    if git["dirty"]:
        logger.warning("dataset built from a working tree with uncommitted changes")
    logger.info("dataset export written to %s", folder)
    return folder


__all__ = [
    "DISCLAIMER",
    "DatasetConfig",
    "DatasetLicenceError",
    "assert_no_denied",
    "export_dataset",
    "load_dataset_config",
    "parse_dataset_config",
    "select_table",
]
