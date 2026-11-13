"""Tests for the clean-data store: schemas, type forcing, key checks, DuckDB + Parquet."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq
import pytest

from etfp import store as st
from etfp.store import (
    SCHEMAS,
    Column,
    DuplicateKeyError,
    MissingValueError,
    SchemaError,
    Store,
    TableSchema,
)
from etfp.timeutil import IST

SPEC_TABLES = {
    "etf_master", "prices_daily", "nav_daily", "inav_snapshots", "underlying_daily",
    "fx_daily", "units_monthly", "events", "etf_day",
}  # fmt: skip
REQUEST_TABLES = {"units_daily", "band_official", "fof_monthly"}  # R1, R2, R4


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "clean")


def units(rows: list[tuple]) -> pd.DataFrame:
    """A units_daily frame from (symbol, date, units, source_file) tuples."""
    return pd.DataFrame(
        {
            "symbol": [r[0] for r in rows],
            "date": [r[1] for r in rows],
            "series": ["EQ"] * len(rows),
            "isin": ["INF247L01AU3"] * len(rows),
            "units_outstanding": [r[2] for r in rows],
            "source_file": [r[3] for r in rows],
        }
    )


# -- registry -----------------------------------------------------------------


def test_registry_covers_spec_and_request_tables() -> None:
    assert set(SCHEMAS) >= SPEC_TABLES | REQUEST_TABLES


def test_registry_shapes_are_sound() -> None:
    for name, schema in SCHEMAS.items():
        assert schema.name == name
        assert set(schema.primary_key) <= set(schema.column_names)
        assert all(c.doc for c in schema.columns), f"{name}: every column needs a definition"
        if "source_file" in schema.column_names:  # lineage is mandatory wherever it exists
            assert "source_file" in schema.required_columns()


def test_r1_and_r2_columns_match_the_requests() -> None:
    assert SCHEMAS["units_daily"].column_names[:2] == ["symbol", "date"]
    assert "units_outstanding" in SCHEMAS["units_daily"].column_names
    band = SCHEMAS["band_official"]
    for col in ("file_date", "session_date", "band_low_official", "band_high_official"):
        assert col in band.column_names
    assert band.primary_key == ("symbol", "file_date")


def test_unknown_table_and_path_like_names_are_rejected(store: Store) -> None:
    with pytest.raises(KeyError, match="unknown table"):
        store.read_table("nope")
    with pytest.raises(KeyError, match="unknown table"):
        store.parquet_path("../escape")


def test_register_schema_refuses_silent_overwrite() -> None:
    schema = TableSchema("tmp_t", ("a",), (Column("a", "string", "x"),), "t")
    st.register_schema(schema)
    try:
        with pytest.raises(ValueError, match="already has a schema"):
            st.register_schema(schema)
        st.register_schema(schema, replace=True)
    finally:
        del SCHEMAS["tmp_t"]


# -- round trip -----------------------------------------------------------------


def test_round_trip_keeps_types_and_exact_big_integers(store: Store) -> None:
    big = 2_586_291_385  # NIFTYBEES units: bigger than a 32-bit integer
    df = units(
        [
            ("NIFTYBEES", date(2026, 11, 9), big, "a/1"),
            ("MONQ50", date(2026, 11, 9), 14_675_018, "a/2"),
        ]
    )
    assert store.write_table("units_daily", df) == 2
    back = store.read_table("units_daily")
    assert list(back["symbol"]) == ["MONQ50", "NIFTYBEES"]  # sorted by primary key
    assert back["units_outstanding"].dtype == "Int64"
    assert list(back["units_outstanding"]) == [14_675_018, big]
    assert back["date"].iloc[0] == date(2026, 11, 9)
    assert type(back["date"].iloc[0]) is date  # a calendar date, not a midnight timestamp


def test_parquet_and_duckdb_hold_the_same_exact_types(store: Store) -> None:
    ts = datetime(2026, 11, 11, 9, 15, tzinfo=IST)
    df = pd.DataFrame(
        {
            "symbol": ["MONQ50"],
            "ts": [ts.astimezone(UTC)],  # given in UTC; must come back as the same instant in IST
            "source": ["amc_motilal"],
            "inav": [110.5],
            "source_file": ["x/1"],
        }
    )
    full = pd.DataFrame({c: [None] for c in SCHEMAS["inav_snapshots"].column_names})
    for col in df.columns:
        full[col] = df[col]
    store.write_table("inav_snapshots", full)

    arrow = pq.read_table(store.parquet_path("inav_snapshots")).schema
    assert str(arrow.field("ts").type) == "timestamp[us, tz=Asia/Kolkata]"
    assert str(arrow.field("total_buy_qty").type) == "int64"

    sql = store.query("select ts, inav from inav_snapshots")
    assert sql["ts"].iloc[0] == pd.Timestamp(ts)
    assert str(sql["ts"].dt.tz) == "Asia/Kolkata"
    back = store.read_table("inav_snapshots")
    assert back["ts"].iloc[0] == pd.Timestamp(ts)  # same instant
    assert back["ts"].iloc[0].utcoffset().total_seconds() == 5.5 * 3600


def test_duckdb_table_matches_parquet_and_is_replaced_not_appended(store: Store) -> None:
    store.write_table("units_daily", units([("A", date(2026, 1, 1), 5, "f/1")]))
    store.write_table(
        "units_daily", units([("B", date(2026, 1, 2), 7, "f/2"), ("C", date(2026, 1, 3), 9, "f/3")])
    )
    assert list(store.query("select symbol from units_daily order by 1")["symbol"]) == ["B", "C"]
    assert list(store.read_table("units_daily")["symbol"]) == ["B", "C"]
    assert store.tables() == ["units_daily"]


def test_ensure_table_creates_empty_but_never_overwrites(store: Store) -> None:
    assert store.ensure_table("events") is True
    empty = store.read_table("events")
    assert len(empty) == 0
    assert list(empty.columns) == SCHEMAS["events"].column_names
    assert empty["verified"].dtype == "boolean"
    assert store.query("select count(*) as n from events")["n"].iloc[0] == 0

    row = {c: None for c in SCHEMAS["events"].column_names}
    row.update(event_id="e1", date=date(2026, 9, 7), verified=False, source_file="seed.csv")
    store.write_table("events", pd.DataFrame([row]))
    assert store.ensure_table("events") is False  # already there: left alone
    assert len(store.read_table("events")) == 1


# -- the checks ------------------------------------------------------------------


def test_duplicate_primary_key_raises_with_details_and_writes_nothing(store: Store) -> None:
    store.write_table("units_daily", units([("OLD", date(2026, 1, 1), 1, "f/old")]))
    dup = units(
        [
            ("MONQ50", date(2026, 11, 9), 1, "raw/a.gz"),
            ("MONQ50", date(2026, 11, 9), 2, "raw/b.gz"),
            ("OTHER", date(2026, 11, 9), 3, "raw/c.gz"),
        ]
    )
    with pytest.raises(DuplicateKeyError) as err:
        store.write_table("units_daily", dup)
    message = str(err.value)
    assert "units_daily" in message and "symbol=MONQ50" in message
    assert "2026-11-09" in message and "raw/a.gz" in message and "raw/b.gz" in message
    assert "OTHER" not in message
    assert list(store.read_table("units_daily")["symbol"]) == ["OLD"]  # old table untouched


def test_every_column_of_the_key_counts(store: Store) -> None:
    same_symbol_other_day = units(
        [("MONQ50", date(2026, 11, 9), 1, "a"), ("MONQ50", date(2026, 11, 10), 1, "b")]
    )
    assert store.write_table("units_daily", same_symbol_other_day) == 2


def test_missing_source_file_is_refused(store: Store) -> None:
    df = units([("MONQ50", date(2026, 11, 9), 1, None)])
    with pytest.raises(MissingValueError, match="source_file"):
        store.write_table("units_daily", df)


def test_missing_key_value_is_refused(store: Store) -> None:
    df = units([(None, date(2026, 11, 9), 1, "a")])
    with pytest.raises(MissingValueError, match="symbol"):
        store.write_table("units_daily", df)


def test_nav_cannot_be_empty(store: Store) -> None:
    df = pd.DataFrame(
        {
            "symbol": ["MON100"],
            "scheme_code": [114984],
            "nav_date": [date(2026, 9, 4)],
            "nav": [float("nan")],
            "nav_source": ["history"],
            "source_file": ["x"],
        }
    )
    with pytest.raises(MissingValueError, match="nav"):
        store.write_table("nav_daily", df)


def test_wrong_columns_are_refused(store: Store) -> None:
    good = units([("A", date(2026, 1, 1), 1, "f")])
    with pytest.raises(SchemaError, match="missing"):
        store.write_table("units_daily", good.drop(columns=["isin"]))
    with pytest.raises(SchemaError, match="unexpected"):
        store.write_table("units_daily", good.assign(surprise=1))


def test_bad_values_are_refused(store: Store) -> None:
    good = units([("A", date(2026, 1, 1), 1, "f")])
    with pytest.raises(SchemaError, match="non-whole"):
        store.write_table("units_daily", good.assign(units_outstanding=[1.5]))
    with pytest.raises(SchemaError, match="time of day"):
        store.write_table("units_daily", good.assign(date=[datetime(2026, 1, 1, 9, 0)]))  # noqa: DTZ001
    with pytest.raises(SchemaError, match="not an ISO date"):
        store.write_table("units_daily", good.assign(date=["01-Jan-2026"]))
    assert store.write_table("units_daily", good.assign(date=["2026-01-01"])) == 1  # ISO text ok


def test_naive_timestamps_are_refused(store: Store) -> None:
    df = pd.DataFrame({c: [None] for c in SCHEMAS["inav_snapshots"].column_names})
    df["symbol"], df["source"], df["source_file"] = "MONQ50", "amc_mirae", "x"
    df["ts"] = pd.to_datetime(["2026-11-11 09:15:00"])  # no time zone
    with pytest.raises(SchemaError, match="timezone-aware"):
        store.write_table("inav_snapshots", df)


def test_open_ended_placeholder_accepts_extra_columns(store: Store) -> None:
    df = pd.DataFrame(
        {"symbol": ["MONQ50"], "date": [date(2026, 11, 9)], "prem_raw": [1.008], "regime": ["x"]}
    )
    store.write_table("etf_day", df)
    back = store.read_table("etf_day")
    assert list(back.columns) == ["symbol", "date", "prem_raw", "regime"]
    assert back["prem_raw"].iloc[0] == pytest.approx(1.008)


def test_read_before_write_says_so(store: Store) -> None:
    with pytest.raises(FileNotFoundError, match="not been built"):
        store.read_table("units_daily")
    with pytest.raises(FileNotFoundError, match="no clean database"):
        store.query("select 1")


def test_default_store_follows_the_project_root(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ETFP_ROOT", str(tmp_path))
    assert Store().dir == tmp_path.resolve() / "data" / "clean"
    assert Store().db_path.name == "etf.duckdb"
