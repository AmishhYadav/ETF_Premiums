"""limit_day (request R13): what happened inside the day when a price limit was touched.

Plain-words background
----------------------
Every day the exchange sets a price corridor ("band") around a base price. A trade cannot
happen above the UPPER limit or below the LOWER limit. When demand is huge and the fund cannot
create new units (our premium story), the price runs up to the upper limit and stays there:
buyers queue, nobody sells. Two very different days look the same in the daily high/low:

* TOUCHED: the price reached the limit, but sellers were still around (the sell side of the
  order book was not empty). The market cleared.
* LOCKED: the price sits at the upper limit and the SELL side of the book is empty: nobody is
  willing to sell at any allowed price. Demand is rationed by the limit. (Mirror image for the
  lower limit: price at the lower limit and the BUY side empty.)

The daily table cannot tell these apart; intraday data can. This module builds one row per
ETF-day on which a limit was touched, from

* the 15-minute recorder snapshots (``inav_snapshots``: last price, total buy/sell quantity,
  order-book depth), and
* Bloomberg 1-minute bars of the ETF (``<SYMBOL>.1M``, see ``docs/bloomberg_template.md``) when
  they were imported. Bars are preferred for the PRICE timeline (touch time, minutes at the
  limit); the order-book quantities exist only in the snapshots, so they are always read there.

How precise is it?  ``obs_source`` and ``sample_interval_min`` say so in every row:
``snapshots_15m`` means the price was seen about every 15 minutes, so the first-touch time can
be up to one interval late and minutes-at-limit is an estimate; ``bloomberg_1m`` means minute
precision; ``none`` means no intraday data exists for that day (the day is in the table only
because the daily high/low touched a limit; every intraday column is then empty).

Rules used (from ``config/rules.yaml``, equity ETFs)
----------------------------------------------------
* Initial band: base +/-10%. A trade at 9.90% from the base starts a cooling-off (15 minutes,
  5 in the last half hour), after which the band widens by 5% of the base, on the side that
  moved only, at most twice per side. So the upper limits are base x 1.10, 1.15, 1.20 (hand
  example, base 100.00: 110.00, 115.00, 120.00) and the lower ones 90.00, 85.00, 80.00.
* "Touched" = within one tick (0.01 rupee) of the limit in force (R10).
* "Limit in force" = the lowest limit of that ladder that no price seen so far today has gone
  beyond. A price above 110.00 proves the band widened to at least 115.00, so afterwards 110.00
  is no longer "the limit". What cannot be seen: a widening that happened while the price sat
  below the new limit (no trade above the old one). That is why ``widenings_*`` is a MINIMUM
  count ("fewest widenings consistent with the prices seen"). ``trigger_hit_*`` says whether
  a trade reached the 9.90% trigger, meaning at least one widening was due even if no price
  went beyond the old limit.
* Widenings also use the day's high / low from ``etf_day`` (the true extreme), not only the
  intraday observations, so they are filled even on days with no intraday data.

Missing data is never zero: a quantity that was not recorded is empty, and ``locked_*`` is
empty when no snapshot at the limit had a known opposite-side quantity.

All times are IST, all prices are on the ``etf_day`` unit scale (adjusted for later splits).
The function only builds the frame; ``etfp.build`` writes it with the store.
"""

from __future__ import annotations

import itertools
import json
import math
from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any, Literal

import pandas as pd

from etfp.collect import bloomberg
from etfp.config import BandRules, load_rules
from etfp.features import bands
from etfp.store import SCHEMAS, Column, Store, TableSchema, coerce, register_schema
from etfp.timeutil import IST

TABLE = "limit_day"
SESSION_OPEN = time(9, 15)
SESSION_CLOSE = time(15, 30)
"""Regular NSE session (IST). Snapshots before the open show the pre-open price; after the
close, the closing snapshot. Neither counts as trading time. Special evening sessions (the
Diwali Muhurat) are outside this window and are therefore reported as 'no intraday data'."""

