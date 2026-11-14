"""Data preparation for the dashboard: plain functions from tables to tables.

Nothing here imports Streamlit or Plotly, reads the clock, or touches the disk (except
``load_tables``, which only reads). The Streamlit app is a thin layer on top, so these
functions can be tested with small hand-made tables.

Reading is read-only and never takes a lock: tables come from the Parquet files in
``data/clean`` (the build replaces each file in one atomic step), not from the DuckDB file, so the
dashboard can stay open while ``etfp build`` runs.

Units: premiums in the tables are fractions (0.25 = 25%). The ``display_*`` functions turn them
into percent numbers for people; the other functions keep fractions.

Wording rule (CLAUDE.md, no investment advice): labels only describe the data ("premium",
"above", "below"). A test scans the dashboard source for advice words.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Protocol

import numpy as np
import pandas as pd

from etfp.alerts.rules import DISCLAIMER_SHORT, paired_inav_premium
from etfp.timeutil import IST

# SPEC section 9: shown on every page.
DISCLAIMER = DISCLAIMER_SHORT

APRIL_2027 = date(2027, 4, 1)
"""Planned return of the price-band anchor to the previous day's NAV (rules.yaml: nav_t1)."""

# Tables the dashboard reads. Any of them may be missing; the views then say so.
TABLE_NAMES = (
    "etf_day",
    "etf_master",
    "inav_snapshots",
    "events",
    "prices_daily",
    "nav_daily",
    "units_daily",
    "band_official",
    "underlying_daily",
    "fx_daily",
)

# Premium-like measures the user can pick, column -> label. Fractions unless noted.
MEASURES: dict[str, str] = {
    "prem_fv": "Premium over fair value",
    "prem_nav_known": "Premium over the latest known NAV",
    "prem_raw": "Premium over the same-day NAV (what the press quotes)",
    "prem_band_ref": "Premium over the T-2 NAV (the pre-Sep-2026 band anchor)",
    "x_april": "Excess over the April 2027 cap (log; above 0 = price is above 1.2 x known NAV)",
}
LOG_MEASURES = frozenset({"x_april"})
"""Measures that are natural-log numbers, not fractions: shown as they are, not as percent."""


class _TableStore(Protocol):
    def has_table(self, table: str) -> bool: ...

    def read_table(self, name: str) -> pd.DataFrame: ...


@dataclass
class Tables:
    """The clean tables the dashboard uses; ``None`` = not built yet."""

    frames: dict[str, pd.DataFrame | None] = field(default_factory=dict)

    def get(self, name: str) -> pd.DataFrame | None:
        return self.frames.get(name)

    @property
    def etf_day(self) -> pd.DataFrame | None:
        return self.get("etf_day")

    @property
    def has_data(self) -> bool:
        df = self.etf_day
        return df is not None and not df.empty and {"symbol", "date"} <= set(df.columns)


def load_tables(store: _TableStore, names: Iterable[str] = TABLE_NAMES) -> Tables:
    """Read each table that exists (read-only). A missing table is ``None``, not an error."""
    frames: dict[str, pd.DataFrame | None] = {}
    for name in names:
        frames[name] = store.read_table(name) if store.has_table(name) else None
    return Tables(frames)


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
def _dates(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series).dt.date


def _num(df: pd.DataFrame, column: str) -> pd.Series:
    """A float column; missing column or text -> NaN."""
    if column not in df.columns:
        return pd.Series(np.nan, index=df.index, dtype="float64")
    return pd.to_numeric(df[column], errors="coerce").astype("float64")


def _flag(df: pd.DataFrame, column: str) -> pd.Series:
    """A True/False column where missing counts as False."""
    if column not in df.columns:
        return pd.Series(False, index=df.index)
    return df[column].fillna(False).astype(bool)


def _prepared(etf_day: pd.DataFrame | None) -> pd.DataFrame:
    if etf_day is None or etf_day.empty or not {"symbol", "date"} <= set(etf_day.columns):
        return pd.DataFrame(columns=["symbol", "date", "_d"])
    df = etf_day.copy()
    df["_d"] = _dates(df["date"])
    return df.sort_values(["symbol", "_d"], kind="stable").drop_duplicates(
        ["symbol", "_d"], keep="last"
    )


