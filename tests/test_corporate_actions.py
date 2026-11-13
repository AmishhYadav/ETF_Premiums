"""Tests for the corporate-actions loader and the split adjustment factors.

All expected numbers are worked out by hand in the comments.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from etfp.config import load_etfs
from etfp.corporate_actions import (
    CorporateAction,
    CorporateActions,
    adjust_values,
    adjustment_factor,
    load_corporate_actions,
)

D = date


def split(symbol="AAA", ex=None, ratio=10.0, nav_ex=None) -> CorporateAction:
    ex = ex or D(2022, 3, 1)
    return CorporateAction(
        symbol=symbol, ex_date=ex, type="split", ratio=ratio, source="test", verified=True,
        nav_ex_date=nav_ex,
    )  # fmt: skip


@pytest.fixture(scope="module")
def real() -> CorporateActions:
    return load_corporate_actions()


# -- the real config file ---------------------------------------------------------------


def test_real_file_has_the_mon100_split(real: CorporateActions):
    (action,) = real.for_symbol("MON100")
    assert action.type == "split"
    assert action.ratio == 10
    assert action.ex_date == D(2021, 6, 17)
    assert action.nav_effective_date == D(2021, 6, 21)
    assert action.source
    assert action.verified is True


def test_every_symbol_in_the_file_exists_in_the_etf_universe(real: CorporateActions):
    universe = set(load_etfs().symbols())
    assert {a.symbol for a in real.actions} <= universe


def test_mon100_price_is_adjusted_down_before_the_ex_date(real: CorporateActions):
    # Raw close 1015.62 on 2021-06-16; ten times smaller units after the split.
    assert adjustment_factor(real, "MON100", D(2021, 6, 16)) == pytest.approx(0.1)
    # Hand check: 1015.62 x 0.1 = 101.562, next-day close was 100.87 (-0.68%), plausible.
    assert 1015.62 * adjustment_factor(real, "MON100", D(2021, 6, 16)) == pytest.approx(101.562)
    # Ex-date itself and later: already new scale.
    assert adjustment_factor(real, "MON100", D(2021, 6, 17)) == 1.0
    assert adjustment_factor(real, "MON100", D(2026, 11, 9)) == 1.0


def test_mon100_nav_switches_scale_four_days_after_the_price(real: CorporateActions):
    # AMFI: 1001.7978 (17 Jun) and 1025.4998 (18 Jun) are still old-scale, 101.5224 (21 Jun)
    # is new-scale.
    assert adjustment_factor(real, "MON100", D(2021, 6, 17), "nav") == pytest.approx(0.1)
    assert adjustment_factor(real, "MON100", D(2021, 6, 18), "nav") == pytest.approx(0.1)
    assert adjustment_factor(real, "MON100", D(2021, 6, 21), "nav") == 1.0
    # Hand check: 1025.4998 x 0.1 = 102.54998, vs 101.5224 three days later (-1.0%), plausible.
    assert pytest.approx(102.54998) == 1025.4998 * 0.1


def test_premium_on_the_gap_days_needs_each_series_its_own_factor(real: CorporateActions):
    # 17 Jun 2021: raw price 100.87 (new scale), raw NAV 1001.7978 (old scale).
    # Raw premium = 100.87 / 1001.7978 - 1 = -89.9%: nonsense. Adjusted with each factor:
    price = 100.87 * adjustment_factor(real, "MON100", D(2021, 6, 17), "price")
    nav = 1001.7978 * adjustment_factor(real, "MON100", D(2021, 6, 17), "nav")
    assert price / nav - 1 == pytest.approx(100.87 / 100.17978 - 1)  # +0.69%, sensible
    assert 0.0 < price / nav - 1 < 0.01


def test_units_outstanding_are_scaled_up_before_the_split(real: CorporateActions):
    assert adjustment_factor(real, "MON100", D(2021, 6, 16), "units") == pytest.approx(10.0)
    assert adjustment_factor(real, "MON100", D(2021, 6, 17), "units") == 1.0


def test_other_symbols_and_unknown_symbols_are_untouched(real: CorporateActions):
    for sym in ("MONQ50", "NIFTYBEES", "NOPE"):
        for kind in ("price", "nav", "units"):
            assert adjustment_factor(real, sym, D(2021, 1, 1), kind) == 1.0  # type: ignore[arg-type]


# -- factor logic with made-up actions --------------------------------------------------


def test_nav_ex_date_defaults_to_the_price_ex_date():
    pool = [split(ex=D(2022, 3, 1))]
    assert adjustment_factor(pool, "AAA", D(2022, 2, 28), "nav") == pytest.approx(0.1)
    assert adjustment_factor(pool, "AAA", D(2022, 3, 1), "nav") == 1.0


def test_two_splits_multiply():
    # 2:1 on 2022-01-03 then 5:1 on 2023-01-02. A price in 2021 is 10x too big in today's units:
    # factor = 1/2 x 1/5 = 0.1. Between the two splits only the later one is still ahead.
    pool = [split(ex=D(2022, 1, 3), ratio=2), split(ex=D(2023, 1, 2), ratio=5)]
    assert adjustment_factor(pool, "AAA", D(2021, 12, 31)) == pytest.approx(0.1)
    assert adjustment_factor(pool, "AAA", D(2022, 6, 1)) == pytest.approx(0.2)
    assert adjustment_factor(pool, "AAA", D(2023, 1, 2)) == 1.0
    assert adjustment_factor(pool, "AAA", D(2021, 12, 31), "units") == pytest.approx(10.0)


def test_reverse_split_ratio_below_one_raises_price_factor():
    # 1 new unit for 2 old ones (ratio 0.5): old prices were half as big, so x2.
    pool = [split(ex=D(2022, 3, 1), ratio=0.5)]
    assert adjustment_factor(pool, "AAA", D(2022, 2, 1)) == pytest.approx(2.0)
    assert adjustment_factor(pool, "AAA", D(2022, 2, 1), "units") == pytest.approx(0.5)


def test_day_before_ex_date_is_adjusted_and_ex_date_is_not():
    pool = [split(ex=D(2022, 3, 1))]
    assert adjustment_factor(pool, "AAA", D(2022, 2, 28)) == pytest.approx(0.1)
    assert adjustment_factor(pool, "AAA", D(2022, 3, 1)) == 1.0


def test_unknown_kind_is_rejected():
    with pytest.raises(ValueError, match="kind"):
        adjustment_factor([], "AAA", D(2022, 1, 1), "volume")  # type: ignore[arg-type]


def test_price_times_units_is_unchanged_by_adjustment():
    """Fund size = price x units must not move across a split."""
    pool = [split(ex=D(2022, 3, 1), ratio=10)]
    before = 1000.0 * 5_000  # Rs 1,000 a unit, 5,000 units
    after = 100.0 * 50_000  # Rs 100 a unit, 50,000 units
    adj_before = (1000.0 * adjustment_factor(pool, "AAA", D(2022, 2, 28))) * (
        5_000 * adjustment_factor(pool, "AAA", D(2022, 2, 28), "units")
    )
    assert before == after == pytest.approx(adj_before)


# -- table helper -----------------------------------------------------------------------


def test_adjust_values_on_a_table_keeps_index_and_input():
    actions = CorporateActions(actions=[split("AAA", D(2022, 3, 1), 10)])
    frame = pd.DataFrame(
        {
            "symbol": ["AAA", "AAA", "BBB"],
            "date": [D(2022, 2, 28), D(2022, 3, 1), D(2022, 2, 28)],
            "close": [1000.0, 100.0, 50.0],
        },
        index=[7, 8, 9],
    )
    out = adjust_values(actions, frame, value="close", kind="price")
    assert out.tolist() == pytest.approx([100.0, 100.0, 50.0])
    assert out.index.tolist() == [7, 8, 9]
    assert frame["close"].tolist() == [1000.0, 100.0, 50.0]  # input untouched


# -- validation -------------------------------------------------------------------------


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "ca.yaml"
    path.write_text(text, encoding="utf-8")
    return path


GOOD = """
actions:
  - {symbol: AAA, type: split, ratio: 2, ex_date: 2022-03-01, verified: false, source: s}
