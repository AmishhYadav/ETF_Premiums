"""Tests for NAV alignment. A small hand-built calendar around US Labor Day (7 Sep 2026).

Hand-built calendar (all Mon-Fri; times in each market's own zone):

    India   (NSE)  : open every weekday below, close 15:30 IST
    US      (NYSE) : open every weekday EXCEPT Mon 7 Sep (Labor Day), close 16:00 New York
    Hong Kong      : open every weekday, close 16:00 Hong Kong

    Days: Fri 28 Aug, then Mon 31 Aug, Tue 1, Wed 2, Thu 3, Fri 4, Mon 7, Tue 8, Wed 9,
    Thu 10 and Fri 11 Sep.

Clock facts used below (September 2026, New York on summer time, UTC-4):
    US close 16:00 EDT = 20:00 UTC = 01:30 IST on the NEXT calendar day.
    India close 15:30 IST = 10:00 UTC.
    HK close 16:00 HKT = 08:00 UTC = 13:30 IST on the same day.
So the US close of date d happens BEFORE the Indian close of d+1 and AFTER the Indian
close of d.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from datetime import date, datetime, time

import pandas as pd
import pytest

from etfp import calendars
from etfp.features import nav_align as na
from etfp.timeutil import HONG_KONG, IST, NEW_YORK

WEEKDAYS = [
    date(2026, 8, 28),
    date(2026, 8, 31),
    date(2026, 9, 1),
    date(2026, 9, 2),
    date(2026, 9, 3),
    date(2026, 9, 4),
    date(2026, 9, 7),
    date(2026, 9, 8),
    date(2026, 9, 9),
    date(2026, 9, 10),
    date(2026, 9, 11),
]
LABOR_DAY = date(2026, 9, 7)


class FakeCalendars:
    """Just enough of etfp.calendars for nav_align, with the sessions listed above."""

    def __init__(self, hk_holidays: set[date] | None = None) -> None:
        hk_off = hk_holidays or set()
        self.sessions = {
            "IN": list(WEEKDAYS),
            "US": [d for d in WEEKDAYS if d != LABOR_DAY],
            "HK": [d for d in WEEKDAYS if d not in hk_off],
        }
        self.zone = {"IN": IST, "US": NEW_YORK, "HK": HONG_KONG}
        self.close = {"IN": time(15, 30), "US": time(16, 0), "HK": time(16, 0)}

    def previous_session(self, market: str, day: date) -> date:
        s = self.sessions[market]
        i = bisect_left(s, day)
        if i == 0:
            raise ValueError("no earlier session")
        return s[i - 1]

    def last_session_on_or_before(self, market: str, day: date) -> date:
        s = self.sessions[market]
        i = bisect_right(s, day)
        if i == 0:
            raise ValueError("no session on or before")
        return s[i - 1]

    def market_close(self, market: str, day: date) -> datetime:
        assert day in self.sessions[market], f"{day} is not a {market} session"
        return datetime.combine(day, self.close[market], tzinfo=self.zone[market])


# NAV dated each weekday. 7 Sep is carried forward over the US holiday (103.99 = 4 Sep
# NAV 104.00 less a day of expenses). Nothing published yet for Fri 11 Sep (evening of t).
NAV = {
    date(2026, 8, 28): 99.00,
    date(2026, 8, 31): 100.00,
    date(2026, 9, 1): 101.00,
    date(2026, 9, 2): 102.00,
    date(2026, 9, 3): 103.00,
    date(2026, 9, 4): 104.00,
    date(2026, 9, 7): 103.99,
    date(2026, 9, 8): 105.00,
    date(2026, 9, 9): 106.00,
    date(2026, 9, 10): 107.00,
}


def nav_frame(values: dict[date, float] = NAV) -> pd.DataFrame:
    return pd.DataFrame({"nav_date": list(values), "nav": list(values.values())})


def row(out: pd.DataFrame, day: date) -> pd.Series:
    return out[out["date"] == day].iloc[0]


@pytest.fixture(scope="module")
def us() -> pd.DataFrame:
    days = WEEKDAYS[1:]  # Mon 31 Aug .. Fri 11 Sep
    return na.align_nav(nav_frame(), days, "US", cal=FakeCalendars())


def test_us_normal_day_uses_previous_day_nav(us: pd.DataFrame) -> None:
    # Thu 3 Sep: NAV dated 3 Sep embeds the US close of 3 Sep = Fri 4 Sep 01:30 IST, AFTER
    # the Indian close of Thu 3 Sep. So the NAV known at the close is the one dated 2 Sep.
    r = row(us, date(2026, 9, 3))
    assert r.nav_same_date == 103.00
    assert r.nav_known_date == date(2026, 9, 2) and r.nav_known_at_close == 102.00
    assert r.nav_known_underlying_date == date(2026, 9, 2)
    assert r.nav_publication_lag_days == 1


def test_us_monday_uses_friday_nav(us: pd.DataFrame) -> None:
    # Mon 31 Aug: NAV dated Fri 28 Aug embeds the US close of Fri (Sat 01:30 IST), before the
    # Monday Indian close -> known; lag is 3 calendar days.
    r = row(us, date(2026, 8, 31))
    assert r.nav_known_date == date(2026, 8, 28) and r.nav_known_at_close == 99.00
    assert r.nav_publication_lag_days == 3
    assert r.nav_same_date == 100.00


def test_us_holiday_monday_nav_is_carried_forward(us: pd.DataFrame) -> None:
    # Mon 7 Sep (Labor Day, no US session): NAV dated 7 Sep (103.99) embeds the Fri 4 Sep close
    # (Sat 01:30 IST), long before Monday 15:30 IST, so it IS known at the close even though it
    # is dated the same day: known date = 7 Sep, lag 0, and the underlying date is 4 Sep.
    r = row(us, LABOR_DAY)
    assert r.nav_same_date == 103.99
    assert r.nav_known_date == LABOR_DAY and r.nav_known_at_close == 103.99
    assert r.nav_known_underlying_date == date(2026, 9, 4)
    assert r.nav_publication_lag_days == 0


def test_us_day_after_holiday_still_uses_friday_close(us: pd.DataFrame) -> None:
    # Tue 8 Sep: NAV dated 8 Sep embeds the 8 Sep US close (Wed 01:30 IST): too late. The
    # freshest known is the carried-forward 7 Sep NAV, still built on the 4 Sep close.
    r = row(us, date(2026, 9, 8))
    assert r.nav_same_date == 105.00
    assert r.nav_known_date == LABOR_DAY and r.nav_known_at_close == 103.99
    assert r.nav_known_underlying_date == date(2026, 9, 4)
    assert r.nav_publication_lag_days == 1
    # Wed 9 Sep is back to a normal t-1: the 8 Sep NAV (US close of 8 Sep).
    r = row(us, date(2026, 9, 9))
    assert r.nav_known_date == date(2026, 9, 8) and r.nav_known_at_close == 105.00


def test_us_friday_before_holiday(us: pd.DataFrame) -> None:
    # Fri 4 Sep: NAV known is the 3 Sep one (US close of 3 Sep = Fri 01:30 IST).
    r = row(us, date(2026, 9, 4))
    assert r.nav_known_date == date(2026, 9, 3) and r.nav_known_at_close == 103.00


def test_nav_t2_counts_indian_trading_days(us: pd.DataFrame) -> None:
    # t2 = two INDIAN sessions earlier, NAV looked up by its own date.
    # Fri 4 Sep -> Wed 2 Sep (102.00). Mon 7 Sep -> Thu 3 Sep (103.00). Tue 8 Sep -> Fri 4 Sep.
    # Wed 9 Sep -> Mon 7 Sep (carried-forward 103.99), even though the US was shut that day.
    assert row(us, date(2026, 9, 4)).nav_t2_date == date(2026, 9, 2)
    assert row(us, date(2026, 9, 4)).nav_t2 == 102.00
    assert row(us, LABOR_DAY).nav_t2 == 103.00
    assert row(us, date(2026, 9, 8)).nav_t2 == 104.00
    assert row(us, date(2026, 9, 9)).nav_t2_date == LABOR_DAY
    assert row(us, date(2026, 9, 9)).nav_t2 == 103.99


def test_missing_same_date_nav_is_nan_but_known_nav_exists(us: pd.DataFrame) -> None:
    # Fri 11 Sep, evening of t: NAV dated 11 Sep not published yet. Known = 10 Sep (107.00).
    r = row(us, date(2026, 9, 11))
    assert pd.isna(r.nav_same_date)
    assert r.nav_known_date == date(2026, 9, 10) and r.nav_known_at_close == 107.00


def test_missing_nav_is_not_forward_filled() -> None:
    # Remove the 8 Sep NAV: on Wed 9 Sep the known NAV falls back to 7 Sep (carried forward)
    # because 8 Sep does not exist; nothing is invented.
    nav = {d: v for d, v in NAV.items() if d != date(2026, 9, 8)}
    out = na.align_nav(nav_frame(nav), WEEKDAYS[1:], "US", cal=FakeCalendars())
    r = row(out, date(2026, 9, 9))
    assert r.nav_known_date == LABOR_DAY and r.nav_known_at_close == 103.99
    assert r.nav_publication_lag_days == 2
    assert pd.isna(row(out, date(2026, 9, 8)).nav_same_date)


def test_hk_nav_is_known_same_day() -> None:
    # HK closes 16:00 HKT = 13:30 IST, before the Indian close, so NAV dated t is known at 15:30 IST
    # on t, also on Monday 7 Sep (HK is open on US Labor Day).
    out = na.align_nav(nav_frame(), WEEKDAYS[1:], "HK", cal=FakeCalendars())
    for d in (date(2026, 8, 31), date(2026, 9, 3), LABOR_DAY, date(2026, 9, 10)):
        r = row(out, d)
        assert r.nav_known_date == d and r.nav_known_at_close == NAV[d]
        assert r.nav_publication_lag_days == 0
        assert r.nav_known_underlying_date == d
    # The not-yet-published 11 Sep NAV: known falls back to 10 Sep (lag 1).
    r = row(out, date(2026, 9, 11))
    assert r.nav_known_date == date(2026, 9, 10) and r.nav_publication_lag_days == 1


def test_hk_holiday_carries_forward_prior_close() -> None:
    # HK shut Wed 9 Sep. NAV dated 9 Sep embeds HK's 8 Sep close (13:30 IST on the 8th), so it is
    # known on 9 Sep and its underlying date is 8 Sep.
    out = na.align_nav(
        nav_frame(), WEEKDAYS[1:], "HK", cal=FakeCalendars(hk_holidays={date(2026, 9, 9)})
    )
    r = row(out, date(2026, 9, 9))
    assert r.nav_known_date == date(2026, 9, 9)
    assert r.nav_known_underlying_date == date(2026, 9, 8)


def test_domestic_etf_same_date_nav_counts_as_known() -> None:
    # Underlying closes at the same 15:30 as the Indian market: "<=" makes NAV(t) known.
    out = na.align_nav(nav_frame(), WEEKDAYS[1:], "IN", cal=FakeCalendars())
    r = row(out, date(2026, 9, 3))
    assert r.nav_known_date == date(2026, 9, 3) and r.nav_known_at_close == 103.00


def test_strike_lag_sessions_shifts_the_rule() -> None:
    # If NAV dated d were struck on the US close BEFORE d's own (older rule), then on Wed 9 Sep the
    # NAV dated 9 Sep embeds the 8 Sep close (Wed 01:30 IST) and is already known at 15:30 IST.
    out = na.align_nav(nav_frame(), WEEKDAYS[1:], "US", strike_lag_sessions=1, cal=FakeCalendars())
    r = row(out, date(2026, 9, 9))
    assert r.nav_known_date == date(2026, 9, 9) and r.nav_known_at_close == 106.00
    assert r.nav_known_underlying_date == date(2026, 9, 8)
    # Tue 8 Sep: NAV dated 8 Sep embeds the previous US session = Fri 4 Sep: known. (7 Sep also
    # embeds the session before 4 Sep = 3 Sep.) Latest known date is 8 Sep.
    r = row(out, date(2026, 9, 8))
    assert r.nav_known_date == date(2026, 9, 8) and r.nav_known_underlying_date == date(2026, 9, 4)


def test_input_validation() -> None:
    cal = FakeCalendars()
    with pytest.raises(ValueError, match="duplicate NAV dates"):
        dup = pd.DataFrame({"nav_date": [date(2026, 9, 1)] * 2, "nav": [1.0, 2.0]})
        na.align_nav(dup, WEEKDAYS[1:], "US", cal=cal)
    with pytest.raises(ValueError, match="non-positive"):
        bad = pd.DataFrame({"nav_date": [date(2026, 9, 1)], "nav": [0.0]})
        na.align_nav(bad, WEEKDAYS[1:], "US", cal=cal)
    with pytest.raises(ValueError, match="ascending and unique"):
        na.align_nav(nav_frame(), [WEEKDAYS[2], WEEKDAYS[1]], "US", cal=cal)
    with pytest.raises(ValueError, match="needs column"):
        na.align_nav(pd.DataFrame({"d": [1]}), WEEKDAYS[1:], "US", cal=cal)


def test_no_nav_known_before_first_nav() -> None:
    out = na.align_nav(
        nav_frame({date(2026, 9, 10): 107.0}), WEEKDAYS[1:], "US", cal=FakeCalendars()
    )
    r = row(out, date(2026, 9, 3))
    assert pd.isna(r.nav_known_at_close) and r.nav_known_date is None
    assert pd.isna(r.nav_publication_lag_days) and pd.isna(r.nav_t2)


# --------------------------------------------------------------------------
# Same checks against the REAL calendars (sanity: library holidays agree with the hand-built week)
# --------------------------------------------------------------------------
def test_real_calendars_labor_day_week() -> None:
    nav = {
        d: v
        for d, v in NAV.items()
        if d >= date(2026, 8, 31)  # start inside the calendar range
    }
    india = calendars.sessions("IN", date(2026, 8, 31), date(2026, 9, 11))
    assert LABOR_DAY in india and not calendars.is_session("US", LABOR_DAY)
    out = na.align_nav(nav_frame(nav), india, "US")
    assert row(out, date(2026, 9, 4)).nav_known_date == date(2026, 9, 3)
    assert row(out, LABOR_DAY).nav_known_date == LABOR_DAY
    assert row(out, LABOR_DAY).nav_known_underlying_date == date(2026, 9, 4)
    assert row(out, date(2026, 9, 8)).nav_known_date == LABOR_DAY
    hk = na.align_nav(nav_frame(nav), india, "HK")
    assert (hk["nav_known_date"].iloc[:5] == hk["date"].iloc[:5]).all()


def test_nav_t2_is_null_when_the_calendar_runs_out() -> None:
    # The hand-built India calendar starts on 28 Aug: Mon 31 Aug has T-1 = 28 Aug but no T-2.
    out = na.align_nav(nav_frame(), WEEKDAYS[1:], "US", cal=FakeCalendars())
    r = row(out, date(2026, 8, 31))
    assert r.nav_t2_date is None and pd.isna(r.nav_t2)


def test_nav_t2_falls_back_when_fund_skips_us_holiday() -> None:
    # Mirae publishes no NAV dated Mon 7 Sep 2026 (US Labor Day, D22).
    # Wed 9 Sep: t2 session = Mon 7 Sep -> no NAV that date -> use Fri 4 Sep (104.00).
    nav = pd.DataFrame(
        {
            "nav_date": [date(2026, 9, 3), date(2026, 9, 4), date(2026, 9, 8), date(2026, 9, 9)],
            "nav": [103.00, 104.00, 105.00, 106.00],
        }
    )
    days = [date(2026, 9, 7), date(2026, 9, 8), date(2026, 9, 9)]
    out = na.align_nav(nav, days, "US")
    r = out.set_index("date").loc[date(2026, 9, 9)]
    assert r.nav_t2_date == date(2026, 9, 7)
    assert r.nav_t2_nav_date == date(2026, 9, 4)
    assert r.nav_t2 == 104.00
    # Same-date NAV is genuinely missing on the holiday: not filled.
    assert pd.isna(out.set_index("date").loc[date(2026, 9, 7)].nav_same_date)


def test_nav_t2_long_gap_is_nan() -> None:
    # A data gap longer than NAV_T2_MAX_STALE_DAYS must not be bridged.
    nav = pd.DataFrame({"nav_date": [date(2026, 8, 20)], "nav": [100.0]})
    out = na.align_nav(nav, [date(2026, 9, 9)], "US")
    r = out.iloc[0]
    assert pd.isna(r.nav_t2) and r.nav_t2_nav_date is None
