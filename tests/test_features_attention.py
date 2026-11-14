"""attention (R15): one Google Trends number per ETF and day, strict about missing terms."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from etfp.config import load_etfs, load_trends_terms
from etfp.features.attention import attention_series

D = date(2026, 11, 9)


def trends(*rows: tuple[str, date, float], geo: str = "India") -> pd.DataFrame:
    return pd.DataFrame(
        {
            "term": [r[0] for r in rows],
            "geo": [geo] * len(rows),
            "date": [r[1] for r in rows],
            "value": [r[2] for r in rows],
        }
    )


def test_two_terms_are_averaged_on_a_day_where_both_exist() -> None:
    t = trends(("A", D, 40.0), ("B", D, 60.0))
    assert attention_series(t, ["A", "B"], [D])[0] == pytest.approx(50.0)


def test_one_missing_term_makes_the_day_empty_not_a_lopsided_average() -> None:
    t = trends(
        ("A", D, 40.0), ("A", D + timedelta(days=1), 10.0), ("B", D + timedelta(days=1), 30.0)
    )
    out = attention_series(t, ["A", "B"], [D, D + timedelta(days=1)])
    assert np.isnan(out[0])  # B has no number on D
    assert out[1] == pytest.approx(20.0)


def test_no_terms_configured_or_no_trends_table_gives_all_nan() -> None:
    days = [D, D + timedelta(days=1)]
    assert np.isnan(attention_series(trends(("A", D, 5.0)), [], days)).all()
    assert np.isnan(attention_series(trends(("A", D, 5.0)), ["Z"], days)).all()  # term not pulled
    empty = pd.DataFrame({"term": [], "geo": [], "date": [], "value": []})
    assert np.isnan(attention_series(empty, ["A"], days)).all()
    assert attention_series(empty, ["A"], []).size == 0


def test_a_real_zero_stays_zero_and_only_the_configured_region_counts() -> None:
    t = pd.concat([trends(("A", D, 0.0)), trends(("A", D, 99.0), geo="Maharashtra")])
    assert attention_series(t, ["A"], [D])[0] == 0.0  # zero interest is a number, not missing


def test_day_not_in_the_trends_series_is_nan() -> None:
    assert np.isnan(attention_series(trends(("A", D, 5.0)), ["A"], [D - timedelta(days=1)])).all()


def test_repeated_term_in_the_list_counts_once() -> None:
    t = trends(("A", D, 40.0), ("B", D, 60.0))
    assert attention_series(t, ["A", "A", "B"], [D])[0] == pytest.approx(50.0)


def test_shipped_term_map_only_names_known_etfs_and_pairs_the_r15_terms() -> None:
    cfg = load_trends_terms()
    known = set(load_etfs().symbols())
    assert {t.etf_symbol for t in cfg.terms} <= known
    assert cfg.terms_for("MONQ50") == ["MONQ50", "Nasdaq Q50 ETF"]
    assert cfg.terms_for("MON100") == ["MON100", "Motilal Oswal Nasdaq 100 ETF"]
    assert cfg.terms_for("MAFANG") == ["MAFANG", "Mirae FANG ETF"]
    assert cfg.terms_for("MASPTOP50") == []
    assert cfg.geo == "India"