SAME_TICK_SECONDS = 120
"""Snapshot rows (NSE ETF table, NSE quote) fetched within this many seconds of each other
are one recorder cycle, not two observations."""
SNAPSHOT_SOURCE_PRIORITY = {"nse_quote": 0, "nse_etf_api": 1}
"""When one cycle has several rows, take the price from the first of these that is present."""
MAX_UNCAPPED_LEVELS = 30
"""Widening levels examined for gold/silver, which have no cap."""
_EPS = 1e-9

ObsSource = Literal["snapshots_15m", "bloomberg_1m", "none"]
Side = Literal["up", "down"]


# ==========================================================================
# The table
# ==========================================================================
def _side_columns(side: Side) -> tuple[Column, ...]:
    up = side == "up"
    word = "upper" if up else "lower"
    opp = "sell" if up else "buy"  # the side of the book that must be empty for a lock
    same = "buy" if up else "sell"
    s = f"_{side}"
    return (
        Column(
            f"touched{s}",
            "bool",
            f"The {word} limit was touched: the day's {'high' if up else 'low'} came within one "
            f"tick of the initial limit (etf_day hit_{side}, or the official-band version), or an "
            "intraday observation came within one tick of the limit in force",
        ),
        Column(
            f"first_touch_ts{s}",
            "timestamp",
            f"IST time of the first intraday observation within one tick of the {word} limit in "
            "force. Late by up to sample_interval_min when obs_source is snapshots_15m. Empty "
            "when no observation showed it",
        ),
        Column(
            f"minutes_at_limit{s}",
            "float",
            f"Estimated minutes the last price stood at the {word} limit: each observation at "
            "the limit is credited the time until the next observation (at most twice the "
            "sampling interval), the last of the day one interval up to the close. Minutes",
        ),
        Column(
            f"minutes_at_limit_min{s}",
            "float",
            "Lower bound: only time between two consecutive observations that were both at the "
            f"{word} limit. Minutes",
        ),
        Column(
            f"n_obs_at_limit{s}",
            "int",
            f"Number of price observations within one tick of the {word} limit in force",
        ),
        Column(
            f"widenings{s}",
            "int",
            f"Fewest band widenings on the {word} side consistent with the prices seen "
            "(0, 1 or 2 for equity ETFs). Uses the intraday observations and the day's "
            f"{'high' if up else 'low'}. Empty when the band could not be computed",
        ),
        Column(
            f"trigger_hit{s}",
            "bool",
            f"A trade came within 0.10 percentage points of the {word} widening trigger "
            "(9.90% from the base, config/rules.yaml): a widening was due after the cooling-off "
            "even if the price never went beyond the old limit. Empty if the rule has no trigger",
        ),
        Column(
            f"limit_end{s}",
            "float",
            f"The {word} limit in force at the end of the day as far as the prices show "
            "(after the minimum widenings). Rupees per unit",
        ),
        Column(
            f"{opp}_qty{s}_max",
            "int",
            f"Largest total {opp} quantity in the order book over the snapshots at the {word} "
            "limit (units). Empty if no snapshot at the limit had the quantity",
        ),
        Column(
            f"{opp}_qty{s}_close",
            "int",
            f"Total {opp} quantity at the last order-book snapshot of the day, if that snapshot "
            f"was at the {word} limit (units). Empty otherwise",
        ),
        Column(
            f"{same}_qty{s}_max",
            "int",
            f"Largest total {same} quantity (the queue at the limit) over the snapshots at the "
            f"{word} limit (units)",
        ),
        Column(
            f"n_book_obs{s}",
            "int",
            f"Snapshots at the {word} limit whose {opp} quantity is known (the denominator of "
            f"locked_{side})",
        ),
        Column(
            f"locked{s}",
            "bool",
            f"LOCKED: at least one snapshot at the {word} limit had an EMPTY {opp} side "
            f"({opp} quantity 0). False if the {opp} side was never empty. Empty when no "
            "snapshot at the limit had the quantity (never treated as 0)",
        ),
        Column(
            f"locked_n_obs{s}",
            "int",
            f"Number of snapshots at the {word} limit with an empty {opp} side",
        ),
        Column(
            f"locked_close{s}",
            "bool",
            f"At the last order-book snapshot of the day: locked (at the {word} limit with the "
            f"{opp} side empty). False if that snapshot was not at the limit or the {opp} side "
            "was not empty. Empty if its quantity is unknown",
        ),
    )


