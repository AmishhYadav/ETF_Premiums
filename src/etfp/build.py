"""Build the analysis table ``etf_day`` (SPEC 8, REQUESTS R1-R3, R9, R10, R12, R14).

``build_all()`` = ``build_clean`` (raw -> clean tables) then ``build_etf_day`` (clean tables ->
one row per ETF per NSE trading day on which the ETF has a price) then write ``etf_day``.
``etf_day`` is rebuilt from scratch every time and never edited by hand.

How one row is made, in plain words
-----------------------------------
1. Prices come from the NSE bhavcopy. A split (MON100 10:1 on 2021-06-17) makes old prices
   look 10x too high, so prices and NAVs are multiplied by an adjustment factor that puts
   every date on today's unit scale. The raw price is kept in ``*_raw`` columns.
2. NAV timing: which NAV could anyone know at 15:30 IST on that day? (``nav_align``, D10).
3. Fair value: that NAV moved to 15:30 IST with the index future and the rupee rate.
   Inputs come from Bloomberg when present for that day, else from free Yahoo bars, else the
   row falls back to NAV and ``fv_method`` says ``nav_only``.
4. Premiums, the computed price band, the OFFICIAL band from NSE's security master, flags for
   touching the limits, the ceiling on the premium ``cap_pi`` (R10) and the inferred band base.
5. Units outstanding (carried forward from the latest security-master file on or before the
   day), creation events, turnover, feeder float, returns and data-quality flags.

Nothing here reads the clock. Every number keeps a pointer to its raw file where one exists
(``source_file``, ``nav_*_source_file``, ``band_source_file``, ``units_source_file``,
``fv_input_files``).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from functools import cache
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from etfp import calendars, paths
from etfp.clean import build_clean, symbol_changes
from etfp.clean.common import list_raw_files, parse_file
from etfp.clean.manual_fof import feeder_units_daily
from etfp.collect import bloomberg, yahoo_intraday
from etfp.config import (
    BandRules,
    Etf,
    EtfUniverse,
    TrendsTerms,
    load_etfs,
    load_rules,
    load_trends_terms,
)
from etfp.corporate_actions import (
    CorporateActions,
    adjust_values,
    adjustment_factor,
    load_corporate_actions,
)
from etfp.export import ETF_DAY_COLUMNS, etf_day_schema_registered
from etfp.features import (
    attention,
    bands,
    beta,
    fair_value,
    limit_days,  # importing registers the limit_day schema
    nav_align,
    premiums,
    quality,
    units,
    valve,  # importing registers the valve_monthly schema
)
from etfp.store import Store, coerce, get_schema

logger = logging.getLogger("etfp.build")

RATIONED_THRESHOLD = 0.02
"""``rationed``: log premium above this (and no recent creation)."""
RATIONED_LOOKBACK = 20
FEEDER_LAG_DAYS = 10
"""A feeder's monthly portfolio is public about 10 days after month end (SEBI), so it is used
only from then on (no look-ahead)."""
MONTHLY_UNITS_MAX_AGE_DAYS = 45
"""A monthly AUM/NAV unit estimate is used for at most this many days after its month end."""
FX_NAV_GRACE_SECONDS = 3600
"""The rate 'at NAV time' is searched from the underlying market's open to one hour after its
close (the exact time each fund house uses is unverified, D10)."""

_YAHOO_FUTURES = {"NQ": "NQ=F", "ES": "ES=F"}
_FX_NAMES = {"USD": ("USDINR", "USDINR=X"), "HKD": ("HKDINR", "HKDINR=X")}  # (Bloomberg, Yahoo)


# ==========================================================================
# Configuration for one build
# ==========================================================================
@dataclass(frozen=True)
class BuildConfig:
    """Everything configurable a build reads besides the clean tables."""

    root: Path
    universe: EtfUniverse
    rules: BandRules
    actions: CorporateActions
    trends_terms: TrendsTerms | None = None  # R15: which search terms belong to which ETF

    @property
    def raw_root(self) -> Path:
        return self.root / "data" / "raw"


def resolve_root(store: Store, root: Path | None = None) -> Path:
    """Project root for a store: given, else ``<root>/data/clean`` -> ``<root>``, else default."""
    if root is not None:
        return Path(root)
    if store.dir.name == "clean" and store.dir.parent.name == "data":
        return store.dir.parent.parent
    return paths.project_root()


def load_config(root: Path) -> BuildConfig:
    """Config files under ``<root>/config``; a missing file falls back to the project's own
    (same rule as ``etfp.clean.make_context``, so throwaway test roots work)."""
    cfg = root / "config"

    def pick(name: str) -> Path | None:
        path = cfg / name
        return path if path.exists() else None

    return BuildConfig(
        root=root,
        universe=load_etfs(pick("etfs.yaml")),
        rules=load_rules(pick("rules.yaml")),
        actions=load_corporate_actions(pick("corporate_actions.yaml")),
        trends_terms=load_trends_terms(pick("trends_terms.yaml")),
    )


# ==========================================================================
# Underlying inputs: sorted time series with "value at a moment" lookups
# ==========================================================================
def _epoch(ts: Any) -> int:
    """Whole seconds since 1970 (UTC) of a timezone-aware datetime or Timestamp."""
    return int(pd.Timestamp(ts).timestamp())


@cache
def _close_s(market: str, day: date) -> int | None:
    try:
        return _epoch(calendars.market_close(market, day))  # type: ignore[arg-type]
    except ValueError:
        return None


@cache
def _open_s(market: str, day: date) -> int | None:
    try:
        return _epoch(calendars.market_open(market, day))  # type: ignore[arg-type]
    except ValueError:
        return None


@dataclass(frozen=True)
class _Series:
    """Observations sorted by ``key`` (epoch seconds). Bloomberg: the observation time.
    Yahoo: the bar END time (a bar stamped 15:25 ends at 15:30, so it is the price at 15:30)."""

    key: np.ndarray
    val: np.ndarray
    file: np.ndarray

    def exact(self, at: int) -> tuple[float, str | None]:
        """The observation stamped at the same minute as ``at``."""
        idx = int(np.searchsorted(self.key, (at // 60) * 60, side="left"))
        if idx < len(self.key) and self.key[idx] // 60 == at // 60:
            return float(self.val[idx]), self.file[idx]
        return math.nan, None

    def window(self, start: int, end: int) -> tuple[float, str | None]:
        """The last observation with ``start <= time <= end``."""
        idx = int(np.searchsorted(self.key, end, side="right")) - 1
        if idx >= 0 and self.key[idx] >= start:
            return float(self.val[idx]), self.file[idx]
        return math.nan, None

    def asof(self, at: int, max_stale: int) -> tuple[float, str | None]:
        """The last observation at or before ``at`` that is at most ``max_stale`` s old."""
        idx = int(np.searchsorted(self.key, at, side="right")) - 1
        if idx >= 0 and at - self.key[idx] <= max_stale:
            return float(self.val[idx]), self.file[idx]
        return math.nan, None


@dataclass(frozen=True)
class Underlying:
    """Futures and FX data available for fair value. Either part may be empty."""

    bbg: dict[str, _Series]
    yahoo: dict[str, _Series]
    bloomberg_files: tuple[Any, ...] = ()

    @property
    def empty(self) -> bool:
        return not self.bbg and not self.yahoo


def load_underlying(raw_root: Path) -> Underlying:
    """Read Bloomberg CSVs and Yahoo bars from the raw store (both optional).

    A malformed file stops the build and names the file (project rule: fail loudly).
    """
    imported = bloomberg.import_directory(raw_root / bloomberg.SOURCE, on_conflict="latest")
    bbg: dict[str, _Series] = {}
    frame = imported.frame
    if len(frame):
        frame = frame[frame["field"] == "PX_LAST"].sort_values("ts_utc", kind="stable")
        for name, grp in frame.groupby("instrument", sort=True):
            bbg[str(name)] = _Series(
                np.array([_epoch(t) for t in grp["ts_utc"]], dtype="int64"),
                grp["value"].to_numpy(dtype=float),
                grp["source_file"].to_numpy(dtype=object),
            )
    bars = []
    for rf in list_raw_files(raw_root, yahoo_intraday.SOURCE):
        bars.append(
            parse_file(
                "etf_day",
                rf,
                lambda b, rf=rf: yahoo_intraday.parse(b, source_file=rf.relpath),
            )
        )
    yahoo: dict[str, _Series] = {}
    if bars:
        allbars = pd.concat(bars, ignore_index=True)
        # A 5m and a 60m bar can start at the same minute: they are different bars (D43).
        allbars = allbars.drop_duplicates(["symbol", "ts", "interval_minutes"], keep="last")
        for name, grp in allbars.groupby("symbol", sort=True):
            end = np.array(
                [
                    _epoch(t) + int(m) * 60
                    for t, m in zip(grp["ts"], grp["interval_minutes"], strict=True)
                ],
                dtype="int64",
            )
            order = np.argsort(end, kind="stable")
            yahoo[str(name)] = _Series(
                end[order],
                grp["close"].to_numpy(dtype=float)[order],
                grp["source_file"].to_numpy(dtype=object)[order],
            )
    return Underlying(bbg, yahoo, imported.files)


@dataclass(frozen=True)
class _Names:
    """Instrument names of one ETF's fair-value inputs in each data source."""

    bbg_future: str | None
    yahoo_future: str | None
    bbg_fx: str | None
    yahoo_fx: str | None


