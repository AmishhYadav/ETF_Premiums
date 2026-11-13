"""trends_daily (R15): chaining overlapping Google Trends windows by the ratio on the overlap."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from etfp.clean import CLEANERS, CleanContext, CleanError
from etfp.clean.manual_trends import COLUMNS, build, parse_trends_csv
from etfp.config import load_etfs
from etfp.store import SCHEMAS, Store

D1 = date(2026, 1, 1)


def day(n: int) -> date:
    """Day number n (1 = 1 Jan 2026)."""
    return D1 + timedelta(days=n - 1)


def trends_file(term: str, first: int, values: list[object], geo: str = "India") -> str:
    """Text in Google's export format: a preamble, a blank line, then Day,<term>: (<geo>)."""
    lines = ["Category: All categories", "", f"Day,{term}: ({geo})"]
    lines += [f"{day(first + i).isoformat()},{v}" for i, v in enumerate(values)]
    return "\n".join(lines) + "\n"


def put(root: Path, pulled: str, name: str, text: str) -> None:
    folder = root / "data" / "raw" / "trends" / pulled
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(text, encoding="utf-8")


def ctx_for(root: Path) -> CleanContext:
    (root / "data" / "raw").mkdir(parents=True, exist_ok=True)
    return CleanContext(raw_root=root / "data" / "raw", universe=load_etfs())


def series(df: pd.DataFrame, term: str = "MONQ50") -> pd.DataFrame:
    return df[df["term"] == term].set_index("date")


# -- parsing ------------------------------------------------------------------------------


def test_parse_reads_the_export_format_and_less_than_one() -> None:
    text = trends_file("MONQ50", 1, [12, "<1", 40, ""])
    parsed = parse_trends_csv(("﻿" + text).encode())
    assert parsed == {("MONQ50", "India"): {day(1): 12.0, day(2): 0.5, day(3): 40.0}}


def test_parse_several_terms_side_by_side() -> None:
    text = "Day,A: (India),B b: (India)\n2026-01-01,1,2\n2026-01-02,3,<1\n"
    parsed = parse_trends_csv(text.encode())
    assert parsed[("A", "India")] == {day(1): 1.0, day(2): 3.0}
    assert parsed[("B b", "India")] == {day(1): 2.0, day(2): 0.5}


@pytest.mark.parametrize(
    ("text", "needle"),
    [
        ("Week,A: (India)\n2026-01-04,5\n", "not 'Day'"),
        ("nothing useful here\n", "no header line"),
        ("Day,A\n2026-01-01,5\n", r"not 'term: \(region\)'"),
        ("Day,A: (India)\n01/01/2026,5\n", "not a date"),
        ("Day,A: (India)\n2026-01-01,lots\n", "not a number"),
        ("Day,A: (India)\n2026-01-01,5\n2026-01-01,6\n", "appears twice"),
    ],
)
def test_parse_errors(text: str, needle: str) -> None:
    with pytest.raises(ValueError, match=needle):
        parse_trends_csv(text.encode())


# -- chaining: hand-computed ------------------------------------------------------------
#
# Window 1, days 1-6:   10  20  40  50  30  20
# Window 2, days 4-9:               25  15  10  30  40  20   (shared days 4,5,6)
#   shared: chain 50+30+20 = 100, window 2: 25+15+10 = 50  -> factor 2
#   new days: day7 30*2=60, day8 40*2=80, day9 20*2=40
# Window 3, days 8-12:                              50  25   5  15  45   (shared days 8,9)
#   shared: chain 80+40 = 120, window 3: 50+25 = 75        -> factor 1.6
#   new days: day10 5*1.6=8, day11 15*1.6=24, day12 45*1.6=72
# Chain before rescaling: 10 20 40 50 30 20 60 80 40 8 24 72   (maximum 80)
# Rescaled so the maximum is 100 (x 1.25): 12.5 25 50 62.5 37.5 25 75 100 50 10 30 90
EXPECTED = [12.5, 25, 50, 62.5, 37.5, 25, 75, 100, 50, 10, 30, 90]


def write_three_windows(root: Path) -> None:
    put(root, "2026-10-01", "w1.csv", trends_file("MONQ50", 1, [10, 20, 40, 50, 30, 20]))
    put(root, "2026-10-02", "w2.csv", trends_file("MONQ50", 4, [25, 15, 10, 30, 40, 20]))
    put(root, "2026-10-03", "w3.csv", trends_file("MONQ50", 8, [50, 25, 5, 15, 45]))


def test_three_windows_chain_to_the_hand_computed_series(tmp_path: Path) -> None:
    write_three_windows(tmp_path)
    out = build(ctx_for(tmp_path))
    assert list(out.columns) == COLUMNS
    assert out["date"].tolist() == [day(n) for n in range(1, 13)]
    assert out["value"].tolist() == pytest.approx(EXPECTED)
    assert out["value"].max() == pytest.approx(100)
    # The number printed in the file that supplied each day, and how many windows saw it.
    assert out["window_value"].tolist() == [10, 20, 40, 50, 30, 20, 30, 40, 20, 5, 15, 45]
    assert out["n_windows"].tolist() == [1, 1, 1, 2, 2, 2, 1, 2, 2, 1, 1, 1]
    # Lineage: the first window that had the day.
    assert out["source_file"].tolist() == (
        ["trends/2026-10-01/w1.csv"] * 6
        + ["trends/2026-10-02/w2.csv"] * 3
        + ["trends/2026-10-03/w3.csv"] * 3
    )
    assert (out["term"] == "MONQ50").all() and (out["geo"] == "India").all()


