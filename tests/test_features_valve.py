"""Hand-computed tests for the monthly valve classification (M5, DECISIONS D33)."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from etfp.features import valve as v
from etfp.store import coerce, get_schema

SIZES = {"AAA": 600_000, "BBB": None}


def daily(rows: list[tuple[str, date, int]]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"symbol": s, "date": d, "units_outstanding": u, "source_file": f"sm/{d}.csv"}
            for s, d, u in rows
        ]
    )


EMPTY_MONTHLY = pd.DataFrame({"symbol": [], "month_end": [], "units": [], "source_file": []})


# Dates NSE refreshed the counts (in real data: any ETF's count changed that day). Includes
# 31 Dec 2024 and 4 Feb 2025, when AAA itself did not move.
REFRESH = [
    date(2024, 12, 31),
    date(2025, 1, 2),
    date(2025, 1, 6),
    date(2025, 2, 4),
    date(2025, 3, 31),
    date(2025, 5, 2),
    date(2025, 5, 5),
]


def run(ud: pd.DataFrame, um: pd.DataFrame = EMPTY_MONTHLY, **kw) -> pd.DataFrame:
    kw.setdefault("refreshes", REFRESH)
    out = v.valve_monthly(ud, um, SIZES, ["AAA", "BBB"], **kw)
    return out.set_index(["symbol", "month"])


# AAA, basket 600,000:
#   Dec 2024: 3,275,018 on 30 Dec, 3,275,018 on 31 Dec            -> first file has no baseline
#   Jan 2025: 3,875,018 (2 Jan, +600,000 vs 31 Dec), 3,875,018, 4,475,018 (+600,000)
#   Feb 2025: 4,475,018 flat                                       -> closed
#   Mar 2025: 4,475,018, then 3,875,018 (-600,000)                 -> redeeming_only
#   Apr 2025: no file                                              -> unknown (no_data)
#   May 2025: 4,475,018 (+600,000 vs 31 Mar, across the gap), 4,175,018 (-300,000: half a basket)
AAA = [
    ("AAA", date(2024, 12, 30), 3_275_018),
    ("AAA", date(2024, 12, 31), 3_275_018),
    ("AAA", date(2025, 1, 2), 3_875_018),
    ("AAA", date(2025, 1, 3), 3_875_018),
    ("AAA", date(2025, 1, 6), 4_475_018),
    ("AAA", date(2025, 2, 3), 4_475_018),
    ("AAA", date(2025, 2, 4), 4_475_018),
    ("AAA", date(2025, 3, 3), 4_475_018),
    ("AAA", date(2025, 3, 31), 3_875_018),
    ("AAA", date(2025, 5, 2), 4_475_018),
    ("AAA", date(2025, 5, 5), 4_175_018),
]


def test_daily_rise_counts_from_previous_months_last_file() -> None:
    t = run(daily(AAA), start_month="2024-12")
    jan = t.loc[("AAA", "2025-01")]
    assert jan.valve_status == "open" and jan.status_rule == "daily_rose"
    assert jan.source == "daily_secmaster"
    # Start = last file of December (31 Dec), not the first file of January.
    assert jan.units_start == 3_275_018 and jan.units_start_date == date(2024, 12, 31)
    assert jan.units_end == 4_475_018 and jan.units_end_date == date(2025, 1, 6)
    assert jan.units_change == 1_200_000  # 4,475,018 - 3,275,018
    assert jan.creation_units_net == pytest.approx(2.0)  # 1,200,000 / 600,000
    assert jan.units_change_pct == pytest.approx(1_200_000 / 3_275_018)
    assert (jan.n_obs, jan.n_creation_days, jan.n_redemption_days) == (3, 2, 0)
    assert jan.all_changes_whole_cu is True
    # Lineage: start file, the two change files, end file (6 Jan is both).
    assert jan.source_file == "sm/2024-12-31.csv;sm/2025-01-02.csv;sm/2025-01-06.csv"


def test_daily_flat_is_closed_and_fall_only_is_redeeming() -> None:
    t = run(daily(AAA), start_month="2024-12")
    feb = t.loc[("AAA", "2025-02")]
    assert (feb.valve_status, feb.status_rule) == ("closed", "daily_flat")
    assert feb.units_change == 0 and feb.n_creation_days == 0 and feb.n_redemption_days == 0
    assert feb.units_start_date == date(2025, 1, 6)
    assert feb.n_refresh_dates == 1  # 4 Feb 2025
    mar = t.loc[("AAA", "2025-03")]
    assert (mar.valve_status, mar.status_rule) == ("redeeming_only", "daily_fell_only")
    assert mar.units_change == -600_000 and mar.creation_units_net == pytest.approx(-1.0)
    assert (mar.n_creation_days, mar.n_redemption_days) == (0, 1)


def test_first_month_and_missing_month() -> None:
    t = run(daily(AAA), start_month="2024-11")
    # Nov 2024: before the first file -> unknown, never guessed.
    nov = t.loc[("AAA", "2024-11")]
    assert (nov.valve_status, nov.status_rule, nov.source) == ("unknown", "no_data", "none")
    assert nov.n_obs == 0 and nov.source_file == "none"
    # Dec 2024: the first file is its own baseline; the second is flat vs it -> closed.
    dec = t.loc[("AAA", "2024-12")]
    assert dec.valve_status == "closed" and dec.units_start_date == date(2024, 12, 30)
    apr = t.loc[("AAA", "2025-04")]
    assert apr.valve_status == "unknown" and apr.status_rule == "no_data"


def test_flat_without_any_refresh_is_unknown_not_closed() -> None:
    # Default refresh calendar = dates any symbol's count changed. AAA's own changes are
    # 2 Jan, 6 Jan, 31 Mar, 2 May, 5 May 2025; February has none, so flat Feb is no evidence.
    t = run(daily(AAA), start_month="2024-12", refreshes=None)
    feb = t.loc[("AAA", "2025-02")]
    assert (feb.valve_status, feb.status_rule) == ("unknown", "daily_no_refresh")
    assert feb.n_refresh_dates == 0 and feb.units_change == 0
    # Another fund moving on 4 Feb 2025 is a refresh: then flat AAA is closed.
    busy = daily([*AAA, ("BBB", date(2025, 1, 6), 100), ("BBB", date(2025, 2, 4), 200)])
    t2 = run(busy, start_month="2024-12", refreshes=None)
    assert t2.loc[("AAA", "2025-02"), "valve_status"] == "closed"
    assert v.refresh_dates(busy)[:4] == [
        date(2025, 1, 2), date(2025, 1, 6), date(2025, 2, 4), date(2025, 3, 31)
    ]  # fmt: skip


def test_refresh_needs_a_real_share_of_funds_to_change() -> None:
    # 20 funds, files on 1 and 8 Feb. On 8 Feb only F00 changes (1/20 = 5%): an odd single-fund
    # update, not a batch (like four unrelated ETFs on 29 Feb 2024). On 15 Feb F00 and F01
    # change (2/20 = 10%): a batch (the share rule is >=).
    rows = []
    for i in range(20):
        sym = f"F{i:02d}"
        rows += [
            (sym, date(2025, 2, 3), 1000),
            (sym, date(2025, 2, 10), 1100 if i == 0 else 1000),
            (sym, date(2025, 2, 17), 1200 if i < 2 else 1000),
        ]
    frame = daily(rows)
    assert v.refresh_dates(frame) == [date(2025, 2, 17)]
    assert v.refresh_dates(frame, min_share=0.05) == [date(2025, 2, 10), date(2025, 2, 17)]


def test_single_first_file_has_no_baseline() -> None:
    t = run(daily([("AAA", date(2024, 2, 5), 1_000_000)]), start_month="2024-02")
    feb = t.loc[("AAA", "2024-02")]
    assert (feb.valve_status, feb.status_rule) == ("unknown", "daily_no_baseline")
    assert feb.units_end == 1_000_000 and feb.n_obs == 1


def test_change_across_gap_and_non_whole_basket() -> None:
    t = run(daily(AAA), start_month="2024-12")
    may = t.loc[("AAA", "2025-05")]
    # 2 May vs 31 Mar (April has no file): +600,000 is dated to May. Then -300,000.
    assert may.units_start == 3_875_018 and may.units_start_date == date(2025, 3, 31)
    assert (may.n_creation_days, may.n_redemption_days) == (1, 1)
    assert may.valve_status == "open"  # a rise and a fall in one month: creation was possible
    assert may.units_change == 300_000 and may.creation_units_net == pytest.approx(0.5)
    assert may.all_changes_whole_cu is False  # -300,000 is half a basket: look at this month


def test_unknown_basket_size_keeps_status() -> None:
    rows = [("BBB", date(2025, 1, 2), 100), ("BBB", date(2025, 1, 3), 250)]
    t = run(daily(rows), start_month="2025-01")
    b = t.loc[("BBB", "2025-01")]
    assert b.valve_status == "open" and b.units_change == 150
    assert pd.isna(b.creation_units_net) and pd.isna(b.creation_unit_size)
    assert b.all_changes_whole_cu is None


def _monthly(sym: str, units: list[float], lags: list[int] | None = None) -> pd.DataFrame:
    ends = [date(2021, m, d) for m, d in ((1, 31), (2, 28), (3, 31), (4, 30), (5, 31))]
    out = pd.DataFrame(
        {
            "symbol": [sym] * len(units),
            "month_end": ends[: len(units)],
            "units": units,
            "source_file": ["manual/units_monthly.csv"] * len(units),
        }
    )
    if lags is not None:
        out["nav_lag_days"] = lags
    return out


def _empty_daily() -> pd.DataFrame:
    return daily([]).reindex(columns=["symbol", "date", "units_outstanding", "source_file"])


def test_monthly_basket_rule() -> None:
    # AAA, basket 600,000, threshold 0.9 basket = 540,000 units. Month-end AUM/NAV units:
    #   Jan 10,000,000                       no previous month  -> unknown
    #   Feb 10,050,000  (+50,000 = 0.08 b)   noise              -> closed
    #   Mar 10,590,000  (+540,000 = 0.90 b)  exactly the limit  -> open
    #   Apr 10,050,001  (-539,999 = -0.8999) just inside        -> closed
    #   May  9,450,001  (-600,000 = -1 b)                       -> redeeming_only
    um = _monthly("AAA", [10_000_000.0, 10_050_000.0, 10_590_000.0, 10_050_001.0, 9_450_001.0])
    t = run(_empty_daily(), um)
    jan, feb, mar, apr, may = (t.loc[("AAA", f"2021-0{m}")] for m in range(1, 6))
    assert (jan.valve_status, jan.status_rule) == ("unknown", "monthly_no_baseline")
    assert (feb.valve_status, feb.status_rule) == ("closed", "monthly_flat")
    assert feb.units_change_pct == pytest.approx(0.005)
    assert (mar.valve_status, mar.status_rule) == ("open", "monthly_up")
    assert mar.units_change == pytest.approx(540_000)
    assert mar.creation_units_net == pytest.approx(0.9)
    assert pd.isna(mar.n_creation_days)  # days are not observable in monthly data
    assert (apr.valve_status, apr.status_rule) == ("closed", "monthly_flat")
    assert (may.valve_status, may.status_rule) == ("redeeming_only", "monthly_down")
    assert may.source == "monthly_manual"
    # end_month defaults to the latest month in the inputs.
    assert t.index.get_level_values("month").max() == "2021-05"


def test_monthly_basket_beats_one_percent_on_a_big_fund() -> None:
    # 100,000,000 units, basket 600,000: +600,000 is +0.6% (the old +-1% rule said closed) but
    # exactly one basket, a creation. And -300,000 (half a basket, -0.3%) stays closed.
    um = _monthly("AAA", [100_000_000.0, 100_600_000.0, 100_300_000.0])
    t = run(_empty_daily(), um)
    assert t.loc[("AAA", "2021-02"), "valve_status"] == "open"
    assert t.loc[("AAA", "2021-02"), "creation_units_net"] == pytest.approx(1.0)
    assert t.loc[("AAA", "2021-03"), "valve_status"] == "closed"


def test_monthly_small_percent_noise_is_not_a_creation_even_if_large() -> None:
    # 10,000,000 units: +201,000 is +2.0% (old rule: open) but only 0.335 basket (a NAV-timing
    # artefact, not a creation): closed.
    t = run(_empty_daily(), _monthly("AAA", [10_000_000.0, 10_201_000.0]))
    assert t.loc[("AAA", "2021-02"), "valve_status"] == "closed"


def test_monthly_unknown_basket_falls_back_to_one_percent() -> None:
    # BBB has no basket size: +0.5% closed, +2.0% open, -2.5% redeeming (old rule, D33).
    um = _monthly("BBB", [10_000_000.0, 10_050_000.0, 10_251_000.0, 10_000_000.0])
    t = run(_empty_daily(), um)
    assert [t.loc[("BBB", f"2021-0{m}"), "valve_status"] for m in (2, 3, 4)] == [
        "closed",
        "open",
        "redeeming_only",
    ]
    assert t.loc[("BBB", "2021-03"), "status_rule"] == "monthly_up"
    assert pd.isna(t.loc[("BBB", "2021-03"), "creation_units_net"])


def test_monthly_timing_flag_when_nav_date_differs() -> None:
    # Same units as the basket test; the March NAV is 2 days older than the month-end and the
    # January NAV is of the month-end itself. Feb compares Jan/Feb (both lag 0): no suffix.
    # Mar (lag 2 at its end) and Apr (lag 2 at its start) get the suffix; May (0, 0) none.
    um = _monthly(
        "AAA",
        [10_000_000.0, 10_050_000.0, 10_590_000.0, 10_050_001.0, 9_450_001.0],
        lags=[0, 0, 2, 0, 0],
    )
    t = run(_empty_daily(), um)
    rules = [t.loc[("AAA", f"2021-0{m}"), "status_rule"] for m in (2, 3, 4, 5)]
    assert rules == ["monthly_flat", "monthly_up_timing", "monthly_flat_timing", "monthly_down"]
    # status itself is unchanged by the flag
    assert t.loc[("AAA", "2021-03"), "valve_status"] == "open"


def test_daily_beats_monthly_in_the_same_month() -> None:
    um = pd.DataFrame(
        {
            "symbol": ["AAA", "AAA"],
            "month_end": [date(2024, 12, 31), date(2025, 1, 31)],
            "units": [3_000_000.0, 9_000_000.0],  # monthly says +200%: ignored for Jan 2025
            "source_file": ["manual/units_monthly.csv"] * 2,
        }
    )
    t = run(daily(AAA), um, start_month="2024-12", end_month="2025-01")
    assert t.loc[("AAA", "2025-01"), "source"] == "daily_secmaster"
    assert t.loc[("AAA", "2025-01"), "units_change"] == 1_200_000


def test_frozen_daily_month_defers_to_monthly_figures() -> None:
    # AAA (basket 600,000): NSE's two Feb 2025 files are identical and no refresh batch ran
    # (daily_no_refresh = unknown), but the AMC's month-end units rose 10,000,000 -> 10,600,000
    # (+1 basket): use the monthly rule, which says open.
    ud = daily([("AAA", date(2025, 2, 5), 10_000_000), ("AAA", date(2025, 2, 20), 10_000_000)])
    um = pd.DataFrame(
        {
            "symbol": ["AAA", "AAA"],
            "month_end": [date(2025, 1, 31), date(2025, 2, 28)],
            "units": [10_000_000.0, 10_600_000.0],
            "source_file": ["manual/units_monthly.csv"] * 2,
        }
    )
    t = run(ud, um, start_month="2025-01", end_month="2025-02", refreshes=[])
    feb = t.loc[("AAA", "2025-02")]
    assert (feb.valve_status, feb.status_rule, feb.source) == (
        "open",
        "monthly_up",
        "monthly_manual",
    )
    assert feb.creation_units_net == pytest.approx(1.0)
    # Without the January figure there is nothing to compare: the daily row (unknown) stays.
    t2 = run(ud, um.iloc[1:], start_month="2025-01", end_month="2025-02", refreshes=[])
    assert t2.loc[("AAA", "2025-02"), "status_rule"] == "daily_no_refresh"


def test_month_after_a_frozen_one_defers_too() -> None:
    # As above, plus March: NSE's first batch (6 Mar) shows +600,000 (+1 basket), which is the
    # February creation published late; the AMC's March figure is flat (10,600,000), so March is
    # closed from the monthly rule rather than open from the daily rows.
    ud = daily(
        [
            ("AAA", date(2025, 2, 5), 10_000_000),
            ("AAA", date(2025, 2, 20), 10_000_000),
            ("AAA", date(2025, 3, 6), 10_600_000),
            ("AAA", date(2025, 3, 20), 10_600_000),
        ]
    )
    um = pd.DataFrame(
        {
            "symbol": ["AAA"] * 3,
            "month_end": [date(2025, 1, 31), date(2025, 2, 28), date(2025, 3, 31)],
            "units": [10_000_000.0, 10_600_000.0, 10_600_000.0],
            "source_file": ["manual/units_monthly.csv"] * 3,
        }
    )
    t = run(ud, um, start_month="2025-02", end_month="2025-03", refreshes=[date(2025, 3, 6)])
    assert t.loc[("AAA", "2025-02"), "valve_status"] == "open"
    mar = t.loc[("AAA", "2025-03")]
    assert (mar.valve_status, mar.status_rule) == ("closed", "monthly_flat")
    # With no AMC figures the daily row stands (it is all we have): open, daily_rose.
    t2 = run(
        ud, EMPTY_MONTHLY, start_month="2025-02", end_month="2025-03", refreshes=[date(2025, 3, 6)]
    )
    assert t2.loc[("AAA", "2025-03"), "status_rule"] == "daily_rose"


def test_grid_and_schema() -> None:
    out = v.valve_monthly(daily(AAA), EMPTY_MONTHLY, SIZES, ["AAA", "BBB"])
    # Every symbol gets every month from 2021-01 through the last data month (May 2025).
    assert len(out) == 2 * len(v.month_range("2021-01", "2025-05"))
    assert set(out["valve_status"]) <= set(v.STATUSES)
    assert (out.loc[out.symbol == "BBB", "valve_status"] == "unknown").all()
    coerce(out, get_schema(v.TABLE))  # fits the registered schema (no missing source_file)


def test_markdown_grid() -> None:
    out = v.valve_monthly(
        daily(AAA), EMPTY_MONTHLY, SIZES, ["AAA"], start_month="2024-12", refreshes=REFRESH
    )
    md = v.valve_status_markdown(out, tracked=["AAA"])
    assert "| AAA | 2025 | O | · | R |   | O |" in md  # Jan..May; April unknown (blank)
    assert "units data through 2025-05-05" in md
    assert "| 2025-01 | +2.0 |" in md  # net baskets table (Jan: +1,200,000 / 600,000)
    assert "| AAA | 2025-01 | open | 3,275,018 | 4,475,018 | +1,200,000 | +2.00 | 2 | 0 |" in md


def test_frozen_file_dates_hand_computed() -> None:
    # 10 funds. Batches (>=10% = 1 fund) on 6 Jan, 13 Jan (gap 7 d, normal) and 17 Feb
    # (gap 35 d, longer than 28: frozen). Files dated 13 Jan..16 Feb carry the 13 Jan counts.
    d = [date(2025, 1, 6), date(2025, 1, 13), date(2025, 1, 20), date(2025, 2, 3),
         date(2025, 2, 17), date(2025, 2, 24)]  # fmt: skip
    rows = []
    for i in range(10):
        s = f"F{i}"
        for day, u in zip(d, [100, 110, 110, 110, 120, 120], strict=True):
            rows.append((s, day, u + i))
    frame = daily(rows)
    assert v.refresh_dates(frame) == [date(2025, 1, 13), date(2025, 2, 17)]
    # 6 Jan: before the first batch, which comes 7 d later (normal): fresh.
    # 13 Jan batch: fresh. 20 Jan, 3 Feb: inside the 35-day gap: flagged. 17 Feb batch: fresh.
    # 24 Feb: after the last batch, only 7 d: fresh.
    assert v.frozen_file_dates(frame) == {date(2025, 1, 20), date(2025, 2, 3)}


def test_frozen_leading_stretch_includes_the_first_file() -> None:
    # No batch until 14 Mar although files start 5 Feb (38 d): all of them flagged, the
    # 14 Mar batch file not (the real 2024 case, D48).
    days = [date(2024, 2, 5), date(2024, 2, 12), date(2024, 3, 11), date(2024, 3, 14)]
    rows = [(f"F{i}", day, 100 + (50 if (day == days[3] and i < 3) else 0)) for i in range(10)
            for day in days]  # fmt: skip
    frame = daily(rows)
    assert v.refresh_dates(frame) == [date(2024, 3, 14)]
    assert v.frozen_file_dates(frame) == set(days[:3])


def test_frozen_trailing_stretch_flags_only_after_four_weeks() -> None:
    days = [date(2025, 1, 6), date(2025, 1, 13), date(2025, 2, 3), date(2025, 2, 11)]
    rows = [(f"F{i}", day, 100 + (50 if day >= days[1] else 0)) for i in range(10) for day in days]
    frame = daily(rows)
    assert v.refresh_dates(frame) == [date(2025, 1, 13)]
    # 3 Feb is 21 d after the last batch (not yet), 11 Feb is 29 d (flagged).
    assert v.frozen_file_dates(frame) == {date(2025, 2, 11)}
    assert v.frozen_file_dates(frame.iloc[0:0]) == set()
