"""Open dataset export: config validation, the two licence guards, and the written folder."""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import date, datetime

import pandas as pd
import pytest
import yaml

from etfp import build  # noqa: F401  (registers the clean schemas the dictionary reads)
from etfp.export import ETF_DAY_COLUMNS
from etfp.export_dataset import (
    DISCLAIMER,
    DatasetLicenceError,
    assert_no_denied,
    export_dataset,
    load_dataset_config,
    parse_dataset_config,
    select_table,
)
from etfp.store import Store
from etfp.timeutil import IST

NOW = datetime(2026, 11, 14, 21, 0, tzinfo=IST)

RAW = {
    "dataset_format_version": "1.0.0",
    "title": "Test dataset",
    "sources": {
        "nse": {"name": "NSE", "licence_status": "unverified", "note": "check"},
        "amfi": {"name": "AMFI", "licence_status": "unverified", "note": "check"},
        "project": {"name": "Us", "licence_status": "own_work"},
    },
    "deny": {
        "sources": {"bloomberg": "licence"},
        "tables": {"china_etf_daily": "Bloomberg"},
        "table_patterns": ["bloomberg", "china"],
        "columns": {"etf_day": {"fv": "Bloomberg futures", "pi": "uses fv"}},
        "column_patterns": ["(^|_)fv(_|$)", "futures"],
        "rows": {"limit_day": {"obs_source": ["bloomberg_1m"]}},
        "lineage_substrings": ["bloomberg", "yahoo"],
    },
    "tables": {
        "etf_day": {
            "from": "clean",
            "primary_key": ["symbol", "date"],
            "date_column": "date",
            "sources": ["nse", "amfi", "project"],
            "columns": ["symbol", "date", "close", "prem_raw", "source_file"],
            "drop": {"close_raw": "not needed"},
        },
        "limit_day": {
            "from": "clean",
            "primary_key": ["symbol", "date"],
            "date_column": "date",
            "sources": ["nse"],
            "columns": ["symbol", "date", "obs_source", "source_file"],
            "drop": {},
        },
    },
}


def cfg(raw=None):
    return parse_dataset_config(copy.deepcopy(raw or RAW))


def etf_day_frame(**extra) -> pd.DataFrame:
    df = pd.DataFrame(
        {
            "symbol": ["MONQ50", "MONQ50"],
            "date": [date(2026, 11, 12), date(2026, 11, 13)],
            "close": [141.93, 142.5],
            "prem_raw": [0.19533, 0.2],
            "source_file": ["nse_bhavcopy_full/2026-11-12/a.csv", "nse_bhavcopy_full/b.csv"],
            "close_raw": [141.93, 142.5],
            "fv": [130.0, 131.0],
            "fv_in_futures_t": [1.0, 1.0],
            "pi": [0.1, 0.1],
        }
    )
    for k, v in extra.items():
        df[k] = v
    return df


def limit_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": ["MONQ50", "MON100", "MAFANG"],
            "date": [date(2026, 10, 1), date(2026, 10, 1), date(2026, 10, 2)],
            "obs_source": ["none", "bloomberg_1m", "snapshots_15m"],
            "source_file": ["etf_day", "bloomberg/2026-10-01/x.csv", "amc_inav/2026-10-02/y"],
        }
    )


# ---------------------------------------------------------------- the real config
def test_real_config_loads_and_denies_every_fair_value_column() -> None:
    real = load_dataset_config()
    spec = real.tables["etf_day"]
    # Every etf_day column is classified (a new column must be added to dataset.yaml).
    for col in ETF_DAY_COLUMNS:
        classified = col in spec.columns or col in spec.drop or real.column_denied("etf_day", col)
        assert classified, f"etf_day.{col} not classified in config/dataset.yaml"
    # Denied names are real columns (a typo in the deny list would protect nothing).
    assert set(real.deny_columns["etf_day"]) <= set(ETF_DAY_COLUMNS)
    for col in ETF_DAY_COLUMNS:
        if col.startswith("fv") or "_fv" in col or col in {"pi", "cap_pi", "rationed"}:
            assert real.column_denied("etf_day", col), col
    for table in ("china_etf_daily", "underlying_daily", "fx_daily"):
        assert real.table_denied(table) and table not in real.tables
    assert real.deny_rows["limit_day"]["obs_source"] == ["bloomberg_1m"]
    unverified = {k for k, s in real.sources.items() if s["licence_status"] == "unverified"}
    assert {"nse", "bse", "amfi", "amc_inav", "google_trends"} <= unverified


# ---------------------------------------------------------------- config validation
def test_config_rejects_a_column_that_is_both_exported_and_denied() -> None:
    raw = copy.deepcopy(RAW)
    raw["tables"]["etf_day"]["columns"].append("prem_fv")  # matches the fv pattern
    with pytest.raises(DatasetLicenceError, match="listed for export but denied"):
        cfg(raw)


def test_config_rejects_a_denied_table_or_source() -> None:
    raw = copy.deepcopy(RAW)
    raw["tables"]["china_etf_daily"] = copy.deepcopy(raw["tables"]["limit_day"])
    with pytest.raises(DatasetLicenceError, match="denied"):
        cfg(raw)
    raw = copy.deepcopy(RAW)
    raw["tables"]["etf_day"]["sources"].append("bloomberg")
    with pytest.raises(DatasetLicenceError, match="denied source"):
        cfg(raw)


# ---------------------------------------------------------------- guard 1
def test_select_keeps_listed_columns_and_drops_denied_and_dropped() -> None:
    out, removed = select_table("etf_day", etf_day_frame(), cfg())
    assert list(out.columns) == ["symbol", "date", "close", "prem_raw", "source_file"]
    assert removed == 0 and len(out) == 2


