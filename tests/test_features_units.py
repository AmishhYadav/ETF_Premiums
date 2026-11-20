"""Tests for units outstanding, creation events and the AUM cross-check (R1, D7)."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from etfp.config import load_etfs
from etfp.features import units as u

# DECISIONS D7 (IssdCptl from the NSE security master).
D7_DATES = [
    date(2024, 7, 1),
    date(2025, 1, 2),
    date(2026, 4, 1),
    date(2026, 9, 4),
    date(2026, 11, 9),
]
D7 = {
    "MONQ50": [3_275_018, 8_075_018, 14_675_018, 14_675_018, 14_675_018],
    "MON100": [498_384_740, 504_884_740, 504_134_740, 504_134_740, 504_134_740],
    "MASPTOP50": [172_474_040, 168_624_040, 168_624_040, 168_624_040, 168_624_040],
}
SIZES = {"MONQ50": 600_000, "MON100": 250_000, "MASPTOP50": None}


def d7_frame() -> pd.DataFrame:
    rows = [
        {"symbol": s, "date": d, "units_outstanding": v, "source_file": f"f/{d}"}
        for s, vals in D7.items()
        for d, v in zip(D7_DATES, vals, strict=True)
    ]
    return pd.DataFrame(rows)


def test_monq50_d7_creation_units() -> None:
    ch = u.units_changes(d7_frame(), SIZES)
    m = ch[ch.symbol == "MONQ50"].reset_index(drop=True)
    # 1 Jul 2024: first observation, no change. 2 Jan 2025: 8,075,018 - 3,275,018 = 4,800,000
    # = 8 creation units of 600,000. 1 Apr 2026: 14,675,018 - 8,075,018 = 6,600,000 = 11.
    # Afterwards flat.
    assert pd.isna(m.units_change.iloc[0]) and pd.isna(m.creation_units_change.iloc[0])
    assert m.units_change.tolist()[1:] == [4_800_000, 6_600_000, 0, 0]
    assert m.creation_units_change.tolist()[1:] == [8.0, 11.0, 0.0, 0.0]
    assert m.creation_multiple_ok.tolist()[1:] == [True, True, True, True]
    # R19 (c): creation units between 2024-07-01 and 2026-09-04 sum to +19.
    window = m[m.date <= date(2026, 9, 4)]
    assert window.creation_units_change.sum() == 19.0
    assert m.event_type.tolist()[1:3] == ["creation", "creation"]
    assert m.event_type.iloc[3] is None or pd.isna(m.event_type.iloc[3])
    # Attribution and gap length are explicit.
    assert m.units_prev_date.iloc[1] == date(2024, 7, 1)
    assert m.days_since_prev.iloc[1] == 185  # 1 Jul 2024 -> 2 Jan 2025 (31+31+30+31+30+31+1)


def test_mon100_creation_then_redemption() -> None:
    ch = u.units_changes(d7_frame(), SIZES)
    m = ch[ch.symbol == "MON100"].reset_index(drop=True)
    # +6,500,000 / 250,000 = +26 creation units; then 504,134,740 - 504,884,740 = -750,000 = -3.
    assert m.creation_units_change.tolist()[1:3] == [26.0, -3.0]
    assert m.event_type.tolist()[1:3] == ["creation", "redemption"]


def test_unknown_creation_unit_size_gives_nan_and_is_reported() -> None:
    ch = u.units_changes(d7_frame(), SIZES)
    a = ch[ch.symbol == "MASPTOP50"].reset_index(drop=True)
    # 168,624,040 - 172,474,040 = -3,850,000 units; size unknown -> NaN, but units_change is kept.
    assert a.units_change.iloc[1] == -3_850_000
    assert a.creation_units_change.isna().all() and a.creation_multiple_ok.isna().all()
    assert a.event_type.iloc[1] == "redemption"
    assert u.missing_creation_unit_sizes(["MONQ50", "MASPTOP50", "MONQ50", "X"], SIZES) == [
        "MASPTOP50",
        "X",
    ]


def test_non_multiple_is_flagged() -> None:
    df = pd.DataFrame(
        {
            "symbol": ["A", "A"],
            "date": [date(2026, 1, 1), date(2026, 1, 2)],
            "units_outstanding": [1_000_000, 1_250_000],  # +250,000 with a 600,000 size
        }
    )
    ch = u.units_changes(df, {"A": 600_000})
    assert ch.creation_units_change.iloc[1] == pytest.approx(250_000 / 600_000)
    assert bool(ch.creation_multiple_ok.iloc[1]) is False


def test_missing_units_row_compares_with_last_valid() -> None:
    df = pd.DataFrame(
        {
            "symbol": ["A"] * 3,
            "date": [date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 5)],
            "units_outstanding": [1_000_000, None, 1_600_000],
        }
    )
    ch = u.units_changes(df, {"A": 600_000})
    assert pd.isna(ch.units_change.iloc[1])
    # 5 Jan is compared with 1 Jan (the last valid value): +600,000 = 1 creation unit, gap 4 days.
    assert ch.units_change.iloc[2] == 600_000 and ch.creation_units_change.iloc[2] == 1.0
    assert ch.units_prev_date.iloc[2] == date(2026, 1, 1) and ch.days_since_prev.iloc[2] == 4


def test_input_order_does_not_matter_and_duplicates_are_errors() -> None:
    shuffled = d7_frame().sample(frac=1, random_state=1)
    ch = u.units_changes(shuffled, SIZES)
    m = ch[ch.symbol == "MONQ50"]
    assert m.date.tolist() == sorted(m.date.tolist())
    assert m.creation_units_change.sum() == 19.0
    with pytest.raises(ValueError, match="duplicate"):
        u.units_changes(pd.concat([d7_frame(), d7_frame().head(1)]), SIZES)
    bad = d7_frame()
    bad.loc[0, "units_outstanding"] = -5
    with pytest.raises(ValueError, match="negative"):
        u.units_changes(bad, SIZES)
    with pytest.raises(ValueError, match="needs column"):
        u.units_changes(d7_frame().drop(columns=["date"]), SIZES)


def test_event_rows_only_where_units_moved() -> None:
    ev = u.unit_change_events(u.units_changes(d7_frame(), SIZES))
    monq = ev[ev.symbol == "MONQ50"]
    assert monq.date.tolist() == [date(2025, 1, 2), date(2026, 4, 1)]
    assert monq.units_new.tolist() == [8_075_018, 14_675_018]
    assert monq.units_prev.tolist() == [3_275_018, 8_075_018]
    assert set(ev.event_type) == {"creation", "redemption"}
    assert list(ev.columns) == list(u.EVENT_COLUMNS)


def test_sizes_from_universe_config() -> None:
    sizes = u.creation_unit_sizes(load_etfs())
    assert set(sizes) >= {"MONQ50", "MAHKTECH"}
    # Report the gap rather than guess: whatever is null in config shows up here.
    gap = u.missing_creation_unit_sizes(list(sizes), sizes)
    assert all(not sizes[s] for s in gap)


def test_aum_crosscheck() -> None:
    units = pd.DataFrame(
        {
            "symbol": ["MONQ50"] * 2,
            "date": [date(2026, 8, 28), date(2026, 9, 4)],
            "units_outstanding": [14_675_018, 14_675_018],
        }
    )
    monthly = pd.DataFrame(
        {
            "symbol": ["MONQ50", "MONQ50", "MONQ50", "MON100"],
            "month_end": [
                date(2026, 8, 31),
                date(2026, 9, 30),
                date(2026, 3, 31),
                date(2026, 8, 31),
            ],
            # Aug: AUM exactly units x NAV -> 14,675,018 x 100 = 1,467,501,800 (rel diff 0).
            # Sep: AUM 10% higher than units x NAV, but the latest count (4 Sep) is 26 days old:
            # not comparable by default (null); flagged +10% once max_gap_days allows it.
            # Mar: no exchange count within 10 days before it -> not comparable (null).
            # MON100: no units rows at all -> null.
            "aum_rs": [1_467_501_800.0, 1_614_251_980.0, 1_000_000.0, 5.0],
            "nav": [100.0, 100.0, 100.0, 1.0],
        }
    )
    out = u.units_aum_crosscheck(units, monthly).set_index(["symbol", "month_end"])
    aug = out.loc[("MONQ50", date(2026, 8, 31))]
    assert (
        aug.rel_diff == pytest.approx(0.0, abs=1e-12) and bool(aug.flag_units_aum_mismatch) is False
    )
    assert aug.units_exchange_date == date(2026, 8, 28)
    sep = out.loc[("MONQ50", date(2026, 9, 30))]
    assert pd.isna(sep.flag_units_aum_mismatch)
    mar = out.loc[("MONQ50", date(2026, 3, 31))]
    assert pd.isna(mar.flag_units_aum_mismatch)
    assert pd.isna(out.loc[("MON100", date(2026, 8, 31))].flag_units_aum_mismatch)
    # With a wider gap allowance the 10% difference is flagged.
    wide = u.units_aum_crosscheck(units, monthly, max_gap_days=30).set_index(
        ["symbol", "month_end"]
    )
    sep = wide.loc[("MONQ50", date(2026, 9, 30))]
    assert (
        sep.rel_diff == pytest.approx(0.10, abs=1e-9) and bool(sep.flag_units_aum_mismatch) is True
    )


# -- dated creation-unit history (D54) ---------------------------------------------------

HDFC_STEPS = [(date(2024, 2, 5), 4_000), (date(2025, 8, 20), 20_000)]


def test_size_in_force_on_each_date() -> None:
    dates = pd.Series([date(2024, 2, 4), date(2024, 2, 5), date(2025, 8, 19), date(2025, 8, 20)])
    sym = pd.Series(["HDFCNIFTY"] * 4)
    out = u.size_on_dates(dates, sym, {"HDFCNIFTY": 20_000}, {"HDFCNIFTY": HDFC_STEPS})
    assert pd.isna(out.iloc[0])  # before the first step: size unknown, not guessed
    assert out.tolist()[1:] == [4_000.0, 4_000.0, 20_000.0]


def test_symbol_without_history_uses_the_single_size_everywhere() -> None:
    dates = pd.Series([date(2024, 2, 5), date(2026, 1, 1), date(2026, 1, 1)])
    sym = pd.Series(["MONQ50", "MONQ50", "OTHER"])
    out = u.size_on_dates(dates, sym, {"MONQ50": 600_000, "OTHER": None}, {"X": HDFC_STEPS})
    assert out.iloc[:2].tolist() == [600_000.0, 600_000.0]
    assert pd.isna(out.iloc[2])


def test_hdfcnifty_changes_use_the_size_in_force() -> None:
    # Hand-computed. 10 Feb 2025 -> 24 Feb 2025: +36,000 units = 9 baskets of 4,000 (NOT 1.8
    # baskets of 20,000). 21 Aug 2025: +320,000 units = 16 baskets of 20,000.
    # 22 Aug 2025: -40,000 = -2 baskets of 20,000. A flat row is not an event.
    frame = pd.DataFrame(
        {
            "symbol": "HDFCNIFTY",
            "date": [date(2025, 2, 10), date(2025, 2, 24), date(2025, 8, 14), date(2025, 8, 21),
                     date(2025, 8, 22), date(2025, 8, 25)],
            "units_outstanding": [1_000_000, 1_036_000, 1_036_000, 1_356_000, 1_316_000, 1_316_000],
            "source_file": "f",
        }
    )  # fmt: skip
    ch = u.units_changes(frame, {"HDFCNIFTY": 20_000}, histories={"HDFCNIFTY": HDFC_STEPS})
    assert ch.creation_unit_size.tolist() == [4_000, 4_000, 4_000, 20_000, 20_000, 20_000]
    assert ch.creation_units_change.iloc[1] == 9.0
    assert ch.creation_units_change.iloc[3] == 16.0
    assert ch.creation_units_change.iloc[4] == -2.0
    assert ch.creation_multiple_ok.iloc[1] and ch.creation_multiple_ok.iloc[3]
    # Without the history the same rows would be 1.8 baskets, flagged not a whole multiple
    old = u.units_changes(frame, {"HDFCNIFTY": 20_000})
    assert old.creation_units_change.iloc[1] == pytest.approx(1.8)
    assert not old.creation_multiple_ok.iloc[1]
    ev = u.unit_change_events(ch)
    assert ev.creation_units_change.tolist() == [9.0, 16.0, -2.0]


def test_rows_before_the_first_step_have_no_size() -> None:
    frame = pd.DataFrame(
        {
            "symbol": "HDFCNIFTY",
            "date": [date(2024, 1, 2), date(2024, 1, 3)],
            "units_outstanding": [1_000_000, 1_004_000],
            "source_file": "f",
        }
    )
    ch = u.units_changes(frame, {"HDFCNIFTY": 20_000}, histories={"HDFCNIFTY": HDFC_STEPS})
    assert ch.creation_units_change.isna().all() and ch.creation_multiple_ok.isna().all()
    assert ch.units_change.iloc[1] == 4_000


def test_real_config_histories_are_ordered_and_end_at_current_size() -> None:
    universe = load_etfs()
    hist = u.creation_unit_histories(universe)
    assert set(hist) == {
        "HDFCNIFTY",
        "NIFTY1",
        "NIFTYBEES",
        "JUNIORBEES",
        "BANKBEES",
        "GOLDBEES",
        "SILVERBEES",
    }
    sizes = u.creation_unit_sizes(universe)
    for sym, steps in hist.items():
        assert [d for d, _ in steps] == sorted(d for d, _ in steps)
        assert steps[-1][1] == sizes[sym]
    assert hist["HDFCNIFTY"][-1] == (date(2025, 8, 20), 20_000)
    # NIFTY1: 13 Nov 2024 +2,575,000 units is 206 baskets of 12,500 (and 51.5 of 50,000)
    nifty1 = pd.DataFrame(
        {
            "symbol": "NIFTY1",
            "date": [date(2024, 10, 28), date(2024, 11, 13)],
            "units_outstanding": [94_389_630, 96_964_630],
            "source_file": "f",
        }
    )
    ch = u.units_changes(nifty1, sizes, histories=hist)
    assert ch.creation_units_change.iloc[1] == 206.0 and ch.creation_multiple_ok.iloc[1]