def _schema() -> TableSchema:
    columns = (
        Column("symbol", "string", "NSE trading symbol"),
        Column("date", "date", "NSE trading day (IST calendar date)"),
        Column(
            "touch_basis",
            "string",
            "How the day qualified: etf_day (daily high/low flag only), intraday (only the "
            "intraday observations), both",
        ),
        Column(
            "limit_basis",
            "string",
            "official (initial limits from the NSE band file, later levels computed), computed "
            "(config/rules.yaml from the base price), none (no band known; intraday columns empty)",
        ),
        Column(
            "obs_source",
            "string",
            "Where the intraday PRICE timeline came from: bloomberg_1m (1-minute bars), "
            "snapshots_15m (recorder snapshots), none. Order-book quantities are always from "
            "the snapshots",
        ),
        Column(
            "sample_interval_min",
            "float",
            "Spacing of the price observations in minutes: 1 for bars, the median gap between "
            "snapshot cycles otherwise. Empty with fewer than two observations",
        ),
        Column("n_obs", "int", "Price observations inside the 09:15-15:30 session (coverage)"),
        Column("first_obs_ts", "timestamp", "IST time of the first price observation in session"),
        Column("last_obs_ts", "timestamp", "IST time of the last price observation in session"),
        Column(
            "n_book_obs",
            "int",
            "Snapshots in session with any order-book quantity (total buy/sell or depth)",
        ),
        *_side_columns("up"),
        *_side_columns("down"),
        Column(
            "source_file",
            "string",
            "Lineage: first .. last raw file of the snapshots and of the Bloomberg bars used "
            "(or 'etf_day' when no intraday data was used)",
        ),
    )
    return TableSchema(
        name=TABLE,
        primary_key=("symbol", "date"),
        columns=columns,
        doc="Intraday diagnostics for every ETF-day on which a price limit was touched (R13).",
    )


if TABLE not in SCHEMAS:
    register_schema(_schema())

COLUMNS = [c.name for c in _schema().columns]


# ==========================================================================
# The band ladder: the limit at each widening level
# ==========================================================================
@dataclass(frozen=True)
class Ladder:
    """Limits of one side by widening level (index 0 = opening limit), rupees on the raw scale.

    ``widens`` is False when no widening rule is known (no rule for the date or no base price,
    only the official opening limit): then no widening count can be given. A fixed band (the
    old T-2 regime) has ``widens`` True with a single level: zero widenings, known.
    """

    side: Side
    limits: tuple[float, ...]
    widens: bool
    tick: float
    trigger: float | None  # price at which a widening is triggered at level 0

    @property
    def top(self) -> int:
        return len(self.limits) - 1

    def crossed(self, level: int, price: float) -> bool:
        """True if ``price`` lies beyond the limit of ``level`` (so the band must have widened)."""
        lim = self.limits[level]
        return price > lim + _EPS if self.side == "up" else price < lim - _EPS

    def level_for(self, price: float, start: int = 0) -> int:
        """Lowest level >= ``start`` whose limit ``price`` has not gone beyond (capped at top)."""
        k = start
        while k < self.top and self.crossed(k, price):
            k += 1
        return k

    def at(self, level: int, price: float) -> bool:
        """``price`` is within one tick of the limit of ``level`` (or beyond it)."""
        lim = self.limits[level]
        slack = self.tick + _EPS
        return price >= lim - slack if self.side == "up" else price <= lim + slack


def _snap(price: float, tick: float) -> float:
    return round(round(price / tick) * tick, 6)


def _number(row: Any, name: str) -> float:
    value = row.get(name) if hasattr(row, "get") else None
    try:
        out = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return math.nan
    return out


