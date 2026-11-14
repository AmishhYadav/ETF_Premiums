"""Runs the real Streamlit page headless (streamlit's AppTest) against a throw-away data folder.

Skipped when streamlit/plotly are not installed (``uv sync --extra dashboard``). These are smoke
tests: the page must run without an exception, show the disclaimer, and show the right message
for an empty folder. The numbers themselves are tested in test_dashboard_data.py.
"""

from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

pytest.importorskip("plotly")
pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest

from etfp.store import Store, get_schema
from etfp.timeutil import IST

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "dashboard" / "app.py"
DISCLAIMER = (
    "For research and information only. Not investment advice. "
    "Figures may contain errors; check official NSE and AMFI data before acting."
)


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fresh project root (config copied in) that the app picks up through ETFP_ROOT."""
    import streamlit as st

    st.cache_data.clear()
    shutil.copytree(ROOT / "config", tmp_path / "config")
    monkeypatch.setenv("ETFP_ROOT", str(tmp_path))
    return tmp_path


def run_app() -> AppTest:
    at = AppTest.from_file(str(APP), default_timeout=60)
    at.run()
    return at


def texts(at: AppTest) -> str:
    parts = (
        [e.value for e in at.warning] + [e.value for e in at.info] + [e.value for e in at.caption]
    )
    return "\n".join(parts)


def blank_table(table: str, /, **values: list) -> pd.DataFrame:
    """A frame with every registered column of ``table`` (empty values) plus the given ones."""
    n = len(next(iter(values.values())))
    frame = pd.DataFrame({c: [None] * n for c in get_schema(table).column_names})
    for col, vals in values.items():
        frame[col] = vals
    return frame


def test_empty_store_shows_build_hint_and_disclaimer(project: Path) -> None:
    at = run_app()
    assert not at.exception
    page = texts(at)
    assert DISCLAIMER in page and "uv run etfp build" in page
    assert len(at.tabs) == 0  # nothing else is drawn without data


def test_page_runs_with_data(project: Path) -> None:
    store = Store(project / "data" / "clean")
    d1, d2 = date(2026, 11, 12), date(2026, 11, 13)
    store.write_table(
        "etf_day",
        pd.DataFrame(
            {
                "symbol": ["MONQ50", "MONQ50"],
                "date": [d1, d2],
                "close": [100.0, 150.0],
                "nav_known_at_close": [80.0, 100.0],
                "fv": [80.0, 120.0],
                "prem_fv": [0.25, 0.25],
                "prem_raw": [0.3, 0.5],
                "band_low_official": [80.0, 100.0],
                "band_high_official": [120.0, 160.0],
                "hit_up": [False, True],
                "dq_missing_close": [False, False],
                "x_april": [0.0, 0.2231436],  # ln(150 / 120)
            }
        ),
    )
    store.write_table(
        "events",
        blank_table(
            "events", event_id=["e1"], date=[d1], type=["warning"], category=["caution"],
            first_tradable_session=[d2], description=["BSE caution"], verified=[False],
            source_file=["data/manual/events.csv:1"],
        ),
    )  # fmt: skip
    store.write_table(
        "etf_master",
        blank_table(
            "etf_master", symbol=["MONQ50"], name=["Motilal Nasdaq Q 50"], tracked=[True],
            source_file=["config/etfs.yaml"],
        ),
    )  # fmt: skip
    stamps = pd.to_datetime(["2026-11-13 10:00", "2026-11-13 10:00"]).tz_localize(IST)
    store.write_table(
        "inav_snapshots",
        blank_table(
            "inav_snapshots", symbol=["MONQ50", "MONQ50"], ts=list(stamps),
            source=["amc_motilal", "nse_etf_api"], last_price=[None, 130.0], inav=[100.0, None],
            source_file=["raw/a", "raw/b"],
        ),
    )  # fmt: skip

    at = run_app()
    assert not at.exception, [e.value for e in at.exception]
    assert DISCLAIMER in texts(at)
    labels = [t.label for t in at.tabs]
    assert labels[:2] == ["Overview", "ETF detail"]
    assert "April 2027" in labels and "Events log" in labels
    assert at.dataframe  # the overview table is drawn
    events_tab = at.tabs[labels.index("Events log")]
    assert events_tab.dataframe and "e1" in events_tab.dataframe[0].value["event_id"].tolist()
    april_tab = at.tabs[labels.index("April 2027")]
    assert april_tab.dataframe  # the latest-x_april table
    assert "Days to 1 Apr 2027 (IST)" in {m.label for m in april_tab.metric}
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Latest data date"] == "2026-11-13"
    assert metrics["Premium over iNAV"] == "30.0%"  # 130 / 100 - 1
