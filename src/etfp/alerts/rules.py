"""Alert rules (SPEC section 10): turn the latest data into a list of plain-words alerts.

What this module does, in plain words
-------------------------------------
``evaluate`` looks at the analysis table ``etf_day`` (one row per ETF per trading day), the
intraday iNAV snapshots and the recorder's heartbeat file, and returns the alerts that are true
*right now*. It does not send anything, remember anything or read the clock: the caller passes
``now``. That keeps it easy to test with small hand-made tables.

Whether an alert was already sent is decided elsewhere (``etfp.alerts.runner``). Here every alert
carries a ``key`` that says exactly what it is about; the same situation always gives the same
key, so the runner can drop repeats.

Units: premiums in ``etf_day`` are fractions (0.25 = a 25% premium). The yaml file speaks
percent (``levels_pct: [25, 50]``) and "points" (percentage points). The conversion happens once,
inside this module. A premium over fair value (``prem_fv``) is ``close / fair value - 1``; fair
value is the latest known NAV moved to the Indian close (SPEC 6.2).

Wording rule (CLAUDE.md): alerts describe what the data shows. They never suggest an action.
"""

from __future__ import annotations

import math
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Literal

import pandas as pd
import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from etfp import calendars, paths
from etfp.config import CollectSettings
from etfp.timeutil import IST, require_aware

# --------------------------------------------------------------------------
# Texts
# --------------------------------------------------------------------------
# Same wording as SPEC section 9, so the dashboard and the alerts say the same thing.
DISCLAIMER_SHORT = (
    "For research and information only. Not investment advice. "
    "Figures may contain errors; check official NSE and AMFI data before acting."
)

RULE_PREMIUM_CROSS = "premium_cross"
RULE_BAND_TOUCH = "band_touch"
RULE_PREMIUM_JUMP = "premium_jump"
RULE_INAV_CROSS = "inav_premium_cross"
RULE_DATA_STALE = "data_stale"
RULE_RECORDER_STALE = "recorder_stale"
RULE_SOURCE_FAILED = "source_failed"

Measure = Literal["prem_fv", "prem_nav_known", "prem_raw", "prem_known", "prem_band_ref"]

_MEASURE_WORDS: dict[str, str] = {
    "prem_fv": "premium over fair value",
    "prem_nav_known": "premium over the latest known NAV",
    "prem_raw": "premium over the same-day NAV",
    "prem_known": "premium over the latest known NAV",
    "prem_band_ref": "premium over the T-2 NAV",
}


# --------------------------------------------------------------------------
# Configuration (config/alerts.yaml)
# --------------------------------------------------------------------------
class _Frozen(BaseModel):
    """Unknown yaml keys are errors, so a typo in a threshold name cannot pass silently."""

    model_config = ConfigDict(frozen=True, extra="forbid")


def _check_levels(values: list[float]) -> list[float]:
    if not values:
        raise ValueError("levels_pct needs at least one level")
    if any(v <= 0 for v in values):
        raise ValueError("levels_pct must all be above 0 (percent)")
    return sorted(set(values))


class PremiumCrossRule(_Frozen):
    enabled: bool = True
    measure: Measure = "prem_fv"
    levels_pct: list[float]

    _levels = field_validator("levels_pct")(_check_levels)


class BandTouchRule(_Frozen):
    enabled: bool = True


class PremiumJumpRule(_Frozen):
    enabled: bool = True
    measure: Measure = "prem_fv"
    points: float = Field(gt=0)  # percentage points


class InavCrossRule(_Frozen):
    enabled: bool = True
    levels_pct: list[float]
    inav_sources: list[str] = Field(min_length=1)
    max_pair_gap_minutes: float = Field(gt=0)

    _levels = field_validator("levels_pct")(_check_levels)


class DataFailureRule(_Frozen):
    enabled: bool = True
    expected_by_ist: time
    check_recorder: bool = True

    @field_validator("expected_by_ist", mode="before")
    @classmethod
    def _quoted_hhmm(cls, value: Any) -> Any:
        # An unquoted 21:00 would be read by yaml as a number (base-60), so insist on text.
        if not isinstance(value, str):
            raise ValueError(f'times must be quoted strings like "21:00", got {value!r}')
        return time.fromisoformat(value)


