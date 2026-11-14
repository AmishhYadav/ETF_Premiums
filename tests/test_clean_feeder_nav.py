"""feeder_nav_daily: feeder funds' NAVs picked out of the AMFI files (synthetic AMFI text)."""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from etfp.clean import CleanError, feeder_nav, make_context
from etfp.config import load_feeders
from test_clean_helpers import save_raw

HISTORY_HEADER = (
    "Scheme Code;NAV Name;Plan;Option;ISIN Div Payout/ISIN Growth;ISIN Div Reinvestment;"
    "Net Asset Value;Date"
)
NAVALL_HEADER = (
    "Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Plan;Option;"
    "Net Asset Value;Date"
)
MON100_REG = "Motilal Oswal Nasdaq 100 Fund of Fund- Regular Plan Growth"
MON100_DIR = "Motilal Oswal Nasdaq 100 Fund of Fund- Direct Plan Growth"
MAFANG_REG = "Mirae Asset NYSE FANG + ETF Fund of Fund - Regular Plan - Growth"


def history(*rows: tuple[int, str, str, str]) -> bytes:
    """(code, name, nav text, date text) rows in AMFI's history layout, with other funds around."""
    lines = [
        HISTORY_HEADER,
        "Open Ended Schemes ( Other Scheme - Index Funds )",
        "",
        "Some Mutual Fund",
        "100001;Some Unrelated Index Fund - Growth;;;INF000000001;;12.3456;12-Nov-2026",
    ]
    lines += [f"{c};{n};;;INF247L01700;;{v};{d}" for c, n, v, d in rows]
    lines += ["100002;Another Unrelated Fund;;;INF000000002;;99.0;12-Nov-2026"]
    return ("\n".join(lines) + "\n").encode()


def navall(*rows: tuple[int, str, str, str]) -> bytes:
    lines = [NAVALL_HEADER, "Open Ended Schemes(Other)", "", "Some Mutual Fund", ""]
    lines += [f"{c};INF247L01700;-;{n};Regular Plan;Growth;{v};{d}" for c, n, v, d in rows]
    return ("\n".join(lines) + "\n").encode()


def build(root: Path) -> pd.DataFrame:
    return feeder_nav.build(make_context(root))


def put(root: Path, source: str, day: date, name: str, content: bytes) -> None:
    save_raw(root, source, day, name, content)


def test_config_lists_the_four_feeders_both_plans_with_unique_codes() -> None:
    feeders = load_feeders().feeders
    assert len(feeders) == 8
    by_etf: dict[str, set[str]] = {}
    for f in feeders:
        by_etf.setdefault(f.etf_symbol, set()).add(f.plan)
    assert by_etf == {
        k: {"regular", "direct"} for k in ("MON100", "MAFANG", "MASPTOP50", "MAHKTECH")
    }
    assert len({f.amfi_scheme_code for f in feeders}) == 8


def test_picks_only_feeder_schemes_with_config_name_and_symbol(tmp_path: Path) -> None:
    put(
        tmp_path, "amfi_nav_history", date(2026, 11, 13), "navhistory_20261112_20261113.txt",
        history(
            (145551, MON100_REG, "62.4382", "12-Nov-2026"),
            (145551, MON100_REG, "61.8534", "13-Nov-2026"),
            (148929, MAFANG_REG, "50.253", "12-Nov-2026"),
        ),
    )  # fmt: skip
    df = build(tmp_path)
    assert list(df.columns) == feeder_nav.COLUMNS
    assert len(df) == 3  # the unrelated funds are not here
    row = df[(df["scheme_code"] == 145551) & (df["nav_date"] == date(2026, 11, 13))].iloc[0]
    assert row["nav"] == pytest.approx(61.8534)
    assert row["etf_symbol"] == "MON100" and row["fund_name"] == MON100_REG
    assert row["source_file"] == "amfi_nav_history/2026-11-13/navhistory_20261112_20261113.txt"
    assert set(df[df["scheme_code"] == 148929]["etf_symbol"]) == {"MAFANG"}


