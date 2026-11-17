"""underlying_bar_values (D43): which free bar is used at 15:30 IST and at the US close."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from etfp.clean import underlying_values as uv
from etfp.collect import yahoo_intraday as yi

FIX = Path(__file__).parent / "fixtures" / "yahoo_intraday"


def test_table_from_fixture_bars() -> None:
    """Fixture README: 15:30 IST on 13 Nov -> NQ 21000.50, USD/INR 83.4550 (5m bars ending
    10:00 UTC, stale 0); US close of 12 Nov (21:00 UTC) -> NQ 21100.00, USD/INR 83.4125."""
    bars = pd.concat(
        [
            yi.parse(
                (FIX / "NQ_F_5m.json").read_bytes(), source_file="yahoo_intraday/d/NQ_F_5m.json"
            ),
            yi.parse(
                (FIX / "USDINR_X_5m.json").read_bytes(), source_file="yahoo_intraday/d/U.json"
            ),
        ],
        ignore_index=True,
    )
    t = uv.values_from_bars(bars).set_index(["date", "moment", "symbol"])
    assert list(t.reset_index().columns) == uv.COLUMNS
    ind = t.loc[(date(2026, 11, 13), "IN_1530")]
    assert ind.loc["NQ=F", "close"] == 21000.50 and ind.loc["USDINR=X", "close"] == 83.4550
    assert (ind["stale_minutes"] == 0).all() and (ind["interval_minutes"] == 5).all()
    us = t.loc[(date(2026, 11, 12), "US_CLOSE")]
    assert us.loc["NQ=F", "close"] == 21100.00 and us.loc["USDINR=X", "close"] == 83.4125
    assert (t["bar_end"] <= t["at"]).all()  # never a bar that ended after the moment
