"""Plotly figures for the dashboard. Each function takes a prepared table (from ``data.py``)
and returns a figure; no Streamlit here, so the figures can be built and checked in tests.

Premiums arrive as fractions and are drawn as percent. Colours are colour-blind friendly
(Okabe-Ito). Text only describes the data.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from dashboard.data import BAND_COLUMNS, LEVEL_COLUMNS, Marker

PALETTE = (
    "#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00", "#56B4E9", "#F0E442", "#000000",
)  # fmt: skip
_GRID = "rgba(128,128,128,0.25)"


def _base_layout(fig: go.Figure, title: str, y_title: str) -> go.Figure:
    fig.update_layout(
        title=title,
        yaxis_title=y_title,
        hovermode="x unified",
        legend={"orientation": "h", "y": -0.2},
        margin={"l": 10, "r": 10, "t": 50, "b": 10},
        template="plotly_white",
    )
    fig.update_xaxes(gridcolor=_GRID)
    fig.update_yaxes(gridcolor=_GRID)
    return fig


def add_markers(fig: go.Figure, markers: Sequence[Marker]) -> None:
    """Vertical lines at regime changes. Planned ones are dashed."""
    for marker in markers:
        x = pd.Timestamp(marker.day)
        fig.add_shape(
            type="line",
            x0=x,
            x1=x,
            y0=0,
            y1=1,
            yref="paper",
            line={"color": "#555555", "width": 1.5, "dash": "dash" if marker.planned else "solid"},
        )
        fig.add_annotation(
            x=x,
            y=1,
            yref="paper",
            text=marker.label,
            showarrow=False,
            textangle=-90,
            xanchor="right",
            yanchor="top",
            font={"size": 10, "color": "#555555"},
        )


def premium_figure(
    series: pd.DataFrame,
    *,
    label: str,
    log_measure: bool,
    markers: Sequence[Marker] = (),
) -> go.Figure:
    """Lines of ``series`` (symbol, date, value), one per ETF.

    Fractions are drawn as percent. A log measure (``x_april``) is drawn as it is. A zero line
    is always shown: for ``x_april`` it is the level of the April 2027 cap.
    """
    fig = go.Figure()
    for i, (symbol, group) in enumerate(series.groupby("symbol", sort=True)):
        y = group["value"] if log_measure else group["value"] * 100.0
        fig.add_trace(
            go.Scatter(
                x=group["date"], y=y, mode="lines", name=str(symbol),
                line={"color": PALETTE[i % len(PALETTE)], "width": 2},
                hovertemplate="%{y:.3f}" if log_measure else "%{y:.1f}%",
            )
        )  # fmt: skip
    fig.add_hline(y=0, line={"color": "#888888", "width": 1})
    add_markers(fig, markers)
    return _base_layout(fig, label, "log ratio" if log_measure else "Premium (%)")


def level_figure(frame: pd.DataFrame, symbol: str) -> go.Figure:
    """Close, known NAV and fair value (rupees) of one ETF."""
    fig = go.Figure()
    styles = {"close": "solid", "nav_known_at_close": "dot", "fv": "dash"}
    for i, (col, name) in enumerate(LEVEL_COLUMNS.items()):
        if col in frame.columns:
            fig.add_trace(
                go.Scatter(
                    x=frame["date"], y=frame[col], mode="lines", name=name,
                    line={"color": PALETTE[i], "width": 2, "dash": styles[col]},
                )
            )  # fmt: skip
    return _base_layout(fig, f"{symbol}: price, known NAV and fair value", "Rupees per unit")


def band_figure(frame: pd.DataFrame, symbol: str, markers: Sequence[Marker] = ()) -> go.Figure:
    """Close against the price limits: official band shaded, computed limits as lines."""
    fig = go.Figure()
    have = set(frame.columns)
    if {"band_low_official", "band_high_official"} <= have:
        fig.add_trace(
            go.Scatter(
                x=frame["date"], y=frame["band_low_official"], mode="lines",
                name=BAND_COLUMNS["band_low_official"], line={"color": "#D55E00", "width": 1.5},
            )
        )  # fmt: skip
        fig.add_trace(
            go.Scatter(
                x=frame["date"], y=frame["band_high_official"], mode="lines",
                name=BAND_COLUMNS["band_high_official"], line={"color": "#D55E00", "width": 1.5},
                fill="tonexty", fillcolor="rgba(213,94,0,0.10)",
            )
        )  # fmt: skip
    computed = (
        ("band_low_initial", "dot", "#009E73"),
        ("band_high_initial", "dot", "#009E73"),
        ("band_low_max", "dash", "#56B4E9"),
        ("band_high_max", "dash", "#56B4E9"),
    )
    for col, dash, colour in computed:
        if col in have:
            fig.add_trace(
                go.Scatter(
                    x=frame["date"], y=frame[col], mode="lines", name=BAND_COLUMNS[col],
                    line={"color": colour, "width": 1.5, "dash": dash},
                )
            )  # fmt: skip
    fig.add_trace(
        go.Scatter(
            x=frame["date"], y=frame["close"], mode="lines", name="Close",
            line={"color": "#000000", "width": 2},
        )
    )  # fmt: skip
    add_markers(fig, markers)
    return _base_layout(fig, f"{symbol}: close and price limits", "Rupees per unit")


def intraday_figure(paired: pd.DataFrame, symbol: str, day: date) -> go.Figure:
    """Top: last traded price and iNAV. Bottom: premium of the price over the iNAV (percent)."""
    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True, row_heights=[0.6, 0.4], vertical_spacing=0.06
    )
    fig.add_trace(
        go.Scatter(x=paired["ts"], y=paired["last_price"], mode="lines+markers", name="Last price",
                   line={"color": "#0072B2"}), row=1, col=1,
    )  # fmt: skip
    fig.add_trace(
        go.Scatter(x=paired["ts"], y=paired["inav"], mode="lines+markers", name="iNAV",
                   line={"color": "#D55E00", "dash": "dot"}), row=1, col=1,
    )  # fmt: skip
    fig.add_trace(
        go.Scatter(x=paired["ts"], y=paired["prem"] * 100.0, mode="lines+markers",
                   name="Premium over iNAV (%)", line={"color": "#009E73"},
                   hovertemplate="%{y:.1f}%"), row=2, col=1,
    )  # fmt: skip
    fig.update_yaxes(title_text="Rupees per unit", row=1, col=1)
    fig.update_yaxes(title_text="Premium (%)", row=2, col=1)
    return _base_layout(fig, f"{symbol}: intraday on {day.isoformat()} (IST)", "Rupees per unit")
