"""Tests for the dashboard's data-preparation functions (no Streamlit, no Plotly needed).

Expected numbers are worked out by hand in the comments.
"""

from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from test_alerts_helpers import FakeStore, ist, snapshots

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # the dashboard/ folder is not an installed package
    sys.path.insert(0, str(ROOT))

from dashboard import data as dd  # noqa: E402

from etfp.config import load_rules  # noqa: E402

BANNED = re.compile(r"\b(buy|sell|undervalued|overvalued|opportunity|cheap|expensive)\b", re.I)
D5, D12, D13 = date(2026, 11, 5), date(2026, 11, 12), date(2026, 11, 13)


def sample_etf_day() -> pd.DataFrame:
    rows = [
        # X: three rows. On 13 Nov: close 150, fair value 120 -> prem_fv 150/120 - 1 = 0.25.
        {"symbol": "X", "date": D5, "close": 100.0, "prem_fv": 0.10, "hit_up": False,
         "units_change": 0},
        {"symbol": "X", "date": D12, "close": 120.0, "prem_fv": 0.20, "hit_up": True,
         "units_change": 0},
        {
            "symbol": "X", "date": D13, "close": 150.0, "nav_known_at_close": 100.0, "fv": 120.0,
            "prem_fv": 0.25, "prem_raw": 0.5, "prem_nav_known": 0.5,
            "band_low_official": 100.0, "band_high_official": 160.0,
            "band_low_initial": 90.0, "band_high_initial": 180.0,
            "hit_up": False, "units_change": 600000, "creation_units_change": 1.0,
            "fv_method": "free_futures", "dq_missing_close": True,
        },
        # Y: only the computed band is known; no premium at all.
        {"symbol": "Y", "date": D13, "close": 100.0, "band_low_initial": 90.0,
         "band_high_initial": 110.0, "dq_missing_close": False},
        # Z exists in etf_day but is not tracked.
        {"symbol": "Z", "date": D13, "close": 50.0, "prem_fv": 9.0},
    ]  # fmt: skip
    df = pd.DataFrame(rows)
    df["hit_up"] = df["hit_up"].astype("boolean")  # as the real table stores it (NA allowed)
    return df


MASTER = pd.DataFrame(
    {
        "symbol": ["X", "Y", "Z"],
        "name": ["X fund", "Y fund", "Z fund"],
        "tracked": [True, True, False],
    }
)


# -- who is shown ----
def test_tracked_symbols() -> None:
    assert dd.tracked_symbols(sample_etf_day(), MASTER) == ["X", "Y"]
    assert dd.tracked_symbols(sample_etf_day(), None) == ["X", "Y", "Z"]  # no master: show all
    assert dd.tracked_symbols(None, MASTER) == []
    assert dd.tracked_symbols(pd.DataFrame(), MASTER) == []


def test_load_tables_missing_tables_are_none() -> None:
    tables = dd.load_tables(FakeStore(etf_day=sample_etf_day()))
    assert tables.has_data and tables.get("inav_snapshots") is None
    assert not dd.load_tables(FakeStore()).has_data
    assert not dd.load_tables(FakeStore(etf_day=pd.DataFrame({"symbol": [], "date": []}))).has_data


# -- overview ----
def test_overview_hand_values_for_x() -> None:
    ov = dd.latest_overview(sample_etf_day(), MASTER)
    assert list(ov["symbol"]) == ["X", "Y"]  # highest premium first, no-premium last, Z excluded
    x = ov.iloc[0]
    assert x["name"] == "X fund" and x["data_date"] == D13 and x["fv_method"] == "free_futures"
    assert x["close"] == 150.0 and x["nav_known"] == 100.0 and x["fv"] == 120.0
    assert x["prem_fv"] == 0.25 and x["prem_raw"] == 0.5 and x["prem_fv_date"] == D13
    assert x["prem_fv_chg_1d"] == pytest.approx(0.05)  # 0.25 - 0.20
    # One week before 13 Nov is 6 Nov: the last row on or before it is 5 Nov (0.10).
    assert x["prem_fv_chg_1w"] == pytest.approx(0.15)
    assert x["band_source"] == "official" and (x["band_low"], x["band_high"]) == (100.0, 160.0)
    assert x["pct_to_upper"] == pytest.approx(160 / 150 - 1)  # +6.67%
    assert x["pct_to_lower"] == pytest.approx(100 / 150 - 1)  # -33.33%
    assert x["band_position"] == pytest.approx((150 - 100) / (160 - 100))  # 0.8333
    assert x["last_hit_up"] == D12  # the latest row did not hit, the one before did
    assert x["units_change_last"] == 600000 and x["creation_units_last"] == 1.0
    assert x["units_change_date"] == D13


