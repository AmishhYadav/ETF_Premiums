"""Slim public snapshot for a hosted dashboard: ``etfp export dashboard``.

A dashboard hosted on the internet (for example Streamlit Community Cloud) cannot read the
project's ``data/clean`` folder, which lives on the owner's Mac. This module writes a small
folder of Parquet files that the dashboard can read instead (``ETFP_DASHBOARD_DATA`` or a
``dashboard_data/`` folder next to ``dashboard/``).

What may leave the project is decided by ``config/dataset.yaml`` (DECISIONS D34, D41), the same
file that governs the open dataset. This module adds only a *narrower* choice on top: the
dashboard needs a handful of the open dataset's columns, so only those are written.

* Every column goes through ``export_dataset.select_table``: a column that the config neither
  publishes, drops nor denies stops the export (so a new column never leaks by default).
* The columns the dashboard wants must all be published columns. A column in the config's
  ``drop`` or ``deny`` list (prices, NAVs, official bands, units, fair value, anything from
  Bloomberg or Yahoo) cannot be written; the check is made again on the finished files.
* No ``source_file`` lineage columns are written: they name local raw-file paths and are not
  needed to draw the page. The full lineage is in ``etfp export dataset``.

Plain-words consequence: the hosted page shows premiums over the NAV, x_april, computed price
limits, unit changes and the events log. It does not show prices, NAVs, fair value, official
limits or intraday iNAV, because those are values copied from NSE/AMFI/AMC sites (or built from
licensed feeds) and are not republished (D41).

Nothing here uploads anything. It writes a local folder; publishing it is the owner's decision.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import pandas as pd

from etfp import paths
from etfp.alerts.rules import DISCLAIMER_SHORT
from etfp.export_dataset import (
    DatasetConfig,
    DatasetLicenceError,
    assert_no_denied,
    load_dataset_config,
    select_table,
)
from etfp.store import Store
from etfp.timeutil import IST, now_ist

SNAPSHOT_KIND = "dashboard_snapshot"  # dashboard/data.py reads this to recognise a snapshot
SNAPSHOT_VERSION = "1.0.0"

# etf_day columns the dashboard draws. All must be published columns in config/dataset.yaml.
ETF_DAY_COLUMNS = (
    "symbol",
    "date",
    "prem_raw",
    "prem_nav_known",
    "prem_band_ref",
    "x_april",
    "band_low_initial",
    "band_high_initial",
    "band_low_max",
    "band_high_max",
    "hit_up",
    "units_change",
    "creation_units_change",
)
# Every published ``dq_*`` flag is added as well (the Data quality tab counts them).

# Tables written. etf_master gives names and the control/tracked labels; events fills its tab.
TABLES = ("etf_day", "etf_master", "events")
# etf_master columns the dashboard uses (names, labels); the rest of the universe sheet is left out.
MASTER_COLUMNS = (
    "symbol", "name", "amc", "underlying_index", "underlying_market", "asset_class",
    "group_label", "tracked",
)  # fmt: skip
# Lineage-only columns left out of the snapshot (see module docstring).
DROP_LINEAGE = frozenset({"source_file", "config_file"})

README_TEXT = """\
# Dashboard snapshot

Derived-only data for the hosted premium dashboard. Written by `etfp export dashboard`.

{disclaimer}

* Tables: {tables}.
* Built {built} (IST), data through {through}.
* No prices, NAVs, fair value, official price limits, units outstanding or intraday iNAV
  (values copied from NSE, AMFI or AMC sites, or built from licensed feeds, are not
  republished; DECISIONS D41). Nothing from Bloomberg or Yahoo.
* Redistribution terms of NSE, BSE and AMFI are not confirmed (DECISIONS D34, still open): do
  not publish this folder until the owner has checked them.
