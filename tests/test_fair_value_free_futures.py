"""Free fair value from 60-minute Yahoo bars (fv_method 'free_futures', D10/D13, D43).

Every number below is hand-computed; the arithmetic is in each test's docstring. The bars
are SYNTHETIC Yahoo chart answers written to a temporary raw folder, then read back through
the real path: ``yahoo_intraday.parse`` -> ``build.load_underlying`` -> ``build.fair_value_inputs``
-> ``fair_value.fair_value_frame``.

Time facts used (all checked with the project calendar in the tests themselves):
* 15:30 IST = 10:00 UTC (India has no daylight saving).
* US close 16:00 New York = 21:00 UTC in November (EST, UTC-5), 20:00 UTC in September (EDT).
* A 60m bar stamped 09:00 UTC covers 09:00-10:00 UTC; its close is the price at 15:30 IST.
"""

from __future__ import annotations

import itertools
import json
import math
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from etfp import build, calendars, paths
from etfp.collect import yahoo_intraday as yi
from etfp.features import fair_value as fvmod

MONQ50 = "MONQ50"  # Nasdaq fund: future NQ=F, currency USD -> USDINR=X


@pytest.fixture(scope="module")
def cfg() -> build.BuildConfig:
    return build.load_config(paths.project_root())


def _utc(y: int, m: int, d: int, hh: int, mm: int = 0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=UTC)


def chart(symbol: str, bars: list[tuple[datetime, float]], interval: str = "60m") -> bytes:
    """A Yahoo v8 chart answer (documented shape) holding the given (bar START, close)."""
    stamps = [int(t.timestamp()) for t, _ in bars]
    closes = [c for _, c in bars]
    body = {
        "chart": {
            "result": [
                {
                    "meta": {"symbol": symbol, "dataGranularity": interval},
                    "timestamp": stamps,
                    "indicators": {
                        "quote": [
                            {
                                "open": closes,
                                "high": closes,
                                "low": closes,
                                "close": closes,
                                "volume": [0] * len(closes),
                            }
                        ]
                    },
                }
            ],
            "error": None,
        }
    }
    return json.dumps(body).encode()


def write(tmp_path: Path, files: dict[str, bytes], day: str = "2026-11-18") -> None:
    folder = tmp_path / yi.SOURCE / day
    folder.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (folder / name).write_bytes(content)


def underlying(tmp_path: Path, files: dict[str, bytes]) -> build.Underlying:
    """Write raw files under <tmp>/yahoo_intraday/2026-11-18/ and load them like the build."""
    write(tmp_path, files)
    return build.load_underlying(tmp_path)


def fv_row(cfg, data, t: date, a: date, nav: float) -> pd.Series:
    etf = cfg.universe.by_symbol(MONQ50)
    use_fut, use_fx = fvmod.fv_terms_for(etf)
    inputs = build.fair_value_inputs(etf, [t], [a], data)
    frame = inputs[["futures_t", "futures_a", "fx_t", "fx_a"]].copy()
    frame["nav_known_at_close"] = nav
    out = fvmod.fair_value_frame(frame, use_futures=use_fut, use_fx=use_fx, source=inputs["source"])
    return pd.concat([inputs.iloc[0], out.iloc[0]])


# A normal pair of days: NAV dated Wed 11 Nov 2026 (US close 21:00 UTC), Indian close Thu 12 Nov
# (10 Nov is an NSE holiday).
T_NOV, A_NOV = date(2026, 11, 12), date(2026, 11, 11)


def nov_files(*, drop_nq_0900: bool = False, drop_fx_0900: bool = False) -> dict[str, bytes]:
    nq = [
        (_utc(2026, 11, 11, 19), 24990.00),
        (_utc(2026, 11, 11, 20), 25000.00),  # 20:00-21:00 UTC: ends at the US close -> F_a
        (_utc(2026, 11, 11, 21), 25010.00),  # after the close: must not be used for F_a
        (_utc(2026, 11, 12, 8), 25200.00),
        (_utc(2026, 11, 12, 9), 25250.00),  # 09:00-10:00 UTC: ends at 15:30 IST -> F_t
        (_utc(2026, 11, 12, 10), 25300.00),  # after 15:30 IST: must not be used
    ]
    fx = [
        (_utc(2026, 11, 11, 20), 94.00),  # -> S_a (rate at the US close of a)
        (_utc(2026, 11, 12, 8), 94.30),
        (_utc(2026, 11, 12, 9), 94.47),  # -> S_t
        (_utc(2026, 11, 12, 10), 94.60),
    ]
    if drop_nq_0900:
        nq = [b for b in nq if b[0] != _utc(2026, 11, 12, 9)]
    if drop_fx_0900:
        fx = [b for b in fx if b[0] != _utc(2026, 11, 12, 9)]
    return {
        yi.history_raw_name("NQ=F"): chart("NQ=F", nq),
        yi.history_raw_name("USDINR=X"): chart("USDINR=X", fx),
    }


