"""Configuration layer: typed models and loaders for the yaml files in ``config/``.

Every model is frozen (cannot be changed after loading) and rejects unknown
fields, so a typo in a yaml file fails loudly at load time instead of silently
doing nothing.

Files
-----
- ``config/etfs.yaml``     the ETF universe (SPEC section 4)
- ``config/rules.yaml``    price-band regimes (SPEC section 7)
- ``config/collect.yaml``  settings for the intraday iNAV snapshot recorder
- ``config/feeders.yaml``  feeder funds (funds of funds) and their AMFI scheme codes (R14)
- ``config/trends_terms.yaml``  which Google Trends search term belongs to which ETF (R15)
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Literal, TypeVar
from zoneinfo import ZoneInfo

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from etfp.paths import config_dir

_Date = date  # alias so model fields named "start"/"end" can use the date type safely

_M = TypeVar("_M", bound=BaseModel)


class _Frozen(BaseModel):
    """Base for all config models: immutable, and unknown yaml keys are errors."""

    model_config = ConfigDict(frozen=True, extra="forbid")


def _load_yaml_model(model: type[_M], path: Path) -> _M:
    """Read a yaml file and validate it into ``model`` with a path-naming error."""
    if not path.exists():
        raise FileNotFoundError(f"config file not found: {path}")
    with path.open(encoding="utf-8") as fh:
        raw: Any = yaml.safe_load(fh)
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: expected a mapping at the top level")
    try:
        return model.model_validate(raw)
    except ValueError as exc:  # pydantic's ValidationError is a ValueError
        raise ValueError(f"{path}: invalid configuration:\n{exc}") from exc


# ==========================================================================
# ETF universe
# ==========================================================================
_ISIN_RE = re.compile(r"^IN[A-Z0-9]{10}$")
_CURRENCY_FOR_MARKET = {"US": "USD", "HK": "HKD", "IN": "INR"}


class Bloomberg(_Frozen):
    """Bloomberg tickers for one ETF (all empty until the licence data is mapped).

    Bloomberg data may not be redistributed, so tickers are only labels here;
    raw Bloomberg files stay out of git.
    """

    etf: str | None = None
    index: str | None = None
    future: str | None = None
    fx: str | None = None


class Etf(_Frozen):
    """One exchange-traded fund in the universe."""

    symbol: str = Field(min_length=1)
    name: str = Field(min_length=1)
    amc: str = Field(min_length=1)  # asset management company (the fund house)
    isin: str  # the 12-character international security id
    amfi_scheme_code: int  # AMFI's id for the scheme, used to match the daily NAV file
    underlying_index: str
    underlying_market: Literal["US", "HK", "IN"]
    currency: Literal["USD", "HKD", "INR"]  # currency of the underlying holdings
    asset_class: Literal["equity", "gold", "silver"]
    # A hypothesis about whether the fund can still create units. The
    # units-outstanding data (SPEC 6.5) decides the real status.
    group: Literal["blocked", "unclear", "possibly_open", "control", "bullion"]
    tracked: bool  # True: gets fair value, iNAV snapshots and a dashboard row
    futures_proxy: Literal["NQ", "ES"] | None = None
    # Units in one creation/redemption basket (a market maker hands in a whole
    # number of baskets and gets that many baskets' worth of new units). None =
    # not known yet; a tracked foreign-market ETF must then list
    # "creation_unit_size" in ``verify`` so the gap is visible, not forgotten.
    creation_unit_size: int | None = Field(default=None, gt=0)
    bloomberg: Bloomberg = Field(default_factory=Bloomberg)
    notes: str | None = None
    verify: list[str] = Field(default_factory=list)  # field names not yet confirmed

    @field_validator("isin")
    @classmethod
    def _isin_shape(cls, value: str) -> str:
        if not _ISIN_RE.match(value):
            raise ValueError(f"ISIN {value!r} does not match ^IN[A-Z0-9]{{10}}$")
        return value

    @model_validator(mode="after")
    def _unknown_creation_unit_is_flagged(self) -> Etf:
        if (
            self.creation_unit_size is None
            and self.tracked
            and self.underlying_market != "IN"
            and "creation_unit_size" not in self.verify
        ):
            raise ValueError(
                f"{self.symbol}: creation_unit_size is unknown, so 'creation_unit_size' "
                "must be listed in its verify list (request R6)"
            )
        return self

    @model_validator(mode="after")
    def _currency_matches_market(self) -> Etf:
        expected = _CURRENCY_FOR_MARKET[self.underlying_market]
        if self.currency != expected:
            raise ValueError(
                f"{self.symbol}: currency {self.currency} does not match underlying market "
                f"{self.underlying_market} (expected {expected})"
            )
        return self


class EtfUniverse(_Frozen):
    """All ETFs the project knows about, with lookup helpers."""

    etfs: tuple[Etf, ...]

    @model_validator(mode="after")
    def _unique_keys(self) -> EtfUniverse:
        for label, values in (
            ("symbol", [e.symbol for e in self.etfs]),
            ("isin", [e.isin for e in self.etfs]),
            ("amfi_scheme_code", [e.amfi_scheme_code for e in self.etfs]),
        ):
            dupes = sorted({str(v) for v in values if values.count(v) > 1})
            if dupes:
                raise ValueError(f"duplicate {label}: {', '.join(dupes)}")
        return self

    def by_symbol(self, symbol: str) -> Etf:
        for etf in self.etfs:
            if etf.symbol == symbol:
                return etf
        raise KeyError(f"unknown ETF symbol {symbol!r}; known symbols: {', '.join(self.symbols())}")

    def tracked(self) -> list[Etf]:
        return [e for e in self.etfs if e.tracked]

    def symbols(self) -> list[str]:
        return [e.symbol for e in self.etfs]


def load_etfs(path: Path | None = None) -> EtfUniverse:
    """Load the ETF universe (default: ``config/etfs.yaml``)."""
    return _load_yaml_model(EtfUniverse, path or config_dir() / "etfs.yaml")


# ==========================================================================
# Price-band rules
# ==========================================================================
BandAssetClass = Literal["equity", "gold", "silver"]


class BandSpec(_Frozen):
    """Shape of the daily price band for one asset class in one regime.

    A price band is the range around a "base price" outside which the exchange
    allows no trades that day. Percentages are in percent (20 means 20%).

    - Fixed band: ``step_pct = 0`` and ``max_widenings = 0``.
    - Dynamic band: starts at ``initial_pct``; after a cooling-off pause, if
      trading reached the edge, it widens by ``step_pct`` of the base price.
      ``max_widenings`` caps the number of widenings per direction
      (None = no cap).
    """

    initial_pct: float = Field(gt=0)
    step_pct: float = Field(ge=0)
    max_widenings: int | None = Field(default=None, ge=0)
    cooling_off_minutes: int | None = Field(default=None, gt=0)
    cooling_off_minutes_last_30: int | None = Field(default=None, gt=0)
    trigger_pct: float | None = Field(default=None, gt=0)  # distance from base that triggers
    # The draft rule asked for a "breadth test" before widening (at least 50 trades from
    # 10 client codes and 3 members on each side at the limit). The final SEBI circular
    # dropped it (request R7), so equity says False; None = not recorded for this class.
    breadth_test: bool | None = None
    # True: only the side that was hit moves (a trade at +9.90% widens the upper limit
    # and leaves the lower limit alone). None = not recorded for this class.
    widen_moving_side_only: bool | None = None
    pre_open_call_auction: bool = False

    def max_pct(self) -> float | None:
        """Widest band in percent (initial + step x widenings); None if uncapped."""
        if self.max_widenings is None:
            return None
        return self.initial_pct + self.step_pct * self.max_widenings


class MissingNavFallback(_Frozen):
    """Which base the exchange used, in a date range, when no NAV was dated exactly T-2.

    ``prev_close``: the previous session's closing price. ``latest_nav``: the latest NAV
    dated before T-2.
    """

    start: _Date | None = None
    end: _Date | None = None
    use: Literal["prev_close", "latest_nav"]
    note: str | None = None

    @model_validator(mode="after")
    def _ordered(self) -> MissingNavFallback:
        if self.start is not None and self.end is not None and self.end < self.start:
            raise ValueError(f"missing_nav_fallback: end {self.end} is before start {self.start}")
        return self

    def covers(self, day: date) -> bool:
        return (self.start is None or day >= self.start) and (self.end is None or day <= self.end)


class Regime(_Frozen):
    """One period during which a particular price-band rule applied."""

    name: str
    start: _Date | None = None  # first calendar day the regime applies (None = open start)
    end: _Date | None = None  # last calendar day the regime applies (None = open end)
    base: Literal["nav_t2", "prev_close", "nav_t1"]  # what the band is centred on
    status: Literal["confirmed", "planned"]
    description: str | None = None
    # Debt ETFs use the "equity" entry. Overnight/liquid ETFs are out of scope.
    bands: dict[BandAssetClass, BandSpec]
    # Only for base nav_t2: what the exchange used when the fund published no NAV dated
    # exactly T-2 (D38). Periods by session date; a day no period covers keeps the latest
    # earlier NAV (the nav_align fallback).
    missing_nav_fallback: tuple[MissingNavFallback, ...] = ()
    verify: list[str] = Field(default_factory=list)

    def missing_nav_rule(self, day: date) -> str:
        """``"prev_close"`` or ``"latest_nav"``: base used when no NAV is dated exactly T-2."""
        for period in self.missing_nav_fallback:
            if period.covers(day):
                return period.use
        return "latest_nav"

    @model_validator(mode="after")
    def _checks(self) -> Regime:
        if self.start is not None and self.end is not None and self.end < self.start:
            raise ValueError(f"regime {self.name}: end {self.end} is before start {self.start}")
        missing = {"equity", "gold", "silver"} - set(self.bands)
        if missing:
            raise ValueError(f"regime {self.name}: missing band specs for {sorted(missing)}")
        if self.missing_nav_fallback and self.base != "nav_t2":
            raise ValueError(f"regime {self.name}: missing_nav_fallback needs base nav_t2")
        return self

    def covers(self, day: date) -> bool:
        return (self.start is None or day >= self.start) and (self.end is None or day <= self.end)

    def band_spec(self, asset_class: str) -> BandSpec:
        """Band shape for an asset class ("debt" is treated as equity)."""
        key = "equity" if asset_class == "debt" else asset_class
        if key not in self.bands:
            raise KeyError(f"no band spec for asset class {asset_class!r}")
        return self.bands[key]  # type: ignore[index]

    def max_pct(self, asset_class: str) -> float | None:
        """Widest possible band in percent for ``asset_class``; None when uncapped."""
        return self.band_spec(asset_class).max_pct()


RoundMode = Literal["up", "down", "nearest"]


class RoundingPeriod(_Frozen):
    """An older rounding rule that applied to sessions from ``start`` to ``end`` (inclusive).

    Fields left out fall back to the current rule in :class:`Rounding`.
    """

    start: _Date | None = None
    end: _Date | None = None
    base: RoundMode | Literal["none"] | None = None
    band_low: RoundMode | None = None
    band_high: RoundMode | None = None
    note: str | None = None

    @model_validator(mode="after")
    def _ordered(self) -> RoundingPeriod:
        if self.start is not None and self.end is not None and self.end < self.start:
            raise ValueError(f"rounding period: end {self.end} is before start {self.start}")
        return self

    def covers(self, day: date) -> bool:
        return (self.start is None or day >= self.start) and (self.end is None or day <= self.end)


class Rounding(_Frozen):
    """How band limits are rounded to the price tick (the smallest price step).

    ``base`` says how the base price is put on the tick grid BEFORE the percentage is
    applied (``none`` = use it as given). A NAV has four decimals; the exchange first
    rounds it to the nearest paisa (D38). ``band_low`` / ``band_high`` round the limits.
    ``history`` lists older rules by session date; the top-level fields are the current rule.
    """

    band_low: RoundMode
    band_high: RoundMode
    base: RoundMode | Literal["none"] = "none"
    history: tuple[RoundingPeriod, ...] = ()
    verify: list[str] = Field(default_factory=list)

    def modes_for(self, day: date | None) -> tuple[str, str, str]:
        """``(base, band_low, band_high)`` rounding modes for the session ``day``.

        The first ``history`` period covering ``day`` overrides the current rule;
        ``day=None`` gives the current rule.
        """
        base, low, high = self.base, self.band_low, self.band_high
        if day is not None:
            for period in self.history:
                if period.covers(day):
                    base = period.base or base
                    low = period.band_low or low
                    high = period.band_high or high
                    break
        return base, low, high


class SpecialSession(_Frozen):
    """A session with its own fixed band width (e.g. NSE disaster-recovery test sessions).

    Every asset class gets a fixed +/-``pct`` band with no widening; the base price is the
    one the regime in force would use.
    """

    date: _Date
    pct: float = Field(gt=0)
    reason: str


class BandRules(_Frozen):
    """All price-band regimes plus the shared constants."""

    regimes: tuple[Regime, ...]
    tick_size: float = Field(gt=0)
    rounding: Rounding
    at_band_tolerance: float = Field(ge=0)  # close within this fraction of a limit = "at band"
    special_sessions: tuple[SpecialSession, ...] = ()
    sources: tuple[str, ...] = ()

    def special_session(self, day: date) -> SpecialSession | None:
        """The special-session override for ``day``, if there is one."""
        for s in self.special_sessions:
            if s.date == day:
                return s
        return None

    @model_validator(mode="after")
    def _ordered_and_disjoint(self) -> BandRules:
        names = [r.name for r in self.regimes]
        if len(set(names)) != len(names):
            raise ValueError(f"duplicate regime names: {names}")
        for prev, nxt in zip(self.regimes, self.regimes[1:], strict=False):
            if prev.end is None:
                raise ValueError(
                    f"regime {prev.name} has no end date but regime {nxt.name} follows it"
                )
            if nxt.start is None:
                raise ValueError(f"regime {nxt.name} has no start date but follows {prev.name}")
            if nxt.start <= prev.end:
                raise ValueError(
                    f"regimes overlap or are out of order: {prev.name} ends {prev.end}, "
                    f"{nxt.name} starts {nxt.start}"
                )
        return self

    def regime_for(self, day: date) -> Regime:
        """The regime in force on ``day``.

        Weekends and holidays between two regimes (for example 2026-09-05 and
        2026-09-06) are legitimately covered by none and raise ``ValueError``.
        """
        for regime in self.regimes:
            if regime.covers(day):
                return regime
        raise ValueError(f"no price-band regime covers {day}")


def load_rules(path: Path | None = None) -> BandRules:
    """Load the price-band regimes (default: ``config/rules.yaml``)."""
    return _load_yaml_model(BandRules, path or config_dir() / "rules.yaml")


# ==========================================================================
# Collector settings
# ==========================================================================
class InavSettings(_Frozen):
    """Schedule of the intraday iNAV snapshot recorder (all times in ``timezone``)."""

    start: time
    end: time
    every_minutes: int
    post_close: time  # one extra snapshot after the close
    timezone: str
    heartbeat_stale_minutes: int = Field(gt=0)

    @field_validator("start", "end", "post_close", mode="before")
    @classmethod
    def _times_must_be_text(cls, value: Any) -> Any:
        # Unquoted 15:30 in yaml may be read as a number; insist on "15:30".
        if not isinstance(value, str):
            raise ValueError(f'times must be quoted strings like "09:15", got {value!r}')
        return value

    @field_validator("timezone")
    @classmethod
    def _known_zone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except Exception as exc:
            raise ValueError(f"unknown time zone {value!r}") from exc
        return value

    @model_validator(mode="after")
    def _schedule_makes_sense(self) -> InavSettings:
        if self.every_minutes <= 0:
            raise ValueError("inav.every_minutes must be greater than 0")
        if not (self.start < self.end <= self.post_close):
            raise ValueError("inav times must satisfy start < end <= post_close")
        return self


class HttpSettings(_Frozen):
    """Politeness settings for web requests."""

    nse_min_interval_s: float
    default_interval_s: float = Field(ge=0)
    max_retries: int = Field(ge=0)

    @field_validator("nse_min_interval_s")
    @classmethod
    def _polite_to_nse(cls, value: float) -> float:
        # Project rule: at least 1 second between NSE calls.
        if value < 1.0:
            raise ValueError("http.nse_min_interval_s must be at least 1.0 (project rule)")
        return value


class Feeder(_Frozen):
    """One feeder fund (fund of funds) that buys one of the ETFs (requests R4, R14).

    A feeder is a mutual fund whose only holding is one ETF. Each plan (Regular and
    Direct) of a feeder is a separate AMFI scheme with its own code and NAV.
    """

    name: str = Field(min_length=1)  # scheme name exactly as AMFI prints it
    amfi_scheme_code: int = Field(gt=0)
    etf_symbol: str = Field(min_length=1)  # the ETF it holds, as in etfs.yaml
    plan: Literal["regular", "direct"]
    verified: bool = False  # True once the code was found in AMFI's own file
    notes: str | None = None


class FeederList(_Frozen):
    feeders: tuple[Feeder, ...]

    @model_validator(mode="after")
    def _unique_codes(self) -> FeederList:
        codes = [f.amfi_scheme_code for f in self.feeders]
        dupes = sorted({str(c) for c in codes if codes.count(c) > 1})
        if dupes:
            raise ValueError(f"duplicate feeder amfi_scheme_code: {', '.join(dupes)}")
        return self


def load_feeders(path: Path | None = None) -> FeederList:
    """Load the feeder-fund list (default: ``config/feeders.yaml``)."""
    return _load_yaml_model(FeederList, path or config_dir() / "feeders.yaml")


class TrendsTerm(_Frozen):
    """One Google Trends search term and the ETF whose attention it measures."""

    term: str = Field(min_length=1)
    etf_symbol: str = Field(min_length=1)


class TrendsTerms(_Frozen):
    """Which search terms measure attention to which ETF (request R15)."""

    geo: str = "India"  # the region label in the Trends file header
    terms: tuple[TrendsTerm, ...]

    @model_validator(mode="after")
    def _no_repeats(self) -> TrendsTerms:
        pairs = [(t.term, t.etf_symbol) for t in self.terms]
        dupes = sorted({f"{a} -> {b}" for a, b in pairs if pairs.count((a, b)) > 1})
        if dupes:
            raise ValueError(f"repeated trends term: {', '.join(dupes)}")
        return self

    def terms_for(self, symbol: str) -> list[str]:
        """The search terms assigned to one ETF, in file order."""
        return [t.term for t in self.terms if t.etf_symbol == symbol]


def load_trends_terms(path: Path | None = None) -> TrendsTerms:
    """Load the term -> ETF map (default: ``config/trends_terms.yaml``)."""
    return _load_yaml_model(TrendsTerms, path or config_dir() / "trends_terms.yaml")


class NseQuoteSettings(_Frozen):
    """The browser-based NSE quote capture (order-book depth, DECISIONS D12).

    NSE refuses plain scripts on its quote API, so a real browser opens the public
    quote page of each ETF and we save the answers the page itself asks for.
    """

    # "auto" = on when the optional `playwright` package is installed (the `inav`
    # extra); true = always try (a missing browser then counts as a failed source);
    # false = never.
    enabled: bool | Literal["auto"] = "auto"
    headless: bool = True
    # Playwright browser channel. None = its own small headless browser. "chromium" =
    # the full browser in its new headless mode (what to try if NSE refuses the first).
    channel: str | None = None
    # Path of a browser program to use instead of the one Playwright downloaded.
    browser_executable: str | None = None
    page_gap_s: float = Field(default=1.5, ge=1.5)  # pause between two page loads
    wait_s: float = Field(default=15.0, gt=0)  # longest wait for one page's own answers
    launch_timeout_s: float = Field(default=30.0, gt=0)
    # Never let the capture eat the next tick: stop starting new pages after this long.
    tick_budget_s: float = Field(default=240.0, gt=0)


class CollectSettings(_Frozen):
    inav: InavSettings
    http: HttpSettings
    nse_quote: NseQuoteSettings = NseQuoteSettings()

    def inav_tick_times(self) -> list[time]:
        """Snapshot times: start, start+every, ... up to end (inclusive), then post_close.

        With the shipped settings this is 09:15, 09:30, ..., 15:30, then 15:45
        (27 times). Returned as naive ``time`` values meant to be read in
        ``inav.timezone``.
        """
        cfg = self.inav
        anchor = date(2000, 1, 1)  # any date; only the clock part is used

        def stamp(t: time) -> datetime:
            return datetime.combine(anchor, t, tzinfo=ZoneInfo("UTC"))  # arithmetic only

        ticks: list[time] = []
        current = stamp(cfg.start)
        last = stamp(cfg.end)
        step = timedelta(minutes=cfg.every_minutes)
        while current <= last:
            ticks.append(current.time())
            current += step
        if cfg.post_close > ticks[-1]:
            ticks.append(cfg.post_close)
        return ticks


def load_collect_settings(path: Path | None = None) -> CollectSettings:
    """Load the recorder settings (default: ``config/collect.yaml``)."""
    return _load_yaml_model(CollectSettings, path or config_dir() / "collect.yaml")
