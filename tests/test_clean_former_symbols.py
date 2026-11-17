"""Symbol changes: rows under a fund's old NSE ticker are relabelled to its current one.

Real case used in the tests: SBI's Nifty 50 ETF traded as SETFNIF50 until 29 Oct 2026
and as SBINIFTY50 from 30 Oct 2026 (same ISIN INF200KA1FS1). Prices below are the real
closes from NSE's full bhavcopy (247.34 on 29 Oct; 248.20 on 30 Oct, PREV_CLOSE 247.34).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from etfp.clean import CleanError, make_context, prices_daily
from etfp.clean.common import map_former_symbols
from etfp.clean.etf_master import build as build_master
from etfp.config import load_etfs
from test_clean_helpers import save_raw

OCT29, OCT30 = date(2026, 10, 29), date(2026, 10, 30)
HEADER = (
    "SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, LAST_PRICE, "
    "CLOSE_PRICE, AVG_PRICE, TTL_TRD_QNTY, TURNOVER_LACS, NO_OF_TRADES, DELIV_QTY, DELIV_PER\n"
)


def _frame(rows: list[tuple]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["symbol", "series", "date", "isin", "close"])


def test_old_symbol_on_or_before_until_is_relabelled():
    df = _frame(
        [
            ("SETFNIF50", "EQ", OCT29, "INF200KA1FS1", 247.34),
            ("NIFTYBEES", "EQ", OCT29, "INF204KB14I2", 270.0),
        ]
    )
    out = map_former_symbols(df, load_etfs(), date_col="date", table="t", source_file="f")
    assert out["symbol"].tolist() == ["SBINIFTY50", "NIFTYBEES"]
    assert out["close"].tolist() == [247.34, 270.0]  # values untouched
    assert df["symbol"].iloc[0] == "SETFNIF50"  # input not modified


def test_old_symbol_after_until_is_left_alone():
    # If NSE ever reused the old ticker for another fund, its rows must not be taken.
    df = _frame([("SETFNIF50", "EQ", OCT30, None, 1.0)])
    out = map_former_symbols(df, load_etfs(), date_col="date", table="t", source_file="f")
    assert out["symbol"].tolist() == ["SETFNIF50"]


def test_isin_mismatch_stops_the_build():
    df = _frame([("SETFNIF50", "EQ", OCT29, "INF000000001", 247.34)])
    with pytest.raises(CleanError, match=r"SETFNIF50 has ISIN INF000000001.*SBINIFTY50"):
        map_former_symbols(df, load_etfs(), date_col="date", table="t", source_file="f")


def test_old_and_new_symbol_on_one_day_stops_the_build():
    df = _frame(
        [
            ("SETFNIF50", "EQ", OCT29, None, 247.34),
            ("SBINIFTY50", "EQ", OCT29, None, 247.34),
        ]
    )
    with pytest.raises(CleanError, match="both its old and new NSE symbol"):
        map_former_symbols(df, load_etfs(), date_col="date", table="t", source_file="f")


def test_prices_daily_joins_history_across_the_symbol_change(tmp_path: Path):
    """Two real bhavcopy rows, one per ticker, become one continuous SBINIFTY50 series."""
    day29 = HEADER + (
        "SETFNIF50, EQ, 29-Oct-2026, 245.22, 246.80, 247.67, 244.27, 247.13, 247.34, "
        "246.46, 1573963, 3879.20, 10764, 900000, 57.18\n"
    )
    day30 = HEADER + (
        "SBINIFTY50, EQ, 30-Oct-2026, 247.34, 247.50, 249.00, 246.10, 248.30, 248.20, "
        "247.90, 358500, 888.73, 4100, 200000, 55.79\n"
    )
    save_raw(tmp_path, "nse_bhavcopy_full", OCT29, "sec_bhavdata_full_29102026.csv",
             day29.encode())  # fmt: skip
    save_raw(tmp_path, "nse_bhavcopy_full", OCT30, "sec_bhavdata_full_30102026.csv",
             day30.encode())  # fmt: skip
    ctx = make_context(tmp_path)
    ctx.master = build_master(ctx)
    prices = prices_daily.build(ctx)
    sbi = prices[prices["symbol"] == "SBINIFTY50"].sort_values("date")
    assert sbi["date"].tolist() == [OCT29, OCT30]
    assert sbi["close"].tolist() == [247.34, 248.20]
    # Lineage still names the file each row came from (the old ticker is in that file).
    assert sbi["source_file"].iloc[0] == (
        "nse_bhavcopy_full/2026-10-29/sec_bhavdata_full_29102026.csv"
    )
    assert "SETFNIF50" not in set(prices["symbol"])