def tracked_symbols(etf_day: pd.DataFrame | None, master: pd.DataFrame | None) -> list[str]:
    """Symbols to show: those marked ``tracked`` in etf_master (if it exists) that have data;
    otherwise every symbol in etf_day. Sorted."""
    if etf_day is None or etf_day.empty or "symbol" not in etf_day.columns:
        return []
    present = {str(s) for s in etf_day["symbol"].dropna()}
    if master is not None and {"symbol", "tracked"} <= set(master.columns):
        flagged = master["tracked"].fillna(False).astype(bool)
        wanted = {str(s) for s in master.loc[flagged, "symbol"]}
        return sorted(present & wanted)
    return sorted(present)


def available_measures(etf_day: pd.DataFrame | None) -> dict[str, str]:
    """The measures of ``MEASURES`` that exist as columns in etf_day (others are hidden)."""
    if etf_day is None:
        return {}
    return {col: label for col, label in MEASURES.items() if col in etf_day.columns}


# --------------------------------------------------------------------------
# Overview table
# --------------------------------------------------------------------------
OVERVIEW_SOURCE_COLUMNS = (
    "close",
    "nav_known_at_close",
    "fv",
    "prem_raw",
    "prem_nav_known",
    "prem_fv",
    "band_low_official",
    "band_high_official",
    "hit_up",
    "units_change",
    "fv_method",
)
"""etf_day columns the overview is built from; the app notes any that are absent."""

OVERVIEW_FIELDS = (
    "symbol",
    "name",
    "data_date",
    "close",
    "nav_known",
    "fv",
    "prem_raw",
    "prem_nav_known",
    "prem_fv",
    "prem_fv_date",
    "prem_fv_chg_1d",
    "prem_fv_chg_1w",
    "band_low",
    "band_high",
    "band_source",
    "pct_to_upper",
    "pct_to_lower",
    "band_position",
    "last_hit_up",
    "units_change_last",
    "creation_units_last",
    "units_change_date",
    "fv_method",
)


def overview_missing_columns(etf_day: pd.DataFrame | None) -> list[str]:
    """Source columns the overview wants that etf_day does not have."""
    have = set(etf_day.columns) if etf_day is not None else set()
    return [c for c in OVERVIEW_SOURCE_COLUMNS if c not in have]


def _value_on_or_before(group: pd.DataFrame, column: str, day: date) -> float:
    """Last non-missing value of ``column`` on a row dated on or before ``day`` (NaN if none)."""
    rows = group[group["_d"] <= day]
    values = _num(rows, column).dropna()
    return float(values.iloc[-1]) if len(values) else float("nan")