class AlertConfig(_Frozen):
    premium_cross: PremiumCrossRule
    band_touch: BandTouchRule
    premium_jump: PremiumJumpRule
    inav_premium_cross: InavCrossRule
    data_failure: DataFailureRule
    dedupe_keep_days: int = Field(ge=1)


def load_alert_config(path: Path | None = None) -> AlertConfig:
    """Load ``config/alerts.yaml`` (or ``path``). Errors name the file."""
    path = path or paths.config_dir() / "alerts.yaml"
    if not path.exists():
        raise FileNotFoundError(f"config file not found: {path}")
    with path.open(encoding="utf-8") as fh:
        raw: Any = yaml.safe_load(fh)
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: expected a mapping at the top level")
    try:
        return AlertConfig.model_validate(raw)
    except ValueError as exc:
        raise ValueError(f"{path}: invalid configuration:\n{exc}") from exc


# --------------------------------------------------------------------------
# The alert record
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Alert:
    """One thing worth telling the owner about.

    ``day`` is the day the alert is about (the data day, or today for data-failure alerts).
    ``key`` is stable: rule, symbol, day and any variant (direction, level), joined by ``|``.
    """

    rule: str
    symbol: str | None
    day: date
    key: str
    message: str


def make_key(rule: str, symbol: str | None, day: date, *variant: str) -> str:
    return "|".join([rule, symbol or "*", day.isoformat(), *variant])


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
def _num(value: Any) -> float | None:
    """A finite float, or None for missing / NaN / infinite / unparseable."""
    try:
        if value is None or pd.isna(value):
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _flag(value: Any) -> bool:
    """True only for a real True. Missing (NA / NaN / None) counts as False, never as True."""
    try:
        if value is None or pd.isna(value):
            return False
    except (TypeError, ValueError):
        return False
    return bool(value)


def _pct(fraction: float) -> str:
    return f"{fraction * 100:.1f}%"


def _points(fraction: float) -> str:
    return f"{abs(fraction) * 100:.1f} points"


def _level_text(level_pct: float) -> str:
    return f"{level_pct:g}%"


def _rupees(value: float) -> str:
    return f"Rs {value:,.2f}"


def _fractions(levels_pct: Iterable[float]) -> list[float]:
    return [round(v / 100.0, 12) for v in levels_pct]


def _above(value: float, level: float) -> bool:
    """'At or above the level'. Rounded so 0.25 vs 0.2500000000001 cannot flip a result."""
    return round(value, 12) >= round(level, 12)


def levels_crossed(
    prev: float, cur: float, levels: Iterable[float]
) -> tuple[list[float], list[float]]:
    """Levels passed between two readings: (crossed upward, crossed downward).

    "At or above L" is the state being tracked. Up = was below L, is now at or above L.
    Down = was at or above L, is now below L. Hand example: 0.20 -> 0.55 with levels
    0.25, 0.50, 1.00 crosses 0.25 and 0.50 upward and nothing downward.
    """
    up = [lv for lv in levels if not _above(prev, lv) and _above(cur, lv)]
    down = [lv for lv in levels if _above(prev, lv) and not _above(cur, lv)]
    return up, down


def _as_dates(series: pd.Series) -> pd.Series:
    """Python dates from a column of dates, ISO strings or midnight Timestamps."""
    return pd.to_datetime(series).dt.date


def _prepare_etf_day(etf_day: pd.DataFrame, tracked: Collection[str] | None) -> pd.DataFrame:
    """Sorted copy with a plain ``_day`` column; one row per (symbol, day); tracked only."""
    if etf_day is None or etf_day.empty or not {"symbol", "date"} <= set(etf_day.columns):
        return pd.DataFrame(columns=["symbol", "_day"])
    df = etf_day.copy()
    if tracked is not None:
        df = df[df["symbol"].isin(set(tracked))]
    if df.empty:
        return pd.DataFrame(columns=[*etf_day.columns, "_day"])
    df["_day"] = _as_dates(df["date"])
    df = df.sort_values(["symbol", "_day"], kind="stable")
    return df.drop_duplicates(["symbol", "_day"], keep="last").reset_index(drop=True)


