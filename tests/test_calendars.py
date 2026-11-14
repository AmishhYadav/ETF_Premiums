"""Tests for etfp.calendars (trading days, close times, NSE overrides)."""

from __future__ import annotations

from datetime import date, time
from pathlib import Path

import pytest

from etfp import calendars as cal
from etfp.timeutil import IST, NEW_YORK


@pytest.fixture(autouse=True)
def _fresh_caches(monkeypatch: pytest.MonkeyPatch):
    """Each test starts from the real yaml and a clean cache."""
    monkeypatch.delenv(cal.NSE_HOLIDAYS_ENV, raising=False)
    cal.clear_caches(libraries=False)
    yield
    cal.clear_caches(libraries=False)


def use_yaml(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, text: str) -> None:
    """Point the calendar module at a temporary overrides file."""
    path = tmp_path / "nse_holidays.yaml"
    path.write_text(text, encoding="utf-8")
    monkeypatch.setenv(cal.NSE_HOLIDAYS_ENV, str(path))
    cal.clear_caches(libraries=False)


# ---- verified facts ------------------------------------------------------
def test_diwali_balipratipada_is_not_an_nse_session():
    assert cal.is_session("IN", date(2026, 11, 10)) is False
    assert cal.is_nse_session(date(2026, 11, 10)) is False


def test_labor_day_2026_india_open_us_closed():
    assert cal.is_session("IN", date(2026, 9, 7)) is True
    assert cal.is_session("US", date(2026, 9, 7)) is False


def test_nse_session_count_2021_to_2026_11_09():
    # Measured with exchange_calendars 4.13.2; guards against silent calendar drift.
    # 1441 library sessions + 6 Diwali Muhurat sessions (2021-2026) + 3 special Saturday
    # sessions in 2024 (20 Jan, 2 Mar, 18 May) from the overrides.
    assert len(cal.sessions("IN", date(2021, 1, 1), date(2026, 11, 9))) == 1450


def test_library_upper_bound_is_read_from_library():
    assert cal.library_end("IN") == date(2026, 12, 31)
    assert cal.library_end("US") > date(2027, 6, 30)
    assert cal.library_end("HK") > date(2027, 6, 30)


def test_sessions_inclusive_and_ascending():
    days = cal.sessions("IN", date(2026, 11, 6), date(2026, 11, 11))
    # Sun 8 Nov 2026 was the Diwali Muhurat session; Tue 10 Nov a holiday.
    assert days == [date(2026, 11, 6), date(2026, 11, 8), date(2026, 11, 9), date(2026, 11, 11)]


# ---- previous / next -----------------------------------------------------
def test_previous_and_next_across_weekend():
    friday, monday = date(2026, 10, 30), date(2026, 11, 2)
    assert cal.previous_session("IN", monday) == friday
    assert cal.next_session("IN", friday) == monday
    # works from a non-session day too
    assert cal.previous_session("IN", date(2026, 11, 1)) == friday
    assert cal.next_session("IN", date(2026, 10, 31)) == monday


def test_muhurat_sunday_is_a_session():
    # NSE's 9 Nov 2026 PREV_CLOSE and the band in the 8 Nov security master are
    # based on the Sunday 8 Nov Muhurat close (DECISIONS D24).
    assert cal.is_session("IN", date(2026, 11, 8))
    assert cal.previous_session("IN", date(2026, 11, 9)) == date(2026, 11, 8)
    assert cal.next_session("IN", date(2026, 11, 6)) == date(2026, 11, 8)
    # Band for Mon 4 Nov 2024 = 1.2 x NAV(31 Oct): T-2 counted Muhurat Fri 1 Nov.
    t = date(2024, 11, 4)
    assert cal.previous_session("IN", cal.previous_session("IN", t)) == date(2024, 10, 31)


