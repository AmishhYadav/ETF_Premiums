"""units_daily (R1) and band_official (R2) from the NSE security-master samples."""

from __future__ import annotations

import gzip
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from etfp import calendars
from etfp.clean import CleanError, band_official, make_context, units_daily
from etfp.clean.etf_master import build as build_master
from test_clean_helpers import build_sample, make_raw_root, save_raw


@pytest.fixture(scope="module")
def tables(tmp_path_factory: pytest.TempPathFactory) -> dict[str, pd.DataFrame]:
    _, store = build_sample(tmp_path_factory)
    return {t: store.read_table(t) for t in ("units_daily", "band_official", "prices_daily")}


def pick(df: pd.DataFrame, symbol: str, key: str, day: date) -> pd.Series:
    sel = df[(df["symbol"] == symbol) & (df[key] == day)]
    assert len(sel) == 1, f"{symbol} {day}: {len(sel)} rows"
    return sel.iloc[0]


# -- units (R1) -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("symbol", "day", "expected"),
    [  # DECISIONS D7 table, read from the saved security-master files
        ("MONQ50", date(2024, 7, 1), 3_275_018),
        ("MONQ50", date(2025, 1, 2), 8_075_018),
        ("MONQ50", date(2026, 9, 4), 14_675_018),
        ("MONQ50", date(2026, 11, 9), 14_675_018),
        ("MON100", date(2026, 11, 9), 504_134_740),
        ("MASPTOP50", date(2024, 7, 1), 172_474_040),
        ("NIFTYBEES", date(2026, 11, 9), 2_586_291_385),  # bigger than 2**31: must stay exact
    ],
)
def test_units_outstanding_matches_decisions_d7(
    tables: dict[str, pd.DataFrame], symbol: str, day: date, expected: int
) -> None:
    r = pick(tables["units_daily"], symbol, "date", day)
    assert r["units_outstanding"] == expected
    assert r["series"] == "EQ"


def test_units_rows_keep_lineage_and_isin(tables: dict[str, pd.DataFrame]) -> None:
    r = pick(tables["units_daily"], "MONQ50", "date", date(2026, 11, 9))
    assert r["source_file"] == "nse_security_master/2026-11-09/NSE_CM_security_09112026.csv.gz"
    assert r["isin"] == "INF247L01AU3"
    assert not tables["units_daily"].duplicated(["symbol", "date"]).any()


def test_units_change_between_files_is_creation_sized(tables: dict[str, pd.DataFrame]) -> None:
    u = tables["units_daily"]
    jump = (
        pick(u, "MONQ50", "date", date(2025, 1, 2))["units_outstanding"]
        - pick(u, "MONQ50", "date", date(2024, 7, 1))["units_outstanding"]
    )
    assert jump == 4_800_000 and jump % 600_000 == 0  # 8 creation units of 600,000 (R6)


# -- bands (R2) -----------------------------------------------------------------


def test_monq50_band_in_file_of_9_nov_applies_to_the_next_session(
    tables: dict[str, pd.DataFrame],
) -> None:
    r = pick(tables["band_official"], "MONQ50", "file_date", date(2026, 11, 9))
    assert (r["band_low_official"], r["band_high_official"]) == (206.71, 252.63)
    assert r["price_range_raw"] == "206.71-252.63"
    # 10 Nov 2026 is an NSE holiday (DECISIONS D11), so "next trading day" is 11 Nov.
    assert not calendars.is_nse_session(date(2026, 11, 10))
    assert r["session_date"] == date(2026, 11, 11)


def test_band_midpoint_is_the_previous_close(tables: dict[str, pd.DataFrame]) -> None:
    """Band = close x 0.90 .. close x 1.10 (DECISIONS D4): the midpoint is the 9 Nov close."""
    band = pick(tables["band_official"], "MONQ50", "file_date", date(2026, 11, 9))
    close = pick(tables["prices_daily"], "MONQ50", "date", date(2026, 11, 9))["close"]
    midpoint = (band["band_low_official"] + band["band_high_official"]) / 2
    assert close == 229.67
    assert midpoint == pytest.approx(close, abs=0.005)
    assert band["band_high_official"] / band["band_low_official"] == pytest.approx(
        1.1 / 0.9, abs=1e-3
    )


