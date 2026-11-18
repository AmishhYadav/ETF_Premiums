"""Shared reader for the AMFI NAV files, used by nav_daily and feeder_nav_daily.

The files are big (about 2.5 MB each, 130+ of them, every mutual fund in India), so each
is parsed once per build and only the rows of the scheme codes we use are kept in
``ctx.cache``: the ETFs of ``config/etfs.yaml`` and the feeder funds of ``config/feeders.yaml``.
Each cleaner then filters to its own codes, exactly as before, so its output does not change.
"""

from __future__ import annotations

import pandas as pd

from etfp.clean.common import CleanContext, RawFile, list_raw_files, parse_file
from etfp.collect import amfi_nav
from etfp.config import load_feeders
from etfp.paths import config_dir

_CACHE_KEY = "amfi_nav_files"
KINDS = (
    (amfi_nav.SOURCE_HISTORY, "history", amfi_nav.parse_history),
    (amfi_nav.SOURCE_NAVALL, "navall", amfi_nav.parse_navall),
)


def feeders_for(ctx: CleanContext):
    """The feeder config next to this raw store (tests use a scratch root), else the repo's."""
    config_path = ctx.raw_root.parent.parent / "config" / "feeders.yaml"
    return load_feeders(config_path if config_path.exists() else config_dir() / "feeders.yaml")


def load(ctx: CleanContext, table: str) -> list[tuple[RawFile, str, pd.DataFrame]]:
    """(raw file, kind, parsed rows of our scheme codes) for every AMFI file, oldest first."""
    if _CACHE_KEY in ctx.cache:
        return ctx.cache[_CACHE_KEY]
    codes = {e.amfi_scheme_code for e in ctx.universe.etfs}
    codes |= {f.amfi_scheme_code for f in feeders_for(ctx).feeders}
    out: list[tuple[RawFile, str, pd.DataFrame]] = []
    for source, kind, parser in KINDS:
        for rf in list_raw_files(ctx.raw_root, source):
            frame = parse_file(table, rf, lambda b, rf=rf, p=parser: p(b, source_file=rf.relpath))
            out.append((rf, kind, frame[frame["scheme_code"].isin(list(codes))]))
    ctx.cache[_CACHE_KEY] = out
    return out