def _names_for(etf: Etf) -> _Names:
    fut = etf.futures_proxy
    fx = _FX_NAMES.get(etf.currency)
    return _Names(
        bloomberg.FUTURES_FOR_PROXY.get(fut) if fut else None,
        _YAHOO_FUTURES.get(fut) if fut else None,
        fx[0] if fx else None,
        fx[1] if fx else None,
    )


def _lookup(
    series: dict[str, _Series], name: str | None, how: str, *args: int
) -> tuple[float, str | None]:
    """One value from ``series[name]`` using ``how`` (exact, window or asof)."""
    if name is None or name not in series:
        return math.nan, None
    return getattr(series[name], how)(*args)


def _level_at_close(
    etf: Etf, data: Underlying, session: date | None, *, tier: str
) -> tuple[float, float, list[str | None]]:
    """Future and FX level at the underlying close of ``session`` from one tier.

    FX is the rate 'at NAV time': last observation between the market's open and one hour
    after its close (Bloomberg), or the last bar ended by the close (Yahoo).
    """
    names = _names_for(etf)
    if session is None:
        return math.nan, math.nan, []
    market = etf.underlying_market
    close_s, open_s = _close_s(market, session), _open_s(market, session)
    if close_s is None or open_s is None:
        return math.nan, math.nan, []
    if tier == "bloomberg":
        f, f_file = _lookup(data.bbg, names.bbg_future, "exact", close_s)
        s, s_file = _lookup(
            data.bbg, names.bbg_fx, "window", open_s, close_s + FX_NAV_GRACE_SECONDS
        )
    else:
        f, f_file = _lookup(data.yahoo, names.yahoo_future, "asof", close_s, 1800)
        s, s_file = _lookup(data.yahoo, names.yahoo_fx, "asof", close_s, 3600)
    return f, s, [f_file, s_file]


def _level_at_india_close(
    etf: Etf, data: Underlying, day: date, *, tier: str
) -> tuple[float, float, list[str | None]]:
    """Future and FX at 15:30 IST on Indian trading day ``day`` from one tier."""
    names = _names_for(etf)
    at = _close_s("IN", day)
    if at is None:
        return math.nan, math.nan, []
    if tier == "bloomberg":
        f, f_file = _lookup(data.bbg, names.bbg_future, "exact", at)
        s, s_file = _lookup(data.bbg, names.bbg_fx, "exact", at)
    else:
        f, f_file = _lookup(data.yahoo, names.yahoo_future, "asof", at, 1800)
        s, s_file = _lookup(data.yahoo, names.yahoo_fx, "asof", at, 3600)
    return f, s, [f_file, s_file]


def fair_value_inputs(
    etf: Etf, days: list[date], a_dates: list[date | None], data: Underlying
) -> pd.DataFrame:
    """The four fair-value inputs per Indian trading day, plus where they came from.

    Per row the best complete tier wins: Bloomberg if all needed inputs exist for that
    day, else Yahoo ('free'), else whatever partial values exist (the fair-value step then
    falls back to NAV). Returns columns ``futures_t, futures_a, fx_t, fx_a, source, files``.
    """
    use_fut, use_fx = fair_value.fv_terms_for(etf)
    n = len(days)
    out = {
        "futures_t": np.full(n, np.nan),
        "futures_a": np.full(n, np.nan),
        "fx_t": np.full(n, np.nan),
        "fx_a": np.full(n, np.nan),
    }
    source = ["free"] * n
    files: list[str] = [""] * n
    if not data.empty and (use_fut or use_fx):
        for i, (t, a) in enumerate(zip(days, a_dates, strict=True)):
            picked = None
            for tier in ("bloomberg", "yahoo"):
                ft, st_, f1 = _level_at_india_close(etf, data, t, tier=tier)
                fa, sa, f2 = _level_at_close(etf, data, a, tier=tier)
                if tier == "yahoo" and use_fut and _free_roll_between(etf, a, t):
                    ft = fa = math.nan  # continuous future may switch contract: no ratio
                vals = {
                    "futures_t": ft if use_fut else math.nan,
                    "futures_a": fa if use_fut else math.nan,
                    "fx_t": st_ if use_fx else math.nan,
                    "fx_a": sa if use_fx else math.nan,
                }
                needed = [
                    k
                    for k, flag in (
                        ("futures_t", use_fut),
                        ("futures_a", use_fut),
                        ("fx_t", use_fx),
                        ("fx_a", use_fx),
                    )
                    if flag
                ]
                complete = all(not math.isnan(vals[k]) for k in needed)
                cand = (tier, vals, f1 + f2, complete)
                if complete:
                    picked = cand
                    break
                if picked is None or sum(not math.isnan(v) for v in vals.values()) > sum(
                    not math.isnan(v) for v in picked[1].values()
                ):
                    picked = cand
            assert picked is not None
            tier, vals, used, _ = picked
            for key, value in vals.items():
                out[key][i] = value
            source[i] = "bloomberg" if tier == "bloomberg" else "free"
            files[i] = ";".join(sorted({f for f in used if f}))
    frame = pd.DataFrame(out)
    frame["source"] = source
    frame["files"] = files
    return frame


