"""Raw files -> clean tables.

``build_clean()`` is the one entry point: it reads everything in ``data/raw/`` and
rewrites the clean tables in ``data/clean/`` (DuckDB + Parquet). It never touches
the raw files. Running it twice gives the same result; it is safe to re-run after
new files arrive or after a cleaner is fixed.

Order matters a little: ``etf_master`` goes first because the other cleaners use
its symbol list (every ETF on NSE's list plus config).

The last four cleaners read files typed by hand (``data/manual/*.csv``: events,
feeder funds, monthly units) or downloaded by hand (``data/raw/trends/``). A
missing file simply gives an empty table. They run after ``etf_master`` because
they check ETF symbols against it.

``bse_prices`` (R9, BSE daily bhavcopy) and ``slb_eligible`` / ``slb_daily`` (R8, NSE
securities lending) come last; they read raw folders that may not exist yet (an empty
table results) and, like the other cleaners, never touch the raw files.

Tables with no cleaner yet (``underlying_daily``, ``fx_daily``, ``etf_day``) are
created EMPTY if they do not exist, so readers can query them; an existing one is
never overwritten here because other importers and the feature code own them.
"""

from __future__ import annotations

import logging
from pathlib import Path

from etfp import paths
from etfp.clean import (
    band_official,
    bse_prices,
    etf_master,
    inav_snapshots,
    manual_events,
    manual_fof,
    manual_trends,
    manual_units,
    nav_daily,
    prices_daily,
    slb,
    units_daily,
)
from etfp.clean.common import CleanContext, CleanError
from etfp.config import load_etfs
from etfp.store import SCHEMAS, Store

logger = logging.getLogger("etfp.clean")

__all__ = ["CLEANERS", "CleanContext", "CleanError", "build_clean", "make_context"]

# (table, module) in the order they run. etf_master must stay first.
CLEANERS = (
    ("etf_master", etf_master),
    ("prices_daily", prices_daily),
    ("nav_daily", nav_daily),
    ("units_daily", units_daily),
    ("band_official", band_official),
    ("inav_snapshots", inav_snapshots),
    ("events", manual_events),
    ("fof_monthly", manual_fof),
    ("units_monthly", manual_units),
    ("trends_daily", manual_trends),
    ("bse_prices", bse_prices),
    ("slb_eligible", slb.ELIGIBLE),
    ("slb_daily", slb.DAILY),
)


def make_context(root: Path | None = None) -> CleanContext:
    """Context for a project root: raw files in <root>/data/raw, config in <root>/config.

    If <root>/config/etfs.yaml does not exist (a throwaway test root), the
    project's own config is used instead.
    """
    root = Path(root) if root is not None else paths.project_root()
    etfs_path = root / "config" / "etfs.yaml"
    universe = load_etfs(etfs_path) if etfs_path.exists() else load_etfs()
    return CleanContext(
        raw_root=root / "data" / "raw", universe=universe, config_file="config/etfs.yaml"
    )


def build_clean(root: Path | None = None) -> dict[str, int]:
    """Rebuild every clean table from the raw files. Returns {table: row count}.

    ``root`` is the project root (default: ``etfp.paths.project_root()``). Raw
    files are read from ``<root>/data/raw`` and tables are written to
    ``<root>/data/clean``. A raw file that cannot be read stops the build with a
    ``CleanError`` naming the table and file; tables built before it stay written.
    """
    root = Path(root) if root is not None else paths.project_root()
    ctx = make_context(root)
    store = Store(root / "data" / "clean")
    counts: dict[str, int] = {}
    for table, module in CLEANERS:
        frame = module.build(ctx)
        if table == "etf_master":
            ctx.master = frame
        counts[table] = store.write_table(table, frame)
        logger.info("clean: %s -> %d rows", table, counts[table])
    for table in SCHEMAS:
        if table not in counts and store.ensure_table(table):
            logger.info("clean: created empty table %s", table)
    return counts
