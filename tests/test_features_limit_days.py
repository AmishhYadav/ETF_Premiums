"""limit_day (R13): touched vs locked, minutes at the limit, widenings. All numbers are hand worked.

Common setup: ETF MONQ50 on 2026-11-10 (regime ``prev_close``: equity, +/-10% initial, widens by
5% of the base, at most twice per side), base price 100.00, tick 0.01. So

    upper limits  110.00  115.00  120.00        lower limits  90.00  85.00  80.00

Snapshots come every 15 minutes from 09:15 to 15:30 (26 cycles, interval 15).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from etfp.collect import bloomberg as bb
from etfp.config import BandRules, load_rules
from etfp.features import limit_days as ld
from etfp.store import Store, coerce, get_schema
from etfp.timeutil import IST

D = date(2026, 11, 10)
SNAP_COLUMNS = [c.name for c in get_schema("inav_snapshots").columns]


@pytest.fixture(scope="module")
def rules() -> BandRules:
    return load_rules()


def at(hhmm: str, day: date = D, seconds: int = 0) -> pd.Timestamp:
    h, m = (int(x) for x in hhmm.split(":"))
    return pd.Timestamp(datetime(day.year, day.month, day.day, h, m, seconds, tzinfo=IST))


def snap(
    hhmm: str,
    price: float | None,
    *,
    source: str = "nse_quote",
    sell: int | None = None,
    buy: int | None = None,
    depth: str | None = None,
    data_ts: pd.Timestamp | None = None,
    seconds: int = 0,
    day: date = D,
) -> dict[str, Any]:
    ts = at(hhmm, day, seconds)
    return {
        "symbol": "MONQ50",
        "ts": ts,
        "source": source,
        "last_price": price,
        "total_buy_qty": buy,
        "total_sell_qty": sell,
        "depth_json": depth,
        "data_ts": data_ts,
        "source_file": f"{source}/{day}/q_{ts:%H%M%S}.json",
    }


def snaps(rows: list[dict[str, Any]]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    for col in SNAP_COLUMNS:
        if col not in df.columns:
            df[col] = None
    return coerce(df[SNAP_COLUMNS], get_schema("inav_snapshots"))


def every_15(start: str = "09:15", end: str = "15:30") -> list[str]:
    t, stop = at(start), at(end)
    out = []
    while t <= stop:
        out.append(f"{t:%H:%M}")
        t += timedelta(minutes=15)
    return out


def etf_row(**kw: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "symbol": "MONQ50",
        "date": D,
        "asset_class": "equity",
        "price_adj_factor": 1.0,
        "band_base_price": 100.0,
        "band_high_official": 110.0,
        "band_low_official": 90.0,
        "high": 105.0,
        "low": 95.0,
        "hit_up": False,
        "hit_down": False,
        "hit_up_official": False,
        "hit_down_official": False,
    }
    row.update(kw)
    return row


NO_BARS = bb.etf_minute_bars(pd.DataFrame(), "MONQ50")
NO_SNAPS = pd.DataFrame()


def run(
    rules: BandRules, row: dict[str, Any], s: pd.DataFrame = NO_SNAPS, b: pd.DataFrame = NO_BARS
):
    return ld.limit_day_row(row, rules, s, b)


# --------------------------------------------------------------------------
# A locked day: price stuck at 110.00 from 10:15 to the close, nobody selling
# --------------------------------------------------------------------------
def test_locked_day(rules: BandRules) -> None:
    rows = []
    for t in every_15():
        px = 105.0 if t <= "10:00" else 110.0
        rows.append(snap(t, px, sell=0 if px == 110.0 else 4000, buy=50000))
        # the NSE ETF table is fetched seconds later in the same cycle: not a second observation
        rows.append(snap(t, px, source="nse_etf_api", seconds=20))
    out = run(rules, etf_row(high=110.0, hit_up=True), snaps(rows))
    assert out is not None
    assert out["obs_source"] == "snapshots_15m"
    assert out["n_obs"] == 26 and out["sample_interval_min"] == 15.0
    assert out["first_obs_ts"] == at("09:15") and out["last_obs_ts"] == at("15:30")
    assert out["touch_basis"] == "both" and out["limit_basis"] == "official"
    assert out["first_touch_ts_up"] == at("10:15")
    # 10:15, 10:30, ..., 15:30 = 22 observations at the limit
    assert out["n_obs_at_limit_up"] == 22
    # 21 gaps of 15 minutes; the 15:30 observation is credited 0 (the market is closed)
    assert out["minutes_at_limit_up"] == 315.0 and out["minutes_at_limit_min_up"] == 315.0
    assert out["widenings_up"] == 0 and out["limit_end_up"] == 110.0
    assert out["trigger_hit_up"] is True  # 110.00 >= 109.90, 9.90% from the base
    assert out["sell_qty_up_max"] == 0 and out["sell_qty_up_close"] == 0
    assert out["buy_qty_up_max"] == 50000
    assert out["n_book_obs_up"] == 22 and out["locked_n_obs_up"] == 22
    assert out["locked_up"] is True and out["locked_close_up"] is True
    # nothing is reported for the side that never touched
    assert out["touched_down"] is False
    assert out["locked_down"] is None and out["minutes_at_limit_down"] is None


# --------------------------------------------------------------------------
# Touched but not locked: two snapshots at 110.00, sellers present
# --------------------------------------------------------------------------
def test_touched_not_locked(rules: BandRules) -> None:
    rows = [
        snap("10:00", 108.0, sell=9000, buy=100),
        snap("10:15", 110.0, sell=3000, buy=90000),
        snap("10:30", 110.0, sell=8000, buy=70000),
        snap("10:45", 108.0, sell=9000, buy=100),
        snap("11:00", 107.0, sell=9500, buy=100),
    ]
    out = run(rules, etf_row(high=110.0, hit_up=True), snaps(rows))
    assert out is not None
    assert out["first_touch_ts_up"] == at("10:15") and out["n_obs_at_limit_up"] == 2
    # 10:15 is credited until 10:30 (15) and 10:30 until 10:45 (15); only the pair 10:15 -> 10:30
    # has both ends at the limit
    assert out["minutes_at_limit_up"] == 30.0 and out["minutes_at_limit_min_up"] == 15.0
    assert out["sell_qty_up_max"] == 8000 and out["buy_qty_up_max"] == 90000
    assert out["n_book_obs_up"] == 2 and out["locked_n_obs_up"] == 0
    assert out["locked_up"] is False
    # the last snapshot (11:00, 107.00) is away from the limit
    assert out["locked_close_up"] is False and out["sell_qty_up_close"] is None
    assert out["widenings_up"] == 0


def test_last_snapshot_at_limit_reports_close_quantity(rules: BandRules) -> None:
    rows = [snap("15:15", 110.0, sell=700), snap("15:30", 110.0, sell=1200)]
    out = run(rules, etf_row(high=110.0, hit_up=True), snaps(rows))
    assert out is not None
    assert out["sell_qty_up_close"] == 1200 and out["sell_qty_up_max"] == 1200
    assert out["locked_close_up"] is False
    # 15:15 credited 15 min; 15:30 credited 0 (close) -> 15; the pair is both at the limit
    assert out["minutes_at_limit_up"] == 15.0 and out["minutes_at_limit_min_up"] == 15.0


# --------------------------------------------------------------------------
# Widenings: 110.00 then 112.00 (beyond the initial limit) then 118.00 then 120.00
# --------------------------------------------------------------------------
def test_widening_day_two_widenings(rules: BandRules) -> None:
    rows = [
        snap("10:00", 110.0, sell=0),
        snap("10:15", 112.0, sell=500),  # beyond 110.00 -> the band widened to 115.00
        snap("10:30", 115.0, sell=0),  # at the new limit
        snap("10:45", 118.0, sell=500),  # beyond 115.00 -> widened to 120.00
        snap("11:00", 120.0, sell=0),
    ]
    out = run(rules, etf_row(high=120.0, hit_up=True, hit_up_max=True), snaps(rows))
    assert out is not None
    assert out["widenings_up"] == 2 and out["limit_end_up"] == 120.0
    # at the limit in force: 10:00 (110, level 0), 10:30 (115, level 1), 11:00 (120, level 2).
    # 112 is not at 115 (needs >= 114.99) and 118 is not at 120 (needs >= 119.99).
    assert out["n_obs_at_limit_up"] == 3
    assert out["first_touch_ts_up"] == at("10:00")
    # each of the three is credited 15 minutes (the last one: one interval before the close)
    assert out["minutes_at_limit_up"] == 45.0
    assert out["minutes_at_limit_min_up"] == 0.0  # no two consecutive observations at a limit
    assert out["locked_n_obs_up"] == 3 and out["locked_up"] is True


def test_one_widening_from_the_daily_high_alone(rules: BandRules) -> None:
    # No intraday data; the day's high 112.00 is beyond 110.00, so at least one widening.
    out = run(rules, etf_row(high=112.0, hit_up=True))
    assert out is not None
    assert out["widenings_up"] == 1 and out["limit_end_up"] == 115.0


def test_widenings_are_capped_by_the_rules(rules: BandRules) -> None:
    out = run(rules, etf_row(high=125.0, hit_up=True))  # an impossible price: capped at 2
    assert out is not None
    assert out["widenings_up"] == 2 and out["limit_end_up"] == 120.0


def test_price_inside_the_initial_limit_is_zero_widenings_and_an_old_limit_is_no_longer_it(
    rules: BandRules,
) -> None:
    # 110.00 first (at the limit), then 113.00: the band is now 115.00, so a later 110.00 is not
    # "at the limit" any more.
    rows = [snap("10:00", 110.0), snap("10:15", 113.0), snap("10:30", 110.0)]
    out = run(rules, etf_row(high=113.0, hit_up=True), snaps(rows))
    assert out is not None
    assert out["n_obs_at_limit_up"] == 1 and out["widenings_up"] == 1


def test_fixed_band_regime_has_zero_widenings(rules: BandRules) -> None:
    # 4 Sep 2026 is in the nav_t2 regime: fixed +/-20% of the base, no widening at all.
    day = date(2026, 9, 4)
    row = etf_row(
        date=day, band_high_official=120.0, band_low_official=80.0, high=120.0, hit_up=True
    )
    s = snaps([snap("10:00", 120.0, sell=0, day=day), snap("10:15", 119.99, sell=0, day=day)])
    out = run(rules, row, s)
    assert out is not None
    assert out["widenings_up"] == 0 and out["limit_end_up"] == 120.0
    assert out["n_obs_at_limit_up"] == 2  # 119.99 is within one tick of 120.00


# --------------------------------------------------------------------------
# Missing quantities are unknown, never zero
# --------------------------------------------------------------------------
def test_missing_quantities_give_empty_locked(rules: BandRules) -> None:
    rows = [snap("10:00", 110.0, source="nse_etf_api"), snap("10:15", 110.0, source="nse_etf_api")]
    out = run(rules, etf_row(high=110.0, hit_up=True), snaps(rows))
    assert out is not None
    assert out["n_obs_at_limit_up"] == 2 and out["minutes_at_limit_up"] == 30.0
    assert out["n_book_obs"] == 0 and out["n_book_obs_up"] == 0
    assert out["locked_up"] is None and out["locked_n_obs_up"] is None
    assert out["sell_qty_up_max"] is None and out["locked_close_up"] is None


def test_unknown_total_does_not_count_as_empty_but_depth_can_prove_sellers(
    rules: BandRules,
) -> None:
    sellers = '{"bid":[[110.0,500]],"ask":[[110.0,300],[110.01,200]]}'
    no_ask = '{"bid":[[110.0,500]],"ask":[]}'
    rows = [
        snap("10:00", 110.0, depth=no_ask),  # total missing, empty depth: still unknown
        snap("10:15", 110.0, depth=sellers),  # total missing, depth shows 500 for sale
    ]
    out = run(rules, etf_row(high=110.0, hit_up=True), snaps(rows))
    assert out is not None
    assert out["n_book_obs_up"] == 1 and out["locked_up"] is False
    assert out["sell_qty_up_max"] == 500  # a lower bound from the depth levels


def test_zero_total_beats_depth(rules: BandRules) -> None:
    rows = [snap("10:00", 110.0, sell=0, buy=900, depth='{"bid":[[110.0,500]],"ask":[]}')]
    out = run(rules, etf_row(high=110.0, hit_up=True), snaps(rows))
    assert out is not None and out["locked_up"] is True and out["buy_qty_up_max"] == 900


# --------------------------------------------------------------------------
# Lower limit mirrors the upper one
# --------------------------------------------------------------------------
def test_locked_down_needs_the_buy_side_empty(rules: BandRules) -> None:
    rows = [
        snap("10:00", 90.0, buy=0, sell=7000),
        snap("10:15", 90.0, buy=0, sell=9000),
        snap("10:30", 91.0, buy=100, sell=100),
    ]
    out = run(rules, etf_row(low=90.0, hit_down=True), snaps(rows))
    assert out is not None
    assert out["touched_down"] is True and out["touched_up"] is False
    assert out["first_touch_ts_down"] == at("10:00") and out["n_obs_at_limit_down"] == 2
    assert out["minutes_at_limit_down"] == 30.0 and out["minutes_at_limit_min_down"] == 15.0
    assert out["buy_qty_down_max"] == 0 and out["sell_qty_down_max"] == 9000
    assert out["locked_down"] is True and out["locked_close_down"] is False
    assert out["widenings_down"] == 0 and out["limit_end_down"] == 90.0
    assert out["locked_up"] is None


def test_lower_side_widening(rules: BandRules) -> None:
    # low 88.00 is beyond 90.00 -> widened to 85.00
    out = run(rules, etf_row(low=88.0, hit_down=True))
    assert out is not None
    assert out["widenings_down"] == 1 and out["limit_end_down"] == 85.0


# --------------------------------------------------------------------------
# Which days appear, and what is left empty
# --------------------------------------------------------------------------
def test_untouched_day_has_no_row(rules: BandRules) -> None:
    rows = [snap(t, 105.0, sell=100) for t in every_15()]
    assert run(rules, etf_row(), snaps(rows)) is None


def test_daily_flag_without_intraday_data_is_a_row_with_empty_diagnostics(
    rules: BandRules,
) -> None:
    out = run(rules, etf_row(high=110.0, hit_up=True))
    assert out is not None
    assert out["obs_source"] == "none" and out["n_obs"] == 0
    assert out["first_obs_ts"] is None and out["sample_interval_min"] is None
    assert out["touch_basis"] == "etf_day"
    assert out["first_touch_ts_up"] is None and out["minutes_at_limit_up"] is None
    assert out["locked_up"] is None and out["n_obs_at_limit_up"] is None
    assert out["source_file"] == "etf_day"


def test_intraday_touch_without_daily_flag(rules: BandRules) -> None:
    out = run(rules, etf_row(), snaps([snap("10:00", 110.0, sell=0)]))
    assert out is not None and out["touch_basis"] == "intraday"
    # a single observation: no spacing to measure, so no minutes estimate
    assert out["n_obs"] == 1 and out["minutes_at_limit_up"] is None
    assert out["n_obs_at_limit_up"] == 1 and out["locked_up"] is True


def test_stale_and_out_of_session_snapshots_are_ignored(rules: BandRules) -> None:
    holiday_replay = at("10:00", date(2026, 11, 6))  # NSE data time from an earlier day
    rows = [
        snap("10:00", 110.0, sell=0, data_ts=holiday_replay),  # stale replay of an old price
        snap("09:00", 110.0, sell=0),  # pre-open
        snap("15:45", 110.0, sell=0),  # after the close
        snap("11:00", 105.0, sell=100),
    ]
    assert run(rules, etf_row(), snaps(rows)) is None
    out = run(rules, etf_row(high=110.0, hit_up=True), snaps(rows))
    assert out is not None and out["n_obs"] == 1  # only the 11:00 snapshot counts


def test_no_band_known_still_flags_the_day(rules: BandRules) -> None:
    row = etf_row(band_base_price=None, band_high_official=None, band_low_official=None)
    out = run(rules, row | {"hit_up": True}, snaps([snap("10:00", 110.0, sell=0)]))
    assert out is not None
    assert out["limit_basis"] == "none" and out["touched_up"] is True
    assert out["locked_up"] is None and out["widenings_up"] is None
    assert out["n_obs"] == 1  # coverage is still reported


def test_official_opening_limit_shifts_the_ladder(rules: BandRules) -> None:
    # The official file says 110.02 (computed 110.00): every level moves by +0.02.
    row = etf_row(band_high_official=110.02, high=110.02, hit_up=True)
    out = run(rules, row, snaps([snap("10:00", 110.02, sell=0), snap("10:15", 110.01, sell=0)]))
    assert out is not None
    assert out["widenings_up"] == 0 and out["limit_end_up"] == pytest.approx(110.02)
    assert out["n_obs_at_limit_up"] == 2  # 110.01 is within one tick of 110.02
    row2 = etf_row(band_high_official=110.02, high=112.0, hit_up=True)
    out2 = run(rules, row2)
    assert out2 is not None and out2["limit_end_up"] == pytest.approx(115.02)


def test_adjusted_prices_are_put_back_on_the_scale_of_the_day(rules: BandRules) -> None:
    # etf_day is on today's unit scale (x2 here, e.g. after a 1:2 consolidation); snapshots
    # hold the prices quoted on the day.
    row = etf_row(
        price_adj_factor=2.0,
        band_base_price=200.0,
        band_high_official=220.0,
        band_low_official=180.0,
        high=220.0,
        hit_up=True,
    )
    out = run(rules, row, snaps([snap("10:00", 110.0, sell=0)]))
    assert out is not None
    assert out["n_obs_at_limit_up"] == 1 and out["locked_up"] is True
    assert out["limit_end_up"] == pytest.approx(220.0)


# --------------------------------------------------------------------------
# Bloomberg minute bars
# --------------------------------------------------------------------------
HEADER = "date,time,timezone,instrument,field,value,note\n"


def bars_frame(spec: list[tuple[str, float, float | None]]) -> pd.DataFrame:
    """Bars as imported by the real parser: (HH:MM, close, high or None)."""
    lines = []
    for hhmm, last, high in spec:
        lines.append(f"{D},{hhmm},Asia/Kolkata,MONQ50.1M,PX_LAST,{last},")
        if high is not None:
            lines.append(f"{D},{hhmm},Asia/Kolkata,MONQ50.1M,PX_HIGH,{high},")
    frame = bb.parse(HEADER + "\n".join(lines) + "\n", "bloomberg/2026-11-14/mon.csv")
    return bb.etf_minute_bars(frame, "MONQ50")


def test_minute_bars_reader_uses_the_dot_1m_instrument() -> None:
    text = (
        HEADER
        + f"{D},10:15,Asia/Kolkata,MONQ50.1M,PX_LAST,110.0,\n"
        + f"{D},10:15,Asia/Kolkata,MONQ50.1M,PX_HIGH,110.0,\n"
        + f"{D},10:15,Asia/Kolkata,MONQ50.1M,VOLUME,1200,\n"
        + f"{D},10:16,Asia/Kolkata,MONQ50.1M,PX_LAST,109.5,\n"
        + f"{D},15:30,Asia/Kolkata,MONQ50,PX_LAST,111.0,daily row: not a bar\n"
        + f"{D},10:15,Asia/Kolkata,MON100.1M,PX_LAST,50.0,other ETF\n"
    )
    frame = bb.parse(text, "bloomberg/2026-11-14/mon.csv")
    bars = bb.etf_minute_bars(frame, "MONQ50")
    assert list(bars.columns) == list(bb.MINUTE_BAR_COLUMNS)
    assert len(bars) == 2
    first = bars.iloc[0]
    assert first["ts_ist"] == at("10:15") and first["last"] == 110.0
    assert first["high"] == 110.0 and first["volume"] == 1200.0
    assert pd.isna(bars.iloc[1]["high"]) and bars.iloc[1]["last"] == 109.5
    assert first["source_file"] == "bloomberg/2026-11-14/mon.csv"
    assert bb.etf_minute_bars(frame, "NOSUCH").empty
    assert bb.etf_minute_bars(pd.DataFrame(), "MONQ50").empty


def test_bars_are_preferred_and_give_minute_precision(rules: BandRules) -> None:
    bars = bars_frame(
        [("10:13", 109.0, None), ("10:14", 109.5, None)]
        + [(f"10:{m}", 110.0, None) for m in range(15, 20)]  # five bars at the limit
        + [("10:20", 109.0, None)]
    )
    rows = [
        snap("10:00", 105.0, sell=100),
        snap("10:15", 110.0, sell=0, buy=40000, seconds=20),  # the order book is from snapshots
        snap("10:30", 105.0, sell=100),
    ]
    out = run(rules, etf_row(high=110.0, hit_up=True), snaps(rows), bars)
    assert out is not None
    assert out["obs_source"] == "bloomberg_1m" and out["sample_interval_min"] == 1.0
    assert out["n_obs"] == 8 and out["first_obs_ts"] == at("10:13")
    assert out["first_touch_ts_up"] == at("10:15")
    assert out["n_obs_at_limit_up"] == 5
    assert out["minutes_at_limit_up"] == 5.0 and out["minutes_at_limit_min_up"] == 4.0
    # the snapshot at 10:15:20 sits at the limit; its empty sell side makes the day locked
    assert out["locked_up"] is True and out["buy_qty_up_max"] == 40000
    assert out["source_file"].startswith("inav_snapshots: ")
    assert "bloomberg: bloomberg/2026-11-14/mon.csv" in out["source_file"]


def test_bar_high_counts_as_a_touch_even_if_the_bar_closed_lower(rules: BandRules) -> None:
    bars = bars_frame([("10:15", 109.5, 110.0), ("10:16", 108.0, 108.5)])
    out = run(rules, etf_row(high=110.0, hit_up=True), b=bars)
    assert out is not None
    assert out["first_touch_ts_up"] == at("10:15")  # the high reached 110.00
    assert out["n_obs_at_limit_up"] == 0 and out["minutes_at_limit_up"] == 0.0


def test_bars_outside_the_session_are_ignored(rules: BandRules) -> None:
    bars = bars_frame([("09:10", 110.0, None), ("15:30", 110.0, None), ("11:00", 105.0, None)])
    assert run(rules, etf_row(), b=bars) is None


def test_falls_back_to_snapshots_on_a_day_without_bars(rules: BandRules) -> None:
    other_day = bars_frame([("10:15", 110.0, None)]).assign(
        ts_ist=lambda d: d["ts_ist"] + pd.Timedelta(days=1)
    )
    out = run(
        rules, etf_row(high=110.0, hit_up=True), snaps([snap("10:00", 110.0, sell=0)]), other_day
    )
    assert out is not None and out["obs_source"] == "snapshots_15m"


# --------------------------------------------------------------------------
# The table: schema, building from the store
# --------------------------------------------------------------------------
def test_schema_is_registered_and_columns_match() -> None:
    schema = get_schema("limit_day")
    assert schema.primary_key == ("symbol", "date")
    assert schema.column_names == ld.COLUMNS
    for side in ("up", "down"):
        for col in ("touched", "first_touch_ts", "minutes_at_limit", "widenings", "locked"):
            assert f"{col}_{side}" in ld.COLUMNS
    assert {"obs_source", "sample_interval_min", "n_obs", "first_obs_ts", "last_obs_ts"} <= set(
        ld.COLUMNS
    )


def test_build_limit_days_from_the_store(tmp_path: Path, rules: BandRules) -> None:
    store = Store(tmp_path / "clean")
    rows = []
    for t in every_15("10:00", "11:00"):
        rows.append(snap(t, 110.0, sell=0, buy=1000))
    other_day = date(2026, 11, 11)
    rows.append(snap("10:00", 100.0, day=other_day))  # a different day, untouched
    store.write_table("inav_snapshots", snaps(rows))
    etf_day = pd.DataFrame(
        [
            etf_row(high=110.0, hit_up=True),
            etf_row(date=other_day),
            etf_row(symbol="MON100", hit_up=True, high=110.0),  # no snapshots for this ETF
        ]
    )
    out = ld.build_limit_days(store, etf_day, rules=rules)
    assert list(out.columns) == ld.COLUMNS
    assert sorted(out["symbol"]) == ["MON100", "MONQ50"]
    mon = out[out["symbol"] == "MONQ50"].iloc[0]
    assert mon["date"] == D and mon["obs_source"] == "snapshots_15m" and mon["n_obs"] == 5
    assert (
        bool(mon["locked_up"]) is True and mon["minutes_at_limit_up"] == 75.0
    )  # 4 gaps of 15, plus one interval for the last
    assert str(mon["first_touch_ts_up"].tz) == "Asia/Kolkata"
    other = out[out["symbol"] == "MON100"].iloc[0]
    assert other["obs_source"] == "none" and pd.isna(other["locked_up"])
    assert other["n_obs"] == 0
    # round trip through the store keeps the types
    assert store.write_table("limit_day", out) == 2
    again = store.read_table("limit_day")
    assert len(again) == 2


def test_build_limit_days_without_any_touch_is_an_empty_typed_frame(
    tmp_path: Path, rules: BandRules
) -> None:
    store = Store(tmp_path / "clean")
    out = ld.build_limit_days(store, pd.DataFrame([etf_row()]), rules=rules)
    assert out.empty and list(out.columns) == ld.COLUMNS
    assert store.write_table("limit_day", out) == 0