def _free_roll_between(etf: Etf, a: date | None, t: date) -> bool:
    """A candidate contract switch of the free continuous future between the US close of
    ``a`` and 15:30 IST on ``t`` (``fair_value.futures_roll_moments``, D43)."""
    start, end = (_close_s(etf.underlying_market, a) if a else None), _close_s("IN", t)
    if start is None or end is None:
        return False
    return fair_value.crosses_futures_roll(
        pd.Timestamp(start, unit="s", tz="UTC").to_pydatetime(),
        pd.Timestamp(end, unit="s", tz="UTC").to_pydatetime(),
    )


def _free_roll_around(etf: Etf, session: date) -> bool:
    """A candidate switch (night or expiry-day open) between the previous session's close and
    this session's close: the close-to-close future return would contain the calendar spread."""
    market = etf.underlying_market
    try:
        prev = calendars.previous_session(market, session)  # type: ignore[arg-type]
    except ValueError:
        return False
    start, end = _close_s(market, prev), _close_s(market, session)
    if start is None or end is None:
        return False
    return fair_value.crosses_futures_roll(
        pd.Timestamp(start, unit="s", tz="UTC").to_pydatetime(),
        pd.Timestamp(end, unit="s", tz="UTC").to_pydatetime(),
        include_expiry_open=True,
    )


def estimate_beta(etf: Etf, nav: pd.Series, data: Underlying) -> beta.BetaEstimate:
    """Beta of the fund against its future for the R12 window (NaN estimate if inputs are short)."""
    use_fut, use_fx = fair_value.fv_terms_for(etf)
    if not use_fut:
        return beta._failed("fund has no futures term")
    market = etf.underlying_market

    def session_of(d: date) -> date:
        return calendars.last_session_on_or_before(market, d)  # type: ignore[arg-type]

    sessions: set[date] = set()
    for d in nav.index:
        if d < beta.BETA_CUTOFF:
            try:
                sessions.add(session_of(d))
            except ValueError:
                continue
    fut: dict[date, float] = {}
    fx: dict[date, float] = {}
    for sess in sorted(sessions):
        for tier in ("bloomberg", "yahoo"):
            f, s, _ = _level_at_close(etf, data, sess, tier=tier)
            if tier == "yahoo" and _free_roll_around(etf, sess):
                continue  # this close may sit on the other side of a contract switch
            if not math.isnan(f) and (not use_fx or not math.isnan(s)):
                fut[sess] = f
                if use_fx:
                    fx[sess] = s
                break
    return beta.fund_beta(
        nav,
        pd.Series(fut, dtype="float64"),
        session_of,
        pd.Series(fx, dtype="float64") if use_fx else None,
    )


# ==========================================================================
# Small helpers
# ==========================================================================
@cache
def _prev_in(day: date) -> date | None:
    try:
        return calendars.previous_session("IN", day)
    except ValueError:
        return None


def _factors(actions: CorporateActions, symbol: str, days: list[date], kind: Any) -> np.ndarray:
    """Adjustment factor per date (puts that date's number on today's unit scale)."""
    return np.array([adjustment_factor(actions, symbol, d, kind) for d in days], dtype="float64")


def _bools(values: list[Any]) -> pd.Series:
    """Nullable boolean Series from a list of True/False/None."""
    return pd.Series(pd.array(values, dtype="boolean"))


def _as_date(value: Any) -> date | None:
    return value if isinstance(value, date) and not pd.isna(value) else None


def _empty(table: str, store: Store) -> pd.DataFrame:
    """A clean table, or an empty frame with its columns if it was never built."""
    if store.has_table(table):
        return store.read_table(table)
    return pd.DataFrame({c: [] for c in get_schema(table).column_names})


# ==========================================================================
# One symbol: prices, NAV timing, fair value
# ==========================================================================
def _price_block(etf: Etf, px: pd.DataFrame, cfg: BuildConfig) -> pd.DataFrame:
    days = list(px["date"])
    pf = _factors(cfg.actions, etf.symbol, days, "price")
    prev_pf = np.array(
        [adjustment_factor(cfg.actions, etf.symbol, _prev_in(d) or d, "price") for d in days]
    )
    px = px.reset_index(drop=True)
    out = pd.DataFrame(
        {
            "symbol": etf.symbol,
            "date": days,
            "underlying_market": etf.underlying_market,
            "asset_class": etf.asset_class,
            "group_label": etf.group,
        }
    )
    for col in ("open", "high", "low", "close", "last", "avg_price"):
        raw = px[col].astype("float64")
        out[col] = raw * pf
        if col in ("close", "high", "low", "last"):
            out[f"{col}_raw"] = raw
    out["prev_close"] = px["prev_close"].astype("float64") * prev_pf
    out["price_adj_factor"] = pf
    for col in ("volume", "value", "trades", "deliv_qty", "deliv_pct"):
        out[col] = px[col]
    out["deliv_share"] = px["deliv_pct"].astype("float64") / 100.0
    value = px["value"].astype("float64")
    trades = px["trades"].astype("float64")
    out["avg_trade"] = (value / trades).where(trades > 0)
    out["source_file"] = px["source_file"]
    return out


