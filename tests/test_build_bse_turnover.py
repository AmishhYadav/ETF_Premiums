"""R9 in etf_day: BSE columns, turnover = (NSE + BSE units) / units, and the NSE-only flag."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from etfp.build import _bse_block, build_all
from etfp.export import etf_day_schema_registered
from etfp.store import Store
from test_clean_helpers import make_raw_root, save_raw

D9 = date(2026, 11, 9)
D21 = date(2021, 1, 4)

HEADER = (
    "TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,XpryDt,"
    "FininstrmActlXpryDt,StrkPric,OptnTp,FinInstrmNm,OpnPric,HghPric,LwPric,ClsPric,LastPric,"
    "PrvsClsgPric,UndrlygPric,SttlmPric,OpnIntrst,ChngInOpnIntrst,TtlTradgVol,TtlTrfVal,"
    "TtlNbOfTxsExctd,SsnId,NewBrdLotQty,Rmks,Rsvd1,Rsvd2,Rsvd3,Rsvd4\n"
)
# Made-up BSE figures for MONQ50 on 9 Nov 2026 (NSE that day: 274,573 units; units 14,675,018).
MONQ50_BSE_ROW = (
    "2026-11-09,2026-11-09,CM,BSE,STK,543437,INF247L01AU3,MONQ50,B,,,,,Motilal Oswal Mutual Fund,"
    "237.00,238.00,229.00,230.10,230.50,231.90,,230.00,,,25000,5750000.00,310,F1,1,,,,,\n"
)


def _etf_day(root: Path) -> pd.DataFrame:
    build_all(root)
    with etf_day_schema_registered():
        return Store(root / "data" / "clean").read_table("etf_day")


def _row(df: pd.DataFrame, symbol: str, day: date) -> pd.Series:
    sel = df[(df["symbol"] == symbol) & (df["date"] == day)]
    assert len(sel) == 1
    return sel.iloc[0]


@pytest.fixture(scope="module")
def with_bse(tmp_path_factory: pytest.TempPathFactory) -> pd.DataFrame:
    root = make_raw_root(tmp_path_factory.mktemp("bse"))
    save_raw(
        root,
        "bse_bhavcopy",
        D9,
        "BhavCopy_BSE_CM_0_0_0_20261109_F_0000.CSV",
        (HEADER + MONQ50_BSE_ROW).encode(),
    )
    return _etf_day(root)


@pytest.fixture(scope="module")
def without_bse(tmp_path_factory: pytest.TempPathFactory) -> pd.DataFrame:
    root = make_raw_root(
        tmp_path_factory.mktemp("nobse"), skip_sources=("bse_bhavcopy", "nse_slb_eligible")
    )
    return _etf_day(root)


def test_turnover_adds_bse_units_to_nse_units(with_bse: pd.DataFrame) -> None:
    r = _row(with_bse, "MONQ50", D9)
    assert r["bse_volume"] == 25_000 and r["bse_trades"] == 310
    assert r["bse_close"] == 230.10 and r["bse_value"] == 5_750_000.00
    # (274,573 NSE + 25,000 BSE) / 14,675,018 units
    assert r["turnover"] == pytest.approx(299_573 / 14_675_018)
    assert r["turnover_nse"] == pytest.approx(274_573 / 14_675_018)  # unchanged definition
    assert not bool(r["turnover_is_nse_only"])
    assert r["turnover"] > r["turnover_nse"]


def test_a_fund_with_no_bse_row_that_day_is_flagged_nse_only(with_bse: pd.DataFrame) -> None:
    r = _row(with_bse, "MASPTOP50", D9)
    assert pd.isna(r["bse_volume"]) and pd.isna(r["bse_close"]) and pd.isna(r["bse_trades"])
    assert bool(r["turnover_is_nse_only"])
    assert r["turnover"] == pytest.approx(r["turnover_nse"])  # BSE counted as zero, but flagged


def test_bse_volume_shows_even_when_units_are_unknown(with_bse: pd.DataFrame) -> None:
    # 2021 has no units file yet: turnover cannot be computed, but the BSE figures are kept.
    r = _row(with_bse, "NIFTYBEES", D21)
    assert pd.isna(r["turnover"]) and pd.isna(r["turnover_nse"])
    assert r["bse_volume"] == 89_596 and r["bse_close"] == 150.71 and r["bse_trades"] == 846
    assert not bool(r["turnover_is_nse_only"])


def test_without_any_bse_file_everything_is_nse_only(without_bse: pd.DataFrame) -> None:
    assert without_bse["bse_volume"].isna().all() and without_bse["bse_close"].isna().all()
    assert without_bse["turnover_is_nse_only"].astype(bool).all()
    pd.testing.assert_series_equal(
        without_bse["turnover"], without_bse["turnover_nse"], check_names=False
    )


def test_bse_columns_have_their_documented_types(with_bse: pd.DataFrame) -> None:
    assert (
        str(with_bse["bse_volume"].dtype) == "Int64"
        and str(with_bse["bse_trades"].dtype) == "Int64"
    )
    assert with_bse["bse_close"].dtype == "float64" and with_bse["bse_value"].dtype == "float64"


# -- _bse_block on its own: split adjustment and alignment -------------------------------


def test_bse_close_gets_the_same_split_factor_as_the_nse_price() -> None:
    days = [date(2021, 6, 16), date(2021, 6, 17), date(2021, 6, 18)]
    bse = pd.DataFrame(
        {
            "symbol": ["MON100", "MON100"],
            "date": [date(2021, 6, 16), date(2021, 6, 18)],
            "close": [920.0, 92.5],
            "volume": pd.array([1_000, 2_000], dtype="Int64"),
            "value": [920_000.0, 185_000.0],
            "trades": pd.array([10, 20], dtype="Int64"),
        }
    )
    factor = pd.Series([0.1, 0.1, 1.0])  # a 10:1 split on 17 Jun: old prices shrink by 10
    out = _bse_block(days, bse, factor)
    assert out["bse_close"].iloc[0] == pytest.approx(92.0)  # 920 x 0.1 on today's unit scale
    assert out["bse_close"].iloc[2] == pytest.approx(92.5)
    assert pd.isna(out["bse_close"].iloc[1])  # 17 Jun: no BSE row, stays empty
    assert out["bse_volume"].tolist()[0] == 1_000 and out["bse_volume"].isna().tolist() == [
        False,
        True,
        False,
    ]
    assert out["bse_volume"].iloc[2] == 2_000  # units are NOT split-adjusted, like NSE volume


def test_bse_block_with_no_bse_rows_is_all_empty() -> None:
    days = [D9]
    bse = pd.DataFrame({c: [] for c in ("symbol", "date", "close", "volume", "value", "trades")})
    out = _bse_block(days, bse, pd.Series([1.0]))
    assert out.shape == (1, 4) and out.isna().all().all()