def test_overview_falls_back_to_computed_band_and_handles_missing_values() -> None:
    y = dd.latest_overview(sample_etf_day(), MASTER).iloc[1]
    assert y["band_source"] == "computed"
    assert y["band_position"] == pytest.approx(0.5)  # (100 - 90) / (110 - 90)
    assert y["pct_to_upper"] == pytest.approx(0.10) and y["pct_to_lower"] == pytest.approx(-0.10)
    assert (
        np.isnan(y["prem_fv"]) and np.isnan(y["prem_fv_chg_1d"]) and np.isnan(y["prem_fv_chg_1w"])
    )
    assert y["last_hit_up"] is None and y["units_change_date"] is None and y["prem_fv_date"] is None


def test_overview_premium_date_lags_data_date_when_latest_nav_is_missing() -> None:
    df = sample_etf_day()
    df.loc[(df["symbol"] == "X") & (df["date"] == D13), "prem_fv"] = np.nan  # NAV not in yet
    x = dd.latest_overview(df, MASTER).iloc[0]
    assert x["data_date"] == D13 and x["prem_fv_date"] == D12 and np.isnan(x["prem_fv"])


def test_overview_one_week_change_is_nan_when_history_is_short() -> None:
    df = sample_etf_day()
    short = df[(df["symbol"] == "X") & (df["date"] != D5)]
    x = dd.latest_overview(short, MASTER).iloc[0]
    assert x["prem_fv_chg_1d"] == pytest.approx(0.05) and np.isnan(x["prem_fv_chg_1w"])


def test_overview_empty_inputs() -> None:
    for empty in (None, pd.DataFrame(), pd.DataFrame({"symbol": [], "date": []})):
        out = dd.latest_overview(empty, MASTER)
        assert out.empty and list(out.columns) == list(dd.OVERVIEW_FIELDS)


def test_overview_without_optional_columns_does_not_crash() -> None:
    bare = pd.DataFrame({"symbol": ["A"], "date": [D13], "close": [10.0]})
    out = dd.latest_overview(bare)
    assert out.iloc[0]["symbol"] == "A" and np.isnan(out.iloc[0]["prem_fv"])
    assert "prem_fv" in dd.overview_missing_columns(bare)


def test_overview_missing_columns_lists_only_absent_ones() -> None:
    df = sample_etf_day().drop(columns=["fv", "hit_up"])
    assert dd.overview_missing_columns(df) == ["fv", "hit_up"]
    assert dd.overview_missing_columns(None) == list(dd.OVERVIEW_SOURCE_COLUMNS)


def test_display_overview_scales_premiums_to_percent_and_renames() -> None:
    shown = dd.display_overview(dd.latest_overview(sample_etf_day(), MASTER))
    assert shown.iloc[0]["Premium, fair value (%)"] == pytest.approx(25.0)
    assert shown.iloc[0]["Change vs 1 day (points)"] == pytest.approx(5.0)
    assert shown.iloc[0]["Upper limit vs close (%)"] == pytest.approx((160 / 150 - 1) * 100)
    assert shown.iloc[0]["Close (Rs)"] == 150.0  # rupees are not scaled
    assert "prem_fv" not in shown.columns


# -- series ----
def test_premium_series_filters_symbol_measure_and_start() -> None:
    df = sample_etf_day()
    out = dd.premium_series(df, ["X"], "prem_fv")
    assert out["date"].tolist() == [D5, D12, D13]
    assert out["value"].tolist() == [0.10, 0.20, 0.25]
    assert dd.premium_series(df, ["X"], "prem_fv", start=D12)["date"].tolist() == [D12, D13]
    assert dd.premium_series(df, ["X", "Y"], "prem_fv")["symbol"].unique().tolist() == [
        "X"
    ]  # Y has none
    assert dd.premium_series(df, ["X"], "no_such_column").empty
    assert dd.premium_series(df, [], "prem_fv").empty
    assert dd.premium_series(None, ["X"], "prem_fv").empty


def test_available_measures_hides_missing_columns() -> None:
    got = dd.available_measures(sample_etf_day())
    assert set(got) == {"prem_fv", "prem_nav_known", "prem_raw"}
    assert dd.available_measures(None) == {}
    assert "x_april" in dd.LOG_MEASURES and "x_april" in dd.MEASURES