def _latest_and_previous(df: pd.DataFrame) -> list[tuple[str, pd.Series, pd.Series | None]]:
    """For each symbol traded on the table's latest day: (symbol, latest row, previous row).

    A symbol whose last row is older than the table's latest day is skipped: it did not trade
    (or was not built) on the latest day, and old news is not re-announced.
    """
    if df.empty:
        return []
    newest = df["_day"].max()
    out: list[tuple[str, pd.Series, pd.Series | None]] = []
    for symbol, group in df.groupby("symbol", sort=True):
        if group["_day"].iloc[-1] != newest:
            continue
        previous = group.iloc[-2] if len(group) > 1 else None
        out.append((str(symbol), group.iloc[-1], previous))
    return out


def _method_note(row: pd.Series, measure: str) -> str:
    method = row.get("fv_method") if measure == "prem_fv" else None
    if method is None or pd.isna(method):
        return ""
    note = f" Fair value method: {method}."
    if method == "nav_only":
        note += " (No market adjustment: fair value equals the latest known NAV.)"
    return note


# --------------------------------------------------------------------------
# Rules on etf_day
# --------------------------------------------------------------------------
def _premium_cross(df: pd.DataFrame, rule: PremiumCrossRule) -> list[Alert]:
    if rule.measure not in df.columns:
        return []
    levels_pct = rule.levels_pct
    levels = _fractions(levels_pct)
    alerts: list[Alert] = []
    for symbol, cur, prev in _latest_and_previous(df):
        now_val = _num(cur[rule.measure])
        before = _num(prev[rule.measure]) if prev is not None else None
        if now_val is None or before is None or prev is None:
            continue  # cannot tell a crossing without both readings
        up, down = levels_crossed(before, now_val, levels)
        day: date = cur["_day"]
        word = _MEASURE_WORDS[rule.measure]
        for direction, hit, verb in (("up", up, "rose above"), ("down", down, "fell below")):
            if not hit:
                continue
            texts = [_level_text(levels_pct[levels.index(lv)]) for lv in hit]
            text = texts[0] if len(texts) == 1 else ", ".join(texts[:-1]) + " and " + texts[-1]
            msg = (
                f"{symbol}: {word} {verb} {text} on {day.isoformat()}: now {_pct(now_val)} "
                f"(previous session {prev['_day'].isoformat()}: {_pct(before)})."
                f"{_method_note(cur, rule.measure)}"
            )
            alerts.append(
                Alert(
                    RULE_PREMIUM_CROSS,
                    symbol,
                    day,
                    make_key(RULE_PREMIUM_CROSS, symbol, day, direction),
                    msg,
                )
            )
    return alerts


def _band_touch(df: pd.DataFrame) -> list[Alert]:
    alerts: list[Alert] = []
    sides = (
        ("upper", ("close_at_upper", "last_at_up"), "band_high_official", "band_high_initial"),
        ("lower", ("close_at_lower", "last_at_down"), "band_low_official", "band_low_initial"),
    )
    for symbol, cur, _prev in _latest_and_previous(df):
        day: date = cur["_day"]
        for side, flag_cols, official_col, initial_col in sides:
            hits = [c for c in flag_cols if c in df.columns and _flag(cur[c])]
            if not hits:
                continue
            what = []
            if flag_cols[0] in hits:
                what.append("the close")
            if flag_cols[1] in hits:
                what.append("the last trade")
            limit = _num(cur.get(official_col)) or _num(cur.get(initial_col))
            close = _num(cur.get("close"))
            detail = []
            if close is not None:
                detail.append(f"close {_rupees(close)}")
            if limit is not None:
                detail.append(f"{side} limit {_rupees(limit)}")
            tail = f" ({'; '.join(detail)})" if detail else ""
            msg = (
                f"{symbol}: {' and '.join(what)} sat at the {side} price limit on "
                f"{day.isoformat()}{tail}."
            )
            alerts.append(
                Alert(
                    RULE_BAND_TOUCH, symbol, day, make_key(RULE_BAND_TOUCH, symbol, day, side), msg
                )
            )
    return alerts