def build_ladders(
    row: Any, rules: BandRules, day: date
) -> tuple[Ladder, Ladder, Literal["official", "computed", "none"]] | None:
    """Upper and lower ladders for one ``etf_day`` row, or None when no limit is known.

    Prices in ``etf_day`` are on today's unit scale; the ladders are put back on the scale of
    the day (divide by ``price_adj_factor``) because the snapshots hold prices as quoted then.

    The opening limit comes from the official band when the row has one; the later levels are
    the computed ladder (base +/-(10% + 5% x level)) shifted by the difference between the
    official and the computed opening limit (zero when they agree).
    """
    tick = float(rules.tick_size)
    pf = _number(row, "price_adj_factor")
    pf = pf if pf > 0 else 1.0
    base = _number(row, "band_base_price")
    base = _snap(base / pf, tick) if base > 0 else math.nan
    off_hi, off_lo = _number(row, "band_high_official"), _number(row, "band_low_official")
    off_hi = _snap(off_hi / pf, tick) if off_hi > 0 else math.nan
    off_lo = _snap(off_lo / pf, tick) if off_lo > 0 else math.nan

    up: list[float] | None = None
    down: list[float] | None = None
    trigger_up = trigger_down = None
    asset = row.get("asset_class") if hasattr(row, "get") else None
    if not math.isnan(base) and isinstance(asset, str):
        try:
            spec = rules.regime_for(day).band_spec(asset)
            top = spec.max_widenings if spec.max_widenings is not None else MAX_UNCAPPED_LEVELS
            if spec.step_pct == 0:
                top = 0
            pairs = [bands.limits_at_level(base, spec, k, rules) for k in range(top + 1)]
            down = [float(lo) for lo, _ in pairs]
            up = [float(hi) for _, hi in pairs]
            if spec.trigger_pct is not None:
                trigger_up = _snap(base * (1 + spec.trigger_pct / 100), tick)
                trigger_down = _snap(base * (1 - spec.trigger_pct / 100), tick)
        except (ValueError, KeyError, bands.MissingBaseError):
            up = down = None  # no rule for this date or asset class
    widens = up is not None and down is not None  # a rule was found: a count can be stated

    basis: Literal["official", "computed", "none"] = "computed"
    if not math.isnan(off_hi) and not math.isnan(off_lo):
        basis = "official"
        if up is None or down is None:
            up, down = [off_hi], [off_lo]
        else:
            shift_up, shift_down = off_hi - up[0], off_lo - down[0]
            up = [round(x + shift_up, 6) for x in up]
            down = [round(x + shift_down, 6) for x in down]
    elif up is None or down is None:
        return None
    return (
        Ladder("up", tuple(up), widens, tick, trigger_up),
        Ladder("down", tuple(down), widens, tick, trigger_down),
        basis,
    )


# ==========================================================================
# Observations of one ETF-day
# ==========================================================================
@dataclass(frozen=True)
class Timeline:
    """Price observations of one ETF-day in time order (ts in seconds since the epoch)."""

    source: ObsSource
    ts: tuple[float, ...]
    stamps: tuple[pd.Timestamp, ...]
    last: tuple[float, ...]
    high: tuple[float, ...]
    low: tuple[float, ...]
    interval_min: float | None
    cap_min: float | None  # most one observation may be credited (None = until the next one)
    lineage: tuple[str, ...]

    def __len__(self) -> int:
        return len(self.ts)


EMPTY_TIMELINE = Timeline("none", (), (), (), (), (), None, None, ())


def _in_session(ts: pd.Series, day: date, *, end_inclusive: bool) -> pd.Series:
    """Rows stamped on ``day`` (IST) between the open and the close."""
    local = ts.dt.tz_convert(IST)
    clock = local.dt.time
    after_open = clock >= SESSION_OPEN
    before_close = (clock <= SESSION_CLOSE) if end_inclusive else (clock < SESSION_CLOSE)
    return (local.dt.date == day) & after_open & before_close


def snapshot_rows(snap: pd.DataFrame, day: date) -> pd.DataFrame:
    """One symbol's snapshot rows usable for ``day``: in session and not a stale replay.

    NSE's own data time (``data_ts``) is older than the fetch time on holidays; a row whose
    data time is on another day shows an old price, so it is dropped.
    """
    if snap.empty:
        return snap
    keep = _in_session(snap["ts"], day, end_inclusive=True)
    if "data_ts" in snap.columns:
        data_day = snap["data_ts"].dt.tz_convert(IST).dt.date
        keep &= data_day.isna() | (data_day == day)
    return snap[keep].sort_values("ts", kind="stable")