def _nav_block(
    etf: Etf, days: list[date], nav: pd.DataFrame, cfg: BuildConfig
) -> tuple[pd.DataFrame, pd.Series]:
    """NAV timing columns and the adjusted NAV series (index = NAV date)."""
    nav = nav.sort_values("nav_date")
    nav_dates = list(nav["nav_date"])
    factor = _factors(cfg.actions, etf.symbol, nav_dates, "nav")
    adjusted = nav["nav"].to_numpy(dtype="float64") * factor
    frame = pd.DataFrame({"nav_date": pd.to_datetime(nav_dates), "nav": adjusted})
    aligned = nav_align.align_nav(frame, days, etf.underlying_market)
    by_date = dict(zip(nav_dates, adjusted, strict=True))
    files = dict(zip(nav_dates, nav["source_file"], strict=True))

    out = aligned.drop(columns=["date"]).reset_index(drop=True)
    out["nav_known"] = out["nav_known_at_close"]
    out["nav_t1"] = [
        float(by_date.get(_prev_in(d), np.nan)) if _prev_in(d) else np.nan for d in days
    ]
    out["nav_adj_factor"] = _factors(cfg.actions, etf.symbol, days, "nav")
    out["nav_same_source_file"] = [files.get(d) for d in days]
    out["nav_known_source_file"] = [files.get(_as_date(d)) for d in out["nav_known_date"]]
    return out, pd.Series(adjusted, index=pd.Index(nav_dates), dtype="float64")


def _fv_block(
    etf: Etf, days: list[date], nav_cols: pd.DataFrame, nav_series: pd.Series, data: Underlying
) -> pd.DataFrame:
    """Fair value (beta 1) and beta fair value with their inputs."""
    use_fut, use_fx = fair_value.fv_terms_for(etf)
    a_dates = [_as_date(a) for a in nav_cols["nav_known_underlying_date"]]
    inputs = fair_value_inputs(etf, days, a_dates, data)
    fvin = inputs[["futures_t", "futures_a", "fx_t", "fx_a"]].copy()
    fvin["nav_known_at_close"] = nav_cols["nav_known_at_close"].to_numpy(dtype="float64")
    fv = fair_value.fair_value_frame(
        fvin, use_futures=use_fut, use_fx=use_fx, source=inputs["source"]
    ).reset_index(drop=True)
    fv["fv_input_files"] = inputs["files"].replace("", None)

    est = estimate_beta(etf, nav_series, data)
    n = len(days)
    if not use_fut:
        fv["fv_beta"] = fv["fv"]
        fv["fv_beta_method"] = "no_futures_term"
        fv["fv_beta_value"] = np.nan
        fv["fv_beta_se"] = np.nan
        fv["fv_beta_n"] = pd.array([pd.NA] * n, dtype="Int64")
        return fv
    scaled = beta.beta_fair_value(
        fvin["nav_known_at_close"],
        fvin["futures_t"],
        fvin["futures_a"],
        fvin["fx_t"] if use_fx else None,
        fvin["fx_a"] if use_fx else None,
        est.beta,
    )
    applied = fv["fv_method"].isin(["bloomberg", "free_futures"])
    fv_beta = pd.Series(np.nan, index=fv.index)
    fv_beta[applied] = scaled[applied]
    fallback = fv["fv_method"] == "nav_only"
    fv_beta[fallback] = fv["fv"][fallback]
    fv["fv_beta"] = fv_beta
    fv["fv_beta_method"] = "beta_scaled" if est.ok else "no_beta"
    fv["fv_beta_value"] = est.beta
    fv["fv_beta_se"] = est.se
    fv["fv_beta_n"] = pd.array([est.n_obs] * n, dtype="Int64")
    return fv


# ==========================================================================
# One symbol: premiums, bands, official band, units, feeders
# ==========================================================================
_FLAG_RENAME = {
    "high_touched_initial_upper": "hit_up",
    "high_touched_max_upper": "hit_up_max",
    "ltp_at_upper": "last_at_up",
    "low_touched_initial_lower": "hit_down",
    "low_touched_max_lower": "hit_down_max",
    "ltp_at_lower": "last_at_down",
}
_BAND_KEEP = (
    "band_base_price", "band_low_initial", "band_high_initial", "band_low_max", "band_high_max",
    "hit_up", "hit_up_max", "last_at_up", "hit_down", "hit_down_max", "last_at_down",
    "widened_up", "widened_down", "widenings_min_up", "widenings_min_down",
    "close_at_upper", "close_at_lower",
)  # fmt: skip


def _computed_band_block(etf: Etf, base: pd.DataFrame, cfg: BuildConfig) -> pd.DataFrame:
    """Computed band and R10 limit flags (all prices on today's unit scale)."""
    inp = pd.DataFrame(
        {
            "symbol": base["symbol"],
            "date": base["date"],
            "nav_t2": base["nav_t2"],
            # Both dates let the band code see "no NAV dated exactly T-2" (D38 fallback).
            "nav_t2_date": base.get("nav_t2_date"),
            "nav_t2_nav_date": base.get("nav_t2_nav_date"),
            "prev_close": base["prev_close"],
            "nav_t1": base["nav_t1"],
            "high": base["high"],
            "low": base["low"],
            "ltp": base["last"],
            "close": base["close"],
        }
    )
    framed = bands.compute_band_frame(inp, cfg.rules, {etf.symbol: etf.asset_class})
    framed = framed.rename(columns=_FLAG_RENAME)
    out = framed[list(_BAND_KEEP)].reset_index(drop=True)
    regimes, kinds = [], []
    for d in base["date"]:
        try:
            r = cfg.rules.regime_for(d)
            regimes.append(r.name)
            kinds.append(r.base)
        except ValueError:  # a date no regime covers (not a trading day)
            regimes.append(None)
            kinds.append(None)
    # The base actually used (D38): prev_close on nav_t2 days with no NAV dated T-2 in
    # the prev-close-fallback era; the rule's base when no band could be computed.
    used = framed["band_base_kind"].reset_index(drop=True)
    kinds = [u if isinstance(u, str) else k for u, k in zip(used, kinds, strict=True)]
    out.insert(0, "band_base_kind", kinds)
    out.insert(0, "regime", regimes)
    return out


