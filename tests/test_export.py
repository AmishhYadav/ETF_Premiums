# ruff: noqa: E501
"""The research export: folder contents, schema.json, manifest.json, LATEST.txt, failure safety."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from datetime import date, datetime

import pandas as pd
import pyarrow.parquet as pq
import pytest

from etfp import export, paths
from etfp.build import build_all
from etfp.export import (
    ETF_DAY_COLUMNS,
    SCHEMA_VERSION,
    export_research,
    parse_open_verify_items,
    recorder_gaps,
)
from etfp.store import Store
from etfp.timeutil import IST
from test_clean_helpers import make_raw_root

NOW = datetime(2026, 11, 13, 22, 0, tzinfo=IST)
EXPECTED_FILES = {
    "etf_day.parquet", "inav_snapshots.parquet", "units_monthly.parquet", "units_daily.parquet",
    "unit_events.parquet", "fof_monthly.parquet", "events.parquet", "trends_daily.parquet", "limit_day.parquet", "slb_eligible.parquet", "slb_daily.parquet", "bse_prices.parquet", "feeder_nav_daily.parquet", "china_etf_daily.parquet", "valve_monthly.parquet", "notices.parquet", "bullion_ref_daily.parquet", "fx_ref_daily.parquet",
    "schema.json", "DATA_DICTIONARY.md", "manifest.json",
}  # fmt: skip

DECISIONS = """# Decisions

## Something else

| a | b | c |
|---|---|---|
| ignore | me | this |

## Open VERIFY items (running list)

| Item | Where it matters | Status |
|---|---|---|
| iNAV response shape | M2 | OPEN |
| Band rounding \\| more days | M3 bands | PRELIMINARY |

## Later section

