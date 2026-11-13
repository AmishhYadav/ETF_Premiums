"""Price-band calculator (SPEC 6.4 and 7, DECISIONS D4-D6, REQUESTS R2 and R10).

What a price band is (plain words)
----------------------------------
Every trading day the exchange draws a corridor around a "base price". No trade
may happen outside the corridor. What the base price is has changed:

* ``nav_t2``     until 4 Sep 2026: base = closing NAV from two Indian trading days
                 earlier. Corridor fixed at +/-20%.
* ``prev_close`` from 7 Sep 2026: base = yesterday's closing PRICE. Equity ETFs
                 start at +/-10%; after a pause the corridor widens by 5% of the
                 base, at most twice per side (so +/-20% at most). Gold and silver
                 start at +/-6% and widen in 3% steps with no cap.
* ``nav_t1``     planned from 1 Apr 2027: base = yesterday's closing NAV.

Why this matters for the research: with a NAV base the price can never run far
above NAV, so a high premium is "cut off" (censored) by the corridor. With a
price base the corridor moves up with the price and the premium is free.

Rounding (D5, PRELIMINARY: seen on one day only)
------------------------------------------------
Limits sit on the price tick (Rs 0.01). The low limit is rounded UP and the
high limit DOWN, so a limit never lies outside the true percentage corridor.
Example seen in official data on 9 Nov 2026: base 229.67 gives
229.67 x 0.9 = 206.703 -> 206.71 and 229.67 x 1.1 = 252.637 -> 252.63.
The tick size and rounding direction are read from ``config/rules.yaml``
(``tick_size``, ``rounding``) and can be changed there. All price arithmetic
uses ``Decimal`` so 100 x 0.9 never becomes 90.00000000000001 and rounds the
wrong way.

Which base goes in (the caller's job)
-------------------------------------
``nav_t2``      NAV *dated* two Indian trading days before the session
                (``nav_align.align_nav`` column ``nav_t2``).
``prev_close``  previous trading day's NSE close (the volume-weighted average of
                the last 30 minutes, D3), not the last trade.
``nav_t1``      NAV dated the previous Indian trading day.

Official band (R2)
------------------
NSE's security-master file dated *d* carries ``PricRg``: the initial band for the
NEXT session after *d* (D4). :func:`attach_official_band` makes that explicit by
storing both dates, and :func:`compare_bands` flags a mismatch beyond one tick.

Flags (D6)
----------
The exchange close is a 30-minute average, so "closed at the limit" cannot be
read from it. Instead we store separate facts, for each side:

* the day's high (low) touched the INITIAL limit,
* the day's high (low) touched the MAXIMUM limit,
* the last traded price sat at the limit that was in force when the day ended.

A widening is inferred from ``high > initial band high``; exact timing needs
minute bars (v2).

Everything here is a pure function: no clock, no files, no network.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import date
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import Any, Literal

import pandas as pd

from etfp import calendars
from etfp.config import BandRules, BandSpec, Regime

RoundMode = Literal["up", "down", "nearest"]

_ROUNDING = {"up": ROUND_CEILING, "down": ROUND_FLOOR, "nearest": ROUND_HALF_UP}
_MAX_UNCAPPED_STEPS = 200  # safety stop when searching levels of an uncapped (gold) band


class MissingBaseError(ValueError):
    """The base price needed by the regime in force was not supplied (or is NaN)."""


# --------------------------------------------------------------------------
# Small exact-arithmetic helpers
# --------------------------------------------------------------------------
def _dec(value: float | int | Decimal) -> Decimal:
    """Exact decimal for a float as printed (229.67 -> Decimal('229.67'))."""
    if isinstance(value, Decimal):
        return value
    return Decimal(str(float(value)))


def _missing(value: Any) -> bool:
    return value is None or bool(pd.isna(value))


def snap_to_tick(value: Decimal, tick: Decimal, mode: RoundMode) -> Decimal:
    """Round ``value`` to a multiple of ``tick``: ``up`` = ceiling, ``down`` = floor."""
    steps = (value / tick).to_integral_value(rounding=_ROUNDING[mode])
    return steps * tick


def band_limits(
    base: float | Decimal,
    pct: float | Decimal,
    *,
    tick: float | Decimal,
    low_mode: RoundMode,
    high_mode: RoundMode,
) -> tuple[Decimal, Decimal]:
    """Low and high limit for a corridor of +/-``pct`` percent around ``base``.

    Hand example: base 229.67, pct 10 -> low 229.67 x 90 / 100 = 206.703, rounded
    up to the tick = 206.71; high 229.67 x 110 / 100 = 252.637, rounded down =
    252.63. The low limit never goes below 0.
    """
    b, p, t = _dec(base), _dec(pct), _dec(tick)
    low = snap_to_tick(b * (Decimal(100) - p) / Decimal(100), t, low_mode)
    high = snap_to_tick(b * (Decimal(100) + p) / Decimal(100), t, high_mode)
    return max(low, Decimal(0)), high


# --------------------------------------------------------------------------
# The band for one session
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Band:
    """Computed price corridor for one session.

    ``*_initial`` is the corridor at the start of the day. ``*_max`` is the widest
    corridor after all allowed widenings. For gold and silver there is no cap, so
    ``uncapped`` is True, ``high_max`` is ``inf`` and ``low_max`` is 0.0.
    """

    session: date
    regime: str
    base_kind: str
    base_price: float
    asset_class: str
    initial_pct: float
    step_pct: float
    max_widenings: int | None  # per side; None = no cap
    low_initial: float
    high_initial: float
    low_max: float
    high_max: float
    uncapped: bool
    tick: float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _level_pct(spec: BandSpec, level: int) -> Decimal:
    return _dec(spec.initial_pct) + _dec(spec.step_pct) * level


def limits_at_level(
    base: float | Decimal, spec: BandSpec, level: int, rules: BandRules
) -> tuple[Decimal, Decimal]:
    """Limits after ``level`` widenings (0 = the opening corridor)."""
    if level < 0:
        raise ValueError("level must be >= 0")
    if spec.max_widenings is not None and level > spec.max_widenings:
        raise ValueError(f"level {level} exceeds max_widenings {spec.max_widenings}")
    return band_limits(
        base,
        _level_pct(spec, level),
        tick=rules.tick_size,
        low_mode=rules.rounding.band_low,
        high_mode=rules.rounding.band_high,
    )


def pick_base(
    regime: Regime,
    *,
    nav_t2: float | None = None,
    prev_close: float | None = None,
    nav_t1: float | None = None,
) -> float:
    """The base price the regime asks for; raises ``MissingBaseError`` if it is absent."""
    supplied = {"nav_t2": nav_t2, "prev_close": prev_close, "nav_t1": nav_t1}
    value = supplied[regime.base]
    if _missing(value):
        raise MissingBaseError(f"regime {regime.name} needs base {regime.base!r} but it is missing")
    if float(value) <= 0:  # type: ignore[arg-type]
        raise ValueError(f"base {regime.base} must be positive, got {value!r}")
    return float(value)  # type: ignore[arg-type]


def compute_band(
    session: date,
    asset_class: str,
    rules: BandRules,
    *,
    nav_t2: float | None = None,
    prev_close: float | None = None,
    nav_t1: float | None = None,
) -> Band:
    """Initial and maximum corridor for ``session``.

    Worked examples (all hand computed, tick 0.01, low up / high down):

    * 9 Nov 2026 file, base 229.67, equity ``prev_close``: initial
      206.71-252.63; maximum (+/-20%): 183.74-275.60 (229.67 x 0.8 = 183.736 up
      to 183.74; 229.67 x 1.2 = 275.604 down to 275.60).
    * ``nav_t2`` NAV 118.2753: 118.2753 x 1.2 = 141.93036 -> high 141.93 (the
      MONQ50 limit on 4 Sep 2026); low 94.62024 -> 94.63.
    * Gold, base 100: initial 94.00-106.00, no maximum.
    """
    regime = rules.regime_for(session)
    base = pick_base(regime, nav_t2=nav_t2, prev_close=prev_close, nav_t1=nav_t1)
    spec = regime.band_spec(asset_class)
    low0, high0 = limits_at_level(base, spec, 0, rules)
    if spec.max_widenings is None:
        # Gold/silver: widening steps have no cap.
        low_max, high_max, uncapped = 0.0, math.inf, True
    else:
        lo, hi = limits_at_level(base, spec, spec.max_widenings, rules)
        low_max, high_max, uncapped = float(lo), float(hi), False
    return Band(
        session=session,
        regime=regime.name,
        base_kind=regime.base,
        base_price=base,
        asset_class=asset_class,
        initial_pct=spec.initial_pct,
        step_pct=spec.step_pct,
        max_widenings=spec.max_widenings,
        low_initial=float(low0),
        high_initial=float(high0),
        low_max=low_max,
        high_max=high_max,
        uncapped=uncapped,
        tick=rules.tick_size,
    )


# --------------------------------------------------------------------------
# Flags from the day's trading (D6, R10)
# --------------------------------------------------------------------------
def _minimum_level(
    base: float,
    spec: BandSpec,
    rules: BandRules,
    *,
    side: Literal["upper", "lower"],
    extreme: Decimal,
) -> tuple[int, Decimal]:
    """Smallest widening level whose limit has not been crossed by ``extreme``.

    Upper side: smallest k with limit_k >= high. Lower side: smallest k with
    limit_k <= low. If the extreme lies beyond every allowed level (our computed
    band must then be wrong) the top level is returned. Returns (level, limit).
    """
    top = spec.max_widenings if spec.max_widenings is not None else _MAX_UNCAPPED_STEPS
    if spec.step_pct == 0:
        top = 0
    level, limit = 0, Decimal(0)
    for level in range(top + 1):
        low_l, high_l = limits_at_level(base, spec, level, rules)
        limit = high_l if side == "upper" else low_l
        if (side == "upper" and limit >= extreme) or (side == "lower" and limit <= extreme):
            break
    return level, limit


def band_flags(
    band: Band,
    rules: BandRules,
    *,
    high: float | None,
    low: float | None,
    last: float | None,
    close: float | None = None,
    at_tolerance: float | None = None,
    touch_ticks: int = 1,
) -> dict[str, bool | int | None]:
    """D6 flags for one ETF-day. Missing inputs give ``None`` for the flags they feed.

    Parameters
    ----------
    high, low : the day's highest and lowest trade.
    last : last traded price (LTP).
    close : NSE close (30-minute average); only used for the weak ``close_at_*`` flags.
    at_tolerance : fraction (0.001 = 0.1%); defaults to ``rules.at_band_tolerance``.
    touch_ticks : "touched" means within this many ticks of the limit (default 1, per R10).

    Keys (upper side; the lower side mirrors them with ``low``):

    * ``high_touched_initial_upper``  high >= initial high limit - 1 tick    (R10 ``hit_up``)
    * ``high_touched_max_upper``      high >= maximum high limit - 1 tick    (R10 ``hit_up_max``)
      Always False for uncapped (gold/silver) bands.
    * ``widened_up``                  high > initial high limit (strictly). The band must
      have widened because no trade can happen outside the corridor.
    * ``widenings_min_up``            fewest widenings consistent with the high.
    * ``ltp_at_upper``                last price within ``at_tolerance`` of the limit in
      force at the END of the day (the lowest limit at or above the day's high, since a
      corridor never narrows during the day).         (R10 ``last_at_up``)
    * ``close_at_upper``              same test on the NSE close. Weak evidence (D3).

    Worked example (equity ``prev_close``, base 100.00, tick 0.01): initial
    90.00-110.00, +5% step at 115.00 / 85.00, maximum 80.00-120.00. A day with
    high 112.00, last 111.90: touched initial (112 >= 109.99), not touched maximum,
    widened_up True (112 > 110), widenings_min_up 1 (limit at level 1 is 115 >=
    112), ltp_at_upper False (111.90 is not within 0.1% of 115.00 = 114.885).
    """
    tol = rules.at_band_tolerance if at_tolerance is None else at_tolerance
    slack = _dec(band.tick) * touch_ticks
    spec = rules.regime_for(band.session).band_spec(band.asset_class)
    base = band.base_price

    out: dict[str, bool | int | None] = {}
    for side, extreme, keys in (
        (
            "upper",
            high,
            (
                "high_touched_initial_upper",
                "high_touched_max_upper",
                "widened_up",
                "widenings_min_up",
                "ltp_at_upper",
                "close_at_upper",
            ),
        ),
        (
            "lower",
            low,
            (
                "low_touched_initial_lower",
                "low_touched_max_lower",
                "widened_down",
                "widenings_min_down",
                "ltp_at_lower",
                "close_at_lower",
            ),
        ),
    ):
        name_t, name_m, name_w, name_k, name_l, name_c = keys
        if _missing(extreme):
            for key in keys:
                out[key] = None
            continue
        ext = _dec(extreme)  # type: ignore[arg-type]
        if side == "upper":
            initial = _dec(band.high_initial)
            out[name_t] = ext >= initial - slack
            out[name_m] = (not band.uncapped) and ext >= _dec(band.high_max) - slack
            out[name_w] = ext > initial
        else:
            initial = _dec(band.low_initial)
            out[name_t] = ext <= initial + slack
            out[name_m] = (not band.uncapped) and ext <= _dec(band.low_max) + slack
            out[name_w] = ext < initial
        level, limit = _minimum_level(base, spec, rules, side=side, extreme=ext)
        out[name_k] = level
        for key, price in ((name_l, last), (name_c, close)):
            if _missing(price):
                out[key] = None
            else:
                out[key] = abs(_dec(price) - limit) <= limit * _dec(tol)  # type: ignore[arg-type]
    return out


# --------------------------------------------------------------------------
# Official band (R2) and regime inference (R10)
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class BandComparison:
    low_diff: float | None  # computed - official, rupees
    high_diff: float | None
    mismatch: bool | None  # None when either band is missing


def compare_bands(
    computed_low: float | None,
    computed_high: float | None,
    official_low: float | None,
    official_high: float | None,
    *,
    tick: float,
    max_ticks: int = 1,
) -> BandComparison:
    """Compare our initial band with the official one. Mismatch = off by more than ``max_ticks``.

    Hand example (tick 0.01): computed 206.71-252.63 vs official 206.71-252.63 -> diffs 0,
    no mismatch. Computed 206.72-252.63 vs official 206.71-252.63 -> low differs by one tick,
    still no mismatch. Computed 206.73 -> two ticks, mismatch.
    """
    if any(_missing(v) for v in (computed_low, computed_high, official_low, official_high)):
        return BandComparison(None, None, None)
    dl = _dec(computed_low) - _dec(official_low)  # type: ignore[arg-type]
    dh = _dec(computed_high) - _dec(official_high)  # type: ignore[arg-type]
    limit = _dec(tick) * max_ticks
    return BandComparison(float(dl), float(dh), abs(dl) > limit or abs(dh) > limit)


def attach_official_band(
    secmaster: pd.DataFrame,
    *,
    series: str = "EQ",
    cal: Any = calendars,
) -> pd.DataFrame:
    """Official ``PricRg`` band with the session it applies to (R2, DECISIONS D4).

    The file dated *d* holds the band for the NEXT Indian session after *d*. So a
    file dated Fri 4 Sep 2026 applies to Mon 7 Sep 2026. Both dates are kept.

    ``secmaster`` is the output of ``collect.nse_secmaster.parse`` (columns
    ``file_date, symbol, series, band_low, band_high, source_file``). Returns columns
    ``symbol, band_file_date, band_applies_date, band_low_official, band_high_official,
    band_source_file``; one row per symbol and file (duplicates are an error).
    """
    needed = {"file_date", "symbol", "series", "band_low", "band_high"}
    missing = needed - set(secmaster.columns)
    if missing:
        raise ValueError(f"security-master frame is missing columns {sorted(missing)}")
    sub = secmaster[secmaster["series"] == series]
    dupes = sub.duplicated(["symbol", "file_date"], keep=False)
    if dupes.any():
        bad = sub.loc[dupes, ["symbol", "file_date"]].drop_duplicates().head(5).to_dict("records")
        raise ValueError(f"duplicate (symbol, file_date) in series {series}: {bad}")
    out = pd.DataFrame(
        {
            "symbol": sub["symbol"].astype(str).to_numpy(),
            "band_file_date": list(sub["file_date"]),
            "band_applies_date": [cal.next_session("IN", d) for d in sub["file_date"]],
            "band_low_official": sub["band_low"].astype(float).to_numpy(),
            "band_high_official": sub["band_high"].astype(float).to_numpy(),
            "band_source_file": (
                sub["source_file"].astype(str).to_numpy()
                if "source_file" in sub.columns
                else [None] * len(sub)
            ),
        }
    )
    return out.reset_index(drop=True)


def infer_regime_from_band(
    official_low: float | None,
    official_high: float | None,
    candidates: dict[str, float | None],
    *,
    tick: float,
    max_ticks: int = 1,
) -> str:
    """Which base reproduces the middle of the official band? (R10 ``regime_inferred``)

    The corridor is symmetric around its base, and rounding moves each limit by less than
    one tick in opposite directions, so ``(low + high) / 2`` is within one tick of the base.
    Example: official 127.74-156.12 -> midpoint 141.93; candidate ``prev_close`` 141.93 matches.

    ``candidates`` maps a name (``"nav_t2"``, ``"prev_close"``, ``"nav_t1"``) to its price.
    Returns the single matching name, ``"ambiguous"`` if several match, ``"other"`` if none
    do, and ``"unknown"`` if the band is missing.
    """
    if _missing(official_low) or _missing(official_high):
        return "unknown"
    mid = (_dec(official_low) + _dec(official_high)) / 2  # type: ignore[arg-type]
    limit = _dec(tick) * max_ticks
    hits = [
        name
        for name, price in candidates.items()
        if not _missing(price) and abs(_dec(price) - mid) <= limit  # type: ignore[arg-type]
    ]
    if len(hits) == 1:
        return hits[0]
    return "ambiguous" if hits else "other"


# --------------------------------------------------------------------------
# DataFrame helper
# --------------------------------------------------------------------------
BAND_COLUMNS = (
    "band_regime",
    "band_base_kind",
    "band_base_price",
    "band_low_initial",
    "band_high_initial",
    "band_low_max",
    "band_high_max",
)
FLAG_COLUMNS = (
    "high_touched_initial_upper",
    "high_touched_max_upper",
    "widened_up",
    "widenings_min_up",
    "ltp_at_upper",
    "close_at_upper",
    "low_touched_initial_lower",
    "low_touched_max_lower",
    "widened_down",
    "widenings_min_down",
    "ltp_at_lower",
    "close_at_lower",
)


def compute_band_frame(
    df: pd.DataFrame,
    rules: BandRules,
    asset_class_by_symbol: dict[str, str],
) -> pd.DataFrame:
    """Add band and flag columns to an ETF-day frame (returns a copy).

    Required columns: ``symbol``, ``date`` (python date), and any of ``nav_t2``,
    ``prev_close``, ``nav_t1`` (the one the regime needs must be non-null on that row).
    Optional (flags are null without them): ``high``, ``low``, ``ltp``, ``close``.
    A row whose base is missing gets null band columns; it is not dropped.
    """
    for col in ("symbol", "date"):
        if col not in df.columns:
            raise ValueError(f"compute_band_frame needs column {col!r}")
    rows: list[dict[str, Any]] = []
    for rec in df.to_dict("records"):
        out: dict[str, Any] = dict.fromkeys(BAND_COLUMNS + FLAG_COLUMNS)
        sym = rec["symbol"]
        if sym not in asset_class_by_symbol:
            raise KeyError(f"no asset class given for symbol {sym!r}")
        try:
            band = compute_band(
                rec["date"],
                asset_class_by_symbol[sym],
                rules,
                nav_t2=rec.get("nav_t2"),
                prev_close=rec.get("prev_close"),
                nav_t1=rec.get("nav_t1"),
            )
        except MissingBaseError:
            rows.append(out)
            continue
        out.update(
            band_regime=band.regime,
            band_base_kind=band.base_kind,
            band_base_price=band.base_price,
            band_low_initial=band.low_initial,
            band_high_initial=band.high_initial,
            band_low_max=band.low_max,
            band_high_max=band.high_max,
        )
        out.update(
            band_flags(
                band,
                rules,
                high=rec.get("high"),
                low=rec.get("low"),
                last=rec.get("ltp"),
                close=rec.get("close"),
            )
        )
        rows.append(out)
    extra = pd.DataFrame(rows, index=df.index)
    for col in FLAG_COLUMNS:
        extra[col] = extra[col].astype("Int64" if col.startswith("widenings_") else "boolean")
    for col in BAND_COLUMNS[2:]:
        extra[col] = extra[col].astype("float64")
    return pd.concat([df, extra], axis=1)
