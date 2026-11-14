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


def test_monthly_threshold_rule() -> None:
    # Month-end AUM/NAV units for AAA: 10,000,000 -> 10,050,000 (+0.5%: closed) ->
    # 10,251,000 (+2.0%: open) -> 10,000,000 (-2.45%: redeeming_only).
    um = pd.DataFrame(
        {
            "symbol": ["AAA"] * 4,
            "month_end": [date(2021, m, d) for m, d in ((1, 31), (2, 28), (3, 31), (4, 30))],
            "units": [10_000_000.0, 10_050_000.0, 10_251_000.0, 10_000_000.0],
            "source_file": ["manual/units_monthly.csv"] * 4,
        }
    )
    t = run(daily([]).reindex(columns=["symbol", "date", "units_outstanding", "source_file"]), um)
    jan, feb, mar, apr = (t.loc[("AAA", m)] for m in ("2021-01", "2021-02", "2021-03", "2021-04"))
    assert (jan.valve_status, jan.status_rule) == ("unknown", "monthly_no_baseline")
    assert (feb.valve_status, feb.status_rule) == ("closed", "monthly_flat")
    assert feb.units_change_pct == pytest.approx(0.005)
    assert (mar.valve_status, mar.status_rule) == ("open", "monthly_up")
    assert mar.units_change == pytest.approx(201_000)
    assert mar.creation_units_net == pytest.approx(201_000 / 600_000)
    assert pd.isna(mar.n_creation_days)  # days are not observable in monthly data
    assert (apr.valve_status, apr.status_rule) == ("redeeming_only", "monthly_down")
    assert apr.source == "monthly_manual"
    # end_month defaults to the latest month in the inputs.
    assert t.index.get_level_values("month").max() == "2021-04"


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
    assert "| AAA | 2025-01 | open | 3,275,018 | 4,475,018 | +1,200,000 | +2.00 | 2 | 0 |" in md
