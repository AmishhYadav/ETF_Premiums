"""etf_master and the whole build_clean() run on the sample raw store."""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from etfp import paths
from etfp.clean import CLEANERS, CleanError, build_clean, make_context
from etfp.clean.etf_master import build as build_master
from etfp.config import load_etfs
from etfp.store import SCHEMAS, Store
from test_clean_helpers import build_sample, fixture_bytes, make_raw_root, save_raw

BUILT = [t for t, _ in CLEANERS]
# Tables read from hand-typed files (data/manual) or hand-downloaded Trends files:
# they are legitimately empty when nobody has entered anything.
MANUAL = {"events", "fof_monthly", "units_monthly", "trends_daily"}


@pytest.fixture(scope="module")
def sample(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Store]:
    return build_sample(tmp_path_factory)


# -- etf_master -----------------------------------------------------------------


def test_master_merges_config_and_nse_list(sample: tuple[Path, Store]) -> None:
    master = sample[1].read_table("etf_master").set_index("symbol")
    q = master.loc["MONQ50"]
    assert q["name"] == "Motilal Oswal Nasdaq Q 50 ETF" and q["amc"] == "Motilal Oswal"
    assert q["amfi_scheme_code"] == 149438 and q["group_label"] == "blocked"
    assert bool(q["tracked"]) is True and q["underlying_market"] == "US"
    assert q["listing_date"] == date(2021, 12, 29)  # from NSE's list
    assert q["source_file"] == "nse_etf_seclist/2026-11-10/eq_etfseclist.csv"
    assert q["config_file"] == "config/etfs.yaml"


def test_master_keeps_listed_etfs_that_are_not_in_config(sample: tuple[Path, Store]) -> None:
    master = sample[1].read_table("etf_master").set_index("symbol")
    other = master.loc["CONS"]
    assert pd.isna(other["amc"]) and pd.isna(other["config_file"])
    assert bool(other["tracked"]) is False  # explicit False, not "unknown"
    assert other["nse_underlying_type"] == "EQUITY"
    assert master.index.is_unique
    assert set(load_etfs().symbols()) <= set(master.index)