def test_level_frame_and_band_frame() -> None:
    df = sample_etf_day()
    lv = dd.level_frame(df, "X")
    assert list(lv.columns) == ["date", "close", "nav_known_at_close", "fv"]
    assert lv["fv"].iloc[-1] == 120.0 and np.isnan(lv["fv"].iloc[0])
    df["band_high_max"] = np.where(
        df["symbol"] == "X", np.inf, 200.0
    )  # gold-style: no widest limit
    bf = dd.band_frame(df, "X")
    assert bf["band_high_max"].isna().all()  # infinity is dropped so the chart skips it
    assert bf["band_high_official"].iloc[-1] == 160.0 and "band_low_max" not in bf.columns


# -- regimes and countdown ----
def test_regime_markers_from_rules_yaml() -> None:
    markers = dd.regime_markers(load_rules().regimes)
    assert [(m.day, m.planned) for m in markers] == [
        (date(2026, 9, 7), False),
        (date(2027, 4, 1), True),
    ]
    assert "(planned)" in markers[1].label and "(planned)" not in markers[0].label
    inside = dd.markers_in_range(markers, date(2026, 1, 1), date(2026, 11, 13))
    assert [m.day for m in inside] == [date(2026, 9, 7)]
    assert dd.markers_in_range(markers, None, D13) == []


def test_days_until() -> None:
    # 14 Nov -> 30 Nov = 16, + Dec 31 + Jan 31 + Feb 28 + Mar 31 = 137, + 1 for 1 April = 138.
    assert dd.days_until(dd.APRIL_2027, date(2026, 11, 14)) == 138
    assert dd.days_until(dd.APRIL_2027, dd.APRIL_2027) == 0
    assert dd.days_until(date(2026, 1, 1), date(2026, 1, 3)) == -2


# -- intraday ----
def test_intraday_premium_and_snapshot_days() -> None:
    snaps = snapshots(
        [
            ("X", ist(D13, 10, 0), "amc_motilal", None, 100.0),
            ("X", ist(D13, 10, 0), "nse_etf_api", 130.0, None),
            ("Y", ist(D13, 10, 0), "amc_motilal", None, 50.0),
            ("Y", ist(D13, 10, 0), "nse_etf_api", 60.0, None),
            ("X", ist(D12, 10, 0), "amc_motilal", None, 100.0),
        ]
    )
    out = dd.intraday_premium(snaps, "X", D13, ["amc_motilal"])
    assert out["symbol"].tolist() == ["X"] and out["prem"].round(10).tolist() == [
        0.3
    ]  # 130/100 - 1
    assert dd.intraday_premium(snaps, "X", date(2026, 11, 16), ["amc_motilal"]).empty
    assert dd.intraday_premium(None, "X", D13, ["amc_motilal"]).empty
    assert dd.snapshot_days(snaps) == [D13, D12]
    assert dd.snapshot_days(None) == []


# -- data quality ----
def test_dq_summary_counts() -> None:
    out = dd.dq_summary(sample_etf_day()).set_index("flag")
    row = out.loc["dq_missing_close"]
    # X on 13 Nov is flagged; Y is False; the other rows have no value (not checked).
    assert row["rows_flagged"] == 1 and row["rows_checked"] == 2
    assert row["latest_day_flagged"] == "X" and row["last_flagged_date"] == D13
    assert (
        dd.dq_summary(None).empty
        and dd.dq_summary(pd.DataFrame({"symbol": ["A"], "date": [D13]})).empty
    )


def test_last_data_dates() -> None:
    snaps = snapshots(
        [
            ("X", ist(D13, 15, 45), "amc_motilal", None, 100.0),
            ("X", ist(D12, 10, 0), "nse_etf_api", 130.0, None),
        ]
    )
    nav = pd.DataFrame({"symbol": ["X"], "nav_date": [D12], "nav": [1.0]})
    tables = dd.Tables({"etf_day": sample_etf_day(), "nav_daily": nav, "inav_snapshots": snaps})
    out = dd.last_data_dates(tables, today=date(2026, 11, 14)).set_index("source")
    assert out.loc["etf_day", "latest"] == "2026-11-13" and out.loc["etf_day", "age_days"] == 1
    assert out.loc["nav_daily", "latest"] == "2026-11-12" and out.loc["nav_daily", "age_days"] == 2
    assert out.loc["prices_daily", "latest"] == "not built" and out.loc["prices_daily", "rows"] == 0
    assert out.loc["inav_snapshots / amc_motilal", "latest"] == "2026-11-13 15:45 IST"
    assert out.loc["inav_snapshots / nse_etf_api", "age_days"] == 2