def snapshot_timeline(rows: pd.DataFrame) -> Timeline:
    """Price timeline from snapshot rows: one observation per recorder cycle.

    Rows fetched within ``SAME_TICK_SECONDS`` of the first row of a cycle are merged; the
    price is taken from the best source (``SNAPSHOT_SOURCE_PRIORITY``), the high/low from all
    of them. The interval is the median gap between cycles.
    """
    priced = rows[rows["last_price"].notna() & (rows["last_price"] > 0)]
    if priced.empty:
        return EMPTY_TIMELINE
    cycles: list[list[Any]] = []
    for rec in priced.itertuples(index=False):
        t = rec.ts.timestamp()
        if cycles and t - cycles[-1][0][0] <= SAME_TICK_SECONDS:
            cycles[-1].append((t, rec))
        else:
            cycles.append([(t, rec)])
    ts, stamps, last, high, low, lineage = [], [], [], [], [], []
    for cyc in cycles:
        best = min(cyc, key=lambda x: (SNAPSHOT_SOURCE_PRIORITY.get(str(x[1].source), 9), x[0]))[1]
        prices = [float(x[1].last_price) for x in cyc]
        ts.append(cyc[0][0])
        stamps.append(cyc[0][1].ts)
        last.append(float(best.last_price))
        high.append(max(prices))
        low.append(min(prices))
        lineage += [str(x[1].source_file) for x in cyc if isinstance(x[1].source_file, str)]
    gaps = [(b - a) / 60 for a, b in itertools.pairwise(ts)]
    interval = float(pd.Series(gaps).median()) if gaps else None
    return Timeline(
        "snapshots_15m",
        tuple(ts),
        tuple(stamps),
        tuple(last),
        tuple(high),
        tuple(low),
        interval,
        2 * interval if interval is not None else None,
        tuple(lineage),
    )


def bar_timeline(bars: pd.DataFrame, day: date) -> Timeline:
    """Price timeline from 1-minute bars of ``day`` (bar start 09:15 to 15:29)."""
    if bars.empty:
        return EMPTY_TIMELINE
    keep = _in_session(bars["ts_ist"], day, end_inclusive=False) & bars["last"].notna()
    sub = bars[keep].sort_values("ts_ist", kind="stable")
    if sub.empty:
        return EMPTY_TIMELINE
    last = sub["last"].astype(float)
    high = sub["high"].astype(float).where(sub["high"].notna(), last)
    low = sub["low"].astype(float).where(sub["low"].notna(), last)
    return Timeline(
        "bloomberg_1m",
        tuple(t.timestamp() for t in sub["ts_ist"]),
        tuple(sub["ts_ist"]),
        tuple(last),
        tuple(high),
        tuple(low),
        1.0,
        None,  # a bar is credited until the next bar: no trade in between means the price stood
        tuple(str(f) for f in sub["source_file"] if isinstance(f, str)),
    )


@dataclass(frozen=True)
class BookRow:
    """One snapshot with order-book quantities (units; None = not recorded)."""

    ts: float
    stamp: pd.Timestamp
    price: float
    buy_qty: int | None
    sell_qty: int | None


def _depth_total(depth_json: Any, side: str) -> int | None:
    """Sum of the quantities of one side of the recorded depth (a lower bound of the total)."""
    if not isinstance(depth_json, str) or not depth_json:
        return None
    try:
        levels = json.loads(depth_json).get(side)
    except (ValueError, AttributeError):
        return None
    if not isinstance(levels, list):
        return None
    total = 0
    for item in levels:
        try:
            qty = item[1]
        except (TypeError, IndexError, KeyError):
            continue
        if isinstance(qty, (int, float)) and not math.isnan(qty):
            total += int(qty)
    return total


def _qty(total: Any, depth_json: Any, depth_side: str) -> int | None:
    """Quantity on one side of the book.

    The recorded total wins. Without a total the depth is used ONLY to prove the side is not
    empty (a positive sum is then a lower bound); a zero or missing depth is not evidence of
    an empty side and gives None, never 0.
    """
    if total is not None and not pd.isna(total):
        return int(total)
    depth = _depth_total(depth_json, depth_side)
    return depth if depth is not None and depth > 0 else None