def test_previous_and_next_across_diwali_holiday():
    assert cal.next_session("IN", date(2026, 11, 9)) == date(2026, 11, 11)
    assert cal.previous_session("IN", date(2026, 11, 11)) == date(2026, 11, 9)
    assert cal.next_session("IN", date(2026, 11, 10)) == date(2026, 11, 11)


def test_previous_and_next_are_strict():
    d = date(2026, 11, 9)
    assert cal.previous_session("IN", d) < d < cal.next_session("IN", d)


def test_last_session_on_or_before():
    assert cal.last_session_on_or_before("US", date(2026, 9, 7)) == date(2026, 9, 4)
    assert cal.last_session_on_or_before("US", date(2026, 9, 4)) == date(2026, 9, 4)
    assert cal.last_session_on_or_before("IN", date(2026, 11, 10)) == date(2026, 11, 9)


def test_t_minus_2_example():
    # T-2 for the price-band base: two Indian trading days before 2026-11-11.
    t = date(2026, 11, 11)
    # 11 Nov -> 9 Nov -> Sun 8 Nov (Muhurat session; 10 Nov was a holiday).
    assert cal.previous_session("IN", cal.previous_session("IN", t)) == date(2026, 11, 8)


# ---- open / close times --------------------------------------------------
def test_market_close_india_is_1530_ist_and_aware():
    close = cal.market_close("IN", date(2026, 11, 9))
    assert close.tzinfo is not None
    assert close.utcoffset() is not None
    assert close.astimezone(IST).time() == time(15, 30)
    assert close.date() == date(2026, 11, 9)


def test_market_open_india_is_0915_ist():
    assert cal.market_open("IN", date(2026, 11, 9)).astimezone(IST).time() == time(9, 15)


def test_market_close_non_session_raises():
    with pytest.raises(ValueError, match="not a IN trading session"):
        cal.market_close("IN", date(2026, 11, 10))
    with pytest.raises(ValueError):
        cal.market_open("IN", date(2026, 11, 1))  # Sunday


def test_us_regular_and_early_close_in_new_york_time():
    regular = cal.market_close("US", date(2026, 11, 25))
    assert regular.astimezone(NEW_YORK).time() == time(16, 0)
    # Day after Thanksgiving is an early close (library lists 2025-11-28).
    early = cal.market_close("US", date(2025, 11, 28))
    assert early.astimezone(NEW_YORK).time() < time(16, 0)
    assert early.astimezone(NEW_YORK).time() == time(13, 0)


def test_hong_kong_close_is_timezone_aware():
    close = cal.market_close("HK", date(2026, 11, 9))
    assert close.utcoffset() is not None
    assert close.tzinfo is not None


# ---- beyond the library's range (India 2027+) ----------------------------
def test_2027_weekday_is_session_and_provisional():
    d = date(2027, 5, 12)  # a Wednesday
    assert cal.is_session("IN", d) is True
    assert cal.is_provisional("IN", d) is True


def test_2027_saturday_is_not_a_session():
    d = date(2027, 5, 15)
    assert d.weekday() == 5
    assert cal.is_session("IN", d) is False


def test_provisional_false_inside_library_range_and_other_markets():
    assert cal.is_provisional("IN", date(2026, 11, 9)) is False
    assert cal.is_provisional("US", date(2027, 5, 12)) is False
    assert cal.is_provisional("HK", date(2027, 5, 12)) is False


def test_2027_close_time_falls_back_to_regular_hours():
    close = cal.market_close("IN", date(2027, 5, 12))
    assert close.astimezone(IST).time() == time(15, 30)
    assert cal.market_open("IN", date(2027, 5, 12)).astimezone(IST).time() == time(9, 15)


