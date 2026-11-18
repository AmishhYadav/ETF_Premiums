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


# --------------------------------------------------------------------------
# Controls, fair-value note, wording, hosted snapshot
# --------------------------------------------------------------------------
D1, D2 = date(2026, 11, 12), date(2026, 11, 13)


def _write_controls_project(project: Path, *, nav_only: bool = True) -> None:
    """Two tracked funds (X, Y) and three controls (C1, C2, C3), two days each.

    Known-NAV premium on 13 Nov: X 0.50, Y 0.20; controls 0.01, 0.03, 0.02 -> control median 0.02.
    """
    store = Store(project / "data" / "clean")
    rows = []
    prem = {"X": (0.4, 0.5), "Y": (0.1, 0.2), "C1": (0.0, 0.01), "C2": (0.02, 0.03),
            "C3": (0.01, 0.02)}  # fmt: skip
    for sym, (p1, p2) in prem.items():
        for day, p in ((D1, p1), (D2, p2)):
            rows.append(
                {
                    "symbol": sym, "date": day, "close": 100.0 * (1 + p),
                    "nav_known_at_close": 100.0, "fv": 100.0, "prem_fv": p,
                    "prem_nav_known": p, "prem_raw": p, "hit_up": False,
                    "fv_method": "nav_only" if nav_only else "futures",
                }
            )  # fmt: skip
    store.write_table("etf_day", pd.DataFrame(rows))
    groups = {"X": "blocked", "Y": "unclear", "C1": "control", "C2": "control", "C3": "control"}
    store.write_table(
        "etf_master",
        blank_table(
            "etf_master", symbol=list(groups), name=[f"Fund {s}" for s in groups],
            group_label=list(groups.values()), tracked=[True, True, False, False, True],
            source_file=["config/etfs.yaml"] * 5,
        ),
    )  # fmt: skip


def test_controls_comparison_and_fair_value_note(project: Path) -> None:
    _write_controls_project(project)
    at = run_app()
    assert not at.exception, [e.value for e in at.exception]
    page = texts(at)
    assert page.count(DISCLAIMER) >= 2 or DISCLAIMER in page
    # Disclaimer at the very top (a warning before any tab) and at the very bottom (last caption).
    assert at.warning[0].value == DISCLAIMER
    assert at.caption[-1].value == DISCLAIMER
    labels = [t.label for t in at.tabs]
    assert "Domestic controls" in labels
    overview = at.tabs[labels.index("Overview")].dataframe[0].value
    assert sorted(overview["Symbol"]) == ["X", "Y"]  # tracked funds only; C3 is tracked: true
    assert "nav_only" in " ".join(i.value for i in at.info)  # plain-words fair-value note
    assert "latest known NAV" in " ".join(i.value for i in at.info)
    controls_tab = at.tabs[labels.index("Domestic controls")]
    latest = controls_tab.dataframe[0].value
    assert sorted(latest["Symbol"]) == ["X", "Y"]
    x_row = latest[latest["Symbol"] == "X"].iloc[0]
    assert x_row["Control median (%)"] == pytest.approx(2.0)  # median of 1, 3, 2 percent
    assert x_row["Difference (points)"] == pytest.approx(48.0)  # 50 - 2
    assert any("3 controls are listed" in c.value for c in controls_tab.caption)
    assert controls_tab.get("plotly_chart")  # the comparison chart is drawn


def test_no_nav_only_note_when_fair_value_is_adjusted(project: Path) -> None:
    _write_controls_project(project, nav_only=False)
    at = run_app()
    assert not at.exception
    assert "nav_only" not in " ".join(i.value for i in at.info)


def test_hosted_snapshot_mode(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The page runs from an `etfp export dashboard` folder with no data/clean at all."""
    from etfp.export_dashboard import export_dashboard

    _write_controls_project(project)
    snap = project / "snapshot"
    export_dashboard(Store(project / "data" / "clean"), snap, root=project,
                     source_frames=_frames_for_snapshot(project))  # fmt: skip
    shutil.rmtree(project / "data")
    monkeypatch.setenv("ETFP_DASHBOARD_DATA", str(snap))
    import streamlit as st

    st.cache_data.clear()
    at = run_app()
    assert not at.exception, [e.value for e in at.exception]
    assert at.warning[0].value == DISCLAIMER and at.caption[-1].value == DISCLAIMER
    assert any("Public snapshot" in i.value for i in at.info)
    labels = [t.label for t in at.tabs]
    overview = at.tabs[labels.index("Overview")].dataframe[0].value
    assert sorted(overview["Symbol"]) == ["X", "Y"]
    assert "Close" not in overview.columns and "Fair value" not in overview.columns


def _frames_for_snapshot(project: Path) -> dict:
    """Full-width source frames (every etf_day column the licence config knows) for the export."""
    from etfp import export

    store = Store(project / "data" / "clean")
    small = store.read_table("etf_day")
    with export.etf_day_schema_registered() as schema:
        full = pd.DataFrame({c: [None] * len(small) for c in schema.column_names})
    for col in small.columns:
        full[col] = small[col].to_numpy()
    full["dq_missing_close"] = False
    return {"etf_day": full, "etf_master": store.read_table("etf_master"), "events": None}
