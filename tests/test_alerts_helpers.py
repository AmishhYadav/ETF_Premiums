"""Shared builders for the alert tests (imported as ``from test_alerts_helpers import ...``)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import pandas as pd

from etfp.alerts.rules import AlertConfig
from etfp.timeutil import IST

# NSE sessions used below (checked against the project calendar):
# Wed 2026-11-11, Thu 11-12, Fri 11-13, Mon 11-16. Sat 11-14 and Tue 11-10 are closed.
D_PREV = date(2026, 11, 12)
D_LAST = date(2026, 11, 13)

CONFIG_DICT: dict[str, Any] = {
    "premium_cross": {"enabled": True, "measure": "prem_fv", "levels_pct": [25, 50, 100]},
    "band_touch": {"enabled": True},
    "premium_jump": {"enabled": True, "measure": "prem_fv", "points": 10},
    "inav_premium_cross": {
        "enabled": True,
        "levels_pct": [25, 50, 100],
        "inav_sources": ["amc_motilal", "amc_mirae", "nse_quote"],
        "max_pair_gap_minutes": 10,
    },
    "data_failure": {"enabled": True, "expected_by_ist": "21:00", "check_recorder": True},
    "dedupe_keep_days": 14,
}


def make_config(**overrides: Any) -> AlertConfig:
    """The standard test config; ``overrides`` replace whole top-level sections."""
    return AlertConfig.model_validate({**CONFIG_DICT, **overrides})


def only(rule: str, enabled: bool = True, **section: Any) -> AlertConfig:
    """A config where every rule is off except ``rule`` (so a test sees one rule at a time).

    ``section`` overrides settings of that rule, e.g. ``only("premium_jump", points=5)``.
    """
    cfg = {k: {**v, "enabled": False} if isinstance(v, dict) else v for k, v in CONFIG_DICT.items()}
    cfg[rule] = {**CONFIG_DICT[rule], "enabled": enabled, **section}
    return AlertConfig.model_validate(cfg)


def etf_day(rows: list[dict[str, Any]]) -> pd.DataFrame:
    """A small etf_day frame. Each row needs symbol and date; other columns are optional."""
    return pd.DataFrame(rows)


def ist(day: date, hh: int, mm: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=IST)


def snapshots(rows: list[tuple[str, datetime, str, float | None, float | None]]) -> pd.DataFrame:
    """inav_snapshots frame from (symbol, ts, source, last_price, inav) tuples."""
    return pd.DataFrame(
        {
            "symbol": [r[0] for r in rows],
            "ts": pd.to_datetime([r[1] for r in rows]),
            "source": [r[2] for r in rows],
            "last_price": [r[3] for r in rows],
            "inav": [r[4] for r in rows],
        }
    )


class FakeStore:
    """Stands in for ``etfp.store.Store``: holds frames by table name."""

    def __init__(self, **tables: pd.DataFrame) -> None:
        self.tables = tables

    def has_table(self, table: str) -> bool:
        return table in self.tables

    def read_table(self, name: str) -> pd.DataFrame:
        return self.tables[name]
