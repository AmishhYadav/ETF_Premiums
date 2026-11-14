"""etf_day.attention end to end: Trends files in data/raw/trends -> trends_daily -> etf_day."""

from __future__ import annotations

import math
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from etfp.build import build_all
from etfp.export import ETF_DAY_COLUMNS, etf_day_schema_registered
from etfp.store import Store
from test_clean_helpers import make_raw_root

D9 = date(2026, 11, 9)  # a price day in the sample fixtures (11 ETFs have a row)


def trends_csv(term: str, rows: list[tuple[str, int]]) -> str:
    lines = ["Category: All categories", "", f"Day,{term}: (India)"]
    return "\n".join(lines + [f"{d},{v}" for d, v in rows]) + "\n"


def put(root: Path, name: str, text: str) -> None:
    folder = root / "data" / "raw" / "trends" / "2026-11-13"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(text, encoding="utf-8")


def etf_day(root: Path) -> pd.DataFrame:
    build_all(root)
    with etf_day_schema_registered():
        return Store(root / "data" / "clean").read_table("etf_day")


def attention(df: pd.DataFrame, symbol: str) -> float:
    sel = df[(df["symbol"] == symbol) & (df["date"] == D9)]
    assert len(sel) == 1
    return float(sel.iloc[0]["attention"])


def test_attention_is_a_documented_column() -> None:
    assert "attention" in ETF_DAY_COLUMNS
    assert ETF_DAY_COLUMNS["attention"].dtype == "float"


def test_no_trends_files_means_empty_attention_everywhere(tmp_path: Path) -> None:
    df = etf_day(make_raw_root(tmp_path))
    assert df["attention"].isna().all()


def test_attention_is_the_mean_of_the_etfs_terms_and_empty_for_etfs_without_terms(
    tmp_path: Path,
) -> None:
    root = make_raw_root(tmp_path)
    # Chained series are rescaled to a maximum of 100: A = 50, 100, 25 and B = 40, 80, 100.
    put(
        root,
        "a.csv",
        trends_csv("MONQ50", [("2026-11-08", 50), ("2026-11-09", 100), ("2026-11-10", 25)]),
    )
    put(
        root,
        "b.csv",
        trends_csv("Nasdaq Q50 ETF", [("2026-11-08", 40), ("2026-11-09", 80), ("2026-11-10", 100)]),
    )
    df = etf_day(root)
    assert attention(df, "MONQ50") == pytest.approx(90.0)  # (100 + 80) / 2
    assert math.isnan(attention(df, "MON100"))  # its terms were never pulled
    assert math.isnan(attention(df, "MASPTOP50"))  # no term configured


def test_one_term_missing_on_the_day_leaves_the_day_empty(tmp_path: Path) -> None:
    root = make_raw_root(tmp_path)
    put(root, "a.csv", trends_csv("MONQ50", [("2026-11-08", 50), ("2026-11-09", 100)]))
    put(root, "b.csv", trends_csv("Nasdaq Q50 ETF", [("2026-11-10", 100), ("2026-11-11", 40)]))
    df = etf_day(root)
    assert math.isnan(attention(df, "MONQ50"))  # B has nothing on 9 Nov