def _premium_jump(df: pd.DataFrame, rule: PremiumJumpRule) -> list[Alert]:
    if rule.measure not in df.columns:
        return []
    alerts: list[Alert] = []
    for symbol, cur, prev in _latest_and_previous(df):
        now_val = _num(cur[rule.measure])
        before = _num(prev[rule.measure]) if prev is not None else None
        if now_val is None or before is None or prev is None:
            continue
        change = now_val - before
        if round(abs(change) * 100, 9) <= rule.points:
            continue
        day: date = cur["_day"]
        verb = "rose" if change > 0 else "fell"
        msg = (
            f"{symbol}: {_MEASURE_WORDS[rule.measure]} {verb} by {_points(change)} on "
            f"{day.isoformat()}: {_pct(now_val)} from {_pct(before)} on "
            f"{prev['_day'].isoformat()} (alert threshold {rule.points:g} points)."
            f"{_method_note(cur, rule.measure)}"
        )
        alerts.append(
            Alert(RULE_PREMIUM_JUMP, symbol, day, make_key(RULE_PREMIUM_JUMP, symbol, day), msg)
        )
    return alerts


# --------------------------------------------------------------------------
# Intraday iNAV premium (snapshots)
# --------------------------------------------------------------------------
_PAIRED_COLUMNS = ["symbol", "ts", "source", "inav", "last_price", "prem"]


def paired_inav_premium(
    snapshots: pd.DataFrame | None,
    day: date,
    sources: Iterable[str],
    max_gap_minutes: float = 10.0,
) -> pd.DataFrame:
    """Premium of the last traded price over the iNAV, per snapshot time, for one IST day.

    Why pairing is needed: fund-house (AMC) pages give the iNAV but no exchange price, and the
    NSE feed gives the price. Each iNAV row is matched with the nearest price row of the same
    ETF; if the two were fetched more than ``max_gap_minutes`` apart there is no pair.
    Per ETF only the first source in ``sources`` that has any iNAV today is used, so two sources
    that disagree cannot make the series flip-flop.

    Returns columns symbol, ts (IST), source, inav, last_price, prem (fraction), sorted by
    symbol and time. ``prem = last_price / inav - 1``. Empty if nothing can be paired.
    """
    empty = pd.DataFrame({c: [] for c in _PAIRED_COLUMNS})
    needed = {"symbol", "ts", "source", "last_price", "inav"}
    if snapshots is None or snapshots.empty or not needed <= set(snapshots.columns):
        return empty
    snap = snapshots[["symbol", "ts", "source", "last_price", "inav"]].copy()
    stamps = pd.to_datetime(snap["ts"])
    if stamps.dt.tz is None:
        raise ValueError("snapshot timestamps must be timezone-aware (project rule)")
    snap["ts"] = stamps.dt.tz_convert(IST)
    snap = snap[snap["ts"].dt.date == day]
    snap["inav"] = pd.to_numeric(snap["inav"], errors="coerce")
    snap["last_price"] = pd.to_numeric(snap["last_price"], errors="coerce")

    inav_rows = snap[(snap["inav"] > 0) & snap["source"].isin(list(sources))]
    price_rows = snap[snap["last_price"] > 0]
    if inav_rows.empty or price_rows.empty:
        return empty

    order = list(sources)
    chosen: dict[str, str] = {}
    for symbol, group in inav_rows.groupby("symbol"):
        present = set(group["source"])
        chosen[str(symbol)] = next(s for s in order if s in present)
    inav_rows = inav_rows[inav_rows["symbol"].map(chosen) == inav_rows["source"]]

    left = inav_rows[["symbol", "ts", "source", "inav"]].sort_values("ts")
    right = (
        price_rows[["symbol", "ts", "last_price"]]
        .rename(columns={"ts": "price_ts"})
        .sort_values("price_ts")
    )
    paired = pd.merge_asof(
        left,
        right,
        left_on="ts",
        right_on="price_ts",
        by="symbol",
        direction="nearest",
        tolerance=pd.Timedelta(minutes=max_gap_minutes),
    )
    paired = paired.dropna(subset=["last_price"])
    if paired.empty:
        return empty
    paired["prem"] = paired["last_price"] / paired["inav"] - 1.0
    paired = paired.drop_duplicates(["symbol", "ts"], keep="last")
    return paired[_PAIRED_COLUMNS].sort_values(["symbol", "ts"]).reset_index(drop=True)