def test_pull_order_does_not_change_the_chain_values(tmp_path: Path) -> None:
    # Same three windows, but pulled in the opposite order (windows 3, 2, 1 by pull date).
    put(tmp_path, "2026-10-03", "w1.csv", trends_file("MONQ50", 1, [10, 20, 40, 50, 30, 20]))
    put(tmp_path, "2026-10-02", "w2.csv", trends_file("MONQ50", 4, [25, 15, 10, 30, 40, 20]))
    put(tmp_path, "2026-10-01", "w3.csv", trends_file("MONQ50", 8, [50, 25, 5, 15, 45]))
    assert build(ctx_for(tmp_path))["value"].tolist() == pytest.approx(EXPECTED)


def test_a_window_on_another_scale_is_rescaled_to_match(tmp_path: Path) -> None:
    # Window 2 shows the same interest on a different scale (all numbers x 0.5, as Trends
    # rescales to its own busiest day). The chain must undo that, giving a straight line.
    put(tmp_path, "2026-10-01", "w1.csv", trends_file("MONQ50", 1, [20, 20, 20, 20]))
    put(tmp_path, "2026-10-02", "w2.csv", trends_file("MONQ50", 3, [10, 10, 10, 10]))
    out = build(ctx_for(tmp_path))
    assert out["value"].tolist() == pytest.approx([100.0] * 6)


def test_two_terms_in_one_file_are_chained_separately(tmp_path: Path) -> None:
    put(tmp_path, "2026-10-01", "a.csv", "Day,A: (India),B: (India)\n" + "\n".join(
        f"{day(n).isoformat()},{a},{b}" for n, a, b in [(1, 10, 100), (2, 20, 50), (3, 40, 25)]
    ) + "\n")  # fmt: skip
    put(tmp_path, "2026-10-02", "b.csv", "Day,A: (India),B: (India)\n" + "\n".join(
        f"{day(n).isoformat()},{a},{b}" for n, a, b in [(2, 10, 100), (3, 20, 50), (4, 30, 10)]
    ) + "\n")  # fmt: skip
    out = build(ctx_for(tmp_path))
    # A: shared days 2,3: chain 20+40=60, new 10+20=30 -> factor 2; day 4 = 60; max 60 -> x100/60.
    a = series(out, "A")["value"].tolist()
    assert a == pytest.approx([100 * v / 60 for v in (10, 20, 40, 60)])
    # B: shared days 2,3: chain 50+25=75, new 100+50=150 -> factor 0.5; day 4 = 5; max 100.
    b = series(out, "B")["value"].tolist()
    assert b == pytest.approx([100, 50, 25, 5])


def test_a_window_inside_the_chain_only_adds_to_the_count(tmp_path: Path) -> None:
    put(tmp_path, "2026-10-01", "w1.csv", trends_file("MONQ50", 1, [10, 20, 40, 50]))
    put(tmp_path, "2026-10-02", "w2.csv", trends_file("MONQ50", 2, [10, 20]))  # same, half scale
    out = build(ctx_for(tmp_path))
    assert out["n_windows"].tolist() == [1, 2, 2, 1]
    assert out["value"].tolist() == pytest.approx([20, 40, 80, 100])


# -- failures name the file --------------------------------------------------------------


def test_a_window_that_does_not_touch_the_chain_is_an_error(tmp_path: Path) -> None:
    put(tmp_path, "2026-10-01", "w1.csv", trends_file("MONQ50", 1, [10, 20, 40]))
    put(tmp_path, "2026-10-02", "gap.csv", trends_file("MONQ50", 10, [5, 6, 7]))
    with pytest.raises(CleanError) as err:
        build(ctx_for(tmp_path))
    assert "trends/2026-10-02/gap.csv" in str(err.value)
    assert "shares no day" in str(err.value)


def test_an_all_zero_overlap_cannot_be_scaled(tmp_path: Path) -> None:
    put(tmp_path, "2026-10-01", "w1.csv", trends_file("MONQ50", 1, [10, 0, 0]))
    put(tmp_path, "2026-10-02", "w2.csv", trends_file("MONQ50", 2, [0, 0, 7]))
    with pytest.raises(CleanError, match=r"trends/2026-10-02/w2\.csv.*cannot be scaled"):
        build(ctx_for(tmp_path))


def test_a_weekly_file_is_an_error_naming_the_file(tmp_path: Path) -> None:
    put(tmp_path, "2026-10-01", "weekly.csv", "Week,A: (India)\n2026-01-04,5\n")
    with pytest.raises(CleanError, match=r"trends/2026-10-01/weekly\.csv: .*not 'Day'"):
        build(ctx_for(tmp_path))


# -- the empty case, the store, registration ----------------------------------------------


def test_no_files_gives_an_empty_table(tmp_path: Path) -> None:
    out = build(ctx_for(tmp_path))
    assert out.empty and list(out.columns) == COLUMNS


def test_non_csv_files_in_the_folder_are_ignored(tmp_path: Path) -> None:
    put(tmp_path, "2026-10-01", "notes.txt", "pulled by hand")
    assert build(ctx_for(tmp_path)).empty


def test_table_is_registered_and_round_trips(tmp_path: Path) -> None:
    assert "trends_daily" in SCHEMAS
    assert SCHEMAS["trends_daily"].primary_key == ("term", "geo", "date")
    write_three_windows(tmp_path)
    store = Store(tmp_path / "clean")
    store.write_table("trends_daily", build(ctx_for(tmp_path)))
    back = store.read_table("trends_daily")
    assert len(back) == 12 and back["n_windows"].dtype == "Int64"
    assert back["value"].tolist() == pytest.approx(EXPECTED)
    store.write_table("trends_daily", build(ctx_for(tmp_path / "none")))  # empty accepted
    order = [t for t, _ in CLEANERS]
    assert order.index("trends_daily") > order.index("etf_master")