def test_history_beats_navall_for_the_same_day_and_a_revision_is_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    put(tmp_path, "amfi_navall", date(2026, 11, 13), "NAVAll.txt",
        navall((145551, MON100_REG, "61.0000", "13-Nov-2026")))  # fmt: skip
    put(tmp_path, "amfi_nav_history", date(2026, 11, 14), "navhistory_20261113_20261114.txt",
        history((145551, MON100_REG, "61.8534", "13-Nov-2026")))  # fmt: skip
    with caplog.at_level(logging.WARNING, logger="etfp.clean"):
        df = build(tmp_path)
    assert len(df) == 1 and df.iloc[0]["nav"] == pytest.approx(61.8534)
    assert "replaces 61.0" in caplog.text and "NAVAll.txt" in caplog.text


def test_navall_alone_gives_its_one_day(tmp_path: Path) -> None:
    put(tmp_path, "amfi_navall", date(2026, 11, 13), "NAVAll.txt",
        navall((145552, MON100_DIR, "63.7597", "13-Nov-2026")))  # fmt: skip
    df = build(tmp_path)
    assert df.iloc[0]["scheme_code"] == 145552 and df.iloc[0]["nav"] == pytest.approx(63.7597)


def test_na_nav_is_skipped_never_zero(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    put(tmp_path, "amfi_nav_history", date(2026, 11, 13), "navhistory_20261112_20261113.txt",
        history((145551, MON100_REG, "N.A.", "12-Nov-2026"),
                (145551, MON100_REG, "61.8534", "13-Nov-2026")))  # fmt: skip
    with caplog.at_level(logging.WARNING, logger="etfp.clean"):
        df = build(tmp_path)
    assert list(df["nav_date"]) == [date(2026, 11, 13)]
    assert "not numbers" in caplog.text


def test_file_without_any_feeder_is_skipped_quietly(tmp_path: Path) -> None:
    # An old AMFI file from before the feeders existed: nothing to take, no error.
    put(tmp_path, "amfi_nav_history", date(2021, 1, 8), "navhistory_20210101_20210108.txt",
        history())  # fmt: skip
    assert build(tmp_path).empty


def test_changed_scheme_name_is_flagged(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    put(tmp_path, "amfi_nav_history", date(2026, 11, 13), "navhistory_20261112_20261113.txt",
        history((145551, "Some Other Fund Entirely", "62.4382", "12-Nov-2026")))  # fmt: skip
    with caplog.at_level(logging.WARNING, logger="etfp.clean"):
        df = build(tmp_path)
    assert len(df) == 1 and "check the scheme code" in caplog.text


def test_no_amfi_files_gives_an_empty_table(tmp_path: Path) -> None:
    df = build(tmp_path)
    assert df.empty and list(df.columns) == feeder_nav.COLUMNS


def test_a_feeder_pointing_at_an_unknown_etf_stops_the_build(tmp_path: Path) -> None:
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "feeders.yaml").write_text(
        "feeders:\n  - {name: X, amfi_scheme_code: 1, etf_symbol: NOSUCH, plan: regular}\n"
    )
    with pytest.raises(ValueError, match="NOSUCH"):
        build(tmp_path)


def test_broken_feeder_line_names_the_file(tmp_path: Path) -> None:
    bad = (HISTORY_HEADER + "\n145551;too;few;fields\n").encode()
    put(tmp_path, "amfi_nav_history", date(2026, 11, 13), "navhistory_20261112_20261113.txt", bad)
    with pytest.raises(CleanError, match=r"navhistory_20261112_20261113\.txt"):
        build(tmp_path)


def test_the_table_is_registered_and_exportable() -> None:
    from etfp.export import COPIED_TABLES
    from etfp.store import SCHEMAS

    assert SCHEMAS[feeder_nav.TABLE].primary_key == ("scheme_code", "nav_date")
    assert "feeder_nav_daily" in COPIED_TABLES