def test_last_data_dates_without_snapshots() -> None:
    out = dd.last_data_dates(dd.Tables({}), today=D13).set_index("source")
    assert out.loc["inav_snapshots", "latest"] == "not built"


# -- wording ----
def test_text_has_no_advice_words_and_disclaimer_matches_spec() -> None:
    assert dd.DISCLAIMER == (
        "For research and information only. Not investment advice. "
        "Figures may contain errors; check official NSE and AMFI data before acting."
    )
    texts = [*dd.MEASURES.values(), *dd.DISPLAY_LABELS.values(), *dd.LEVEL_COLUMNS.values()]
    texts += list(dd.BAND_COLUMNS.values())
    for text in texts:
        assert not BANNED.search(text), text
    for name in ("app.py", "charts.py", "data.py"):
        path = ROOT / "dashboard" / name
        if path.exists():
            found = BANNED.findall(path.read_text(encoding="utf-8"))
            assert not found, f"{name}: {found}"


# --------------------------------------------------------------------------
# April 2027 countdown
# --------------------------------------------------------------------------
def test_days_to_april_uses_the_indian_date() -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    utc = ZoneInfo("UTC")
    # 14 Nov 2026 12:00 IST -> 138 days (see test_days_until).
    assert dd.days_to_april(datetime(2026, 11, 14, 12, 0, tzinfo=dd.IST)) == 138
    # 31 Mar 2027 18:29 UTC = 23:59 IST on 31 Mar: still 1 day to go.
    assert dd.days_to_april(datetime(2027, 3, 31, 18, 29, tzinfo=utc)) == 1
    # 31 Mar 2027 18:30 UTC = 00:00 IST on 1 Apr: the day has come (0), even though the UTC
    # (and New York) date is still 31 Mar.
    assert dd.days_to_april(datetime(2027, 3, 31, 18, 30, tzinfo=utc)) == 0
    assert dd.days_to_april(datetime(2027, 3, 31, 14, 30, tzinfo=ZoneInfo("America/New_York"))) == 0
    # Just before midnight IST on 13 Nov 2026 -> 139; just after -> 138.
    assert dd.days_to_april(datetime(2026, 11, 13, 23, 59, tzinfo=dd.IST)) == 139
    assert dd.days_to_april(datetime(2026, 11, 14, 0, 0, tzinfo=dd.IST)) == 138
    # After the date: negative.
    assert dd.days_to_april(datetime(2027, 4, 3, 9, 0, tzinfo=dd.IST)) == -2


def test_days_to_april_refuses_naive_datetimes() -> None:
    from datetime import datetime

    with pytest.raises(ValueError, match="timezone-aware"):
        dd.days_to_april(datetime(2026, 11, 14, 12, 0))  # noqa: DTZ001 (naive on purpose)


def x_april_day() -> pd.DataFrame:
    # x_april = ln(close / (1.2 x NAV)).
    #   A on D12: close 120, NAV 100 -> ln(120 / 120) = 0.
    #   A on D13: close 132, NAV 100 -> ln(1.1) = 0.0953102 -> close 10% above 1.2 x NAV.
    #   B on D12: close 90,  NAV 100 -> ln(0.75) = -0.2876821 -> 25% below.
    #   B on D13: no NAV -> x_april missing, row dropped.
    return pd.DataFrame(
        {
            "symbol": ["A", "A", "B", "B"],
            "date": [D12, D13, D12, D13],
            "x_april": [0.0, np.log(1.1), np.log(0.75), np.nan],
            "x_april_fv": [0.01, 0.02, 0.03, 0.04],
        }
    )


def test_x_april_series_and_close_vs_cap() -> None:
    out = dd.x_april_series(x_april_day(), ["A", "B"])
    assert list(out.columns) == ["symbol", "date", "value", "close_vs_cap"]
    assert list(zip(out["symbol"], out["date"], strict=True)) == [
        ("A", D12),
        ("A", D13),
        ("B", D12),
    ]
    assert out["close_vs_cap"].tolist() == pytest.approx([0.0, 0.10, -0.25])
    # Start date and the fair-value variant.
    late = dd.x_april_series(x_april_day(), ["A", "B"], column="x_april_fv", start=D13)
    assert late["value"].tolist() == pytest.approx([0.02, 0.04])
    # Missing column, no symbols, no table: empty but with the columns.
    for frame in (
        dd.x_april_series(x_april_day().drop(columns="x_april"), ["A"]),
        dd.x_april_series(x_april_day(), []),
        dd.x_april_series(None, ["A"]),
    ):
        assert frame.empty and "close_vs_cap" in frame.columns


