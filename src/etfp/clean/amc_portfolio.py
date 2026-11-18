"""Automatic rows for ``fof_monthly`` and ``units_monthly`` from the AMCs' monthly portfolios.

Plain words
-----------
``etfp.collect.amc_portfolio`` saves the portfolio Excel files of Motilal Oswal, Mirae Asset
and Nippon India untouched and parses them into one row per holding. This module turns those
holdings into the two monthly tables that used to be typed by hand:

``fof_monthly`` (feeder funds). A feeder (fund of funds) owns only units of one ETF. Its file
    lists that single holding; ``Quantity`` is the number of ETF units the feeder held on the
    month-end date, and the GRAND TOTAL line is the feeder's own net assets (AUM). The two
    plans (Regular, Direct) of a feeder share one portfolio, but ``feeder_units_daily`` ADDS
    the units of every scheme code, so the portfolio is written ONCE, on the Regular plan's
    scheme code. (A manual row for the Direct plan with units would be counted twice.)

``units_monthly`` (units outstanding). An ETF's file gives its total net assets in rupees. One
    unit is worth the NAV, so ``units = AUM / NAV`` (worked example: AUM Rs 10,000,000,000 and
    NAV Rs 100 per unit = 100,000,000 units). The NAV is the last AMFI NAV on or before the
    month-end date (clean table ``nav_daily``); the month end can fall on a weekend, so the
    NAV date may be a day or two earlier, and a gap of more than 5 days skips the row. The
    result is an estimate: AUM and NAV are rounded, and the AUM includes small cash balances.

Numbers are as published, not adjusted for unit splits (MON100's 10:1 split, June 2021):
before the split the AUM is divided by the old, ten-times-larger NAV, so units are on the old
scale; ``config/corporate_actions.yaml`` adjusts when needed.

Manual rows win: a hand-typed row for the same key (feeder + month, or symbol + month) replaces
the automatic one, and a difference above 1% is logged as a warning. Every automatic row keeps
the raw file in ``source_file`` (``<raw path>#<sheet>``).

Parsing ~600 workbooks takes a couple of minutes, so each file's parsed rows are cached in
``data/clean/_amc_portfolio_cache/v<PARSER_VERSION>_<sha256>.parquet`` (the key is the parser
version plus the file's own SHA-256, so a changed raw file or parser is parsed again). The
cache holds nothing that cannot be rebuilt from raw.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from etfp.clean.common import CleanContext, list_raw_files
from etfp.collect import amc_portfolio as ap
from etfp.config import load_feeders

logger = logging.getLogger("etfp.clean")

CONFLICT_TOLERANCE = 0.01  # manual vs automatic: log above 1%
SAME_FILE_TOLERANCE = 0.001  # two files for one month: warn above 0.1%
MAX_NAV_AGE_DAYS = 5
CACHE_DIR_NAME = "_amc_portfolio_cache"
# Bump whenever the workbook parser (collect/amc_portfolio.py parse) changes what it returns,
# so cached rows from the old parser are not reused.
PARSER_VERSION = 1

# ETF units sometimes carry an older ISIN in old files: MON100's units were INF247L01031 before
# the 10:1 split of June 2021 and INF247L01AP3 after (seen in Motilal's Dec 2020 portfolio).
ISIN_ALIASES: dict[str, frozenset[str]] = {"MON100": frozenset({"INF247L01031"})}

FOF_COLUMNS = [
    "amfi_scheme_code", "month_end", "fund_name", "etf_symbol", "aum", "etf_units_held",
    "fof_net_flow", "subscription_status", "notice_date", "source_file",
]  # fmt: skip
UNITS_COLUMNS = ["symbol", "month_end", "aum", "nav", "units", "source", "source_file"]


# -- parsing every raw file (cached) ------------------------------------------------------


def _cache_dir(ctx: CleanContext) -> Path:
    return ctx.raw_root.parent / "clean" / CACHE_DIR_NAME


def holdings(ctx: CleanContext) -> pd.DataFrame:
    """Every holding of our target schemes in every saved portfolio file (``HOLDINGS_COLUMNS``).

    ``source_file`` is the raw path relative to the raw root. Also adds ``raw_day`` (the
    download folder date) so a later download of the same month can win.
    """
    if "amc_portfolio_holdings" in ctx.cache:
        return ctx.cache["amc_portfolio_holdings"]
    cache_dir = _cache_dir(ctx)
    frames: list[pd.DataFrame] = []
    for amc, source in ap.SOURCES.items():
        for rf in list_raw_files(ctx.raw_root, source):
            if rf.name.startswith("listing_") or not rf.name.lower().endswith((".xls", ".xlsx")):
                continue
            content = rf.path.read_bytes()
            digest = hashlib.sha256(content).hexdigest()
            cached = cache_dir / f"v{PARSER_VERSION}_{digest}.parquet"
            frame: pd.DataFrame | None = None
            if cached.exists():
                try:
                    frame = pd.read_parquet(cached)
                except Exception:  # damaged cache file: parse again
                    frame = None
            if frame is None:
                try:
                    frame = ap.parse(content, amc=amc, source_file=rf.relpath)
                except ap.PortfolioParseError as exc:
                    # A portfolio file we cannot read is named loudly, not silently skipped.
                    logger.warning("amc_portfolio: skipping %s: %s", rf.relpath, exc)
                    continue
                try:
                    cache_dir.mkdir(parents=True, exist_ok=True)
                    frame.to_parquet(cached, index=False)
                except OSError:
                    pass  # cache is optional
            frame = frame.copy()
            frame["source_file"] = rf.relpath  # same bytes may sit under several paths
            frame["raw_day"] = rf.day
            frames.append(frame)
    frames = [f for f in frames if not f.empty]
    out = (
        pd.concat(frames, ignore_index=True)
        if frames
        else pd.DataFrame({c: [] for c in [*ap.HOLDINGS_COLUMNS, "raw_day"]})
    )
    ctx.cache["amc_portfolio_holdings"] = out
    return out


def _one_per_month(frame: pd.DataFrame, keys: list[str], what: str) -> pd.DataFrame:
    """Keep one file per (keys): the one downloaded latest; warn if two files disagree."""
    if frame.empty:
        return frame
    frame = frame.sort_values([*keys, "raw_day", "source_file"])
    out = []
    for key, group in frame.groupby(keys, sort=True):
        files = group["source_file"].unique()
        last = group[group["source_file"] == files[-1]]
        if len(files) > 1:
            first = group[group["source_file"] == files[0]]
            a, b = float(first["aum_inr"].iloc[0]), float(last["aum_inr"].iloc[0])
            if abs(a - b) > SAME_FILE_TOLERANCE * abs(b):
                logger.warning(
                    "amc_portfolio: %s %s: files disagree on AUM (%s: %.0f vs %s: %.0f); "
                    "using the later download",
                    what, key, files[0], a, files[-1], b,
                )  # fmt: skip
        out.append(last)
    return pd.concat(out, ignore_index=True)


# -- fof_monthly ---------------------------------------------------------------------------


def fof_rows(ctx: CleanContext) -> pd.DataFrame:
    """Automatic ``fof_monthly`` rows: one per feeder per month end, on the Regular plan code."""
    feeders = load_feeders().feeders
    regular = {f.etf_symbol: f for f in feeders if f.plan == "regular"}
    isin_of = {e.symbol: e.isin for e in ctx.universe.etfs}
    h = holdings(ctx)
    h = h[h["role"] == "fof"]
    if h.empty:
        return pd.DataFrame({c: [] for c in FOF_COLUMNS})
    h = _one_per_month(h, ["etf_symbol", "month_end"], "feeder")
    rows = []
    for (symbol, month_end), g in h.groupby(["etf_symbol", "month_end"], sort=True):
        feeder = regular.get(str(symbol))
        if feeder is None:
            logger.warning(
                "amc_portfolio: no Regular-plan feeder in config/feeders.yaml for %s", symbol
            )
            continue
        wanted = {isin_of[str(symbol)], *ISIN_ALIASES.get(str(symbol), frozenset())}
        mine = g[g["isin"].isin(wanted)]
        file = g["source_file"].iloc[0]
        if mine.empty:
            others = sorted({str(x) for x in g["isin"].dropna()})
            logger.warning(
                "amc_portfolio: %s: the %s feeder holds ISIN(s) %s, not %s; row skipped",
                file, symbol, others or "none", sorted(wanted),
            )  # fmt: skip
            continue
        rows.append(
            {
                "amfi_scheme_code": feeder.amfi_scheme_code,
                "month_end": month_end,
                "fund_name": feeder.name,
                "etf_symbol": str(symbol),
                "aum": float(g["aum_inr"].iloc[0]),
                "etf_units_held": float(mine["quantity"].sum()),
                "fof_net_flow": None,
                "subscription_status": None,
                "notice_date": None,
                "source_file": f"{file}#{g['sheet'].iloc[0]}",
            }
        )
    return pd.DataFrame(rows, columns=FOF_COLUMNS)


# -- units_monthly -------------------------------------------------------------------------


def _nav_table(ctx: CleanContext) -> pd.DataFrame:
    """Clean ``nav_daily`` (symbol, nav_date, nav), read from data/clean; empty if absent."""
    if "amc_nav" in ctx.cache:
        return ctx.cache["amc_nav"]
    path = ctx.raw_root.parent / "clean" / "nav_daily.parquet"
    if path.exists():
        nav = pd.read_parquet(path, columns=["symbol", "nav_date", "nav"])
        nav["nav_date"] = pd.to_datetime(nav["nav_date"]).dt.date
        nav = nav.dropna(subset=["nav"]).sort_values(["symbol", "nav_date"])
    else:
        logger.warning("amc_portfolio: %s not found; units_monthly gets no automatic rows", path)
        nav = pd.DataFrame({"symbol": [], "nav_date": [], "nav": []})
    ctx.cache["amc_nav"] = nav
    return nav


def nav_on_or_before(nav: pd.DataFrame, symbol: str, day: date) -> tuple[float, date] | None:
    """Latest (nav, nav_date) for the symbol on or before ``day`` within 5 days, else None."""
    sub = nav[(nav["symbol"] == symbol) & (nav["nav_date"] <= day)]
    if sub.empty:
        return None
    last = sub.iloc[-1]
    if day - last["nav_date"] > timedelta(days=MAX_NAV_AGE_DAYS):
        return None
    return float(last["nav"]), last["nav_date"]


def units_rows(ctx: CleanContext) -> pd.DataFrame:
    """Automatic ``units_monthly`` rows: units = month-end AUM / NAV, for each of the 6 ETFs."""
    h = holdings(ctx)
    h = h[h["role"] == "etf"]
    if h.empty:
        return pd.DataFrame({c: [] for c in UNITS_COLUMNS})
    h = _one_per_month(h, ["etf_symbol", "month_end"], "ETF")
    nav = _nav_table(ctx)
    rows = []
    skipped = 0
    for (symbol, month_end), g in h.groupby(["etf_symbol", "month_end"], sort=True):
        found = nav_on_or_before(nav, str(symbol), month_end)
        if found is None:
            skipped += 1
            logger.warning(
                "amc_portfolio: no AMFI NAV for %s within %d days before %s; units row skipped",
                symbol, MAX_NAV_AGE_DAYS, month_end,
            )  # fmt: skip
            continue
        value, nav_day = found
        aum = float(g["aum_inr"].iloc[0])
        amc = g["amc"].iloc[0]
        rows.append(
            {
                "symbol": str(symbol),
                "month_end": month_end,
                "aum": aum,
                "nav": value,
                "units": aum / value,
                "source": f"amc_portfolio_{amc} AUM / AMFI NAV of {nav_day}",
                "source_file": f"{g['source_file'].iloc[0]}#{g['sheet'].iloc[0]}",
            }
        )
    if skipped:
        logger.warning("amc_portfolio: %d ETF month(s) had no NAV and were skipped", skipped)
    return pd.DataFrame(rows, columns=UNITS_COLUMNS)


# -- merging with the hand-typed rows ------------------------------------------------------


def _merge(
    manual: pd.DataFrame,
    auto: pd.DataFrame,
    keys: list[str],
    compare: list[str],
    table: str,
    columns: list[str],
) -> pd.DataFrame:
    """Manual rows win; automatic rows fill the rest. Differences above 1% are logged."""
    if auto.empty:
        return manual
    if manual.empty:
        merged = auto
    else:
        both = manual.merge(auto, on=keys, suffixes=("", "_auto"))
        for _, r in both.iterrows():
            for col in compare:
                m, a = r[col], r[f"{col}_auto"]
                if (
                    pd.notna(m)
                    and pd.notna(a)
                    and a != 0
                    and abs(m - a) > CONFLICT_TOLERANCE * abs(a)
                ):
                    logger.warning(
                        "%s: manual row %s and the AMC portfolio differ on %s: %s vs %s (%.1f%%); "
                        "manual row kept (%s vs %s)",
                        table, tuple(r[k] for k in keys), col, f"{m:,.4f}", f"{a:,.4f}",
                        100 * (m - a) / a, r["source_file"], r["source_file_auto"],
                    )  # fmt: skip
        taken = set(map(tuple, manual[keys].itertuples(index=False, name=None)))
        keep = [k not in taken for k in auto[keys].itertuples(index=False, name=None)]
        merged = pd.concat([manual, auto[keep]], ignore_index=True)
    out = merged[columns].sort_values(keys, kind="stable").reset_index(drop=True)
    logger.info(
        "%s: %d automatic row(s) from AMC portfolios, %d typed", table, len(auto), len(manual)
    )
    return out


def merge_fof(ctx: CleanContext, manual: pd.DataFrame) -> pd.DataFrame:
    """``manual`` fof_monthly rows plus automatic ones for the months the manual file lacks."""
    return _merge(
        manual, fof_rows(ctx), ["amfi_scheme_code", "month_end"],
        ["etf_units_held", "aum"], "fof_monthly", FOF_COLUMNS,
    )  # fmt: skip


def merge_units(ctx: CleanContext, manual: pd.DataFrame) -> pd.DataFrame:
    """``manual`` units_monthly rows plus automatic ones for the months the manual file lacks."""
    return _merge(
        manual, units_rows(ctx), ["symbol", "month_end"], ["units", "aum"],
        "units_monthly", UNITS_COLUMNS,
    )  # fmt: skip


# -- cross-check against NSE's own units outstanding ---------------------------------------


def crosscheck(ctx: CleanContext, units_daily: pd.DataFrame) -> pd.DataFrame:
    """AUM/NAV units vs NSE security-master ``units_outstanding`` on the same month end.

    NSE's file has one row per trading day; for a month end that is a weekend the last
    trading day on or before it is used. Columns: ``symbol``, ``month_end``, ``units_auto``,
    ``units_nse``, ``nse_date``, ``diff_pct`` ((auto - nse) / nse x 100).
    """
    auto = units_rows(ctx)
    cols = ["symbol", "month_end", "units_auto", "units_nse", "nse_date", "diff_pct"]
    if auto.empty or units_daily.empty:
        return pd.DataFrame({c: [] for c in cols})
    daily = units_daily[["symbol", "date", "units_outstanding"]].copy()
    daily["date"] = pd.to_datetime(daily["date"]).dt.date
    daily = daily.sort_values(["symbol", "date"])
    rows = []
    for _, r in auto.iterrows():
        sub = daily[(daily["symbol"] == r["symbol"]) & (daily["date"] <= r["month_end"])]
        if sub.empty or r["month_end"] - sub.iloc[-1]["date"] > timedelta(days=5):
            continue
        n = sub.iloc[-1]
        rows.append(
            {
                "symbol": r["symbol"],
                "month_end": r["month_end"],
                "units_auto": r["units"],
                "units_nse": float(n["units_outstanding"]),
                "nse_date": n["date"],
                "diff_pct": 100
                * (r["units"] - float(n["units_outstanding"]))
                / float(n["units_outstanding"]),
            }
        )
    return pd.DataFrame(rows, columns=cols)