def test_time_facts_from_the_calendar() -> None:
    assert calendars.market_close("IN", T_NOV).astimezone(UTC) == _utc(2026, 11, 12, 10)
    assert calendars.market_close("US", A_NOV).astimezone(UTC) == _utc(2026, 11, 11, 21)
    assert calendars.market_close("US", date(2026, 9, 4)).astimezone(UTC) == _utc(2026, 9, 4, 20)


def test_free_futures_hand_example(cfg, tmp_path) -> None:
    """NAV_a 200 (dated 11 Nov), F 25,000 -> 25,250, S 94.00 -> 94.47.

    F_t / F_a = 25,250 / 25,000 = 1.01
    S_t / S_a = 94.47 / 94.00   = 1.005
    FV = 200 x 1.01 x 1.005 = 202 x 1.005 = 203.01
    The 21:00 and 10:00 UTC bars (25,010 / 25,300) start AFTER the moments and are ignored.
    """
    row = fv_row(cfg, underlying(tmp_path, nov_files()), T_NOV, A_NOV, 200.0)
    assert (row.futures_a, row.futures_t, row.fx_a, row.fx_t) == (25000.0, 25250.0, 94.0, 94.47)
    assert row.fv_method == "free_futures"
    assert row.fv == pytest.approx(203.01, abs=1e-9)
    assert row.fv_futures_ratio == pytest.approx(1.01)
    assert row.fv_fx_ratio == pytest.approx(1.005)
    assert "NQ_F_60m_history.json" in row.files and "USDINR_X_60m_history.json" in row.files


def test_stale_futures_bar_falls_back_to_nav(cfg, tmp_path) -> None:
    """The NQ bar 09:00-10:00 UTC is missing, so the freshest NQ bar ended 09:00 UTC:
    stale = 10:00 - 09:00 = 60 min > 30 min allowed for futures -> F_t missing -> nav_only,
    FV = NAV_a = 200 (no partial adjustment with FX alone, D20).
    """
    row = fv_row(cfg, underlying(tmp_path, nov_files(drop_nq_0900=True)), T_NOV, A_NOV, 200.0)
    assert math.isnan(row.futures_t)
    assert row.fv_method == "nav_only" and row.fv == 200.0


def test_fx_may_be_one_hour_stale(cfg, tmp_path) -> None:
    """USD/INR bar 09:00-10:00 missing: the 08:00-09:00 bar (94.30) ended 60 min before 15:30
    IST, inside FX's 60-min allowance (build: FX quotes are thinner than futures).
    FV = 200 x 25,250/25,000 x 94.30/94.00 = 200 x 1.01 x 1.0031914894 = 202.6446809
    """
    row = fv_row(cfg, underlying(tmp_path, nov_files(drop_fx_0900=True)), T_NOV, A_NOV, 200.0)
    assert row.fx_t == 94.30 and row.fv_method == "free_futures"
    assert row.fv == pytest.approx(200 * 1.01 * 94.30 / 94.00)
    assert row.fv == pytest.approx(202.6446809, abs=1e-6)


def test_no_bars_at_all_is_nav_only(cfg, tmp_path) -> None:
    """Missing-bar fallback: no Yahoo file -> every input NaN -> FV = NAV_a = 123.45."""
    row = fv_row(cfg, build.load_underlying(tmp_path), T_NOV, A_NOV, 123.45)
    assert row.fv_method == "nav_only" and row.fv == 123.45
    assert row[["futures_t", "futures_a", "fx_t", "fx_a"]].isna().all()


