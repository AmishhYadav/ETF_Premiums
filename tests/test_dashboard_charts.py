"""Figure-building tests. Skipped when plotly is not installed (``uv sync --extra dashboard``)."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("plotly")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard import charts  # noqa: E402
from dashboard.data import Marker  # noqa: E402

D1, D2, D3 = date(2026, 11, 11), date(2026, 11, 12), date(2026, 11, 13)


def test_premium_figure_scales_fractions_to_percent_and_draws_markers() -> None:
    series = pd.DataFrame(
        {
            "symbol": ["A", "A", "B"],
            "date": [D1, D2, D2],
            "value": [0.25, 0.5, 1.0],
        }
    )
    markers = [
        Marker(D2, "prev_close: band base changes", False),
        Marker(D3, "later (planned)", True),
    ]
    fig = charts.premium_figure(series, label="Premium", log_measure=False, markers=markers)
    assert [t.name for t in fig.data] == ["A", "B"]
    assert list(fig.data[0].y) == [25.0, 50.0] and list(fig.data[1].y) == [100.0]
    dashes = [s.line.dash for s in fig.layout.shapes if s.yref == "paper"]
    assert dashes == ["solid", "dash"]  # the planned marker is dashed
    assert fig.layout.yaxis.title.text == "Premium (%)"


def test_log_measure_is_not_scaled() -> None:
    series = pd.DataFrame({"symbol": ["A"], "date": [D1], "value": [0.1]})
    fig = charts.premium_figure(series, label="x", log_measure=True)
    assert list(fig.data[0].y) == [0.1] and fig.layout.yaxis.title.text == "log ratio"


def test_band_figure_uses_only_available_columns() -> None:
    frame = pd.DataFrame(
        {
            "date": [D1, D2],
            "close": [100.0, 110.0],
            "band_low_official": [80.0, 88.0],
            "band_high_official": [120.0, 132.0],
            "band_high_max": [np.nan, np.nan],
        }
    )
    fig = charts.band_figure(frame, "A")
    names = [t.name for t in fig.data]
    assert names == [
        "Official lower limit",
        "Official upper limit",
        "Computed upper limit (widest)",
        "Close",
    ]
    assert fig.data[1].fill == "tonexty"
    only_close = charts.band_figure(frame[["date", "close"]], "A")
    assert [t.name for t in only_close.data] == ["Close"]


def test_level_and_intraday_figures_build() -> None:
    lv = pd.DataFrame({"date": [D1], "close": [1.0], "fv": [0.9]})
    assert [t.name for t in charts.level_figure(lv, "A").data] == ["Close", "Fair value"]
    ts = pd.to_datetime(["2026-11-13 10:00", "2026-11-13 10:15"]).tz_localize("Asia/Kolkata")
    paired = pd.DataFrame(
        {"ts": ts, "last_price": [120.0, 130.0], "inav": [100.0, 100.0], "prem": [0.2, 0.3]}
    )
    fig = charts.intraday_figure(paired, "A", D3)
    assert [t.name for t in fig.data] == ["Last price", "iNAV", "Premium over iNAV (%)"]
    assert list(fig.data[2].y) == pytest.approx([20.0, 30.0])
    fig.to_dict()  # must be serialisable (this is what Streamlit sends to the browser)