def test_friday_file_applies_to_monday(tables: dict[str, pd.DataFrame]) -> None:
    # 4 Sep 2026 is a Friday; the first prev_close-regime session is Monday 7 Sep (D5).
    r = pick(tables["band_official"], "MONQ50", "file_date", date(2026, 9, 4))
    assert (r["band_low_official"], r["band_high_official"]) == (127.74, 156.12)
    assert r["session_date"] == date(2026, 9, 7)


def test_old_regime_band_is_plus_minus_20_percent(tables: dict[str, pd.DataFrame]) -> None:
    r = pick(tables["band_official"], "MONQ50", "file_date", date(2024, 7, 1))
    assert r["band_high_official"] / r["band_low_official"] == pytest.approx(1.5, abs=0.01)
    assert r["session_date"] == date(2024, 7, 2)


def test_regular_band_not_the_block_deal_band(tables: dict[str, pd.DataFrame]) -> None:
    # The same file has a block-deal (BL) row for MONQ50 with the band 222.78-236.56.
    r = pick(tables["band_official"], "MONQ50", "file_date", date(2026, 11, 9))
    assert r["series"] == "EQ" and r["band_low_official"] != 222.78


def test_gold_etf_band_is_plus_minus_6_percent_after_the_change(
    tables: dict[str, pd.DataFrame],
) -> None:
    r = pick(tables["band_official"], "GOLDBEES", "file_date", date(2026, 9, 4))
    assert r["band_high_official"] / r["band_low_official"] == pytest.approx(1.06 / 0.94, abs=1e-3)


# -- odd files (synthetic) ------------------------------------------------------


HEADER = (
    "TckrSymb,SctySrs,FinInstrmNm,ISIN,ParVal,IssdCptl,PricRg,"
    "CallAuctnInd,ListgDt,DelFlg,SLBMElgblty"
)


def synthetic_secmaster(rows: list[str]) -> bytes:
    return gzip.compress(("\n".join([HEADER, *rows]) + "\n").encode())


def build_both(root: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    ctx = make_context(root)
    ctx.master = build_master(ctx)
    return units_daily.build(ctx), band_official.build(ctx)


def test_be_series_is_used_when_there_is_no_eq_row_and_missing_band_stays_empty(
    tmp_path: Path,
) -> None:
    root = make_raw_root(tmp_path, skip_sources=("nse_security_master",))
    data = synthetic_secmaster(
        [
            "MAFANG,BE,MIRAE FANG,INF769K01HF4,10,2.47303596E+08,200.00-250.00,1,1,N,",
            "MAFANG,BL,MIRAE FANG,INF769K01HF4,10,2.47303596E+08,100.00-300.00,1,1,N,",
            "NIFTYBEES,EQ,NIP NIFTY,INF204KB14I2,100,2.58629139E+09,No Band,1,1,N,",
        ]
    )
    save_raw(
        root, "nse_security_master", date(2026, 11, 12), "NSE_CM_security_12112026.csv.gz", data
    )
    units, bands = build_both(root)

    fang = bands[bands["symbol"] == "MAFANG"].iloc[0]
    assert fang["series"] == "BE" and (fang["band_low_official"], fang["band_high_official"]) == (
        200.0,
        250.0,
    )
    assert fang["session_date"] == date(2026, 11, 13)  # Thursday file -> Friday session

    nifty = bands[bands["symbol"] == "NIFTYBEES"].iloc[0]
    assert pd.isna(nifty["band_low_official"]) and pd.isna(nifty["band_high_official"])
    assert nifty["price_range_raw"] == "No Band"  # the absence is visible, not a zero

    # units are still read from the row even though it has no band: 2.58629139E+09 exactly
    assert units[units["symbol"] == "NIFTYBEES"]["units_outstanding"].iloc[0] == 2_586_291_390


def test_file_stored_under_the_wrong_date_is_refused(tmp_path: Path) -> None:
    root = make_raw_root(tmp_path, skip_sources=("nse_security_master",))
    save_raw(
        root,
        "nse_security_master",
        date(2026, 11, 13),  # folder says 13th, file name says 12th
        "NSE_CM_security_12112026.csv.gz",
        synthetic_secmaster(["MAFANG,EQ,M,INF769K01HF4,10,1,1.00-2.00,1,1,N,"]),
    )
    with pytest.raises(CleanError, match="named for 2026-11-12"):
        build_both(root)


def test_empty_security_master_gives_empty_tables(tmp_path: Path) -> None:
    units, bands = build_both(tmp_path)
    assert units.empty and list(units.columns) == units_daily.COLUMNS
    assert bands.empty and list(bands.columns) == band_official.COLUMNS