| x | y | z |
|---|---|---|
| nope | nope | nope |
"""


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    root = make_raw_root(tmp_path_factory.mktemp("export"))
    build_all(root)
    return root, Store(root / "data" / "clean")


@pytest.fixture(scope="module")
def exported(built, tmp_path_factory):
    root, store = built
    work = tmp_path_factory.mktemp("exp_out")
    decisions = work / "DECISIONS.md"
    decisions.write_text(DECISIONS, encoding="utf-8")
    out_root = work / "exports" / "research"
    folder = export_research(
        store, out_root, root=root, now=NOW, repo_dir=work, decisions_path=decisions
    )
    return folder, out_root, store, root


def test_definitions_are_complete_and_well_formed() -> None:
    assert ETF_DAY_COLUMNS
    for name, doc in ETF_DAY_COLUMNS.items():
        assert doc.definition.strip(), f"{name} has no definition"
        assert doc.dtype in {"string", "float", "int", "date", "timestamp", "bool"}, name
    schema = export.etf_day_schema()
    assert schema.column_names == list(ETF_DAY_COLUMNS)
    assert schema.primary_key == ("symbol", "date")


def test_every_blueprint_measure_is_exported_or_a_known_gap() -> None:
    text = (paths.project_root() / "docs" / "BLUEPRINT.md").read_text(encoding="utf-8")
    section = text.split("## Measures", 1)[1].split("\n## ", 1)[0]
    wanted: set[str] = set()
    for line in section.splitlines():
        if line.startswith("|") and not line.startswith("|---") and "Variable" not in line:
            first = line.split("|")[1]
            wanted |= set(re.findall(r"`([a-z_0-9]+)`", first))
    missing = wanted - set(ETF_DAY_COLUMNS)
    assert missing == set()  # `attention` (R15, Google Trends) joined etf_day in schema 1.3.0


def test_folder_has_every_file(exported) -> None:
    folder, out_root, _, _ = exported
    assert folder == out_root / "2026-11-13"  # India date of the build time
    assert {p.name for p in folder.iterdir()} == EXPECTED_FILES
    assert not [p for p in out_root.iterdir() if p.name.startswith(".")]  # no temp leftovers


def test_latest_txt_points_at_the_folder(exported) -> None:
    folder, out_root, _, _ = exported
    assert (out_root / "LATEST.txt").read_text().strip() == str(folder.resolve())


def test_etf_day_parquet_round_trips_with_typed_columns(exported, built) -> None:
    folder, _, store, _ = exported
    table = pq.read_table(folder / "etf_day.parquet")
    assert table.num_rows == 16
    assert table.column_names == list(ETF_DAY_COLUMNS)
    assert str(table.schema.field("date").type) == "date32[day]"
    assert str(table.schema.field("units_outstanding").type) == "int64"
    assert str(table.schema.field("rationed").type) == "bool"
    inav = pq.read_table(folder / "inav_snapshots.parquet")
    # R18/CLAUDE.md: total buy/sell quantity and order-book depth are kept
    assert {"total_buy_qty", "total_sell_qty", "depth_json", "bid_qty", "ask_qty"} <= set(
        inav.column_names
    )
    assert inav.num_rows == store.read_table("inav_snapshots").shape[0]


def test_schema_json_documents_every_exported_column(exported) -> None:
    folder, *_ = exported
    schema = json.loads((folder / "schema.json").read_text())
    assert schema["schema_version"] == SCHEMA_VERSION == "1.7.0"
    assert [c["version"] for c in schema["changelog"]] == [
        "1.0.0", "1.1.0", "1.2.0", "1.3.0", "1.4.0", "1.5.0", "1.6.0", "1.7.0"
    ]  # fmt: skip
    assert "MEANING CHANGE" in schema["changelog"][1]["changes"]  # 1.1.0 turnover
    for name in (p.stem for p in folder.glob("*.parquet")):
        cols = pq.read_table(folder / f"{name}.parquet").column_names
        documented = [c["name"] for c in schema["tables"][name]["columns"]]
        assert documented == cols, name
        for c in schema["tables"][name]["columns"]:
            assert set(c) == {"name", "type", "definition", "units", "formula", "source"}
            assert c["definition"].strip(), f"{name}.{c['name']} has no definition"
    assert schema["tables"]["etf_day"]["primary_key"] == ["symbol", "date"]


def test_data_dictionary_lists_columns_and_changelog(exported) -> None:
    folder, *_ = exported
    text = (folder / "DATA_DICTIONARY.md").read_text()
    assert "Schema version **1.7.0**" in text
    assert "## valve_monthly" in text and "| valve_status |" in text
    assert "## etf_day" in text and "| cap_pi |" in text and "| prem_nav_known |" in text
    assert text.count("\n| ") > len(ETF_DAY_COLUMNS)


def test_manifest_contents(exported) -> None:
    folder, _, _, _ = exported
    m = json.loads((folder / "manifest.json").read_text())
    assert m["schema_version"] == "1.7.0"
    assert m["build_time_ist"] == "2026-11-13T22:00:00+05:30"
    assert m["git"] == {"commit": "uncommitted", "dirty": True}  # the temp folder is no repo
    assert m["date_range"]["etf_day"] == {"first": "2021-01-04", "last": "2026-11-09"}
    assert m["date_range"]["units_daily"]["first"] == "2024-07-01"
    assert m["date_range"]["inav_snapshots"]["first"] == "2026-11-10"
    counts = m["row_counts"]
    assert counts["tables"]["etf_day"] == 16 == sum(counts["etf_day_by_symbol"].values())
    assert counts["etf_day_by_symbol"]["MONQ50"] == 1
    nulls = m["missing_etf_days_per_column"]
    assert nulls["etf_day_rows"] == 16
    assert nulls["null_counts"]["band_high_official"] == 16  # no band file covers these days
    assert "close" not in nulls["null_counts"]  # complete columns are not listed
    assert m["dq_flag_counts"]["all"]["dq_nav_stale"] >= 1
    assert "MONQ50" in m["dq_flag_counts"]["by_symbol"]
    assert m["fv_method_counts"]["MONQ50"] == {"no_nav": 1}  # its only NAV is 60 days old (D31)
    # all international sizes are confirmed (D36); only domestic/bullion controls remain
    assert m["creation_unit_sizes_missing"] and "MASPTOP50" not in m["creation_unit_sizes_missing"]
    assert m["recorder_gaps"]["rows_by_source"]["nse_etf_api"] == 11
    assert m["missing_nse_sessions_by_symbol"]["NIFTYBEES"]["count"] > 1000  # sparse fixtures
    assert m["bloomberg_files"] == []
    assert m["open_verify_items"] == [
        {"item": "iNAV response shape", "where_it_matters": "M2", "status": "OPEN"},
        {
            "item": "Band rounding | more days",
            "where_it_matters": "M3 bands",
            "status": "PRELIMINARY",
        },
    ]


def test_manifest_file_hashes_match_the_files(exported) -> None:
    folder, *_ = exported
    m = json.loads((folder / "manifest.json").read_text())
    assert set(m["files"]) == EXPECTED_FILES - {"manifest.json"}
    for name, info in m["files"].items():
        data = (folder / name).read_bytes()
        assert info["sha256"] == hashlib.sha256(data).hexdigest()
        assert info["bytes"] == len(data)


def test_unit_events_and_events_are_exported(exported) -> None:
    folder, *_ = exported
    ue = pd.read_parquet(folder / "unit_events.parquet")
    assert set(ue["event_type"]) <= {"creation", "redemption"}
    mon = ue[ue["symbol"] == "MONQ50"]
    assert (mon["event_type"] == "creation").all()
    assert mon["creation_units_change"].sum() == pytest.approx(19.0)
    ev = pd.read_parquet(folder / "events.parquet")
    assert (ev["type"] == "units_change").sum() == len(ue)
    ud = pd.read_parquet(folder / "units_daily.parquet")
    assert {"units_change", "creation_units_change", "units_prev"} <= set(ud.columns)


def test_valve_monthly_is_exported(exported) -> None:
    folder, *_ = exported
    vm = pd.read_parquet(folder / "valve_monthly.parquet")
    assert not vm.duplicated(["symbol", "month"]).any()
    assert set(vm["valve_status"]) <= {"open", "closed", "redeeming_only", "unknown"}
    assert vm["month"].min() == "2021-01"
    # Fixture security master (D7 dates): MONQ50 rises in Jan 2025 and Apr 2026.
    mon = vm[vm["symbol"] == "MONQ50"].set_index("month")
    assert mon.loc["2025-01", "valve_status"] == "open"
    assert mon.loc["2025-01", "creation_units_net"] == pytest.approx(8.0)
    assert mon.loc["2021-06", "valve_status"] == "unknown"  # no units data then
    m = json.loads((folder / "manifest.json").read_text())
    assert m["row_counts"]["tables"]["valve_monthly"] == len(vm)
    assert m["valve_status_counts_by_symbol"]["MONQ50"]["open"] >= 2


def test_reexport_same_day_replaces_the_folder(exported, built) -> None:
    folder, out_root, store, root = exported
    (folder / "stale.txt").write_text("left over")
    again = export_research(store, out_root, root=root, now=NOW, repo_dir=out_root)
    assert again == folder and not (folder / "stale.txt").exists()
    assert {p.name for p in folder.iterdir()} == EXPECTED_FILES
    assert not [p for p in out_root.iterdir() if p.name.startswith(".")]


def test_failed_export_keeps_the_previous_good_one(exported, built, monkeypatch) -> None:
    folder, out_root, store, root = exported
    before = (out_root / "LATEST.txt").read_text()
    first_hash = hashlib.sha256((folder / "etf_day.parquet").read_bytes()).hexdigest()

    def boom(*a, **k):
        raise RuntimeError("manifest failed")

    monkeypatch.setattr(export, "_manifest", boom)
    with pytest.raises(RuntimeError, match="manifest failed"):
        export_research(
            store,
            out_root,
            root=root,
            now=datetime(2026, 11, 14, 9, 0, tzinfo=IST),
            repo_dir=out_root,
        )
    assert (out_root / "LATEST.txt").read_text() == before
    assert not (out_root / "2026-11-14").exists()
    assert not [p for p in out_root.iterdir() if p.name.startswith(".")]
    assert hashlib.sha256((folder / "etf_day.parquet").read_bytes()).hexdigest() == first_hash


def test_export_before_build_says_so(tmp_path) -> None:
    store = Store(tmp_path / "data" / "clean")
    with pytest.raises(FileNotFoundError, match="etfp build"):
        export_research(store, tmp_path / "out", root=tmp_path, now=NOW, repo_dir=tmp_path)
    assert not (tmp_path / "out" / "LATEST.txt").exists()


def test_a_second_day_moves_latest(exported, built) -> None:
    folder, out_root, store, root = exported
    nxt = export_research(
        store, out_root, root=root, now=datetime(2026, 11, 14, 8, 0, tzinfo=IST), repo_dir=out_root
    )
    assert nxt.name == "2026-11-14"
    assert (out_root / "LATEST.txt").read_text().strip() == str(nxt.resolve())
    assert folder.exists()  # the older export stays
    shutil.rmtree(nxt)
    (out_root / "LATEST.txt").write_text(str(folder.resolve()) + "\n")


# ---- pieces ----------------------------------------------------------------------------------------------------------
def test_open_verify_parser_reads_only_that_table(tmp_path) -> None:
    md = tmp_path / "D.md"
    md.write_text(DECISIONS, encoding="utf-8")
    items = parse_open_verify_items(md)
    assert [i["item"] for i in items] == ["iNAV response shape", "Band rounding | more days"]
    assert parse_open_verify_items(tmp_path / "missing.md") == []
    md.write_text("# no such section\n", encoding="utf-8")
    assert parse_open_verify_items(md) == []


def test_real_decisions_file_has_open_items() -> None:
    items = parse_open_verify_items(paths.project_root() / "docs" / "DECISIONS.md")
    assert items and all(set(i) == {"item", "where_it_matters", "status"} for i in items)


def _snaps(stamps: list[str]) -> pd.DataFrame:
    return pd.DataFrame({"ts": pd.to_datetime(stamps).tz_localize(IST), "source": "nse_etf_api"})


def test_recorder_gaps_find_missing_sessions_and_long_pauses() -> None:
    snaps = _snaps(
        [
            "2026-11-02 09:15", "2026-11-02 09:30", "2026-11-02 09:45",  # fine
            "2026-11-04 09:15", "2026-11-04 10:00", "2026-11-04 10:15",  # 45 min hole
            "2026-11-05 21:20",  # evening-only file: not intraday coverage
        ]
    )  # fmt: skip
    g = recorder_gaps(snaps)
    assert g["first_day"] == "2026-11-02" and g["last_day"] == "2026-11-05"
    assert g["sessions_without_intraday_snapshots"] == ["2026-11-03", "2026-11-05"]
    assert g["intraday_gaps"] == [{"date": "2026-11-04", "max_gap_minutes": 45.0}]
    empty = recorder_gaps(snaps.iloc[0:0])
    assert empty["snapshot_rows"] == 0 and empty["sessions_without_intraday_snapshots"] == []


def test_git_info_reports_commit_and_dirty_flag(tmp_path) -> None:
    if shutil.which("git") is None:
        pytest.skip("git not installed")
    assert export._git_info(tmp_path) == {"commit": "uncommitted", "dirty": True}
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    ident = [
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@example.invalid",
        "-c",
        "commit.gpgsign=false",
    ]
    (tmp_path / "a.txt").write_text("a")
    subprocess.run(["git", "add", "a.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", *ident, "commit", "-q", "-m", "x"], cwd=tmp_path, check=True)
    clean = export._git_info(tmp_path)
    assert re.fullmatch(r"[0-9a-f]{40}", clean["commit"]) and clean["dirty"] is False
    (tmp_path / "b.txt").write_text("b")
    assert export._git_info(tmp_path)["dirty"] is True


def test_date_helper_is_ist_based() -> None:
    late = datetime(2026, 11, 13, 23, 30, tzinfo=IST)
    assert late.date() == date(2026, 11, 13)


def test_manifest_reports_limit_day_coverage(exported) -> None:
    folder = exported[0]
    m = json.loads((folder / "manifest.json").read_text())
    ld = m["limit_day"]
    assert ld is not None and ld["rows"] == sum(ld["by_obs_source"].values())
    assert isinstance(ld["days_without_intraday"], list)


def test_1_3_0_changelog_and_new_columns_and_tables(exported) -> None:
    folder, *_ = exported
    schema = json.loads((folder / "schema.json").read_text())
    entry = next(e for e in schema["changelog"] if e["version"] == "1.3.0")
    assert (
        "MEANING CHANGE: x_april is now ln(close / (1.2 x nav_known_at_close))" in entry["changes"]
    )
    assert "x_april_fv" in entry["changes"] and "x_april_nav_known is removed" in entry["changes"]
    cols = {c["name"]: c for c in schema["tables"]["etf_day"]["columns"]}
    assert "x_april_nav_known" not in cols
    assert cols["x_april"]["formula"] == "ln(close / (1.2 x nav_known_at_close))"
    assert cols["x_april_fv"]["formula"] == "ln(close / (1.2 x fv))"
    assert "attention" in cols and cols["attention"]["type"] == "float"
    assert {"feeder_nav_daily", "china_etf_daily"} <= set(schema["tables"])