"""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _slim_columns(name: str, selected: pd.DataFrame, cfg: DatasetConfig) -> list[str]:
    """Columns of the already-selected (publishable) table to keep for the dashboard."""
    if name == "etf_day":
        wanted = list(ETF_DAY_COLUMNS) + [c for c in selected.columns if c.startswith("dq_")]
        bad = [c for c in ETF_DAY_COLUMNS if c not in cfg.tables["etf_day"].columns]
        if bad:
            raise DatasetLicenceError(
                f"dashboard snapshot wants etf_day column(s) {bad} that config/dataset.yaml does "
                "not publish; the dashboard code and the licence config disagree"
            )
        return [c for c in wanted if c in selected.columns]
    if name == "etf_master":
        return [c for c in MASTER_COLUMNS if c in selected.columns]
    return [c for c in selected.columns if c not in DROP_LINEAGE]


def _read_frames(store: Store, root: Path) -> dict[str, pd.DataFrame | None]:
    """The full (unfiltered) clean tables the snapshot is cut from; None = not built."""
    from etfp import build, export  # lazy: heavy imports; build registers the schemas

    out: dict[str, pd.DataFrame | None] = {}
    with export.etf_day_schema_registered():
        out["etf_day"] = store.read_table("etf_day") if store.has_table("etf_day") else None
    out["etf_master"] = store.read_table("etf_master") if store.has_table("etf_master") else None
    bcfg = build.load_config(root)
    out["events"] = build.build_events(store, universe=bcfg.universe)
    return out


def export_dashboard(
    store: Store,
    out_dir: Path | None = None,
    *,
    root: Path | None = None,
    now: Any = None,
    config_path: Path | None = None,
    source_frames: dict[str, pd.DataFrame | None] | None = None,
) -> Path:
    """Write the snapshot folder ``out_dir`` (default ``exports/dashboard``) and return it.

    Files are written under temporary names and moved in only after every check passes, so a
    failed licence check leaves the previous snapshot untouched. ``source_frames`` replaces
    reading the store (tests).
    """
    from etfp import build, export

    root = build.resolve_root(store, root)
    now = now.astimezone(IST) if now is not None else now_ist()
    out_dir = Path(out_dir) if out_dir is not None else root / "exports" / "dashboard"
    cfg = load_dataset_config(config_path)

    if source_frames is None:
        if not store.has_table("etf_day"):
            raise FileNotFoundError("etf_day has not been built; run `etfp build` first")
        source_frames = _read_frames(store, root)

    frames: dict[str, pd.DataFrame] = {}
    for name in TABLES:
        df = source_frames.get(name)
        if df is None:
            continue
        selected, _ = select_table(name, df, cfg)  # guard 1: every column is classified
        assert_no_denied({name: selected}, cfg)  # guard 2 on the whole table, not just our slice
        frames[name] = selected.loc[:, _slim_columns(name, selected, cfg)].reset_index(drop=True)
    if "etf_day" in frames and "etf_master" in frames:  # only funds that have data rows
        have = set(frames["etf_day"]["symbol"].dropna())
        master = frames["etf_master"]
        frames["etf_master"] = master[master["symbol"].isin(have)].reset_index(drop=True)
    if "etf_day" not in frames or frames["etf_day"].empty:
        raise DatasetLicenceError("etf_day has no publishable rows; nothing to export")
    assert_no_denied(frames, cfg)  # guard 2, in memory

    out_dir.parent.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f".{out_dir.name}.", dir=out_dir.parent))
    try:
        for name, df in frames.items():
            df.to_parquet(tmp / f"{name}.parquet", index=False, compression="zstd")
        back = {n: pd.read_parquet(tmp / f"{n}.parquet") for n in frames}
        assert_no_denied(back, cfg)  # guard 2 again, on what is on disk

        dates = pd.to_datetime(frames["etf_day"]["date"])
        through = dates.max().date().isoformat()
        unverified = sorted(
            k for k, s in cfg.sources.items() if s.get("licence_status") == "unverified"
        )
        git = export._git_info(paths.project_root())
        manifest: dict[str, Any] = {
            "kind": SNAPSHOT_KIND,
            "snapshot_version": SNAPSHOT_VERSION,
            "schema_version": export.SCHEMA_VERSION,
            "built_ist": now.isoformat(timespec="seconds"),
            "data_through": through,
            "git_commit": git["commit"],
            "git_dirty": git["dirty"],
            "disclaimer": DISCLAIMER_SHORT,
            "rows": {n: len(df) for n, df in frames.items()},
            "columns": {n: list(df.columns) for n, df in frames.items()},
            "symbols": int(frames["etf_day"]["symbol"].nunique()),
            "not_included": (
                "prices, NAVs, fair value, official price limits, units outstanding, intraday "
                "iNAV, anything from Bloomberg or Yahoo (config/dataset.yaml, DECISIONS D41)"
            ),
            "licence_checks_open": unverified,
            "dataset_config_sha256": export._sha256(cfg.path) if cfg.path else None,
            "files": {},
        }
        (tmp / "README.md").write_text(
            README_TEXT.format(
                disclaimer=DISCLAIMER_SHORT,
                tables=", ".join(frames),
                built=now.strftime("%Y-%m-%d %H:%M"),
                through=through,
            ),
            encoding="utf-8",
        )
        for path in sorted(tmp.iterdir()):
            manifest["files"][path.name] = {"sha256": _sha256(path), "bytes": path.stat().st_size}
        (tmp / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

        # Move in: replace each file, then remove files of an older snapshot that are gone now.
        for path in tmp.iterdir():
            os.replace(path, out_dir / path.name)  # same file system, so atomic per file
        for old in out_dir.glob("*.parquet"):
            if old.name not in manifest["files"]:
                old.unlink()
    finally:
        for left in tmp.iterdir() if tmp.exists() else []:
            left.unlink(missing_ok=True)
        if tmp.exists():
            tmp.rmdir()
    return out_dir