def test_us_holiday_uses_the_close_before_the_holiday(cfg, tmp_path) -> None:
    """Tue 8 Sep 2026, the day after US Labor Day (7 Sep). The NAV known at 15:30 IST is the one
    dated 7 Sep, carried forward from the US close of Fri 4 Sep (D18), so a = 4 Sep.

    F_a = bar 19:00-20:00 UTC on 4 Sep (16:00 New York, EDT) = 24,000
    F_t = bar 09:00-10:00 UTC on 8 Sep = 23,760   ratio 23,760 / 24,000 = 0.99
    S_a = 94.40, S_t = 94.4472                     ratio 94.4472 / 94.40 = 1.0005
    FV = 150 x 0.99 x 1.0005 = 148.5 x 1.0005 = 148.57425
    No bar on the holiday itself is needed or used.
    """
    assert calendars.last_session_on_or_before("US", date(2026, 9, 7)) == date(2026, 9, 4)
    files = {
        "NQ_F_60m_history.json": chart(
            "NQ=F",
            [
                (_utc(2026, 9, 4, 19), 24000.0),
                (_utc(2026, 9, 7, 12), 23900.0),
                (_utc(2026, 9, 8, 9), 23760.0),
            ],
        ),
        "USDINR_X_60m_history.json": chart(
            "USDINR=X", [(_utc(2026, 9, 4, 19), 94.40), (_utc(2026, 9, 8, 9), 94.4472)]
        ),
    }
    row = fv_row(cfg, underlying(tmp_path, files), date(2026, 9, 8), date(2026, 9, 4), 150.0)
    assert row.fv_method == "free_futures"
    assert row.fv == pytest.approx(148.57425, abs=1e-9)


def test_contract_roll_night_is_not_adjusted(cfg, tmp_path) -> None:
    """Thu 10 Sep 2026 is the CME roll date of the Sep contract (expiry Fri 18 Sep; roll date 8
    days earlier). Its session opens Wed 9 Sep 18:00 New York = 22:00 UTC, which lies between
    F_a (US close Wed 9 Sep, 20:00 UTC) and F_t (15:30 IST Thu 10 Sep, 10:00 UTC).

    Say the market rose 0.25% overnight but Yahoo's NQ=F switched to the Dec contract, which
    trades ~1% above Sep: F_a = 24,000 (Sep), F_t = 24,300 (Dec; = 24,060 Sep-equivalent).
    Unguarded: FV = 100 x 24,300/24,000 = 101.25 (true move: 100.25, so 1% too high).
    Guarded: the futures inputs are dropped -> nav_only, FV = 100.
    The next day (a = Thu 10 Sep, t = Fri 11 Sep) has no candidate switch between the readings
    and is adjusted normally: FV = 100 x 24,543/24,300 = 101.0.
    """
    nq = [
        (_utc(2026, 9, 9, 19), 24000.0),
        (_utc(2026, 9, 10, 9), 24300.0),
        (_utc(2026, 9, 10, 19), 24300.0),
        (_utc(2026, 9, 11, 9), 24543.0),
    ]
    fx = [(t, 94.0) for t, _ in nq]
    data = underlying(
        tmp_path,
        {
            "NQ_F_60m_history.json": chart("NQ=F", nq),
            "USDINR_X_60m_history.json": chart("USDINR=X", fx),
        },
    )
    assert fvmod.futures_roll_moments(_utc(2026, 9, 9, 20), _utc(2026, 9, 10, 10)) == [
        _utc(2026, 9, 9, 22)
    ]
    rolled = fv_row(cfg, data, date(2026, 9, 10), date(2026, 9, 9), 100.0)
    assert rolled.fv_method == "nav_only" and rolled.fv == 100.0
    assert math.isnan(rolled.futures_t) and rolled.fx_t == 94.0  # only the future is dropped
    nxt = fv_row(cfg, data, date(2026, 9, 11), date(2026, 9, 10), 100.0)
    assert nxt.fv_method == "free_futures" and nxt.fv == pytest.approx(101.0)


def test_5m_and_60m_bars_with_the_same_start_are_both_kept(cfg, tmp_path) -> None:
    """A daily 5m file and a 60m history file both have a bar stamped 09:00 UTC on 12 Nov.
    They are different bars: 5m ends 09:05 (close 25,111), 60m ends 10:00 (close 25,250).
    The price at 15:30 IST is the 60m bar's close (ended exactly at 10:00); dropping either
    "duplicate" by start time alone would lose it (bug fixed in build.load_underlying).
    """
    write(tmp_path, nov_files())
    # the 5m file sits in a LATER folder, so it is read last (the old code kept it, lost the 60m)
    five = chart("NQ=F", [(_utc(2026, 11, 12, 9), 25111.0)], interval="5m")
    write(tmp_path, {"NQ_F_5m.json": five}, day="2026-11-19")
    row = fv_row(cfg, build.load_underlying(tmp_path), T_NOV, A_NOV, 200.0)
    assert row.futures_t == 25250.0 and row.fv == pytest.approx(203.01)