def book_rows(rows: pd.DataFrame) -> list[BookRow]:
    """Snapshots (in session, with a price) that carry any order-book quantity."""
    out: list[BookRow] = []
    for rec in rows.itertuples(index=False):
        price = rec.last_price
        if price is None or pd.isna(price) or price <= 0:
            continue
        buy = _qty(rec.total_buy_qty, rec.depth_json, "bid")
        sell = _qty(rec.total_sell_qty, rec.depth_json, "ask")
        if buy is None and sell is None:
            continue
        out.append(BookRow(rec.ts.timestamp(), rec.ts, float(price), buy, sell))
    return out


# ==========================================================================
# Diagnostics of one side
# ==========================================================================
def _running_levels(tl: Timeline, ladder: Ladder) -> list[int]:
    """Limit level in force after each observation (never goes down during a day)."""
    levels, level = [], 0
    extreme = tl.high if ladder.side == "up" else tl.low
    for price in extreme:
        level = ladder.level_for(price, level)
        levels.append(level)
    return levels


def _credit_minutes(
    tl: Timeline, at: Sequence[bool], close_ts: float
) -> tuple[float | None, float | None]:
    """(estimate, lower bound) of minutes at the limit from the observations at the limit."""
    if not any(at):
        return 0.0, 0.0
    if tl.interval_min is None:
        return None, None  # one observation only: no spacing to measure
    est = low = 0.0
    n = len(at)
    for i in range(n):
        if not at[i]:
            continue
        if i + 1 < n:
            gap = (tl.ts[i + 1] - tl.ts[i]) / 60
            credit = gap if tl.cap_min is None else min(gap, tl.cap_min)
            est += credit
            if at[i + 1]:
                low += credit
        else:
            est += min(tl.interval_min, max((close_ts - tl.ts[i]) / 60, 0.0))
    return est, low


def diagnose_side(
    ladder: Ladder,
    tl: Timeline,
    book: Sequence[BookRow],
    *,
    day_extreme: float | None,
    close_ts: float,
) -> dict[str, Any]:
    """All R13 numbers of one side (see the column documentation for the meaning).

    ``tl`` is the price timeline (may be empty), ``book`` the order-book snapshots,
    ``day_extreme`` the day's high (upper) or low (lower) from ``etf_day`` on the day's scale.
    """
    up = ladder.side == "up"
    opp, same = ("sell", "buy") if up else ("buy", "sell")
    out: dict[str, Any] = {
        "first_touch_ts": None,
        "minutes_at_limit": None,
        "minutes_at_limit_min": None,
        "n_obs_at_limit": None,
        "widenings": None,
        "trigger_hit": None,
        "limit_end": None,
        f"{opp}_qty_max": None,
        f"{opp}_qty_close": None,
        f"{same}_qty_max": None,
        "n_book_obs": None,
        "locked": None,
        "locked_n_obs": None,
        "locked_close": None,
        "_touched_intraday": False,
    }
    extreme = tl.high if up else tl.low
    levels = _running_levels(tl, ladder)
    final_level = levels[-1] if levels else 0
    if day_extreme is not None and not math.isnan(day_extreme):
        final_level = ladder.level_for(day_extreme, final_level)
    out["limit_end"] = ladder.limits[final_level]
    if ladder.widens:
        out["widenings"] = final_level
    seen = list(extreme) + ([day_extreme] if day_extreme is not None else [])
    if ladder.trigger is not None and seen:
        hit = [(p >= ladder.trigger - _EPS) if up else (p <= ladder.trigger + _EPS) for p in seen]
        out["trigger_hit"] = any(hit)

    # -- price timeline: first touch and minutes at the limit --------------------------
    if len(tl):
        touched = [ladder.at(lv, p) for lv, p in zip(levels, extreme, strict=True)]
        at_limit = [ladder.at(lv, p) for lv, p in zip(levels, tl.last, strict=True)]
        if any(touched):
            out["first_touch_ts"] = tl.stamps[touched.index(True)]
            out["_touched_intraday"] = True
        est, low = _credit_minutes(tl, at_limit, close_ts)
        out["minutes_at_limit"], out["minutes_at_limit_min"] = est, low
        out["n_obs_at_limit"] = int(sum(at_limit))

    # -- order book at the limit --------------------------------------------------------
    if len(tl) or book:
        out["n_book_obs"] = 0
    if book and ladder.limits:
        ts_arr = list(tl.ts)

        def level_at(row: BookRow) -> int:
            i = bisect_right(ts_arr, row.ts) - 1  # last price observation at or before it
            return ladder.level_for(row.price, levels[i] if i >= 0 else 0)

        at_rows = [r for r in book if ladder.at(level_at(r), r.price)]
        if at_rows:
            out["_touched_intraday"] = True
        opp_q = [getattr(r, f"{opp}_qty") for r in at_rows]
        same_q = [getattr(r, f"{same}_qty") for r in at_rows]
        known = [q for q in opp_q if q is not None]
        out["n_book_obs"] = len(known)
        if known:
            out[f"{opp}_qty_max"] = max(known)
            out["locked_n_obs"] = sum(1 for q in known if q == 0)
            out["locked"] = out["locked_n_obs"] > 0
        known_same = [q for q in same_q if q is not None]
        if known_same:
            out[f"{same}_qty_max"] = max(known_same)
        last = book[-1]
        if ladder.at(level_at(last), last.price):
            q = getattr(last, f"{opp}_qty")
            out[f"{opp}_qty_close"] = q
            out["locked_close"] = None if q is None else q == 0
        else:
            out["locked_close"] = False
    return out


