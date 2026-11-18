"""Hosted-dashboard snapshot (`etfp export dashboard`): only allowed columns leave, and the
folder is something the dashboard can read. Uses the real config/dataset.yaml."""

from __future__ import annotations

import json
from datetime import date, datetime

import pandas as pd
import pytest

from etfp import build  # noqa: F401  (registers the clean schemas)
from etfp import export_dashboard as ed
from etfp.alerts.rules import DISCLAIMER_SHORT
from etfp.export import ETF_DAY_COLUMNS
from etfp.export_dataset import DatasetLicenceError, load_dataset_config
from etfp.store import Store
from etfp.timeutil import IST

NOW = datetime(2026, 11, 18, 9, 0, tzinfo=IST)
D1, D2 = date(2026, 11, 12), date(2026, 11, 13)
SENTINEL = 777.5  # value planted in every column the snapshot must not carry


def etf_day_full() -> pd.DataFrame:
    """Every etf_day column the pipeline writes; numbers in source columns are the sentinel."""
    n = 4
    published = set(load_dataset_config().tables["etf_day"].columns)
    df = pd.DataFrame({c: [0.25 if c in published else SENTINEL] * n for c in ETF_DAY_COLUMNS})
    df["symbol"] = ["X", "X", "C1", "C1"]
    df["date"] = [D1, D2, D1, D2]
    for col in ("source_file", "units_source_file", "band_source_file", "nav_same_source_file",
                "nav_known_source_file", "fv_input_files", "regime", "band_base_kind",
                "regime_inferred_method", "units_source", "fv_beta_method", "fv_method",
                "underlying_market", "asset_class", "group_label", "creation_event"):  # fmt: skip
        if col in df.columns:
            df[col] = "text"
    df["fv_input_files"] = "yahoo/2026-11-12/a.json"  # a licensed lineage path
    df["prem_raw"] = [0.5, 0.6, 0.0, 0.01]
    df["prem_nav_known"] = [0.5, 0.6, 0.0, 0.01]
    df["x_april"] = [0.1, 0.2, -0.5, -0.5]
    df["hit_up"] = [False, True, False, False]
    for col in df.columns:
        if col.startswith("dq_"):
            df[col] = False
    return df


def master_full() -> pd.DataFrame:
    spec = load_dataset_config().tables["etf_master"]
    df = pd.DataFrame({c: ["text"] * 3 for c in spec.columns})
    df["symbol"] = ["X", "C1", "NOT_IN_ETF_DAY"]
    df["group_label"] = ["blocked", "control", "control"]
    df["tracked"] = [True, False, False]
    return df


def events_full() -> pd.DataFrame:
    spec = load_dataset_config().tables["events"]
    df = pd.DataFrame({c: ["text"] for c in spec.columns})
    df["date"], df["first_tradable_session"] = D1, D2
    df["ts_ist"] = pd.Timestamp("2026-11-12 10:00", tz=IST)
    df["verified"] = False
    return df


@pytest.fixture
def snapshot(tmp_path):
    out = tmp_path / "snap"
    frames = {"etf_day": etf_day_full(), "etf_master": master_full(), "events": events_full()}
    folder = ed.export_dashboard(
        Store(tmp_path / "data" / "clean"), out, root=tmp_path, now=NOW, source_frames=frames
    )
    return folder


def test_snapshot_folder_contents_and_row_counts(snapshot) -> None:
    names = sorted(p.name for p in snapshot.iterdir())
    assert names == [
        "README.md", "etf_day.parquet", "etf_master.parquet", "events.parquet", "manifest.json"
    ]  # fmt: skip
    manifest = json.loads((snapshot / "manifest.json").read_text())
    assert manifest["kind"] == ed.SNAPSHOT_KIND
    assert manifest["rows"] == {
        "etf_day": 4,
        "etf_master": 2,
        "events": 1,
    }  # master: only funds with rows
    assert manifest["data_through"] == "2026-11-13"
    assert manifest["symbols"] == 2
    assert manifest["built_ist"].endswith("+05:30")  # timezone-aware, India
    assert manifest["disclaimer"] == DISCLAIMER_SHORT
    for name in ("etf_day.parquet", "etf_master.parquet", "events.parquet"):
        assert manifest["files"][name]["bytes"] == (snapshot / name).stat().st_size
    assert "nse" in manifest["licence_checks_open"]  # D34 still open
    assert len(pd.read_parquet(snapshot / "etf_day.parquet")) == 4