def _inav_cross(snapshots: pd.DataFrame | None, rule: InavCrossRule, day: date) -> list[Alert]:
    paired = paired_inav_premium(snapshots, day, rule.inav_sources, rule.max_pair_gap_minutes)
    levels_pct = rule.levels_pct
    levels = _fractions(levels_pct)
    alerts: list[Alert] = []
    seen: set[str] = set()
    for symbol, group in paired.groupby("symbol", sort=True):
        rows = group.reset_index(drop=True)
        for i in range(1, len(rows)):
            before, cur = float(rows.at[i - 1, "prem"]), float(rows.at[i, "prem"])
            up, down = levels_crossed(before, cur, levels)
            for direction, hit, verb in (("up", up, "rose above"), ("down", down, "fell below")):
                if not hit:
                    continue
                top = hit[-1] if direction == "up" else hit[0]
                key = make_key(
                    RULE_INAV_CROSS,
                    str(symbol),
                    day,
                    direction,
                    _level_text(levels_pct[levels.index(top)]),
                )
                if key in seen:
                    continue  # the same level crossed again later the same day
                seen.add(key)
                texts = [_level_text(levels_pct[levels.index(lv)]) for lv in hit]
                text = texts[0] if len(texts) == 1 else ", ".join(texts[:-1]) + " and " + texts[-1]
                at = rows.at[i, "ts"].strftime("%H:%M")
                msg = (
                    f"{symbol}: intraday premium over iNAV {verb} {text} at {at} IST on "
                    f"{day.isoformat()}: last price {_rupees(float(rows.at[i, 'last_price']))} vs "
                    f"iNAV {_rupees(float(rows.at[i, 'inav']))} = {_pct(cur)} "
                    f"(previous reading {_pct(before)}; iNAV source {rows.at[i, 'source']})."
                )
                alerts.append(Alert(RULE_INAV_CROSS, str(symbol), day, key, msg))
    return alerts


# --------------------------------------------------------------------------
# Data-failure rules
# --------------------------------------------------------------------------
def expected_session(now: datetime, expected_by: time) -> date | None:
    """The NSE session whose data should already be built at ``now`` (IST).

    On a session day the answer is today once the clock passes ``expected_by``, else the
    session before it. On a weekend or holiday it is the last session before today. Returns
    None if the calendar does not cover the date (the caller then skips the check).
    """
    local = now.astimezone(IST)
    today = local.date()
    try:
        if calendars.is_nse_session(today):
            return today if local.time() >= expected_by else calendars.previous_session("IN", today)
        return calendars.last_session_on_or_before("IN", today)
    except ValueError:
        return None


def _data_stale(etf_day: pd.DataFrame, now: datetime, rule: DataFailureRule) -> list[Alert]:
    today = now.astimezone(IST).date()
    expected = expected_session(now, rule.expected_by_ist)
    if expected is None:
        return []
    key = make_key(RULE_DATA_STALE, None, today)
    if etf_day is None or etf_day.empty or "date" not in etf_day.columns:
        msg = "Data check: the ETF table is empty or missing. Run `uv run etfp build`."
        return [Alert(RULE_DATA_STALE, None, today, key, msg)]
    latest = _as_dates(etf_day["date"]).max()
    if latest >= expected:
        return []
    msg = (
        f"Data check: the latest ETF data is for {latest.isoformat()}, but the NSE session of "
        f"{expected.isoformat()} should be built by {rule.expected_by_ist:%H:%M} IST. "
        "A download or the build may have failed."
    )
    return [Alert(RULE_DATA_STALE, None, today, key, msg)]


def _parse_stamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError:
        return None
    return stamp if stamp.tzinfo is not None else None