def _official_band_block(
    base: pd.DataFrame, computed: pd.DataFrame, band_o: pd.DataFrame, cfg: BuildConfig
) -> pd.DataFrame:
    """Official band (R2) joined on the session it applies to, with mismatch and cap_pi."""
    tick = cfg.rules.tick_size
    sym = base["symbol"].iloc[0]
    sub = band_o[band_o["symbol"] == sym].sort_values("file_date")
    # Two files can point at one session (a file dated on a holiday): the latest file wins.
    sub = sub.drop_duplicates("session_date", keep="last")
    joined = base[["date"]].merge(sub, how="left", left_on="date", right_on="session_date")
    pf = base["price_adj_factor"].to_numpy()
    low = joined["band_low_official"].astype("float64").to_numpy() * pf
    high = joined["band_high_official"].astype("float64").to_numpy() * pf

    out = pd.DataFrame(
        {
            "band_low_official": low,
            "band_high_official": high,
            "band_low": low,
            "band_high": high,
            "band_file_date": list(joined["file_date"]),
            "band_session_date": list(joined["session_date"]),
            "band_source_file": list(joined["source_file"]),
        }
    )
    diffs_low, diffs_high, mismatch = [], [], []
    for cl, ch, ol, oh in zip(
        computed["band_low_initial"], computed["band_high_initial"], low, high, strict=True
    ):
        cmp_ = bands.compare_bands(cl, ch, ol, oh, tick=tick)
        diffs_low.append(cmp_.low_diff)
        diffs_high.append(cmp_.high_diff)
        mismatch.append(cmp_.mismatch)
    out["band_low_diff"] = pd.array(diffs_low, dtype="Float64").astype("float64")
    out["band_high_diff"] = pd.array(diffs_high, dtype="Float64").astype("float64")
    out["band_mismatch"] = _bools(mismatch)
    eps = 1e-9
    h, lo_px = base["high"].to_numpy(), base["low"].to_numpy()
    hit_up = [
        None if np.isnan(a) or np.isnan(b) else bool(a + eps >= b - tick)
        for a, b in zip(h, high, strict=True)
    ]
    hit_dn = [
        None if np.isnan(a) or np.isnan(b) else bool(a - eps <= b + tick)
        for a, b in zip(lo_px, low, strict=True)
    ]
    out["hit_up_official"] = _bools(hit_up)
    out["hit_down_official"] = _bools(hit_dn)
    return out


def implied_base_prices(
    low: float, high: float, rules: BandRules, *, pct: float = 10.0, search_ticks: int = 30
) -> list[float]:
    """Every price on the tick grid whose +/-``pct`` band rounds to exactly ``low``-``high``.

    A published band is the base price times 0.9 and 1.1, rounded to the tick (low up, high
    down), so the base can be read back from it. Example (D4): 127.74-156.12 at +/-10% gives
    exactly one base, 141.93, which is therefore the previous close. Several answers can fit
    when the band is wide relative to the tick; an empty list means no base on the grid fits
    (wrong percentage, or the rounding rule differs).
    """
    tick = Decimal(str(rules.tick_size))
    mid = (Decimal(str(low)) + Decimal(str(high))) / 2
    centre = (mid / tick).to_integral_value() * tick
    found = []
    for k in range(-search_ticks, search_ticks + 1):
        base = centre + k * tick
        if base <= 0:
            continue
        lo, hi = bands.band_limits(
            base,
            pct,
            tick=tick,
            low_mode=rules.rounding.band_low,
            high_mode=rules.rounding.band_high,
        )
        if lo == Decimal(str(low)) and hi == Decimal(str(high)):
            found.append(float(base))
    return found


def _regime_inferred_block(
    base: pd.DataFrame, official: pd.DataFrame, first_band_session: date | None, cfg: BuildConfig
) -> pd.DataFrame:
    """R10 ``regime_inferred``: which base reproduces the middle of the official band.

    Before the first security-master file there is no official band, so the test is on
    prices: if any trade fell outside 0.8-1.2 x NAV(t-2) (one tick of slack), the NAV(t-2)
    rule cannot have been in force (``not_nav_t2``); otherwise the data are consistent with
    it (``nav_t2_consistent``). Consistent is weaker than proven.
    """
    tick = cfg.rules.tick_size
    label: list[str] = []
    method: list[str | None] = []
    for i in range(len(base)):
        lo, hi = official["band_low_official"].iloc[i], official["band_high_official"].iloc[i]
        t = base["date"].iloc[i]
        if not (np.isnan(lo) or np.isnan(hi)):
            cands = {
                "nav_t2": base["nav_t2"].iloc[i],
                "prev_close": base["prev_close"].iloc[i],
                "nav_t1": base["nav_t1"].iloc[i],
            }
            label.append(bands.infer_regime_from_band(lo, hi, cands, tick=tick))
            method.append("official_band")
        elif first_band_session is None or t < first_band_session:
            nav_t2 = base["nav_t2"].iloc[i]
            high, low = base["high"].iloc[i], base["low"].iloc[i]
            if np.isnan(nav_t2) or np.isnan(high) or np.isnan(low):
                label.append("unknown")
            else:
                lo_lim, hi_lim = bands.band_limits(
                    nav_t2, 20, tick=tick,
                    low_mode=cfg.rules.rounding.band_low, high_mode=cfg.rules.rounding.band_high,
                )  # fmt: skip
                slack = Decimal(str(tick))
                outside = Decimal(str(high)) > hi_lim + slack or Decimal(str(low)) < lo_lim - slack
                label.append("not_nav_t2" if outside else "nav_t2_consistent")
            method.append("price_test")
        else:
            label.append("unknown")
            method.append(None)
    return pd.DataFrame({"regime_inferred": label, "regime_inferred_method": method})