"""


def test_loader_reads_a_valid_file(tmp_path: Path):
    loaded = load_corporate_actions(write(tmp_path, GOOD))
    assert loaded.actions[0].ratio == 2 and loaded.actions[0].verified is False


@pytest.mark.parametrize(
    ("text", "message"),
    [
        (GOOD.replace("ratio: 2", "ratio: 1"), "other than 1"),
        (GOOD.replace("ratio: 2", "ratio: 0"), "greater than 0"),
        (GOOD.replace("ratio: 2", "ratio: -3"), "greater than 0"),
        (GOOD.replace("type: split", "type: bonus"), "split"),
        (GOOD.replace("symbol: AAA", "symbol: aaa"), "upper-case"),
        (GOOD.replace(", source: s", ""), "source"),
        (GOOD.replace("verified: false, ", ""), "verified"),
        (GOOD.replace("ex_date: 2022-03-01", "ex_date: 'not a date'"), "ex_date"),
        (GOOD.replace("}", ", extra_field: 1}"), "extra_field"),
        (GOOD.replace("}", "}\n  - " + GOOD.split("- ")[1].strip()), "duplicate"),
        ("- just a list\n", "mapping"),
    ],
)
def test_loader_rejects_bad_files(tmp_path: Path, text: str, message: str):
    with pytest.raises(ValueError, match=message):
        load_corporate_actions(write(tmp_path, text))


def test_missing_file_is_a_clear_error(tmp_path: Path):
    with pytest.raises(FileNotFoundError, match="corporate_actions"):
        load_corporate_actions(tmp_path / "corporate_actions.yaml")
