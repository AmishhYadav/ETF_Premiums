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

Rounding (D38, checked on 7,668 official bands Feb 2024 - Sep 2026)
------------------------------------------------------------------
Limits sit on the price tick (Rs 0.01). Two steps:

1. The base is first put on the tick grid, to the NEAREST paisa (half up). A NAV
   has four decimals (118.2753 -> 118.28); a closing price is already on the grid.
2. The limits are base x (100 -/+ pct) / 100, rounded to the tick. The high limit
   is always rounded DOWN. The low limit was rounded DOWN for sessions up to
   8 Dec 2025 and UP from 9 Dec 2025 (NSE changed it; seen in every ETF at once).

Example (9 Nov 2026): base 229.67 gives 229.67 x 0.9 = 206.703 -> 206.71 and
229.67 x 1.1 = 252.637 -> 252.63. Example (7 Feb 2024): NAV 78.8640 -> base 78.86;
78.86 x 0.8 = 63.088 -> 63.08 (down, old rule); 78.86 x 1.2 = 94.632 -> 94.63.
The tick size and rounding rules (with their dates) are read from
``config/rules.yaml`` (``tick_size``, ``rounding``). All price arithmetic uses
``Decimal`` so 100 x 0.9 never becomes 90.00000000000001 and rounds the wrong way.

Special sessions and missing NAVs (D38)
---------------------------------------
* NSE's Saturday disaster-recovery test sessions (2 Mar and 18 May 2024) had a
  fixed +/-5% band around the usual base: ``special_sessions`` in rules.yaml.
* ``nav_t2``: when the fund published no NAV dated exactly T-2 (a US or Hong Kong
  holiday, a Saturday special session, the Muhurat session), the exchange used the
  previous close until Oct 2025 and the latest earlier NAV after that:
  ``missing_nav_fallback`` in rules.yaml. ``band_base_kind`` then says
  ``prev_close``.

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
    base_mode: RoundMode | Literal["none"] = "none",
) -> tuple[Decimal, Decimal]:
    """Low and high limit for a corridor of +/-``pct`` percent around ``base``.

    Hand example: base 229.67, pct 10 -> low 229.67 x 90 / 100 = 206.703, rounded
    up to the tick = 206.71; high 229.67 x 110 / 100 = 252.637, rounded down =
    252.63. The low limit never goes below 0.

    ``base_mode`` first puts the base on the tick grid ("none" = use as given):
    NAV 78.8640 with ``nearest`` -> 78.86.
    """
    b, p, t = _dec(base), _dec(pct), _dec(tick)
    if base_mode != "none":
        b = snap_to_tick(b, t, base_mode)
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
    base: float | Decimal,
    spec: BandSpec,
    level: int,
    rules: BandRules,
    session: date | None = None,
) -> tuple[Decimal, Decimal]:
    """Limits after ``level`` widenings (0 = the opening corridor).

    ``session`` picks the rounding rule in force that day (None = the current rule).
    """
    if level < 0:
        raise ValueError("level must be >= 0")
    if spec.max_widenings is not None and level > spec.max_widenings:
        raise ValueError(f"level {level} exceeds max_widenings {spec.max_widenings}")
    base_mode, low_mode, high_mode = rules.rounding.modes_for(session)
    return band_limits(
        base,
        _level_pct(spec, level),
        tick=rules.tick_size,
        low_mode=low_mode,  # type: ignore[arg-type]
        high_mode=high_mode,  # type: ignore[arg-type]
        base_mode=base_mode,  # type: ignore[arg-type]
    )