def _symbol_overview(sym: str, group: pd.DataFrame, name: str) -> dict[str, Any]:
    last = group.iloc[-1]
    last_day: date = last["_d"]
    close = _num(group, "close").iloc[-1]
    prem_fv_now = _num(group, "prem_fv").iloc[-1]
    with_premium = _num(group, "prem_fv").dropna()
    # Day of the newest premium. Differs from data_date when the latest day's NAV is not in yet.
    last_premium_day = group.loc[with_premium.index[-1], "_d"] if len(with_premium) else None

    # Change in the fair-value premium vs the previous row and vs one calendar week earlier.
    prev = _num(group, "prem_fv").iloc[-2] if len(group) > 1 else float("nan")
    week_ago = _value_on_or_before(group, "prem_fv", last_day - timedelta(days=7))

    # Price limits: the official band if NSE published one, else the computed initial band.
    low_off = _num(group, "band_low_official").iloc[-1]
    high_off = _num(group, "band_high_official").iloc[-1]
    low_init = _num(group, "band_low_initial").iloc[-1]
    high_init = _num(group, "band_high_initial").iloc[-1]
    if np.isfinite(low_off) and np.isfinite(high_off):
        low, high, band_source = low_off, high_off, "official"
    elif np.isfinite(low_init) and np.isfinite(high_init):
        low, high, band_source = low_init, high_init, "computed"
    else:
        low, high, band_source = np.nan, np.nan, ""
    ok = np.isfinite(close) and close > 0 and np.isfinite(low) and np.isfinite(high)
    # Headroom to each limit relative to the close: +x% means the limit is x% above the close.
    pct_to_upper = high / close - 1 if ok else np.nan
    pct_to_lower = low / close - 1 if ok else np.nan
    position = (close - low) / (high - low) if ok and high > low else np.nan

    hits = group.loc[_flag(group, "hit_up").to_numpy(), "_d"]
    last_hit = hits.iloc[-1] if len(hits) else None

    change = (
        pd.to_numeric(group["units_change"], errors="coerce") if "units_change" in group else None
    )
    ch_date, ch_units, ch_creation = None, np.nan, np.nan
    if change is not None:
        moved = group[(change.fillna(0) != 0).to_numpy()]
        if len(moved):
            ch_date = moved["_d"].iloc[-1]
            ch_units = float(_num(moved, "units_change").iloc[-1])
            ch_creation = float(_num(moved, "creation_units_change").iloc[-1])

    method = last.get("fv_method") if "fv_method" in group.columns else None
    return {
        "symbol": sym,
        "name": name,
        "data_date": last_day,
        "close": close,
        "nav_known": _num(group, "nav_known_at_close").iloc[-1],
        "fv": _num(group, "fv").iloc[-1],
        "prem_raw": _num(group, "prem_raw").iloc[-1],
        "prem_nav_known": _num(group, "prem_nav_known").iloc[-1],
        "prem_fv": prem_fv_now,
        "prem_fv_date": last_premium_day,
        "prem_fv_chg_1d": prem_fv_now - prev,
        "prem_fv_chg_1w": prem_fv_now - week_ago,
        "band_low": low,
        "band_high": high,
        "band_source": band_source,
        "pct_to_upper": pct_to_upper,
        "pct_to_lower": pct_to_lower,
        "band_position": position,
        "last_hit_up": last_hit,
        "units_change_last": ch_units,
        "creation_units_last": ch_creation,
        "units_change_date": ch_date,
        "fv_method": None if method is None or pd.isna(method) else str(method),
    }


def latest_overview(
    etf_day: pd.DataFrame | None, master: pd.DataFrame | None = None
) -> pd.DataFrame:
    """One row per tracked ETF from its latest etf_day row, highest fair-value premium first.

    Band position: ``band_position`` = (close - low) / (high - low): 0 at the lower limit, 1 at the
    upper. ``pct_to_upper`` = high / close - 1 (how far above the close the upper limit is).
    ``prem_fv_chg_1d`` and ``_1w`` are changes in the fair-value premium in fraction points versus
    the previous row and the last row on or before 7 calendar days earlier (NaN if the history
    does not reach back that far).
    """
    df = _prepared(etf_day)
    wanted = set(tracked_symbols(etf_day, master))
    names: dict[str, str] = {}
    if master is not None and {"symbol", "name"} <= set(master.columns):
        names = {
            str(s): str(n)
            for s, n in zip(master["symbol"], master["name"], strict=False)
            if pd.notna(n)
        }
    rows = [
        _symbol_overview(str(sym), g, names.get(str(sym), str(sym)))
        for sym, g in df.groupby("symbol", sort=True)
        if str(sym) in wanted
    ]
    out = pd.DataFrame(rows, columns=list(OVERVIEW_FIELDS))
    return out.sort_values(
        "prem_fv", ascending=False, na_position="last", kind="stable"
    ).reset_index(drop=True)


