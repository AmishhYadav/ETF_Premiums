"""fof_monthly (R4, R14): the CSV loader and the daily carry-forward of feeder holdings."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from etfp.clean import CLEANERS, CleanContext, CleanError
from etfp.clean.manual_fof import COLUMNS, FEEDER_UNITS_COLUMNS, build, feeder_units_daily
from etfp.config import load_etfs
from etfp.store import Store

HEADER = (
    "amfi_scheme_code,month_end,fund_name,etf_symbol,aum,etf_units_held,"
    "fof_net_flow,subscription_status,notice_date"
)


def make_ctx(root: Path, csv_text: str | None) -> CleanContext:
    (root / "data" / "raw").mkdir(parents=True, exist_ok=True)
    if csv_text is not None:
        (root / "data" / "manual").mkdir(exist_ok=True)
        (root / "data" / "manual" / "fof_monthly.csv").write_text(csv_text, encoding="utf-8")
    return CleanContext(raw_root=root / "data" / "raw", universe=load_etfs())


def rows(root: Path, *lines: str) -> pd.DataFrame:
    return build(make_ctx(root, HEADER + "\n" + "\n".join(lines) + "\n"))


GOOD = '111,2026-10-31,Mirae FANG+ FoF,MAFANG,30850000000,"1,200,000",-5000000,sip_shut,2024-01-05'


def test_a_good_row_is_parsed(tmp_path: Path) -> None:
    out = rows(tmp_path, GOOD)
    r = out.iloc[0]
    assert list(out.columns) == COLUMNS
    assert r["amfi_scheme_code"] == 111 and r["etf_symbol"] == "MAFANG"
    assert r["month_end"] == date(2026, 10, 31) and r["notice_date"] == date(2024, 1, 5)
    assert r["etf_units_held"] == 1_200_000  # thousands commas accepted when quoted
    assert r["fof_net_flow"] == -5_000_000  # outflows are negative
    assert r["subscription_status"] == "sip_shut"
    assert r["source_file"] == "data/manual/fof_monthly.csv:2"


@pytest.mark.parametrize(
    ("line", "needle"),
    [
        ("111,2026-10-30,F,MAFANG,,,,,", "not the last day of a month"),
        ("111,2026-10-31,F,MAFANG;MON100,,,,,", "exactly one ETF symbol"),
        ("111,2026-10-31,F,NOPE,,,,,", "unknown ETF symbol 'NOPE'"),
        ("111,2026-10-31,F,MAFANG,,,,paused,", "must be one of open, lump_sum_shut"),
        ("111,2026-10-31,F,MAFANG,,-5,,,", "must be at least 0"),
        ("111,2026-10-31,F,MAFANG,abc,,,,", "not a number"),
        ("x,2026-10-31,F,MAFANG,,,,,", "not a number"),
        ("111,2026-10-31,,MAFANG,,,,,", "'fund_name' is required"),
        ("111.5,2026-10-31,F,MAFANG,,,,,", "whole number"),
    ],
)
def test_bad_rows_name_file_and_line(tmp_path: Path, line: str, needle: str) -> None:
    with pytest.raises(CleanError) as err:
        rows(tmp_path, line)
    assert needle in str(err.value)
    assert "fof_monthly: data/manual/fof_monthly.csv line 2:" in str(err.value)


def test_duplicate_feeder_month_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(CleanError, match=r"feeder 111 has two rows for 2026-10-31"):
        rows(tmp_path, "111,2026-10-31,F,MAFANG,,,,,", "111,2026-10-31,F,MAFANG,,,,,")


def test_a_feeder_that_switches_etf_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(CleanError, match=r"feeder 111 holds MAFANG .* but MON100"):
        rows(tmp_path, "111,2026-09-30,F,MAFANG,,,,,", "111,2026-10-31,F,MON100,,,,,")


def test_missing_and_header_only_files_give_empty_tables(tmp_path: Path) -> None:
    missing = build(make_ctx(tmp_path / "a", None))
    header_only = build(make_ctx(tmp_path / "b", HEADER + "\n"))
    for out in (missing, header_only):
        assert out.empty and list(out.columns) == COLUMNS
    Store(tmp_path / "clean").write_table("fof_monthly", missing)  # the schema accepts it


def test_loaded_rows_round_trip_through_the_store(tmp_path: Path) -> None:
    store = Store(tmp_path / "clean")
    store.write_table("fof_monthly", rows(tmp_path, GOOD, "222,2026-10-31,G,MON100,,,,open,"))
    back = store.read_table("fof_monthly")
    assert sorted(back["amfi_scheme_code"]) == [111, 222]
    assert back["notice_date"].isna().sum() == 1


def test_registered_after_etf_master() -> None:
    order = [t for t, _ in CLEANERS]
    assert order.index("fof_monthly") > order.index("etf_master")


# -- feeder_units_daily: hand-computed ---------------------------------------------------


def feeders() -> pd.DataFrame:
    """Two feeders of MAFANG (111, 222) and one of MON100 (333)."""
    return pd.DataFrame(
        {
            "amfi_scheme_code": [111, 111, 111, 222, 222, 333],
            "month_end": [
                date(2026, 1, 31),
                date(2026, 2, 28),
                date(2026, 3, 31),
                date(2026, 2, 28),
                date(2026, 3, 31),
                date(2026, 1, 31),
            ],
            "etf_symbol": ["MAFANG"] * 5 + ["MON100"],
            "etf_units_held": [1000.0, 1500.0, np.nan, 400.0, 600.0, 50.0],
        }
    )


DAYS = [
    date(2026, 1, 30),
    date(2026, 1, 31),
    date(2026, 2, 15),
    date(2026, 2, 28),
    date(2026, 3, 31),
    date(2026, 4, 5),
]


def test_feeder_units_are_carried_forward_and_summed() -> None:
    out = feeder_units_daily(feeders(), DAYS)
    assert list(out.columns) == FEEDER_UNITS_COLUMNS
    fang = out[out["symbol"] == "MAFANG"].reset_index(drop=True)
    nan = np.nan
    # 30 Jan: nobody has reported -> unknown (NaN), not zero.
    # 31 Jan: feeder 111 only (1000). 15 Feb: still 1000 (Feb figure not out yet).
    # 28 Feb: 1500 + 400. 31 Mar: 111 blank -> its 1500 carries on, + 600 = 2100. 5 Apr: same.
    expected = [nan, 1000.0, 1000.0, 1900.0, 2100.0, 2100.0]
    np.testing.assert_array_equal(fang["feeder_units"].to_numpy(dtype=float), expected)
    assert fang["feeders_counted"].tolist() == [0, 1, 1, 2, 2, 2]
    assert fang["oldest_month_end"].tolist() == [
        None,
        date(2026, 1, 31),
        date(2026, 1, 31),
        date(2026, 2, 28),
        date(2026, 2, 28),  # feeder 111's blank March was skipped, so Feb 28 is still its latest
        date(2026, 2, 28),
    ]
    mon = out[out["symbol"] == "MON100"]["feeder_units"].to_numpy(dtype=float)
    np.testing.assert_array_equal(mon, [nan, 50, 50, 50, 50, 50])


def test_lag_days_stops_look_ahead() -> None:
    # With a 10-day publication lag, 28 Feb sees only what was out by 18 Feb: 1000.
    out = feeder_units_daily(feeders(), DAYS, lag_days=10)
    fang = out[out["symbol"] == "MAFANG"]["feeder_units"].to_numpy(dtype=float)
    # 30 Jan, 31 Jan: nothing public. 15 Feb: Jan 31 (1000). 28 Feb: as_of 18 Feb -> 1000.
    # 31 Mar: as_of 21 Mar -> 1500 + 400. 5 Apr: as_of 26 Mar -> same.
    np.testing.assert_array_equal(fang, [np.nan, np.nan, 1000.0, 1000.0, 1900.0, 1900.0])


def test_dates_need_not_be_sorted_or_unique() -> None:
    a = feeder_units_daily(feeders(), DAYS)
    b = feeder_units_daily(feeders(), [*DAYS[::-1], DAYS[0]])
    pd.testing.assert_frame_equal(a, b)


def test_empty_inputs_and_missing_columns() -> None:
    empty = feeder_units_daily(feeders().iloc[0:0], DAYS)
    assert empty.empty and list(empty.columns) == FEEDER_UNITS_COLUMNS
    assert feeder_units_daily(feeders(), []).empty
    with pytest.raises(ValueError, match="missing column"):
        feeder_units_daily(feeders().drop(columns=["etf_units_held"]), DAYS)
    with pytest.raises(ValueError, match="lag_days"):
        feeder_units_daily(feeders(), DAYS, lag_days=-1)


def test_a_feeder_with_no_reported_units_gives_no_rows() -> None:
    df = feeders().assign(etf_units_held=np.nan)
    assert feeder_units_daily(df, DAYS).empty