def test_2027_holiday_list_from_yaml(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    use_yaml(
        monkeypatch,
        tmp_path,
        "holidays:\n  2027:\n    - 2027-05-12\n    - {date: 2027-05-13, reason: example}\n",
    )
    assert cal.is_session("IN", date(2027, 5, 12)) is False
    assert cal.is_session("IN", date(2027, 5, 13)) is False
    assert cal.is_session("IN", date(2027, 5, 14)) is True
    assert cal.is_provisional("IN", date(2027, 5, 12)) is False
    assert cal.is_provisional("IN", date(2027, 5, 14)) is False
    # 2028 has no list: still provisional
    assert cal.is_provisional("IN", date(2028, 5, 10)) is True
    assert cal.next_session("IN", date(2027, 5, 11)) == date(2027, 5, 14)


# ---- overrides inside the library's range --------------------------------
def test_extra_holiday_removes_a_session(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    assert cal.is_session("IN", date(2026, 11, 9)) is True
    use_yaml(
        monkeypatch,
        tmp_path,
        "extra_holidays:\n  - {date: 2026-11-09, reason: test closure}\n",
    )
    assert cal.is_session("IN", date(2026, 11, 9)) is False
    assert cal.next_session("IN", date(2026, 11, 6)) == date(2026, 11, 11)
    with pytest.raises(ValueError):
        cal.market_close("IN", date(2026, 11, 9))


def test_extra_session_adds_a_saturday(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    saturday = date(2026, 11, 7)
    assert cal.is_session("IN", saturday) is False
    use_yaml(
        monkeypatch,
        tmp_path,
        "extra_sessions:\n  - {date: 2026-11-07, reason: test special session}\n",
    )
    assert cal.is_session("IN", saturday) is True
    assert cal.previous_session("IN", date(2026, 11, 9)) == saturday
    assert cal.market_close("IN", saturday).astimezone(IST).time() == time(15, 30)


def test_override_without_reason_is_rejected(tmp_path: Path):
    path = tmp_path / "bad.yaml"
    path.write_text("extra_holidays:\n  - {date: 2026-11-09}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="reason"):
        cal.load_nse_overrides(path)


def test_override_unknown_key_is_rejected(tmp_path: Path):
    path = tmp_path / "bad.yaml"
    path.write_text("extra_holidayz: []\n", encoding="utf-8")
    with pytest.raises(ValueError):
        cal.load_nse_overrides(path)


def test_holiday_listed_under_wrong_year_is_rejected(tmp_path: Path):
    path = tmp_path / "bad.yaml"
    path.write_text("holidays:\n  2027:\n    - 2028-01-26\n", encoding="utf-8")
    with pytest.raises(ValueError, match="listed under year"):
        cal.load_nse_overrides(path)


def test_real_overrides_file_loads_with_muhurat_sessions():
    ov = cal.load_nse_overrides()
    assert ov.extra_holidays == ()
    assert [s.date for s in ov.extra_sessions] == [
        date(2021, 11, 4),
        date(2022, 10, 24),
        date(2023, 11, 12),
        date(2024, 1, 20),
        date(2024, 3, 2),
        date(2024, 5, 18),
        date(2024, 11, 1),
        date(2025, 10, 21),
        date(2026, 11, 8),
    ]
    assert all(("Muhurat" in s.reason) or ("Saturday" in s.reason) for s in ov.extra_sessions)
    assert 2027 not in ov.holidays  # must be filled from the NSE circular (VERIFY)


# ---- range checks --------------------------------------------------------
@pytest.mark.parametrize("bad", [date(2020, 12, 31), date(2031, 1, 1), date(2206, 1, 1)])
def test_out_of_range_dates_raise(bad: date):
    with pytest.raises(ValueError):
        cal.is_session("IN", bad)
    with pytest.raises(ValueError):
        cal.previous_session("US", bad)
    with pytest.raises(ValueError):
        cal.market_close("HK", bad)


def test_no_session_before_sample_start():
    with pytest.raises(ValueError, match="no IN session"):
        cal.previous_session("IN", date(2021, 1, 1))


def test_unknown_market_raises():
    with pytest.raises(ValueError, match="unknown market"):
        cal.is_session("JP", date(2026, 1, 5))  # type: ignore[arg-type]