def band_spec_for(session: date, asset_class: str, rules: BandRules) -> BandSpec:
    """Band shape on ``session``: the regime's, unless the day is a special session.

    A special session (e.g. the 2 Mar 2024 disaster-recovery test) has a fixed
    +/-``pct`` band for every asset class and no widening.
    """
    special = rules.special_session(session)
    if special is not None:
        return BandSpec(initial_pct=special.pct, step_pct=0, max_widenings=0)
    return rules.regime_for(session).band_spec(asset_class)


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
    nav_t2_exact: bool | None = None,
) -> Band:
    """Initial and maximum corridor for ``session``.

    ``nav_t2_exact`` (``nav_t2`` regime only): False when the fund published no NAV dated
    exactly T-2 and ``nav_t2`` is an older NAV. The regime's ``missing_nav_fallback`` then
    decides: ``prev_close`` swaps the base for the previous close (``base_kind`` becomes
    ``prev_close``), ``latest_nav`` keeps the older NAV. None/True = no fallback.

    Worked examples (all hand computed, tick 0.01):

    * 9 Nov 2026 file, base 229.67, equity ``prev_close`` (low up / high down):
      initial 206.71-252.63; maximum (+/-20%): 183.74-275.60 (229.67 x 0.8 = 183.736
      up to 183.74; 229.67 x 1.2 = 275.604 down to 275.60).
    * ``nav_t2`` NAV 118.2753 on 4 Sep 2026: base 118.28; 118.28 x 1.2 = 141.936 ->
      high 141.93 (the MONQ50 limit); 118.28 x 0.8 = 94.624 -> low 94.63 (up).
    * ``nav_t2`` NAV 78.8640 on 7 Feb 2024 (low still rounded down): base 78.86 ->
      63.08-94.63 (NSE's MAFANG band).
    * Special session 2 Mar 2024, NAV 470.0147: base 470.01 x 0.95 = 446.5095 -> 446.50;
      x 1.05 = 493.5105 -> 493.51 (NSE's BANKBEES band).
    * Gold, base 100: initial 94.00-106.00, no maximum.
    """
    regime = rules.regime_for(session)
    base_kind = regime.base
    if (
        regime.base == "nav_t2"
        and nav_t2_exact is False
        and regime.missing_nav_rule(session) == "prev_close"
    ):
        base_kind = "prev_close"
    supplied = {"nav_t2": nav_t2, "prev_close": prev_close, "nav_t1": nav_t1}
    value = supplied[base_kind]
    if _missing(value):
        raise MissingBaseError(f"regime {regime.name} needs base {base_kind!r} but it is missing")
    if float(value) <= 0:  # type: ignore[arg-type]
        raise ValueError(f"base {base_kind} must be positive, got {value!r}")
    base = float(value)  # type: ignore[arg-type]
    spec = band_spec_for(session, asset_class, rules)
    low0, high0 = limits_at_level(base, spec, 0, rules, session)
    if spec.max_widenings is None:
        # Gold/silver: widening steps have no cap.
        low_max, high_max, uncapped = 0.0, math.inf, True
    else:
        lo, hi = limits_at_level(base, spec, spec.max_widenings, rules, session)
        low_max, high_max, uncapped = float(lo), float(hi), False
    return Band(
        session=session,
        regime=regime.name,
        base_kind=base_kind,
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
    session: date | None = None,
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
        low_l, high_l = limits_at_level(base, spec, level, rules, session)
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
    spec = band_spec_for(band.session, band.asset_class, rules)
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
        level, limit = _minimum_level(
            base, spec, rules, side=side, extreme=ext, session=band.session
        )
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


def _as_day(value: Any) -> date | None:
    if _missing(value):
        return None
    return value.date() if isinstance(value, pd.Timestamp) else value


def _nav_t2_exact(rec: dict[str, Any]) -> bool | None:
    """False if the NAV behind ``nav_t2`` is not dated T-2 (or there is none); None if unknown."""
    if "nav_t2_date" not in rec or "nav_t2_nav_date" not in rec:
        return None
    t2, used = _as_day(rec["nav_t2_date"]), _as_day(rec["nav_t2_nav_date"])
    if t2 is None:
        return None
    return used == t2


def compute_band_frame(
    df: pd.DataFrame,
    rules: BandRules,
    asset_class_by_symbol: dict[str, str],
) -> pd.DataFrame:
    """Add band and flag columns to an ETF-day frame (returns a copy).

    Required columns: ``symbol``, ``date`` (python date), and any of ``nav_t2``,
    ``prev_close``, ``nav_t1`` (the one the regime needs must be non-null on that row).
    Optional (flags are null without them): ``high``, ``low``, ``ltp``, ``close``.
    Optional ``nav_t2_date`` and ``nav_t2_nav_date``: when both are present and differ, no
    NAV was dated exactly T-2 and the regime's ``missing_nav_fallback`` applies.
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
                nav_t2_exact=_nav_t2_exact(rec),
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