def test_no_dropped_or_denied_column_leaks(snapshot) -> None:
    cfg = load_dataset_config()
    day = pd.read_parquet(snapshot / "etf_day.parquet")
    spec = cfg.tables["etf_day"]
    assert set(day.columns) <= set(spec.columns)  # a subset of what the open dataset publishes
    assert not set(day.columns) & set(spec.drop)  # nothing from the drop list
    for col in day.columns:
        assert cfg.column_denied("etf_day", col) is None, col
    for must_be_absent in ("close", "nav_known_at_close", "band_low_official", "units", "fv",
                           "prem_fv", "fv_method", "usdinr_ref", "source_file"):  # fmt: skip
        assert must_be_absent not in day.columns
    for name in ("etf_day", "etf_master", "events"):  # no sentinel value anywhere
        frame = pd.read_parquet(snapshot / f"{name}.parquet")
        numeric = frame.select_dtypes("number")
        assert not (numeric == SENTINEL).any().any(), name
    text = b"".join(p.read_bytes() for p in snapshot.glob("*.parquet")).lower()
    assert b"bloomberg" not in text and b"yahoo" not in text  # no licensed lineage path
    # What the dashboard needs is there.
    for col in ("symbol", "date", "prem_raw", "prem_nav_known", "x_april", "hit_up",
                "band_low_initial", "dq_nav_stale"):  # fmt: skip
        assert col in day.columns
    master = pd.read_parquet(snapshot / "etf_master.parquet")
    assert sorted(master["symbol"]) == ["C1", "X"]
    assert "isin" not in master.columns and "source_file" not in master.columns


def test_readme_has_the_disclaimer(snapshot) -> None:
    readme = (snapshot / "README.md").read_text()
    assert DISCLAIMER_SHORT in readme
    assert "D34" in readme  # says the licence check is still open


def test_unclassified_new_column_stops_the_export(tmp_path) -> None:
    day = etf_day_full()
    day["brand_new_column"] = 1.0
    frames = {"etf_day": day, "etf_master": master_full(), "events": events_full()}
    with pytest.raises(DatasetLicenceError, match="not classified"):
        ed.export_dashboard(
            Store(tmp_path / "c"), tmp_path / "snap", root=tmp_path, now=NOW, source_frames=frames
        )
    assert not (tmp_path / "snap").exists() or not list((tmp_path / "snap").iterdir())


def test_licensed_lineage_in_a_published_column_stops_the_export(tmp_path) -> None:
    day = etf_day_full()
    day["nav_known_source_file"] = "bloomberg/2026-11-12/x.csv"
    frames = {"etf_day": day, "etf_master": master_full(), "events": events_full()}
    with pytest.raises(DatasetLicenceError, match="licence guard"):
        ed.export_dashboard(
            Store(tmp_path / "c"), tmp_path / "snap", root=tmp_path, now=NOW, source_frames=frames
        )


def test_dashboard_columns_are_all_published_columns() -> None:
    spec = load_dataset_config().tables["etf_day"]
    assert set(ed.ETF_DAY_COLUMNS) <= set(spec.columns)


def test_second_export_replaces_the_first(tmp_path) -> None:
    out = tmp_path / "snap"
    frames = {"etf_day": etf_day_full(), "etf_master": master_full(), "events": events_full()}
    kw = {"root": tmp_path, "now": NOW, "source_frames": frames}
    ed.export_dashboard(Store(tmp_path / "c"), out, **kw)
    (out / "stale.parquet").write_bytes(b"old")
    ed.export_dashboard(Store(tmp_path / "c"), out, **kw)
    assert not (out / "stale.parquet").exists()
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".snap")]  # temp folder gone