def _units_block(
    days: list[date],
    units_d: pd.DataFrame,
    changes: pd.DataFrame,
    units_m: pd.DataFrame,
    size: int | None,
) -> pd.DataFrame:
    """Units outstanding carried forward, change events assigned to rows, monthly fallback."""
    n = len(days)
    day_arr = np.array(days, dtype="datetime64[D]")
    u = units_d.sort_values("date")
    ud = np.array(list(u["date"]), dtype="datetime64[D]")
    pos = np.searchsorted(ud, day_arr, side="right") - 1 if len(ud) else np.full(n, -1)
    have = pos >= 0
    safe = np.clip(pos, 0, None)
    units_out = pd.array(
        [u["units_outstanding"].iloc[p] if h else pd.NA for p, h in zip(safe, have, strict=True)],
        dtype="Int64",
    )
    file_date = [u["date"].iloc[p] if h else None for p, h in zip(safe, have, strict=True)]
    src_file = [u["source_file"].iloc[p] if h else None for p, h in zip(safe, have, strict=True)]
    stale = pd.array(
        [(d - f).days if f is not None else pd.NA for d, f in zip(days, file_date, strict=True)],
        dtype="Int64",
    )

    # Changes land on the first row dated on or after the file that showed them.
    chg = np.zeros(n)
    cu_chg = np.zeros(n)
    cu_known = np.ones(n, dtype=bool)
    multiple_ok = np.ones(n, dtype=bool)
    gap = np.zeros(n)
    touched = np.zeros(n, dtype=bool)
    ev = changes[changes["event_type"].notna()]
    for rec in ev.itertuples():
        idx = int(np.searchsorted(day_arr, np.datetime64(rec.date), side="left"))
        if idx >= n:
            continue
        touched[idx] = True
        chg[idx] += float(rec.units_change)
        if pd.isna(rec.creation_units_change):
            cu_known[idx] = False
        else:
            cu_chg[idx] += float(rec.creation_units_change)
        multiple_ok[idx] &= (
            bool(rec.creation_multiple_ok) if not pd.isna(rec.creation_multiple_ok) else False
        )
        gap[idx] = max(gap[idx], float(rec.days_since_prev))
    first_row = int(np.argmax(have)) if have.any() else -1
    units_change = [
        pd.NA if (not h or (i == first_row and not touched[i])) else round(c)
        for i, (h, c) in enumerate(zip(have, chg, strict=True))
    ]
    creation = [
        np.nan if (not h or (i == first_row and not touched[i]) or size is None or not k) else c
        for i, (h, c, k) in enumerate(zip(have, cu_chg, cu_known, strict=True))
    ]

    # Monthly AUM / NAV estimate for days before the first security-master file (R11).
    if "units" not in units_m.columns:
        units_m = pd.DataFrame({"month_end": [], "units": []})
    m = units_m.dropna(subset=["units"]).sort_values("month_end")
    me = np.array(list(m["month_end"]), dtype="datetime64[D]")
    mpos = np.searchsorted(me, day_arr, side="right") - 1 if len(me) else np.full(n, -1)
    units_all, units_src = [], []
    for i in range(n):
        if have[i]:
            units_all.append(float(units_out[i]))
            units_src.append("security_master")
            continue
        p = int(mpos[i])
        if p >= 0 and (days[i] - m["month_end"].iloc[p]).days <= MONTHLY_UNITS_MAX_AGE_DAYS:
            units_all.append(float(m["units"].iloc[p]))
            units_src.append("monthly_aum_nav")
        else:
            units_all.append(np.nan)
            units_src.append(None)
    cu_series = pd.Series(creation, dtype="float64")
    return pd.DataFrame(
        {
            "units_outstanding": units_out,
            "units_file_date": file_date,
            "units_stale_days": stale,
            "units_source_file": src_file,
            "units": units_all,
            "units_source": units_src,
            "units_change": pd.array(units_change, dtype="Int64"),
            "creation_units_change": cu_series,
            "creation_event": cu_series,
            "creation_multiple_ok": _bools(
                [
                    bool(ok) if t and h else None
                    for ok, t, h in zip(multiple_ok, touched, have, strict=True)
                ]
            ),
            "units_change_days_since_prev": pd.array(
                [int(g) if t else pd.NA for g, t in zip(gap, touched, strict=True)], dtype="Int64"
            ),
        }
    )


def _units_stale_flag(
    units_source: pd.Series, units_file_date: pd.Series, frozen_files: frozenset[date]
) -> pd.Series:
    """``dq_units_stale``: True where ``units`` is a security-master count taken from a file in
    a frozen stretch (``valve.frozen_file_dates``); False for every other row, including rows
    whose units come from the monthly AUM/NAV estimate or are missing."""
    return _bools(
        [
            bool(src == "security_master" and f in frozen_files)
            for src, f in zip(units_source, units_file_date, strict=True)
        ]
    )


def _bse_block(days: list[date], bse: pd.DataFrame, price_factor: pd.Series) -> pd.DataFrame:
    """BSE close, units, rupees and trades on the NSE row's date (empty where BSE has no row).

    The BSE close gets the same split adjustment as the NSE prices (``price_adj_factor``),
    so ``close`` and ``bse_close`` are on the same unit scale. Units and rupees are left as
    published, like the NSE ``volume`` and ``value``.
    """
    by_day = bse.drop_duplicates("date").set_index("date").reindex(days)
    return pd.DataFrame(
        {
            "bse_close": by_day["close"].astype("float64").to_numpy() * price_factor.to_numpy(),
            "bse_volume": pd.array(by_day["volume"].to_numpy(), dtype="Int64"),
            "bse_value": by_day["value"].astype("float64").to_numpy(),
            "bse_trades": pd.array(by_day["trades"].to_numpy(), dtype="Int64"),
        },
        index=price_factor.index,
    )


def _feeder_block(symbol: str, days: list[date], fof: pd.DataFrame) -> pd.DataFrame:
    """Feeder-held ETF units (lagged so only published figures count) for one symbol."""
    sub = fof[fof["etf_symbol"] == symbol] if len(fof) else fof
    res = feeder_units_daily(sub, days, lag_days=FEEDER_LAG_DAYS)
    if res.empty:
        return pd.DataFrame(
            {"feeder_units": np.full(len(days), np.nan), "feeder_units_asof": [None] * len(days)}
        )
    res = res[res["symbol"] == symbol].reset_index(drop=True)
    return pd.DataFrame(
        {
            "feeder_units": res["feeder_units"].astype("float64"),
            "feeder_units_asof": list(res["oldest_month_end"]),
        }
    )


# ==========================================================================
# Assemble
# ==========================================================================
def _symbol_frame(
    etf: Etf,
    px: pd.DataFrame,
    tables: dict[str, pd.DataFrame],
    changes: pd.DataFrame,
    aum_flags: dict[date, bool | None],
    first_band_session: date | None,
    cfg: BuildConfig,
    data: Underlying,
    frozen_files: frozenset[date] = frozenset(),
) -> pd.DataFrame:
    sym = etf.symbol
    px = px.sort_values("date").reset_index(drop=True)
    days = list(px["date"])
    prices = _price_block(etf, px, cfg)
    nav_cols, nav_series = _nav_block(
        etf, days, tables["nav_daily"][lambda d: d["symbol"] == sym], cfg
    )
    fv_cols = _fv_block(etf, days, nav_cols, nav_series, data)
    df = pd.concat([prices, nav_cols, fv_cols], axis=1)

    prem = premiums.premium_columns(df)
    df = pd.concat([df, prem], axis=1)
    df["prem_known"] = df["prem_nav_known"]
    df["pi"] = df["log_prem_fv"]

    computed = _computed_band_block(etf, df, cfg)
    official = _official_band_block(df, computed, tables["band_official"], cfg)
    df = pd.concat([df, computed, official], axis=1)
    df["cap_pi"] = premiums.log_ratio(df["band_high_official"], df["fv"])
    df["cap_pi_computed"] = premiums.log_ratio(df["band_high_initial"], df["fv"])
    df = pd.concat([df, _regime_inferred_block(df, official, first_band_session, cfg)], axis=1)

    units_d = tables["units_daily"][lambda d: d["symbol"] == sym]
    ch = changes[changes["symbol"] == sym]
    units_m = tables["units_monthly"][lambda d: d["symbol"] == sym]
    df = pd.concat([df, _units_block(days, units_d, ch, units_m, etf.creation_unit_size)], axis=1)

    vol = df["volume"].astype("float64")
    units_f = df["units"].astype("float64")
    bse = _bse_block(
        days, tables["bse_prices"][lambda d: d["symbol"] == sym], df["price_adj_factor"]
    )
    df = pd.concat([df, bse], axis=1)
    df["turnover_nse"] = premiums.turnover_units(vol, units_f)
    # R9: units traded on both exchanges / units outstanding. A day with no BSE row counts BSE
    # as zero in the sum but is flagged NSE-only, so the understated days can be dropped.
    both = vol + df["bse_volume"].astype("float64").fillna(0.0)
    df["turnover"] = premiums.turnover_units(both, units_f)
    df["turnover_is_nse_only"] = _bools([bool(x) for x in df["bse_volume"].isna()])

    df = pd.concat([df, _feeder_block(sym, days, tables["fof_monthly"])], axis=1)
    free_units = units_f - df["feeder_units"]
    df["float_net"] = (free_units * df["nav_known_at_close"]).where(df["feeder_units"].notna())

    # R15: Google Trends attention for this ETF's configured terms (NaN when not all exist).
    terms = cfg.trends_terms
    df["attention"] = attention.attention_series(
        tables["trends_daily"],
        terms.terms_for(sym) if terms is not None else [],
        days,
        geo=terms.geo if terms is not None else "India",
    )

    df["excess_rupees"] = vol * (df["avg_price"] - 1.2 * df["nav_t2"]).clip(lower=0)

    df["dq_nav_same_date_missing"] = _bools([bool(x) for x in df["nav_same_date"].isna()])
    df["dq_nav_t2_missing"] = _bools([bool(x) for x in df["nav_t2"].isna()])
    # D51: the security-master file behind ``units`` sits in a stretch where NSE did not refresh
    # IssdCptl for more than 4 weeks, so the count may be out of date. Flag only; the figure is
    # never replaced (the AMC monthly units are a different measure).
    df["dq_units_stale"] = _units_stale_flag(
        df["units_source"], df["units_file_date"], frozen_files
    )
    df["dq_units_aum_mismatch"] = _bools(
        [aum_flags.get((pd.Timestamp(d) + pd.offsets.MonthEnd(0)).date()) for d in days]
    )
    return df