def test_config_isin_disagreement_is_logged_and_config_wins(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    root = make_raw_root(tmp_path)
    text = (paths.config_dir() / "etfs.yaml").read_text()
    assert "INF247L01AU3" in text
    (root / "config").mkdir()
    (root / "config" / "etfs.yaml").write_text(text.replace("INF247L01AU3", "INF247L01AU9"))
    ctx = make_context(root)  # prefers <root>/config/etfs.yaml
    with caplog.at_level(logging.WARNING, logger="etfp.clean"):
        master = build_master(ctx).set_index("symbol")
    assert master.loc["MONQ50", "isin"] == "INF247L01AU9"
    assert "INF247L01AU9" in caplog.text and "INF247L01AU3" in caplog.text


def test_master_without_the_nse_list_falls_back_to_config(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    root = make_raw_root(tmp_path, skip_sources=("nse_etf_seclist",))
    with caplog.at_level(logging.WARNING, logger="etfp.clean"):
        master = build_master(make_context(root))
    assert set(master["symbol"]) == set(load_etfs().symbols())
    assert (master["source_file"] == "config/etfs.yaml").all()
    assert master["listing_date"].isna().all()
    assert "no NSE ETF list" in caplog.text


# -- build_clean ------------------------------------------------------------------


def test_build_returns_row_counts_and_writes_every_table(sample: tuple[Path, Store]) -> None:
    root, store = sample
    for table in SCHEMAS:  # built ones, plus the empty placeholders
        assert store.has_table(table), table
    assert (root / "data" / "clean" / "etf.duckdb").is_file()
    for table in ("underlying_daily", "fx_daily", *sorted(MANUAL)):
        assert len(store.read_table(table)) == 0


def test_counts_equal_table_sizes(tmp_path: Path) -> None:
    root = make_raw_root(tmp_path)
    counts = build_clean(root)
    store = Store(root / "data" / "clean")
    assert list(counts) == BUILT
    assert all(counts[t] > 0 for t in BUILT if t not in MANUAL)
    for table, n in counts.items():
        assert len(store.read_table(table)) == n
        assert store.query(f"select count(*) as n from {table}")["n"].iloc[0] == n


def test_rebuild_gives_identical_tables(tmp_path: Path) -> None:
    root = make_raw_root(tmp_path)
    build_clean(root)
    store = Store(root / "data" / "clean")
    first = {t: store.read_table(t) for t in BUILT}
    build_clean(root)
    for t in BUILT:
        pd.testing.assert_frame_equal(first[t], store.read_table(t))


def test_every_row_traces_to_a_real_raw_file(sample: tuple[Path, Store]) -> None:
    root, store = sample
    raw = root / "data" / "raw"
    for table in BUILT:
        df = store.read_table(table)
        assert df["source_file"].notna().all(), table
        for rel in set(df["source_file"]) - {"config/etfs.yaml"}:
            assert (raw / rel).is_file(), f"{table}: {rel} is not in the raw store"


def test_sql_join_of_price_and_official_band(sample: tuple[Path, Store]) -> None:
    """The same question a dashboard asks: bands next to the close they were built from."""
    out = sample[1].query(
        """
        select p.close, b.band_low_official, b.band_high_official, b.session_date
        from prices_daily p
        join band_official b on b.symbol = p.symbol and b.file_date = p.date
        where p.symbol = 'MONQ50' and p.date = DATE '2026-11-09'
        """
    )
    assert len(out) == 1
    r = out.iloc[0]
    assert (r["band_low_official"], r["band_high_official"]) == (206.71, 252.63)
    assert r["band_low_official"] == pytest.approx(r["close"] * 0.9, abs=0.01)
    assert r["band_high_official"] == pytest.approx(r["close"] * 1.1, abs=0.01)


def test_build_does_not_overwrite_tables_owned_by_other_importers(tmp_path: Path) -> None:
    root = make_raw_root(tmp_path)
    store = Store(root / "data" / "clean")
    row = {c: None for c in SCHEMAS["fx_daily"].column_names}
    row.update(pair="USDINR", date=date(2026, 9, 7), source_file="seed.csv")
    store.write_table("fx_daily", pd.DataFrame([row]))
    build_clean(root)
    assert list(store.read_table("fx_daily")["pair"]) == ["USDINR"]


def test_empty_raw_store_still_builds(tmp_path: Path) -> None:
    counts = build_clean(tmp_path)
    assert counts["etf_master"] == len(load_etfs().etfs)
    assert all(counts[t] == 0 for t in BUILT if t != "etf_master")
    assert set(Store(tmp_path / "data" / "clean").tables()) == set(SCHEMAS)


def test_raw_files_are_never_touched(tmp_path: Path) -> None:
    root = make_raw_root(tmp_path)
    raw = root / "data" / "raw"
    before = {p: p.read_bytes() for p in raw.rglob("*") if p.is_file()}
    build_clean(root)
    after = {p: p.read_bytes() for p in raw.rglob("*") if p.is_file()}
    assert before == after


def test_a_changed_source_format_stops_the_build_and_names_the_file(tmp_path: Path) -> None:
    root = make_raw_root(tmp_path, skip_sources=("nse_security_master",))
    bad = fixture_bytes("nse_secmaster/not_gzip_404.html")
    save_raw(root, "nse_security_master", date(2026, 11, 9), "NSE_CM_security_09112026.csv.gz", bad)
    with pytest.raises(CleanError) as err:
        build_clean(root)
    message = str(err.value)
    assert "units_daily" in message
    assert "nse_security_master/2026-11-09/NSE_CM_security_09112026.csv.gz" in message
    assert "not a gzip file" in message
    store = Store(root / "data" / "clean")
    assert store.has_table("prices_daily")  # built before the failure: kept
    assert not store.has_table("units_daily")  # the broken one is not half-written