def _recorder_alerts(
    heartbeat: dict | None, now: datetime, collect: CollectSettings
) -> list[Alert]:
    local = now.astimezone(IST)
    today = local.date()
    inav = collect.inav
    if not calendars.is_nse_session(today):
        return []
    stale_after = timedelta(minutes=inav.heartbeat_stale_minutes)
    window_start = datetime.combine(today, inav.start, tzinfo=IST) + stale_after
    window_end = datetime.combine(today, inav.post_close, tzinfo=IST)
    if not window_start <= local <= window_end:
        return []  # the recorder is not expected to be ticking right now

    alerts: list[Alert] = []
    finished = _parse_stamp(heartbeat.get("last_tick_finished")) if heartbeat else None
    key = make_key(RULE_RECORDER_STALE, None, today)
    if finished is None:
        msg = (
            "iNAV recorder: no readable heartbeat while the market is open. "
            "Check `uv run etfp status`."
        )
        return [Alert(RULE_RECORDER_STALE, None, today, key, msg)]
    age = local - finished.astimezone(IST)
    if age > stale_after:
        minutes = int(age.total_seconds() // 60)
        msg = (
            "iNAV recorder: the last tick finished at "
            f"{finished.astimezone(IST):%Y-%m-%d %H:%M} IST, {minutes} minutes ago "
            f"(limit {inav.heartbeat_stale_minutes}). It may have stopped."
        )
        alerts.append(Alert(RULE_RECORDER_STALE, None, today, key, msg))
        return alerts  # a stopped recorder says nothing new about its individual sources

    sources = heartbeat.get("sources") if heartbeat else None
    if isinstance(sources, dict) and finished.astimezone(IST).date() == today:
        for name in sorted(sources):
            info = sources[name]
            if not isinstance(info, dict) or info.get("ok", True):
                continue
            last_ok = info.get("last_ok_at") or "never"
            msg = (
                f"iNAV recorder: source {name} failed in the last tick "
                f"({info.get('error_type') or 'unknown error'}); it last worked at {last_ok}."
            )
            alerts.append(
                Alert(
                    RULE_SOURCE_FAILED, name, today, make_key(RULE_SOURCE_FAILED, name, today), msg
                )
            )
    return alerts


# --------------------------------------------------------------------------
# The entry point
# --------------------------------------------------------------------------
def evaluate(
    etf_day: pd.DataFrame | None,
    *,
    config: AlertConfig,
    now: datetime,
    tracked: Collection[str] | None = None,
    snapshots: pd.DataFrame | None = None,
    heartbeat: dict | None = None,
    collect: CollectSettings | None = None,
) -> list[Alert]:
    """All alerts that are true at ``now``. Pure: reads nothing, sends nothing, remembers nothing.

    ``tracked`` limits the premium/band rules to those symbols (None = every symbol in the
    table). ``collect`` enables the recorder checks (they need the iNAV schedule); without it
    those checks are skipped.
    """
    require_aware(now, "now")
    today = now.astimezone(IST).date()
    frame = _prepare_etf_day(etf_day if etf_day is not None else pd.DataFrame(), tracked)

    alerts: list[Alert] = []
    if config.premium_cross.enabled:
        alerts += _premium_cross(frame, config.premium_cross)
    if config.band_touch.enabled:
        alerts += _band_touch(frame)
    if config.premium_jump.enabled:
        alerts += _premium_jump(frame, config.premium_jump)
    if config.inav_premium_cross.enabled:
        snaps = snapshots
        if snaps is not None and tracked is not None and "symbol" in snaps.columns:
            snaps = snaps[snaps["symbol"].isin(set(tracked))]
        alerts += _inav_cross(snaps, config.inav_premium_cross, today)
    if config.data_failure.enabled:
        alerts += _data_stale(
            etf_day if etf_day is not None else pd.DataFrame(), now, config.data_failure
        )
        if config.data_failure.check_recorder and collect is not None:
            alerts += _recorder_alerts(heartbeat, now, collect)

    unique: dict[str, Alert] = {}
    for alert in alerts:
        unique.setdefault(alert.key, alert)
    return list(unique.values())