DISPLAY_LABELS = {
    "symbol": "Symbol",
    "name": "Name",
    "data_date": "Data date",
    "close": "Close (Rs)",
    "nav_known": "NAV known at close (Rs)",
    "fv": "Fair value (Rs)",
    "prem_raw": "Premium, same-day NAV (%)",
    "prem_nav_known": "Premium, known NAV (%)",
    "prem_fv": "Premium, fair value (%)",
    "prem_fv_date": "Premium as of",
    "prem_fv_chg_1d": "Change vs 1 day (points)",
    "prem_fv_chg_1w": "Change vs 1 week (points)",
    "band_low": "Lower limit (Rs)",
    "band_high": "Upper limit (Rs)",
    "band_source": "Limit source",
    "pct_to_upper": "Upper limit vs close (%)",
    "pct_to_lower": "Lower limit vs close (%)",
    "band_position": "Position in band (0 = lower, 1 = upper)",
    "last_hit_up": "Last day at upper limit",
    "units_change_last": "Last change in units",
    "creation_units_last": "...in creation units",
    "units_change_date": "Date of last change",
    "fv_method": "Fair value method",
}
_PERCENT_FIELDS = (
    "prem_raw",
    "prem_nav_known",
    "prem_fv",
    "prem_fv_chg_1d",
    "prem_fv_chg_1w",
    "pct_to_upper",
    "pct_to_lower",
)


def display_overview(overview: pd.DataFrame) -> pd.DataFrame:
    """The overview with premiums as percent numbers (0.25 -> 25.0) and readable column names."""
    out = overview.copy()
    for col in _PERCENT_FIELDS:
        out[col] = out[col] * 100.0
    return out.rename(columns=DISPLAY_LABELS)


# --------------------------------------------------------------------------
# Time series
# --------------------------------------------------------------------------
def premium_series(
    etf_day: pd.DataFrame | None,
    symbols: Sequence[str],
    measure: str,
    *,
    start: date | None = None,
) -> pd.DataFrame:
    """Long table ``symbol, date, value`` for the chosen measure, oldest first.

    Fractions for the premium measures; ``x_april`` stays a natural-log number. Rows with no
    value are dropped. An unknown measure or missing column gives an empty table.
    """
    empty = pd.DataFrame({"symbol": [], "date": [], "value": []})
    df = _prepared(etf_day)
    if measure not in df.columns or not symbols:
        return empty
    df = df[df["symbol"].isin(list(symbols))]
    out = pd.DataFrame(
        {"symbol": df["symbol"].astype(str), "date": df["_d"], "value": _num(df, measure)}
    ).dropna(subset=["value"])
    if start is not None:
        out = out[out["date"] >= start]
    return out.reset_index(drop=True)


LEVEL_COLUMNS = {
    "close": "Close",
    "nav_known_at_close": "NAV known at close",
    "fv": "Fair value",
}


def level_frame(etf_day: pd.DataFrame | None, symbol: str) -> pd.DataFrame:
    """Price, known NAV and fair value (rupees) for one ETF over time; only existing columns."""
    df = _prepared(etf_day)
    df = df[df["symbol"] == symbol]
    out = pd.DataFrame({"date": df["_d"]})
    for col in LEVEL_COLUMNS:
        if col in df.columns:
            out[col] = _num(df, col)
    return out.reset_index(drop=True)


BAND_COLUMNS = {
    "band_low_official": "Official lower limit",
    "band_high_official": "Official upper limit",
    "band_low_initial": "Computed lower limit (initial)",
    "band_high_initial": "Computed upper limit (initial)",
    "band_low_max": "Computed lower limit (widest)",
    "band_high_max": "Computed upper limit (widest)",
}


def band_frame(etf_day: pd.DataFrame | None, symbol: str) -> pd.DataFrame:
    """Close and the band limits for one ETF over time. Infinite limits (gold/silver have no
    widest limit) become NaN so charts skip them. Only existing columns are returned."""
    df = _prepared(etf_day)
    df = df[df["symbol"] == symbol]
    out = pd.DataFrame({"date": df["_d"], "close": _num(df, "close")})
    for col in BAND_COLUMNS:
        if col in df.columns:
            values = _num(df, col)
            out[col] = values.where(np.isfinite(values))
    return out.reset_index(drop=True)


# --------------------------------------------------------------------------
# Regime markers
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Marker:
    day: date
    label: str
    planned: bool
    kind: str = "regime"
    """``regime`` (a price-band rule change from rules.yaml) or ``event`` (a row of ``events``)."""