def _fx_ref_on(days: pd.Series, fx_ref: pd.DataFrame, pair: str) -> pd.Series:
    """FBIL reference rate dated exactly on each day (NaN if none; never carried forward)."""
    sub = fx_ref[fx_ref["pair"] == pair] if len(fx_ref) else fx_ref
    if sub.empty:
        return pd.Series(np.nan, index=days.index, dtype="float64")
    by_day = dict(zip(sub["date"], sub["rate"].astype("float64"), strict=True))
    return pd.Series([by_day.get(d, np.nan) for d in days], index=days.index, dtype="float64")


def _add_rationed(df: pd.DataFrame) -> pd.Series:
    """``rationed``: pi above the threshold and no creation in the previous 20 rows."""
    out = pd.Series(pd.NA, index=df.index, dtype="boolean")
    for _, grp in df.groupby("symbol", sort=False):
        created = (grp["units_change"].astype("float64").fillna(0) > 0).astype(float)
        recent = created.shift(1).rolling(RATIONED_LOOKBACK, min_periods=1).max().fillna(0) > 0
        value = (grp["pi"] > RATIONED_THRESHOLD) & ~recent
        known = grp["pi"].notna() & grp["units_outstanding"].notna()
        out.loc[grp.index] = value.where(known).astype("boolean")
    return out


def build_etf_day(
    store: Store,
    *,
    root: Path | None = None,
    config: BuildConfig | None = None,
    underlying: Underlying | None = None,
) -> pd.DataFrame:
    """The analysis table from the clean tables (does not write it; see ``build_all``).

    One row per configured ETF per NSE trading day on which it has a price, columns exactly
    as documented in ``etfp.export.ETF_DAY_COLUMNS``.
    """
    root = resolve_root(store, root)
    cfg = config or load_config(root)
    data = underlying if underlying is not None else load_underlying(cfg.raw_root)
    prices = store.read_table("prices_daily")
    tables = {
        name: _empty(name, store)
        for name in (
            "nav_daily",
            "units_daily",
            "band_official",
            "units_monthly",
            "fof_monthly",
            "bse_prices",
            "trends_daily",
            "fx_ref_daily",  # FBIL reference rates -> usdinr_ref (D43)
        )
    }
    sizes = units.creation_unit_sizes(cfg.universe)

    ud = tables["units_daily"]
    if len(ud):
        changes = units.units_changes(
            ud[["symbol", "date", "units_outstanding", "source_file"]],
            sizes,
            histories=units.creation_unit_histories(cfg.universe),
        )
    else:
        changes = pd.DataFrame({c: [] for c in units.CHANGE_COLUMNS})

    aum_flags: dict[str, dict[date, bool | None]] = {}
    um = tables["units_monthly"].dropna(subset=["aum", "nav"])
    if len(um) and len(ud):
        monthly = um.rename(columns={"aum": "aum_rs"})[["symbol", "month_end", "aum_rs", "nav"]]
        check = units.units_aum_crosscheck(ud, monthly)
        for rec in check.itertuples():
            flag = (
                None if pd.isna(rec.flag_units_aum_mismatch) else bool(rec.flag_units_aum_mismatch)
            )
            aum_flags.setdefault(rec.symbol, {})[rec.month_end] = flag

    # D51: file dates inside a frozen stretch of the security master (all funds together).
    frozen = frozenset(valve.frozen_file_dates(ud)) if len(ud) else frozenset()

    bo = tables["band_official"]
    first_band = min(bo["session_date"]) if len(bo) else None

    frames = []
    for etf in cfg.universe.etfs:
        px = prices[prices["symbol"] == etf.symbol]
        if px.empty:
            continue
        frames.append(
            _symbol_frame(
                etf,
                px,
                tables,
                changes,
                aum_flags.get(etf.symbol, {}),
                first_band,
                cfg,
                data,
                frozen,
            )
        )
    if not frames:
        return pd.DataFrame({c: [] for c in ETF_DAY_COLUMNS})
    df = pd.concat(frames, ignore_index=True)
    df = premiums.add_return_columns(
        df, price_col="close", nav_col="nav_known_at_close", fv_col="fv"
    )
    # SPEC 12 / D53: a duplicate (symbol, date) stops the build; the other checks only flag.
    quality.require_no_duplicates(df)
    df = quality.add_quality_flags(
        df, markets={e.symbol: e.underlying_market for e in cfg.universe.etfs}
    )
    quality.log_build_summary(df)
    df["rationed"] = _add_rationed(df)
    df["usdinr_ref"] = _fx_ref_on(df["date"], tables["fx_ref_daily"], "USDINR")

    missing = [c for c in ETF_DAY_COLUMNS if c not in df.columns]
    extra = [c for c in df.columns if c not in ETF_DAY_COLUMNS]
    if missing or extra:
        raise ValueError(
            f"etf_day columns and ETF_DAY_COLUMNS disagree: missing {missing}, undocumented {extra}"
        )
    return df[list(ETF_DAY_COLUMNS)].sort_values(["symbol", "date"]).reset_index(drop=True)