def test_select_fails_on_an_unclassified_column() -> None:
    df = etf_day_frame(new_signal=[1.0, 2.0])
    with pytest.raises(DatasetLicenceError, match=r"not classified.*new_signal"):
        select_table("etf_day", df, cfg())


def test_select_fails_on_a_missing_listed_column() -> None:
    df = etf_day_frame().drop(columns=["prem_raw"])
    with pytest.raises(DatasetLicenceError, match="missing column"):
        select_table("etf_day", df, cfg())


def test_new_column_matching_a_deny_pattern_is_refused_not_exported() -> None:
    out, _ = select_table("etf_day", etf_day_frame(fv_new_method=[1, 2]), cfg())
    assert "fv_new_method" not in out.columns


def test_select_removes_denied_rows() -> None:
    out, removed = select_table("limit_day", limit_frame(), cfg())
    assert removed == 1
    assert "bloomberg_1m" not in set(out["obs_source"])
    assert list(out["symbol"]) == ["MONQ50", "MAFANG"]


# ---------------------------------------------------------------- guard 2
def test_guard_passes_clean_frames() -> None:
    c = cfg()
    frames = {
        "etf_day": select_table("etf_day", etf_day_frame(), c)[0],
        "limit_day": select_table("limit_day", limit_frame(), c)[0],
    }
    assert_no_denied(frames, c)


@pytest.mark.parametrize(
    ("frames", "message"),
    [
        ({"etf_day": etf_day_frame()[["symbol", "date", "fv"]]}, "denied column etf_day.fv"),
        ({"etf_day": pd.DataFrame({"symbol": ["A"], "pi": [0.1]})}, "denied column etf_day.pi"),
        ({"limit_day": pd.DataFrame({"x_futures_close": [1.0]})}, "futures"),
        ({"china_etf_daily": pd.DataFrame({"code": ["513100"]})}, "denied table"),
        ({"prices_daily": pd.DataFrame({"symbol": ["A"]})}, "not listed"),
        ({"limit_day": limit_frame()}, "denied row"),
        (
            {"etf_day": pd.DataFrame({"source_file": ["bloomberg/x.csv"]})},
            "bloomberg",
        ),
        ({"etf_day": pd.DataFrame({"units_source": ["yahoo_intraday/2026/x"]})}, "yahoo"),
    ],
)
def test_guard_fails_on_anything_denied(frames, message) -> None:
    with pytest.raises(DatasetLicenceError, match=message):
        assert_no_denied(frames, cfg())


# ---------------------------------------------------------------- the written folder
def _write_cfg(tmp_path, raw=None):
    path = tmp_path / "dataset.yaml"
    path.write_text(yaml.safe_dump(raw or RAW), encoding="utf-8")
    return path


def test_export_writes_folder_with_checksums(tmp_path) -> None:
    out_root = tmp_path / "exports" / "dataset"
    folder = export_dataset(
        Store(tmp_path / "data" / "clean"),
        out_root,
        root=tmp_path,
        now=NOW,
        repo_dir=tmp_path,
        config_path=_write_cfg(tmp_path),
        source_frames={"etf_day": etf_day_frame(), "limit_day": limit_frame()},
    )
    assert folder == out_root / "2026-11-14"
    names = {p.name for p in folder.iterdir()}
    assert names == {
        "etf_day.csv", "etf_day.parquet", "limit_day.csv", "limit_day.parquet",
        "DATA_DICTIONARY.md", "README.md", "LICENSE-DATA", "manifest.json",
    }  # fmt: skip
    manifest = json.loads((folder / "manifest.json").read_text())
    assert set(manifest["files"]) == names - {"manifest.json"}
    for name, meta in manifest["files"].items():
        assert hashlib.sha256((folder / name).read_bytes()).hexdigest() == meta["sha256"]
    assert manifest["row_counts"]["tables"] == {"etf_day": 2, "limit_day": 2}
    assert manifest["tables"]["limit_day"]["rows_removed_by_licence_filter"] == 1
    assert manifest["date_range"]["etf_day"] == {"first": "2026-11-12", "last": "2026-11-13"}
    assert manifest["code"]["git_commit"] == "uncommitted"
    assert manifest["build_time_ist"] == "2026-11-14T21:00:00+05:30"
    for table in ("etf_day", "limit_day"):
        cols = pd.read_csv(folder / f"{table}.csv").columns
        assert list(cols) == list(pd.read_parquet(folder / f"{table}.parquet").columns)
        assert not [c for c in cols if "fv" in c or c == "pi"]
    readme = (folder / "README.md").read_text()
    assert DISCLAIMER in readme and "Redistribution questions for the owner" in readme
    assert "TO BE DECIDED BY OWNER" in (folder / "LICENSE-DATA").read_text()
    dictionary = (folder / "DATA_DICTIONARY.md").read_text()
    assert "| prem_raw |" in dictionary and "| fv |" not in dictionary
    assert (out_root / "LATEST.txt").read_text().strip() == str(folder.resolve())


def test_export_refuses_and_leaves_no_folder(tmp_path) -> None:
    out_root = tmp_path / "exports" / "dataset"
    with pytest.raises(DatasetLicenceError, match="not classified"):
        export_dataset(
            Store(tmp_path / "data" / "clean"),
            out_root,
            root=tmp_path,
            now=NOW,
            repo_dir=tmp_path,
            config_path=_write_cfg(tmp_path),
            source_frames={"etf_day": etf_day_frame(surprise=[1, 2]), "limit_day": limit_frame()},
        )
    assert not (out_root / "2026-11-14").exists()
    assert not (out_root / "LATEST.txt").exists()