def regime_markers(regimes: Iterable[Any]) -> list[Marker]:
    """One marker per regime that has a start date (from ``rules.yaml``).

    The first regime has no start date (it covers all earlier history) and gets no marker.
    ``planned`` regimes are not in force yet and are drawn dashed.
    """
    markers = []
    for regime in regimes:
        start = getattr(regime, "start", None)
        if start is None:
            continue
        planned = getattr(regime, "status", "") == "planned"
        markers.append(
            Marker(
                start,
                f"{regime.name}: band base changes" + (" (planned)" if planned else ""),
                planned,
            )
        )
    return sorted(markers, key=lambda m: m.day)


def markers_in_range(
    markers: Iterable[Marker], first: date | None, last: date | None
) -> list[Marker]:
    """Markers on or between the two dates (both ends included); nothing if either is None."""
    if first is None or last is None:
        return []
    return [m for m in markers if first <= m.day <= last]


def days_until(target: date, today: date) -> int:
    """Calendar days from ``today`` to ``target`` (negative once the date has passed)."""
    return (target - today).days


def days_to_april(now: datetime, target: date = APRIL_2027) -> int:
    """Calendar days from the Indian date of ``now`` to ``target`` (1 Apr 2027 by default).

    ``now`` must be timezone-aware: it is converted to Asia/Kolkata first, so 31 Mar 2027 at
    19:00 UTC (already 1 Apr 00:30 in India) gives 0, not 1. A naive datetime is refused
    (project rule: no naive datetimes). Negative once the date has passed.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("days_to_april needs a timezone-aware datetime (project rule)")
    return days_until(target, now.astimezone(IST).date())


# --------------------------------------------------------------------------
# April 2027 countdown
# --------------------------------------------------------------------------
APRIL_CAP_MULTIPLE = 1.2
"""x_april compares the close with 1.2 x the known NAV (D30): +20% is the widest an equity ETF's
price may move from its base in one day under the planned NAV-anchored rule (initial +10%, two
5% widenings)."""


def x_april_series(
    etf_day: pd.DataFrame | None,
    symbols: Sequence[str],
    *,
    column: str = "x_april",
    start: date | None = None,
) -> pd.DataFrame:
    """Long table ``symbol, date, value, close_vs_cap`` for the April-2027 gauge, oldest first.

    ``value`` is the log number from etf_day (``x_april`` = ln(close / (1.2 x known NAV)), or
    ``x_april_fv`` on fair value). ``close_vs_cap`` turns it into a plain fraction:
    exp(value) - 1 = close / (1.2 x NAV) - 1, e.g. value 0.10 -> 0.105 (close 10.5% above
    1.2 x NAV); value 0 -> 0 (close exactly at 1.2 x NAV). Rows with no value are dropped.
    """
    out = premium_series(etf_day, symbols, column, start=start)
    out["close_vs_cap"] = np.expm1(out["value"].astype("float64"))
    return out


def x_april_latest(series: pd.DataFrame) -> pd.DataFrame:
    """Newest row of each symbol in an ``x_april_series`` table, highest value first.

    Columns: symbol, date, value, close_vs_cap, above_cap (value > 0).
    """
    columns = ["symbol", "date", "value", "close_vs_cap", "above_cap"]
    if series.empty:
        return pd.DataFrame(columns=columns)
    last = series.sort_values(["symbol", "date"], kind="stable").groupby("symbol").tail(1).copy()
    last["above_cap"] = last["value"] > 0
    return last[columns].sort_values("value", ascending=False, kind="stable").reset_index(drop=True)


# --------------------------------------------------------------------------
# Events
# --------------------------------------------------------------------------
EVENT_COLUMNS = (
    "date",
    "ts_ist",
    "first_tradable_session",
    "category",
    "type",
    "direction",
    "symbols",
    "description",
    "source_primary",
    "url",
    "verified",
    "event_id",
)
"""Order of the events-log columns (those that exist in the table)."""


def events_table(events: pd.DataFrame | None) -> pd.DataFrame:
    """The ``events`` clean table for display: newest first, publication time as IST text.

    ``ts_ist`` becomes ``YYYY-MM-DD HH:MM IST`` (empty when unknown); dates stay ``date``
    objects; ``verified`` missing counts as False. A missing or empty table gives an empty frame
    with the standard columns. Nothing is filtered out.
    """
    if events is None or events.empty:
        return pd.DataFrame(columns=list(EVENT_COLUMNS))
    df = events.copy()
    keep = [c for c in EVENT_COLUMNS if c in df.columns]
    for col in ("date", "first_tradable_session"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce").dt.date
    sort_ts = pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns, Asia/Kolkata]")
    if "ts_ist" in df.columns:
        stamps = pd.to_datetime(df["ts_ist"], errors="coerce")
        if len(stamps.dropna()) and stamps.dt.tz is None:
            raise ValueError("events.ts_ist must be timezone-aware (project rule)")
        if stamps.dt.tz is not None:
            stamps = stamps.dt.tz_convert(IST)
            sort_ts = stamps
        df["ts_ist"] = [f"{t:%Y-%m-%d %H:%M} IST" if pd.notna(t) else "" for t in stamps]
    if "verified" in df.columns:
        df["verified"] = _flag(df, "verified")
    sort_date = pd.to_datetime(df["date"]) if "date" in df.columns else sort_ts
    df = df.assign(_d=sort_date, _t=sort_ts).sort_values(
        ["_d", "_t"], ascending=False, na_position="last", kind="stable"
    )
    return df[keep].reset_index(drop=True)


def _event_symbols(value: Any) -> set[str]:
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return set()
    return {s.strip() for s in str(value).split(";") if s.strip()}


def event_markers(
    events: pd.DataFrame | None,
    symbols: Sequence[str] = (),
    *,
    skip_rule_days: Iterable[date] = (),
) -> list[Marker]:
    """One chart marker per event that concerns any of ``symbols`` (empty ``symbols`` column =
    all ETFs; empty ``symbols`` argument = every event).

    The marker sits on ``first_tradable_session`` (the first session whose prices could react),
    falling back to ``date``. ``rule`` events on a day in ``skip_rule_days`` are left out: those
    are already drawn as price-band regime markers.
    """
    if events is None or events.empty:
        return []
    wanted = set(symbols)
    skip = set(skip_rule_days)
    out: list[Marker] = []
    for _, row in events.iterrows():
        day = None
        for col in ("first_tradable_session", "date"):
            if col in events.columns and pd.notna(row[col]):
                day = pd.Timestamp(row[col]).date()
                break
        if day is None:
            continue
        affected = _event_symbols(row.get("symbols"))
        if wanted and affected and not (affected & wanted):
            continue
        category = row.get("category")
        category = "" if category is None or pd.isna(category) else str(category)
        if category == "rule" and day in skip:
            continue
        text = row.get("description")
        text = "" if text is None or pd.isna(text) else str(text)
        if len(text) > 60:
            text = text[:57] + "..."
        label = f"{category}: {text}" if category else text
        out.append(Marker(day, label, False, "event"))
    return sorted(out, key=lambda m: m.day)


# --------------------------------------------------------------------------
# Intraday iNAV
# --------------------------------------------------------------------------
def snapshot_days(snapshots: pd.DataFrame | None) -> list[date]:
    """IST dates that have snapshots, newest first."""
    if snapshots is None or snapshots.empty or "ts" not in snapshots.columns:
        return []
    stamps = pd.to_datetime(snapshots["ts"])
    if stamps.dt.tz is None:
        raise ValueError("snapshot timestamps must be timezone-aware (project rule)")
    return sorted(set(stamps.dt.tz_convert(IST).dt.date), reverse=True)


def intraday_premium(
    snapshots: pd.DataFrame | None,
    symbol: str,
    day: date,
    sources: Sequence[str],
    max_gap_minutes: float = 10.0,
) -> pd.DataFrame:
    """Price, iNAV and premium (fraction) for one ETF over one day: see
    ``etfp.alerts.rules.paired_inav_premium`` for how a price and an iNAV are paired."""
    if snapshots is None or snapshots.empty or "symbol" not in snapshots.columns:
        return paired_inav_premium(None, day, sources)
    own = snapshots[snapshots["symbol"] == symbol]
    return paired_inav_premium(own, day, sources, max_gap_minutes)


# --------------------------------------------------------------------------
# Data quality
# --------------------------------------------------------------------------
def _dq_description(flag: str) -> str:
    try:  # the column definitions live with the export code; the dashboard works without them
        from etfp.export import ETF_DAY_COLUMNS

        return str(ETF_DAY_COLUMNS[flag].definition)
    except (ImportError, KeyError, AttributeError):
        return ""


def dq_summary(etf_day: pd.DataFrame | None) -> pd.DataFrame:
    """Count of rows carrying each ``dq_*`` flag, plus what the latest day shows.

    A flag means "a human should look" (SPEC 12); nothing is dropped or corrected. Columns:
    flag, meaning, rows_flagged, rows_checked (flag not missing), latest_day_flagged (symbols),
    last_flagged_date.
    """
    columns = [
        "flag",
        "meaning",
        "rows_flagged",
        "rows_checked",
        "latest_day_flagged",
        "last_flagged_date",
    ]
    df = _prepared(etf_day)
    flags = [c for c in df.columns if c.startswith("dq_")]
    if df.empty or not flags:
        return pd.DataFrame(columns=columns)
    newest = df["_d"].max()
    rows = []
    for col in flags:
        values = df[col]
        hit = values.fillna(False).astype(bool)
        latest = df.loc[hit.to_numpy() & (df["_d"] == newest).to_numpy(), "symbol"]
        flagged_days = df.loc[hit.to_numpy(), "_d"]
        rows.append(
            {
                "flag": col,
                "meaning": _dq_description(col),
                "rows_flagged": int(hit.sum()),
                "rows_checked": int(values.notna().sum()),
                "latest_day_flagged": ", ".join(sorted({str(s) for s in latest})),
                "last_flagged_date": flagged_days.max() if len(flagged_days) else None,
            }
        )
    return pd.DataFrame(rows, columns=columns)


_SOURCE_DATE_COLUMNS = (
    ("etf_day", "date"),
    ("prices_daily", "date"),
    ("nav_daily", "nav_date"),
    ("units_daily", "date"),
    ("band_official", "file_date"),
    ("underlying_daily", "date"),
    ("fx_daily", "date"),
    ("events", "date"),
)


def last_data_dates(tables: Tables, today: date) -> pd.DataFrame:
    """Latest date in each source table (and each iNAV source), with its age in days.

    Columns: source, latest (text), rows, age_days (today minus the latest date; None if the table
    is empty or missing). A table that was never built shows ``latest = "not built"``.
    """
    rows: list[dict[str, Any]] = []
    for name, column in _SOURCE_DATE_COLUMNS:
        frame = tables.get(name)
        if frame is None:
            rows.append({"source": name, "latest": "not built", "rows": 0, "age_days": None})
            continue
        latest = _dates(frame[column]).max() if len(frame) and column in frame.columns else None
        rows.append(
            {
                "source": name,
                "latest": latest.isoformat() if latest else "no rows",
                "rows": len(frame),
                "age_days": (today - latest).days if latest else None,
            }
        )
    snaps = tables.get("inav_snapshots")
    if snaps is None or snaps.empty or not {"ts", "source"} <= set(snaps.columns):
        rows.append(
            {
                "source": "inav_snapshots",
                "latest": "not built" if snaps is None else "no rows",
                "rows": 0,
                "age_days": None,
            }
        )
    else:
        stamps = pd.to_datetime(snaps["ts"]).dt.tz_convert(IST)
        for source, group in snaps.assign(_ts=stamps).groupby("source", sort=True):
            latest_ts = group["_ts"].max()
            rows.append(
                {
                    "source": f"inav_snapshots / {source}",
                    "latest": f"{latest_ts:%Y-%m-%d %H:%M} IST",
                    "rows": len(group),
                    "age_days": (today - latest_ts.date()).days,
                }
            )
    return pd.DataFrame(rows, columns=["source", "latest", "rows", "age_days"])