# ==========================================================================
# Events (R1: an event row whenever units change, beside the manual events)
# ==========================================================================
UNITS_EVENT_TYPE = "units_change"


def build_unit_tables(store: Store, universe: EtfUniverse) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(per-file unit changes, one row per change with its event columns)."""
    ud = _empty("units_daily", store)
    if ud.empty:
        return (
            pd.DataFrame({c: [] for c in units.CHANGE_COLUMNS}),
            pd.DataFrame({c: [] for c in units.EVENT_COLUMNS}),
        )
    changes = units.units_changes(
        ud[["symbol", "date", "units_outstanding", "source_file"]],
        units.creation_unit_sizes(universe),
        histories=units.creation_unit_histories(universe),
    )
    return changes, units.unit_change_events(changes)


def unit_events_as_events(unit_events: pd.DataFrame) -> pd.DataFrame:
    """Unit changes in the ``events`` table layout, type ``units_change``.

    ``verified`` is True when the change is a whole number of creation units (the exchange
    file itself is the primary document, and the basket check supports it); False when the
    size is unknown or the change is not a whole number. Category and direction are left
    empty on purpose: they are set by hand before prices are looked at (R16).
    """
    cols = get_schema("events").column_names
    if unit_events.empty:
        return pd.DataFrame({c: [] for c in cols})
    rows = []
    for e in unit_events.itertuples():
        cu = (
            ""
            if pd.isna(e.creation_units_change)
            else f"; {e.creation_units_change:+.2f} creation units"
        )
        rows.append(
            {
                "event_id": f"{UNITS_EVENT_TYPE}:{e.symbol}:{e.date.isoformat()}",
                "date": e.date,
                "ts_ist": pd.NaT,
                "type": UNITS_EVENT_TYPE,
                "category": None,
                "direction": None,
                "symbols": e.symbol,
                "first_tradable_session": None,
                "description": (
                    f"{e.symbol}: units outstanding {int(e.units_prev):,} -> {int(e.units_new):,} "
                    f"({int(e.units_change):+,}{cu}); {e.event_type}; {int(e.days_since_prev)} "
                    "days since the previous security-master file"
                ),
                "url": None,
                "source_primary": e.source_file,
                "verified": bool(e.creation_multiple_ok)
                if pd.notna(e.creation_multiple_ok)
                else False,
                "source_file": e.source_file,
            }
        )
    return pd.DataFrame(rows, columns=cols)


def build_events(store: Store, *, universe: EtfUniverse | None = None) -> pd.DataFrame:
    """Manual events plus derived unit-change events, in the ``events`` table layout."""
    universe = universe or load_config(resolve_root(store)).universe
    schema = get_schema("events")
    manual = _empty("events", store)
    _, unit_ev = build_unit_tables(store, universe)
    derived = unit_events_as_events(unit_ev)
    parts = [coerce(f, schema) for f in (manual, derived) if len(f)]
    if not parts:
        return coerce(pd.DataFrame({c: [] for c in schema.column_names}), schema)
    out = pd.concat(parts, ignore_index=True)
    out = out.drop_duplicates("event_id", keep="first")
    return coerce(out.sort_values(["date", "event_id"], kind="stable"), schema).reset_index(
        drop=True
    )


def build_valve_monthly(store: Store, cfg: BuildConfig) -> pd.DataFrame:
    """The ``valve_monthly`` table (M5, D33) from units_daily and units_monthly.

    Monthly AUM/NAV units are put on today's unit scale first (a 10:1 split multiplies a
    pre-split count by 10), so a split is not mistaken for a creation. The daily
    security-master counts start in 2024, after the only split, and are used as published.
    """
    ud = _empty("units_daily", store)
    um = _empty("units_monthly", store)
    if len(um):
        um = um.copy()
        um["units"] = adjust_values(cfg.actions, um, value="units", kind="units", day="month_end")
        # Days between the month-end and the date of the NAV used (D48). The explicit
        # ``nav_date`` column is the source (D51); only rows that lack it fall back to the old
        # text "AUM / AMFI NAV of 2022-04-29" in ``source`` (tables built before D51).
        navd = (
            pd.to_datetime(um["nav_date"])
            if "nav_date" in um.columns
            else pd.Series(pd.NaT, index=um.index)
        )
        text = pd.to_datetime(um["source"].str.extract(r"AMFI NAV of (\d{4}-\d{2}-\d{2})")[0])
        navd = navd.fillna(text)
        um["nav_lag_days"] = (pd.to_datetime(um["month_end"]) - navd).dt.days
    frame = valve.valve_monthly(
        ud,
        um,
        units.creation_unit_sizes(cfg.universe),
        [e.symbol for e in cfg.universe.etfs],
        histories=units.creation_unit_histories(cfg.universe),
    )
    return coerce(frame, get_schema(valve.TABLE))


def build_all(root: Path | None = None) -> dict[str, Any]:
    """Raw -> clean tables -> ``etf_day``. Returns row counts.

    ``root`` is the project root (default: the real project). Safe to re-run: every table
    is rebuilt from the raw files.
    """
    root = Path(root) if root is not None else paths.project_root()
    counts = build_clean(root)
    store = Store(root / "data" / "clean")
    with etf_day_schema_registered():
        frame = build_etf_day(store, root=root)
        n = store.write_table("etf_day", frame)
    logger.info("etf_day: %d rows", n)
    # R13: intraday diagnostics for days a price limit was touched.
    imported = bloomberg.import_directory(
        root / "data" / "raw" / bloomberg.SOURCE, on_conflict="latest"
    )
    n_ld = store.write_table(
        "limit_day", limit_days.build_limit_days(store, frame, bloomberg_frame=imported.frame)
    )
    logger.info("limit_day: %d rows", n_ld)
    n_vm = store.write_table(valve.TABLE, build_valve_monthly(store, load_config(root)))
    logger.info("valve_monthly: %d rows", n_vm)
    # D45: renames NSE lists that the config does not map (already logged as warnings).
    unmapped = symbol_changes.warnings_in(store.read_table(symbol_changes.TABLE))
    return {
        "clean": counts,
        "etf_day": n,
        "limit_day": n_ld,
        "valve_monthly": n_vm,
        "symbol_change_warnings": [
            f"{r.etf_symbol}: {r.detail}" for r in unmapped.itertuples(index=False)
        ],
    }


__all__ = [
    "BuildConfig",
    "Underlying",
    "build_all",
    "build_etf_day",
    "build_events",
    "build_unit_tables",
    "build_valve_monthly",
    "fair_value_inputs",
    "implied_base_prices",
    "load_config",
    "load_underlying",
    "unit_events_as_events",
]