def test_roll_calendar() -> None:
    """Third Fridays: 20 Mar, 19 Jun, 18 Sep, 18 Dec 2026 and 21 Mar 2025. 19 Jun 2026 is a US
    holiday (Juneteenth), so that contract expires the session before: Thu 18 Jun 2026."""
    assert not calendars.is_session("US", date(2026, 6, 19))
    got = [fvmod.cme_quarterly_expiry(2026, m) for m in (3, 6, 9, 12)]
    assert got == [date(2026, 3, 20), date(2026, 6, 18), date(2026, 9, 18), date(2026, 12, 18)]
    assert fvmod.cme_quarterly_expiry(2025, 3) == date(2025, 3, 21)
    with pytest.raises(ValueError):
        fvmod.cme_quarterly_expiry(2026, 4)
    moments = fvmod.futures_roll_moments(
        _utc(2026, 9, 1, 0), _utc(2026, 9, 30, 0), include_expiry_open=True
    )
    # 18:00 EDT 9 Sep, 18:00 EDT 17 Sep, 09:30 EDT 18 Sep
    assert moments == [_utc(2026, 9, 9, 22), _utc(2026, 9, 17, 22), _utc(2026, 9, 18, 13, 30)]
    with pytest.raises(ValueError):
        fvmod.futures_roll_moments(datetime(2026, 9, 1), _utc(2026, 9, 2, 0))  # noqa: DTZ001


def test_beta_from_60m_history_reports_its_sample_size(cfg, tmp_path) -> None:
    """Beta through the free tier: a NAV that moves exactly 0.8 x the future's log return plus
    the FX return. Sessions whose previous-close -> close interval holds a candidate contract
    switch are left out (their return would contain the calendar spread).

    Short history (US sessions 2 Mar - 4 Sep 2026): fewer than 250 pairs, so fv_beta_n says
    how many were used and the estimate is still exact (0.8) because there is no noise.
    """
    sessions = calendars.sessions("US", date(2026, 3, 2), date(2026, 9, 4))
    rng = np.random.default_rng(11)
    f_ret, s_ret = rng.normal(0, 0.01, len(sessions)), rng.normal(0, 0.002, len(sessions))
    fut, fx = 20000 * np.exp(np.cumsum(f_ret)), 90 * np.exp(np.cumsum(s_ret))
    nav = pd.Series(100 * np.exp(np.cumsum(0.8 * f_ret + s_ret)), index=pd.Index(sessions))
    starts = [calendars.market_close("US", d).astimezone(UTC).replace(minute=0) for d in sessions]
    starts = [s - pd.Timedelta(hours=1) for s in starts]  # the bar ENDING at the close
    data = underlying(
        tmp_path,
        {
            "NQ_F_60m_history.json": chart("NQ=F", list(zip(starts, fut, strict=True))),
            "USDINR_X_60m_history.json": chart("USDINR=X", list(zip(starts, fx, strict=True))),
        },
    )
    etf = cfg.universe.by_symbol(MONQ50)
    est = build.estimate_beta(etf, nav, data)
    guarded = [d for d in sessions[1:] if build._free_roll_around(etf, d)]
    # Mar and Jun 2026 rolls lie inside the window: roll-date sessions Thu 12 Mar and Thu
    # 11 Jun, expiry sessions Fri 20 Mar and Thu 18 Jun (Juneteenth moved it).
    assert guarded == [date(2026, 3, 12), date(2026, 3, 20), date(2026, 6, 11), date(2026, 6, 18)]
    # a guarded session has no level, which removes its own pair and the next one
    assert est.ok and est.beta == pytest.approx(0.8, abs=1e-9)
    assert est.n_obs < 250
    usable = {d for d in sessions if d not in guarded}
    expected = sum(1 for p, d in itertools.pairwise(sessions) if p in usable and d in usable)
    assert est.n_obs == expected