def test_x_april_latest() -> None:
    latest = dd.x_april_latest(dd.x_april_series(x_april_day(), ["A", "B"]))
    assert latest["symbol"].tolist() == ["A", "B"]  # highest first
    assert latest["date"].tolist() == [D13, D12]  # B's newest value is from D12
    assert latest["above_cap"].tolist() == [True, False]
    assert latest["close_vs_cap"].tolist() == pytest.approx([0.10, -0.25])
    assert dd.x_april_latest(dd.x_april_series(None, ["A"])).empty


# --------------------------------------------------------------------------
# Events
# --------------------------------------------------------------------------
def sample_events() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "event_id": ["e1", "e2", "e3", "e4"],
            "date": [date(2026, 9, 25), date(2026, 10, 10), date(2026, 9, 7), date(2026, 10, 10)],
            "ts_ist": pd.to_datetime(
                [None, "2026-10-10 18:05", None, "2026-10-10 09:00"]
            ).tz_localize(dd.IST),
            "type": ["warning", "other", "rule", "other"],
            "category": ["caution", "amc_notice", "rule", "media"],
            "direction": [None, "premium_reducing", None, "neutral"],
            "symbols": [None, "MONQ50", None, "MAFANG; MASPTOP50"],
            "first_tradable_session": [
                date(2026, 9, 28),
                date(2026, 10, 13),
                date(2026, 9, 7),
                None,
            ],
            "description": ["BSE caution", "Motilal notice " + "x" * 80, "Regime starts", "News"],
            "url": [None, "https://example.org/n", None, None],
            "source_primary": [None, "data/raw/x.pdf", None, None],
            "verified": [False, True, None, False],
            "source_file": ["data/manual/events.csv:1"] * 4,
        }
    )


def test_events_table_sorted_newest_first_with_ist_text() -> None:
    out = dd.events_table(sample_events())
    # 10 Oct twice (18:05 before 09:00), then 25 Sep, then 7 Sep.
    assert out["event_id"].tolist() == ["e2", "e4", "e1", "e3"]
    assert out["ts_ist"].tolist() == ["2026-10-10 18:05 IST", "2026-10-10 09:00 IST", "", ""]
    assert out["verified"].tolist() == [True, False, False, False]  # missing -> False
    assert list(out.columns)[:4] == ["date", "ts_ist", "first_tradable_session", "category"]
    assert out["first_tradable_session"].iloc[0] == date(2026, 10, 13)
    assert "source_file" not in out.columns


def test_events_table_empty_and_missing() -> None:
    for frame in (None, sample_events().iloc[0:0]):
        out = dd.events_table(frame)
        assert out.empty and list(out.columns) == list(dd.EVENT_COLUMNS)


def test_events_table_refuses_naive_timestamps() -> None:
    events = sample_events()
    events["ts_ist"] = pd.to_datetime([None, "2026-10-10 18:05", None, None])
    with pytest.raises(ValueError, match="timezone-aware"):
        dd.events_table(events)


def test_event_markers_filter_and_placement() -> None:
    events = sample_events()
    marks = dd.event_markers(events, ["MONQ50"], skip_rule_days=[date(2026, 9, 7)])
    # e1 (all ETFs) on its first tradable session 28 Sep; e2 (MONQ50) on 13 Oct.
    # e3 is a rule on a regime day -> skipped; e4 concerns other ETFs -> skipped.
    assert [m.day for m in marks] == [date(2026, 9, 28), date(2026, 10, 13)]
    assert all(m.kind == "event" and not m.planned for m in marks)
    assert marks[0].label == "caution: BSE caution"
    assert marks[1].label.endswith("...") and len(marks[1].label) <= len("amc_notice: ") + 60
    # e4 falls back to its date (no first tradable session); no symbol filter = every event.
    every = dd.event_markers(events)
    assert len(every) == 4 and date(2026, 10, 10) in [m.day for m in every]
    assert dd.event_markers(events, ["MAFANG"])[-1].label == "media: News"
    assert dd.event_markers(None, ["X"]) == [] and dd.event_markers(events.iloc[0:0]) == []
