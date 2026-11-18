"""Tests for the SPEC 12 data-quality flags. Each case says why the answer is what it is."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from etfp.features import quality as q


def flags(s: pd.Series) -> list[object]:
    return [None if pd.isna(v) else bool(v) for v in s]


def test_duplicates_mark_every_copy() -> None:
    df = pd.DataFrame(
        {
            "symbol": ["A", "A", "A", "B"],
            "date": [date(2026, 9, 1), date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 1)],
        }
    )
    assert flags(q.flag_duplicates(df)) == [True, True, False, False]
    with pytest.raises(ValueError):
        q.flag_duplicates(df.drop(columns=["date"]))


def test_extreme_premium_threshold_is_strict_and_two_sided() -> None:
    s = pd.Series([3.5, -3.5, 3.0, 2.99, np.nan])
    assert flags(q.flag_extreme_premium(s)) == [True, True, False, False, None]
    # 235% (the highest the press reported) is not extreme at the 300% threshold.
    assert flags(q.flag_extreme_premium(pd.Series([2.35]))) == [False]
    assert flags(q.flag_extreme_premium(pd.Series([2.35]), threshold=2.0)) == [True]


def test_premium_jump_per_symbol_and_in_date_order() -> None:
    # A: 0.30 -> 0.95 (jump 0.65 > 0.5), then 0.95 -> 0.60 (0.35). B starts at 0.10 -> not compared
    # with A's last value. Rows are shuffled: order must come from the date column.
    df = pd.DataFrame(
        {
            "symbol": ["A", "A", "A", "B", "B"],
            "date": [
                date(2026, 9, 1),
                date(2026, 9, 2),
                date(2026, 9, 3),
                date(2026, 9, 1),
                date(2026, 9, 2),
            ],
            "prem_fv": [0.30, 0.95, 0.60, 0.10, 0.60],
        }
    )
    shuffled = df.sample(frac=1, random_state=5)
    out = q.flag_premium_jump(shuffled)
    assert list(out.index) == list(shuffled.index)
    by_key = {(r.symbol, r.date): flags(out)[i] for i, r in enumerate(shuffled.itertuples())}
    assert by_key[("A", date(2026, 9, 1))] is None  # first row of the symbol
    assert by_key[("A", date(2026, 9, 2))] is True
    assert by_key[("A", date(2026, 9, 3))] is False
    assert by_key[("B", date(2026, 9, 1))] is None
    assert by_key[("B", date(2026, 9, 2))] is False  # change is exactly 0.50, not more than 0.50


def test_fv_far_from_nav_with_and_without_market_move() -> None:
    fv = pd.Series([107.0, 107.0, 104.0, 107.0, np.nan])
    nav = pd.Series([100.0] * 5)
    move = pd.Series([0.01, 0.08, 0.01, np.nan, 0.01])
    # 1: gap 7% > 5%, futures moved 1% -> flag. 2: same gap but futures moved 8% -> no flag.
    # 3: gap 4% -> fine. 4: move unknown -> no excuse -> flag. 5: FV missing -> null.
    assert flags(q.flag_fv_far_from_nav(fv, nav, move)) == [True, False, False, True, None]
    # Without any move information the gap alone decides.
    assert flags(q.flag_fv_far_from_nav(fv, nav))[:2] == [True, True]


def test_negative_units() -> None:
    assert flags(q.flag_negative_units(pd.Series([5, 0, -1, np.nan]))) == [False, False, True, None]


def test_missing_values_and_expected_mask() -> None:
    close = pd.Series([10.0, np.nan, 12.0, np.nan])
    assert flags(q.flag_missing(close)) == [False, True, False, True]
    expected = pd.Series([True, True, True, False])
    assert flags(q.flag_missing(close, expected=expected)) == [False, True, False, False]


def test_nav_stale_and_lookahead() -> None:
    lag = pd.Series([1, 3, 5, 6, 8, -1, np.nan])
    assert flags(q.flag_nav_stale(lag)) == [False, False, False, True, True, True, None]


def test_add_quality_flags_adds_all_columns_and_keeps_rows() -> None:
    df = pd.DataFrame(
        {
            "symbol": ["A", "A", "A"],
            "date": [date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 2)],
            "close": [100.0, np.nan, 100.0],
            "nav_known_at_close": [100.0, 100.0, 100.0],
            "fv": [100.0, 108.0, 100.0],
            "prem_fv": [0.0, 3.2, 0.0],
            "units_outstanding": [10, -4, 10],
            "nav_publication_lag_days": [1, 9, 1],
            "fv_in_futures_t": [101.0, 101.0, 101.0],
            "fv_in_futures_a": [100.0, 100.0, 100.0],
        }
    )
    out = q.add_quality_flags(df)
    assert len(out) == 3 and list(out.index) == list(df.index)
    assert set(q.DQ_COLUMNS) <= set(out.columns)
    assert flags(out["dq_duplicate_row"]) == [False, True, True]
    assert flags(out["dq_prem_fv_extreme"]) == [False, True, False]
    assert flags(out["dq_missing_close"]) == [False, True, False]
    assert flags(out["dq_negative_units"]) == [False, True, False]
    assert flags(out["dq_nav_stale"]) == [False, True, False]
    # futures moved +1% so the 8% FV gap on row 2 is not excused.
    assert flags(out["dq_fv_far_from_nav"]) == [False, True, False]
    summary = q.dq_summary(out)
    assert summary["dq_duplicate_row"] == 2 and summary["dq_negative_units"] == 1


def test_add_quality_flags_with_absent_columns_gives_null_flags() -> None:
    out = q.add_quality_flags(pd.DataFrame({"x": [1, 2]}))
    assert out["dq_negative_units"].isna().all() and out["dq_prem_fv_jump"].isna().all()


# ---------------------------------------------------------------------------
# D53: duplicates stop the build, NAV alignment against calendars, gaps, summary
# ---------------------------------------------------------------------------
def test_require_no_duplicates_raises_and_names_the_key() -> None:
    ok = pd.DataFrame({"symbol": ["A", "B"], "date": [date(2026, 9, 1)] * 2})
    q.require_no_duplicates(ok)  # no error
    bad = pd.DataFrame({"symbol": ["A", "A", "B"], "date": [date(2026, 9, 1)] * 3})
    with pytest.raises(q.DuplicateRowsError, match="A"):
        q.require_no_duplicates(bad)


def test_expected_underlying_session_by_hand() -> None:
    # US: Tue 8 Sep 2026 -> Fri 4 Sep (Labor Day Mon 7 Sep; the 8 Sep close is ~01:30 IST on
    # the 9th, after India closed). Wed 9 Sep -> 8 Sep.
    assert q.expected_underlying_session("US", date(2026, 9, 8)) == date(2026, 9, 4)
    assert q.expected_underlying_session("US", date(2026, 9, 9)) == date(2026, 9, 8)
    # Mon 7 Sep (US holiday, India open): Friday's close.
    assert q.expected_underlying_session("US", date(2026, 9, 7)) == date(2026, 9, 4)
    # Hong Kong closes 13:30 IST, before India: the same day. India itself: the same day.
    assert q.expected_underlying_session("HK", date(2026, 9, 9)) == date(2026, 9, 9)
    assert q.expected_underlying_session("IN", date(2026, 9, 9)) == date(2026, 9, 9)


def _align_frame(rows: list[tuple[str, date, date | None]]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": [r[0] for r in rows],
            "date": [r[1] for r in rows],
            "nav_known_underlying_date": [r[2] for r in rows],
            "nav_known_date": [r[2] for r in rows],
        }
    )


def test_nav_misaligned_flags_look_ahead_only_by_default() -> None:
    d = date(2026, 9, 8)  # Tue; expected US underlying session 4 Sep
    df = _align_frame(
        [
            ("US1", d, date(2026, 9, 4)),  # exactly right -> False
            ("US1", d, date(2026, 9, 8)),  # that close happens after India closes -> True
            ("US1", d, date(2026, 9, 3)),  # a session behind -> False by default
            ("US1", d, None),  # no NAV used -> NA
            ("XX", d, date(2026, 9, 4)),  # unknown market -> NA
        ]
    )
    mk = {"US1": "US"}
    assert flags(q.flag_nav_misaligned(df, mk)) == [False, True, False, None, None]
    assert flags(q.flag_nav_misaligned(df, mk, include_behind=True)) == [
        False,
        True,
        True,
        None,
        None,
    ]


def test_nav_misaligned_flags_nav_dated_after_the_day() -> None:
    df = _align_frame([("US1", date(2026, 9, 8), date(2026, 9, 4))])
    df["nav_known_date"] = date(2026, 9, 9)  # a NAV dated tomorrow: look-ahead
    assert flags(q.flag_nav_misaligned(df, {"US1": "US"})) == [True]


def test_session_gaps_lists_missing_nse_sessions() -> None:
    # Tue 1, Wed 2 and Fri 4 Sep 2026 present; Thu 3 Sep is an NSE session with no row.
    df = pd.DataFrame(
        {"symbol": ["A", "A", "A", "B"], "date": [date(2026, 9, d) for d in (1, 2, 4, 1)]}
    )
    assert q.session_gaps(df) == {"A": [date(2026, 9, 3)]}


def test_build_summary_counts_every_dq_column(caplog: pytest.LogCaptureFixture) -> None:
    df = pd.DataFrame(
        {
            "symbol": ["A", "A"],
            "date": [date(2026, 9, 1), date(2026, 9, 3)],  # 2 Sep missing
            "dq_prem_fv_jump": pd.array([True, False], dtype="boolean"),
            "dq_new_future_flag": pd.array([pd.NA, True], dtype="boolean"),
        }
    )
    with caplog.at_level("WARNING"):
        out = q.log_build_summary(df)
    assert out["dq_counts"] == {"dq_prem_fv_jump": 1, "dq_new_future_flag": 1}
    assert out["session_gaps"] == {"A": 1}
    assert "dq_prem_fv_jump" in caplog.text and "no row on 1 NSE sessions" in caplog.text


def test_add_quality_flags_appends_nav_misaligned_when_markets_given() -> None:
    df = _align_frame([("US1", date(2026, 9, 8), date(2026, 9, 8))])
    assert flags(q.add_quality_flags(df, markets={"US1": "US"})["dq_nav_misaligned"]) == [True]
    assert flags(q.add_quality_flags(df)["dq_nav_misaligned"]) == [None]  # no markets: unchecked


def test_every_quality_column_is_documented_for_the_export() -> None:
    from etfp.export import ETF_DAY_COLUMNS

    assert set(q.DQ_COLUMNS) <= set(ETF_DAY_COLUMNS)
