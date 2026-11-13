"""Configuration layer: typed models and loaders for the yaml files in ``config/``.

Every model is frozen (cannot be changed after loading) and rejects unknown
fields, so a typo in a yaml file fails loudly at load time instead of silently
doing nothing.

Files
-----
- ``config/etfs.yaml``     the ETF universe (SPEC section 4)
- ``config/rules.yaml``    price-band regimes (SPEC section 7)
- ``config/collect.yaml``  settings for the intraday iNAV snapshot recorder
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
    creation_unit_size: int | None = None  # units per creation/redemption basket
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
    pre_open_call_auction: bool = False

    def max_pct(self) -> float | None:
        """Widest band in percent (initial + step x widenings); None if uncapped."""
        if self.max_widenings is None:
            return None
        return self.initial_pct + self.step_pct * self.max_widenings


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
    verify: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _checks(self) -> Regime:
        if self.start is not None and self.end is not None and self.end < self.start:
            raise ValueError(f"regime {self.name}: end {self.end} is before start {self.start}")
        missing = {"equity", "gold", "silver"} - set(self.bands)
        if missing:
            raise ValueError(f"regime {self.name}: missing band specs for {sorted(missing)}")
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


class Rounding(_Frozen):
    """How band limits are rounded to the price tick (the smallest price step)."""

    band_low: Literal["up", "down", "nearest"]
    band_high: Literal["up", "down", "nearest"]
    verify: list[str] = Field(default_factory=list)


class BandRules(_Frozen):
    """All price-band regimes plus the shared constants."""

    regimes: tuple[Regime, ...]
    tick_size: float = Field(gt=0)
    rounding: Rounding
    at_band_tolerance: float = Field(ge=0)  # close within this fraction of a limit = "at band"
    sources: tuple[str, ...] = ()

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


class CollectSettings(_Frozen):
    inav: InavSettings
    http: HttpSettings

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
