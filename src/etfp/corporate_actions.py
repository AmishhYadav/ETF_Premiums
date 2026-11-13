"""Corporate actions that change an ETF's unit: stock splits, and how to adjust for them.

Plain-words background
----------------------
A *face-value split* cuts one unit into several smaller ones. Motilal Oswal Nasdaq 100
ETF (MON100) split 10:1: a unit worth Rs 1,015 on 16 June 2021 became ten units worth
about Rs 101.5 each on 17 June 2021. A holder lost nothing, but a raw price chart shows a
fake 90% crash, and a raw NAV series jumps the same way. Any return, premium or average
that crosses the split date is garbage unless both series are put on the same scale first.

Convention used everywhere in this project: **adjust to the CURRENT scale.** The factor
for a past date turns the number seen back then into the number you would see today:

    adjusted value = raw value x adjustment_factor(symbol, date, kind)

* ``kind="price"``: exchange price. Before the ex-date a 10:1 split needs 1/10 (the old
  unit was 10 times bigger). From the ex-date on the factor is 1.
* ``kind="nav"``: the fund house's published NAV. Same idea, but the NAV can switch scale
  on a different day than the exchange price (MON100: price on 17 Jun 2021, AMFI NAV only
  from 21 Jun 2021), so it has its own optional ``nav_ex_date``.
* ``kind="units"``: units outstanding. After a 10:1 split there are 10 times as many
  units, so a pre-split count is multiplied by 10 to be comparable. Keeps
  ``units x NAV`` (the fund's total value, i.e. its size) unchanged.

Because price and NAV move by the same factor, the premium (price / NAV - 1) of a date is
unaffected as long as both are adjusted with their own factor. Between the exchange ex-date
and the NAV ex-date the raw price and raw NAV are on DIFFERENT scales, so the raw premium
on those days (17 and 18 Jun 2021 for MON100) is meaningless until adjusted.

Pure functions: nothing here reads the clock or the network.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from datetime import date
from pathlib import Path
from typing import Any, Literal

import pandas as pd
import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from etfp.paths import config_dir

AdjustKind = Literal["price", "nav", "units"]
_KINDS: tuple[str, ...] = ("price", "nav", "units")

_Date = date  # alias: a model field called "date"-ish must not shadow the type


class CorporateAction(BaseModel):
    """One dated change of unit for one symbol."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str = Field(min_length=1)
    ex_date: _Date
    type: Literal["split"]
    ratio: float = Field(gt=0, description="new units per old unit; 10 means a 10:1 split")
    source: str = Field(min_length=1, description="where the date/ratio came from")
    verified: bool
    nav_ex_date: _Date | None = None

    @field_validator("symbol")
    @classmethod
    def _upper_symbol(cls, value: str) -> str:
        if value != value.strip().upper():
            raise ValueError(f"symbol {value!r} must be upper-case with no spaces")
        return value

    @field_validator("ratio")
    @classmethod
    def _not_identity(cls, value: float) -> float:
        if not math.isfinite(value) or value == 1:
            raise ValueError("ratio must be a finite number other than 1")
        return value

    @property
    def nav_effective_date(self) -> date:
        """First date the published NAV is on the new scale."""
        return self.nav_ex_date or self.ex_date


class CorporateActions(BaseModel):
    """All known actions (the content of config/corporate_actions.yaml)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    actions: list[CorporateAction]

    @model_validator(mode="after")
    def _unique(self) -> CorporateActions:
        seen: set[tuple[str, date]] = set()
        for action in self.actions:
            key = (action.symbol, action.ex_date)
            if key in seen:
                raise ValueError(
                    f"duplicate corporate action for {action.symbol} on {action.ex_date}"
                )
            seen.add(key)
        return self

    def for_symbol(self, symbol: str) -> list[CorporateAction]:
        """Actions of one symbol, oldest first."""
        return sorted((a for a in self.actions if a.symbol == symbol), key=lambda a: a.ex_date)


def default_path() -> Path:
    return config_dir() / "corporate_actions.yaml"


def load_corporate_actions(path: Path | None = None) -> CorporateActions:
    """Read and validate the yaml file. A typo or an unknown field fails loudly."""
    path = path or default_path()
    if not path.exists():
        raise FileNotFoundError(f"config file not found: {path}")
    with path.open(encoding="utf-8") as fh:
        raw: Any = yaml.safe_load(fh)
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: expected a mapping at the top level")
    try:
        return CorporateActions.model_validate(raw)
    except ValueError as exc:  # pydantic's ValidationError is a ValueError
        raise ValueError(f"{path}: invalid corporate actions:\n{exc}") from exc


def _effective_date(action: CorporateAction, kind: AdjustKind) -> date:
    return action.nav_effective_date if kind == "nav" else action.ex_date


def adjustment_factor(
    actions: CorporateActions | Iterable[CorporateAction],
    symbol: str,
    day: date,
    kind: AdjustKind = "price",
) -> float:
    """Number to multiply a raw value seen on ``day`` by, to express it on today's scale.

    Worked example (MON100, 10:1 split, price ex-date 2021-06-17, NAV ex-date 2021-06-21):
      price on 2021-06-16 -> 0.1   (old scale; the raw 1015.62 becomes 101.562)
      price on 2021-06-17 -> 1.0   (already the new scale)
      NAV   on 2021-06-18 -> 0.1   (the NAV was still on the old scale)
      NAV   on 2021-06-21 -> 1.0
      units on 2021-06-16 -> 10.0  (more, smaller units after the split)
    A symbol with no actions always gives 1.0.
    """
    if kind not in _KINDS:
        raise ValueError(f"kind must be one of {_KINDS}, got {kind!r}")
    pool = actions.actions if isinstance(actions, CorporateActions) else actions
    factor = 1.0
    for action in pool:
        if action.symbol != symbol or day >= _effective_date(action, kind):
            continue  # not this symbol, or the date is already on the new scale
        if action.type == "split":
            factor *= action.ratio if kind == "units" else 1.0 / action.ratio
    return factor


def adjust_values(
    actions: CorporateActions,
    frame: pd.DataFrame,
    *,
    value: str,
    kind: AdjustKind,
    symbol: str = "symbol",
    day: str = "date",
) -> pd.Series:
    """Adjusted copy of one column of a table: ``frame[value] x factor(symbol, date)``.

    Returns a new Series with the frame's index; the input is not modified. Cached per
    (symbol, date) so a table of thousands of rows only evaluates each pair once.
    """
    cache: dict[tuple[str, date], float] = {}

    def factor(sym: str, d: date) -> float:
        key = (sym, d)
        if key not in cache:
            cache[key] = adjustment_factor(actions, sym, d, kind)
        return cache[key]

    factors = [factor(s, d) for s, d in zip(frame[symbol], frame[day], strict=True)]
    return frame[value] * pd.Series(factors, index=frame.index, dtype="float64")