# ==========================================================================
# The table builder
# ==========================================================================
def _flag(row: Any, *names: str) -> bool:
    for name in names:
        value = row.get(name)
        if value is not None and not pd.isna(value) and bool(value):
            return True
    return False


def _lineage(parts: dict[str, Sequence[str]]) -> str:
    chunks = []
    for label, files in parts.items():
        files = sorted(set(files))
        if files:
            span = files[0] if len(files) == 1 else f"{files[0]} .. {files[-1]}"
            chunks.append(f"{label}: {span} ({len(files)} file(s))")
    return " | ".join(chunks) if chunks else "etf_day"


def _timestamp(value: Any) -> pd.Timestamp | None:
    return None if value is None else pd.Timestamp(value)


def limit_day_row(
    row: Any,
    rules: BandRules,
    snapshots: pd.DataFrame,
    bars: pd.DataFrame,
) -> dict[str, Any] | None:
    """The ``limit_day`` row of one ETF-day, or None when no limit was touched.

    ``row`` is an ``etf_day`` row (mapping-like), ``snapshots`` the symbol's ``inav_snapshots``
    rows and ``bars`` its Bloomberg minute bars (``bloomberg.etf_minute_bars``); either may be
    empty. Pass only the symbol's own rows; they are filtered to the day here.
    """
    day: date = row["date"]
    flag_up = _flag(row, "hit_up", "hit_up_official")
    flag_down = _flag(row, "hit_down", "hit_down_official")

    day_rows = snapshot_rows(snapshots, day) if not snapshots.empty else snapshots
    book = book_rows(day_rows) if len(day_rows) else []
    tl = bar_timeline(bars, day) if not bars.empty else EMPTY_TIMELINE
    if not len(tl) and len(day_rows):
        tl = snapshot_timeline(day_rows)

    built = build_ladders(row, rules, day)
    close_ts = datetime.combine(day, SESSION_CLOSE, tzinfo=IST).timestamp()
    pf = _number(row, "price_adj_factor")
    pf = pf if pf > 0 else 1.0
    high, low = _number(row, "high"), _number(row, "low")
    sides: dict[Side, dict[str, Any]] = {}
    if built is not None:
        ladder_up, ladder_down, basis = built
        sides["up"] = diagnose_side(
            ladder_up, tl, book, day_extreme=None if math.isnan(high) else high / pf,
            close_ts=close_ts,
        )  # fmt: skip
        sides["down"] = diagnose_side(
            ladder_down, tl, book, day_extreme=None if math.isnan(low) else low / pf,
            close_ts=close_ts,
        )  # fmt: skip
    else:
        basis = "none"

    intraday_up = bool(sides.get("up", {}).get("_touched_intraday"))
    intraday_down = bool(sides.get("down", {}).get("_touched_intraday"))
    touched_up, touched_down = flag_up or intraday_up, flag_down or intraday_down
    if not (touched_up or touched_down):
        return None
    from_flags = flag_up or flag_down
    from_intraday = intraday_up or intraday_down
    touch_basis = (
        "both" if from_flags and from_intraday else "etf_day" if from_flags else "intraday"
    )

    out: dict[str, Any] = dict.fromkeys(COLUMNS)  # every column present; empty = unknown
    out |= {
        "symbol": row["symbol"],
        "date": day,
        "touch_basis": touch_basis,
        "limit_basis": basis,
        "obs_source": tl.source,
        "sample_interval_min": tl.interval_min,
        "n_obs": len(tl),
        "first_obs_ts": tl.stamps[0] if len(tl) else None,
        "last_obs_ts": tl.stamps[-1] if len(tl) else None,
        "n_book_obs": len(book),
    }
    for side, touched in (("up", touched_up), ("down", touched_down)):
        out[f"touched_{side}"] = touched
        if not touched:
            continue  # nothing to say about a side that never touched its limit
        for key, value in sides.get(side, {}).items():
            if key.startswith("_"):
                continue
            if key == "first_touch_ts":
                value = _timestamp(value)
            elif key == "limit_end" and value is not None:
                value = value * pf  # back to the etf_day unit scale
            # sell_qty_max -> sell_qty_up_max; widenings -> widenings_up
            name = key.replace("_qty_", f"_qty_{side}_", 1) if "_qty_" in key else f"{key}_{side}"
            out[name] = value
    lineage = {"inav_snapshots": [], "bloomberg": []}
    if len(day_rows):
        lineage["inav_snapshots"] = [str(f) for f in day_rows["source_file"].dropna()]
    if tl.source == "bloomberg_1m":
        lineage["bloomberg"] = list(tl.lineage)
    out["source_file"] = _lineage(lineage)
    return out


