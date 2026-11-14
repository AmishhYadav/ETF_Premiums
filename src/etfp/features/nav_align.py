"""NAV alignment: which NAV was knowable when the Indian market closed? (R3, DECISIONS D10)

The problem in plain words
--------------------------
A fund publishes one NAV (net asset value per unit) per date. A US-linked ETF
that lists in India closes at 15:30 IST, but its NAV for date *t* is struck from
the US close of date *t*, which happens about 10 hours LATER (01:30-02:30 IST on
the next calendar morning). So when the Indian market closes on *t* the NAV
dated *t* does not exist yet, even in principle. The freshest NAV whose
underlying market had already closed is normally the one dated *t-1*.

For a Hong Kong ETF the underlying closes at 16:00 HKT = 13:30 IST, before the
Indian close, so the NAV dated *t* is already "known" by 15:30 IST.

This module computes, for every Indian trading day *t*:

``nav_same_date``            NAV dated *t* (NaN if not published; the press quotes
                             this one, but it is not synchronous with the close).
``nav_known_date``           date of the freshest NAV whose underlying close is at
                             or before 15:30 IST on *t* (see rule below).
``nav_known_at_close``       that NAV.
``nav_known_underlying_date`` the underlying-market session whose close is baked
                             into that NAV: the "a" in fair value
                             ``FV = NAV_a x F_t/F[close of a] x S_t/S_a``.
``nav_publication_lag_days`` calendar days from ``nav_known_date`` to *t*
                             (a 3 on Mondays for US ETFs: Friday's NAV).
``nav_t2_date`` / ``nav_t2`` the Indian session two trading days before *t* and
                             the NAV for it: the old price-band base (D5). If the
                             fund published no NAV dated exactly that session
                             (Mirae skips US holidays, D22) the latest NAV dated
                             before it is used, at most ``NAV_T2_MAX_STALE_DAYS``
                             calendar days older; otherwise NaN.
``nav_t2_nav_date``          date of the NAV actually used for ``nav_t2``.

The rule
--------
NAV dated *d* embeds the close of the last underlying session on or before *d*
(``u(d)``; on a US holiday the NAV is carried forward from the earlier close).
That NAV is "known at the Indian close of *t*" when ``close(u(d)) <= close of the
Indian session on t``. We take the latest such *d*. Examples (US ETF):

* Tue 8 Sep 2026: NAV dated 8 Sep embeds the US close of 8 Sep (Wed 01:30 IST), too
  late. NAV dated 7 Sep embeds the US close of Fri 4 Sep (Labor Day on the 7th) and
  is known. So ``nav_known_date`` = 7 Sep.
* Mon 7 Sep 2026 (US Labor Day): NAV dated 7 Sep is carried forward from the 4 Sep
  close, which happened before Monday's Indian close, so ``nav_known_date`` = 7 Sep
  = ``nav_same_date``, with ``nav_known_underlying_date`` = 4 Sep. The information is
  Friday's even though the NAV is dated Monday.

``<=`` (not ``<``) is used so a domestic ETF, whose NAV is struck on the same
15:30 prices, counts its own-date NAV as known.

``strike_lag_sessions`` generalises the rule for funds that struck NAV *d* from an
older close (D10 says the rule may differ before 1 Jul 2023): with 1, NAV dated *d*
embeds the close of the underlying session before ``u(d)``.

Calendars come from :mod:`etfp.calendars` (or any object with the same functions,
which is how the tests inject a small hand-built calendar).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from typing import Any, Protocol

import numpy as np
import pandas as pd

from etfp import calendars
from etfp.calendars import Market

OUTPUT_COLUMNS = (
    "date",
    "nav_same_date",
    "nav_known_date",
    "nav_known_at_close",
    "nav_known_underlying_date",
    "nav_publication_lag_days",
    "nav_t2_date",
    "nav_t2",
    "nav_t2_nav_date",
)


class CalendarLike(Protocol):
    """The calendar functions this module needs (``etfp.calendars`` provides them)."""

    def previous_session(self, market: Market, day: date) -> date: ...
    def last_session_on_or_before(self, market: Market, day: date) -> date: ...
    def market_close(self, market: Market, day: date) -> datetime: ...


# How stale a fallback NAV may be for the T-2 band base. A fund that skips a
# US holiday leaves a one-session hole; a long weekend plus a holiday is at most
# about 4 calendar days. Anything older is a data gap, not a holiday, so NaN.
NAV_T2_MAX_STALE_DAYS = 5


def _clean_nav(nav: pd.DataFrame) -> pd.Series:
    """NAV frame (``nav_date``, ``nav``) -> float Series indexed by python ``date``, sorted."""
    for col in ("nav_date", "nav"):
        if col not in nav.columns:
            raise ValueError(f"NAV frame needs column {col!r}; has {list(nav.columns)}")
    dates = pd.to_datetime(nav["nav_date"]).dt.date
    series = pd.Series(nav["nav"].astype(float).to_numpy(), index=pd.Index(dates, name="nav_date"))
    series = series.dropna()
    dupes = series.index[series.index.duplicated()]
    if len(dupes):
        raise ValueError(
            f"duplicate NAV dates (de-duplicate in the clean step): {sorted(set(dupes))[:5]}"
        )
    if (series <= 0).any():
        bad = series[series <= 0].index[:5].tolist()
        raise ValueError(f"non-positive NAV on {bad}")
    return series.sort_index()


def _utc_ns(ts: datetime) -> int:
    return int(pd.Timestamp(ts).tz_convert("UTC").value)


def align_nav(
    nav: pd.DataFrame,
    india_days: Sequence[date],
    underlying_market: Market,
    *,
    strike_lag_sessions: int = 0,
    cal: CalendarLike = calendars,  # type: ignore[assignment]
) -> pd.DataFrame:
    """Align one ETF's NAV series to Indian trading days.

    Parameters
    ----------
    nav : columns ``nav_date`` (date) and ``nav`` (float, rupees per unit) for ONE ETF.
    india_days : the Indian trading days to produce rows for (ascending, unique).
    underlying_market : ``"US"``, ``"HK"`` or ``"IN"``: where the fund's holdings trade.
    strike_lag_sessions : see module docstring (0 = NAV dated d uses the close of d).
    cal : calendar provider.

    Returns one row per day in ``india_days`` with ``OUTPUT_COLUMNS``. Date columns
    hold python ``date`` objects (``None`` when unknown); NAV columns are floats (NaN when
    unknown). Nothing is forward-filled except through the "latest known NAV" rule.
    """
    days = list(india_days)
    if days != sorted(set(days)):
        raise ValueError("india_days must be ascending and unique")
    series = _clean_nav(nav)

    # Underlying close instant embedded in each NAV date.
    nav_dates = list(series.index)
    info_ns: list[int | None] = []
    info_session: list[date | None] = []
    for d in nav_dates:
        try:
            u = cal.last_session_on_or_before(underlying_market, d)
            for _ in range(strike_lag_sessions):
                u = cal.previous_session(underlying_market, u)
        except ValueError:  # before the calendar range: this NAV can never be matched
            info_ns.append(None)
            info_session.append(None)
            continue
        info_session.append(u)
        info_ns.append(_utc_ns(cal.market_close(underlying_market, u)))

    # Keep only NAVs whose embedded close is known; sort key is monotone in date.
    usable = [i for i, v in enumerate(info_ns) if v is not None]
    use_ns = np.array([info_ns[i] for i in usable], dtype="int64")
    if len(use_ns) > 1 and (np.diff(use_ns) < 0).any():  # pragma: no cover - defensive
        raise ValueError("underlying close times are not monotone in NAV date")

    nav_ord = np.array([d.toordinal() for d in nav_dates], dtype="int64")
    rows: list[dict[str, Any]] = []
    for t in days:
        india_close_ns = _utc_ns(cal.market_close("IN", t))
        # Count of usable NAVs whose underlying close is <= India close on t.
        k = int(np.searchsorted(use_ns, india_close_ns, side="right"))
        if k:
            idx = usable[k - 1]
            known_date: date | None = nav_dates[idx]
            known_nav = float(series.iloc[idx])
            known_underlying = info_session[idx]
            lag: int | None = (t - known_date).days
        else:
            known_date, known_nav, known_underlying, lag = None, float("nan"), None, None

        try:
            t2: date | None = cal.previous_session("IN", cal.previous_session("IN", t))
        except ValueError:  # start of the calendar range: no T-2 exists
            t2 = None
        # The band base: NAV for session t2, or, when the fund published no NAV
        # dated t2 (a US holiday at Mirae), the last NAV before it -- that is
        # the latest NAV anyone could have used.
        t2_nav, t2_nav_date = float("nan"), None
        if t2 is not None:
            pos = int(np.searchsorted(nav_ord, t2.toordinal(), side="right"))
            if pos:
                cand = nav_dates[pos - 1]
                if (t2 - cand).days <= NAV_T2_MAX_STALE_DAYS:
                    t2_nav, t2_nav_date = float(series.iloc[pos - 1]), cand
        rows.append(
            {
                "date": t,
                "nav_same_date": float(series.get(t, float("nan"))),
                "nav_known_date": known_date,
                "nav_known_at_close": known_nav,
                "nav_known_underlying_date": known_underlying,
                "nav_publication_lag_days": lag,
                "nav_t2_date": t2,
                "nav_t2": t2_nav,
                "nav_t2_nav_date": t2_nav_date,
            }
        )
    out = pd.DataFrame(rows, columns=list(OUTPUT_COLUMNS))
    out["nav_publication_lag_days"] = out["nav_publication_lag_days"].astype("Int64")
    return out