def build_limit_days(
    store: Store,
    etf_day: pd.DataFrame,
    *,
    bloomberg_frame: pd.DataFrame | None = None,
    rules: BandRules | None = None,
) -> pd.DataFrame:
    """The ``limit_day`` table (not written; the caller writes it with the store).

    One row per ETF-day in ``etf_day`` on which a limit was touched, by the daily flags or by
    the intraday data. ``bloomberg_frame`` is the imported long-format frame
    (``bloomberg.import_directory(...).frame``); None means "no Bloomberg data". Snapshots are
    read from the store's ``inav_snapshots`` table when it exists.
    """
    rules = rules or load_rules()
    frame = bloomberg_frame if bloomberg_frame is not None else pd.DataFrame()
    snap_groups: dict[tuple[str, date], pd.DataFrame] = {}
    if store.has_table("inav_snapshots"):
        snaps = store.read_table("inav_snapshots")
        if len(snaps):
            snaps = snaps.assign(_day=snaps["ts"].dt.tz_convert(IST).dt.date)
            snap_groups = {k: g.drop(columns="_day") for k, g in snaps.groupby(["symbol", "_day"])}
    bar_cache: dict[str, pd.DataFrame] = {}
    no_snapshots = pd.DataFrame()

    rows = []
    for rec in etf_day.to_dict("records"):
        symbol = rec["symbol"]
        if symbol not in bar_cache:
            bar_cache[symbol] = bloomberg.etf_minute_bars(frame, symbol)
        snap = snap_groups.get((symbol, rec["date"]), no_snapshots)
        result = limit_day_row(rec, rules, snap, bar_cache[symbol])
        if result is not None:
            rows.append(result)
    schema = SCHEMAS[TABLE]
    if not rows:
        return coerce(pd.DataFrame({c: [] for c in schema.column_names}), schema)
    return coerce(pd.DataFrame(rows).reindex(columns=schema.column_names), schema)


__all__ = [
    "COLUMNS",
    "TABLE",
    "BookRow",
    "Ladder",
    "Timeline",
    "bar_timeline",
    "book_rows",
    "build_ladders",
    "build_limit_days",
    "diagnose_side",
    "limit_day_row",
    "snapshot_rows",
    "snapshot_timeline",
]
