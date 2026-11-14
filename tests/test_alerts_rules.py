"""Tests for the alert rules. Premium values are fractions (0.25 = 25%), as in etf_day.

Every expectation below is worked out by hand in the comment above it.
"""

from __future__ import annotations

import re
from datetime import date, datetime

import pandas as pd
import pytest

from etfp.alerts import rules
from etfp.alerts.rules import AlertConfig, evaluate, levels_crossed, load_alert_config
from etfp.config import load_collect_settings
from etfp.paths import config_dir
from test_alerts_helpers import (
    D_LAST,
    D_PREV,
    etf_day,
    ist,
    make_config,
    only,
    snapshots,
)

NOW = ist(D_LAST, 22, 0)  # Friday 13 Nov 2026, 22:00 IST: after the 21:00 "data should be in" time
BANNED = re.compile(r"\b(buy|sell|undervalued|overvalued|opportunity|cheap|expensive)\b", re.I)


def two_days(
    prev: float | None, cur: float | None, symbol: str = "MONQ50", **extra
) -> pd.DataFrame:
    return etf_day(
        [
            {"symbol": symbol, "date": D_PREV, "prem_fv": prev, "close": 100.0},
            {"symbol": symbol, "date": D_LAST, "prem_fv": cur, "close": 100.0, **extra},
        ]
    )


def run(df: pd.DataFrame, cfg: AlertConfig, **kw):
    return evaluate(df, config=cfg, now=kw.pop("now", NOW), **kw)


# -- config ----
def test_shipped_config_loads() -> None:
    cfg = load_alert_config(config_dir() / "alerts.yaml")
    assert cfg.premium_cross.levels_pct == [25, 50, 100]
    assert cfg.premium_jump.points == 10
    assert "amc_nippon" not in cfg.inav_premium_cross.inav_sources  # untrusted source (D15)


def test_config_rejects_unknown_key_and_bad_levels(tmp_path) -> None:
    bad = tmp_path / "alerts.yaml"
    bad.write_text(
        (config_dir() / "alerts.yaml").read_text().replace("prem_fv\n", "prem_fv\n  typo: 1\n", 1)
    )
    with pytest.raises(ValueError, match="invalid configuration"):
        load_alert_config(bad)
    with pytest.raises(ValueError):
        make_config(premium_cross={"levels_pct": [-5]})
    with pytest.raises(ValueError):
        make_config(premium_cross={"levels_pct": []})


# -- crossing helper ----
def test_levels_crossed_hand_examples() -> None:
    levels = [0.25, 0.50, 1.00]
    assert levels_crossed(0.20, 0.55, levels) == ([0.25, 0.50], [])
    assert levels_crossed(1.05, 0.95, levels) == ([], [1.00])
    assert levels_crossed(0.30, 0.40, levels) == ([], [])
    assert levels_crossed(0.24, 0.25, levels) == (
        [0.25],
        [],
    )  # landing exactly on a level counts as above
    assert levels_crossed(0.25, 0.24, levels) == (
        [],
        [0.25],
    )  # leaving it downward counts as a fall
    assert levels_crossed(0.25, 0.25, levels) == ([], [])


# -- premium_cross ----
def test_premium_cross_up_two_levels_is_one_alert() -> None:
    # 20% -> 55% passes 25% and 50% upward: one alert naming both.
    alerts = run(two_days(0.20, 0.55, fv_method="free_futures"), only("premium_cross"))
    assert len(alerts) == 1
    a = alerts[0]
    assert a.rule == "premium_cross" and a.symbol == "MONQ50" and a.day == D_LAST
    assert a.key == "premium_cross|MONQ50|2026-11-13|up"
    assert "rose above 25% and 50%" in a.message
    assert "now 55.0%" in a.message and "2026-11-12: 20.0%" in a.message
    assert "Fair value method: free_futures" in a.message


def test_premium_cross_down_and_nav_only_note() -> None:
    alerts = run(two_days(1.05, 0.95, fv_method="nav_only"), only("premium_cross"))
    assert [a.key for a in alerts] == ["premium_cross|MONQ50|2026-11-13|down"]
    assert "fell below 100%" in alerts[0].message
    assert "fair value equals the latest known NAV" in alerts[0].message


@pytest.mark.parametrize(
    ("prev", "cur"),
    [(0.30, 0.40), (None, 0.60), (0.60, None), (float("nan"), 0.60), (0.55, 0.55)],
)
def test_premium_cross_nothing_when_no_crossing_or_missing_reading(prev, cur) -> None:
    assert run(two_days(prev, cur), only("premium_cross")) == []


def test_premium_cross_needs_a_previous_row() -> None:
    df = etf_day([{"symbol": "MONQ50", "date": D_LAST, "prem_fv": 0.60}])
    assert run(df, only("premium_cross")) == []


def test_premium_cross_ignores_symbol_without_a_row_on_the_latest_day_and_untracked() -> None:
    old = etf_day(
        [
            {"symbol": "OLD", "date": date(2026, 11, 11), "prem_fv": 0.10},
            {
                "symbol": "OLD",
                "date": D_PREV,
                "prem_fv": 0.60,
            },  # crossed, but not on the latest day
            {"symbol": "MONQ50", "date": D_PREV, "prem_fv": 0.20},
            {"symbol": "MONQ50", "date": D_LAST, "prem_fv": 0.22},
            {"symbol": "OTHER", "date": D_PREV, "prem_fv": 0.20},
            {"symbol": "OTHER", "date": D_LAST, "prem_fv": 0.60},
        ]
    )
    alerts = run(old, only("premium_cross"))
    assert [a.symbol for a in alerts] == ["OTHER"]
    assert run(old, only("premium_cross"), tracked={"MONQ50"}) == []


def test_premium_cross_accepts_iso_string_and_timestamp_dates() -> None:
    df = two_days(0.20, 0.30)
    df["date"] = [pd.Timestamp("2026-11-12"), "2026-11-13"]
    assert [a.day for a in run(df, only("premium_cross"))] == [D_LAST]


def test_premium_cross_other_measure_is_used_and_missing_column_is_quiet() -> None:
    df = two_days(0.0, 0.0)
    df["prem_raw"] = [0.40, 0.60]
    cfg = only("premium_cross", measure="prem_raw", levels_pct=[50])
    out = run(df, cfg)
    assert len(out) == 1 and "premium over the same-day NAV rose above 50%" in out[0].message
    assert run(two_days(0.2, 0.6).drop(columns=["prem_fv"]), only("premium_cross")) == []


# -- premium_jump ----
def test_premium_jump_threshold_is_strict() -> None:
    # 30% -> 45% is 15 points (> 10): alert. 30% -> 40% is exactly 10 points: no alert.
    up = run(two_days(0.30, 0.45), only("premium_jump"))
    assert len(up) == 1 and "rose by 15.0 points" in up[0].message
    assert up[0].key == "premium_jump|MONQ50|2026-11-13"
    assert run(two_days(0.30, 0.40), only("premium_jump")) == []
    down = run(two_days(0.50, 0.30), only("premium_jump"))
    assert len(down) == 1 and "fell by 20.0 points" in down[0].message


# -- band_touch ----
def test_band_touch_upper_and_lower_and_missing_flags() -> None:
    both = two_days(
        0.3, 0.3, close_at_upper=True, last_at_up=False, band_high_official=250.0,
        close_at_lower=False, last_at_down=True, band_low_initial=80.0,
    )  # fmt: skip
    keys = {a.key: a for a in run(both, only("band_touch"))}
    assert set(keys) == {
        "band_touch|MONQ50|2026-11-13|upper",
        "band_touch|MONQ50|2026-11-13|lower",
    }
    upper = keys["band_touch|MONQ50|2026-11-13|upper"].message
    assert "the close sat at the upper price limit" in upper and "upper limit Rs 250.00" in upper
    lower = keys["band_touch|MONQ50|2026-11-13|lower"].message
    assert "the last trade sat at the lower price limit" in lower and "Rs 80.00" in lower


def test_band_touch_unknown_flag_is_not_an_alert() -> None:
    df = two_days(0.3, 0.3, close_at_upper=pd.NA, last_at_up=None)
    df["close_at_upper"] = pd.array([False, pd.NA], dtype="boolean")
    assert run(df, only("band_touch")) == []
    assert run(two_days(0.3, 0.3), only("band_touch")) == []  # columns absent


def test_band_touch_old_day_not_reannounced() -> None:
    df = etf_day(
        [
            {"symbol": "A", "date": D_PREV, "close_at_upper": True},
            {"symbol": "B", "date": D_LAST, "close_at_upper": False},
        ]
    )
    assert run(df, only("band_touch")) == []


# -- disabled / wording ----
def test_disabled_rules_give_nothing() -> None:
    df = two_days(0.2, 0.9, close_at_upper=True)
    assert run(df, only("premium_cross", enabled=False)) == []


def test_no_advice_words_in_any_message() -> None:
    df = two_days(0.20, 0.90, close_at_upper=True, band_high_official=120.0, fv_method="nav_only")
    snaps = snapshots(
        [
            ("MONQ50", ist(D_LAST, 10, 0), "amc_motilal", None, 100.0),
            ("MONQ50", ist(D_LAST, 10, 0), "nse_etf_api", 120.0, None),
            ("MONQ50", ist(D_LAST, 10, 15), "amc_motilal", None, 100.0),
            ("MONQ50", ist(D_LAST, 10, 15), "nse_etf_api", 160.0, None),
        ]
    )
    collect = load_collect_settings()
    alerts = run(
        df, make_config(), now=ist(D_LAST, 11, 0), snapshots=snaps, heartbeat=None, collect=collect
    )
    stale = run(etf_day([{"symbol": "A", "date": D_PREV}]), make_config())  # data_stale
    alerts += stale
    assert {a.rule for a in alerts} >= {
        "premium_cross", "premium_jump", "band_touch", "inav_premium_cross", "recorder_stale",
        "data_stale",
    }  # fmt: skip
    for a in alerts:
        assert not BANNED.search(a.message), a.message


# -- iNAV ----
def snaps_for(prices: list[float], inav: float = 100.0, source: str = "amc_motilal"):
    rows = []
    for i, price in enumerate(prices):
        t = ist(D_LAST, 10, 15 * i)
        rows.append(("MONQ50", t, source, None, inav))
        rows.append(("MONQ50", t, "nse_etf_api", price, None))
    return snapshots(rows)


def test_paired_premium_hand_values() -> None:
    # price 120 / iNAV 100 - 1 = 0.20; 130 -> 0.30; 160 -> 0.60
    out = rules.paired_inav_premium(snaps_for([120, 130, 160]), D_LAST, ["amc_motilal"])
    assert out["prem"].round(10).tolist() == [0.2, 0.3, 0.6]
    assert out["source"].unique().tolist() == ["amc_motilal"]


def test_paired_premium_respects_gap_day_and_source_list() -> None:
    far = snapshots(
        [
            ("A", ist(D_LAST, 10, 0), "amc_motilal", None, 100.0),
            ("A", ist(D_LAST, 10, 30), "nse_etf_api", 120.0, None),  # 30 minutes apart
        ]
    )
    assert rules.paired_inav_premium(far, D_LAST, ["amc_motilal"], 10).empty
    assert len(rules.paired_inav_premium(far, D_LAST, ["amc_motilal"], 45)) == 1
    assert rules.paired_inav_premium(snaps_for([120]), D_PREV, ["amc_motilal"]).empty  # other day
    assert rules.paired_inav_premium(
        snaps_for([120], source="amc_nippon"), D_LAST, ["amc_motilal"]
    ).empty
    assert rules.paired_inav_premium(None, D_LAST, ["amc_motilal"]).empty


def test_paired_premium_uses_first_source_in_priority_order_only() -> None:
    both = pd.concat(
        [
            snaps_for([120], inav=100.0, source="amc_mirae"),
            snaps_for([120], inav=50.0, source="amc_motilal"),
        ]
    )
    out = rules.paired_inav_premium(both, D_LAST, ["amc_motilal", "amc_mirae"])
    assert out["source"].unique().tolist() == ["amc_motilal"]  # priority list order wins
    assert out["prem"].round(10).tolist() == [1.4]  # 120 / 50 - 1


def test_paired_premium_rejects_naive_timestamps() -> None:
    naive = snaps_for([120])
    naive["ts"] = naive["ts"].dt.tz_localize(None)
    with pytest.raises(ValueError, match="timezone-aware"):
        rules.paired_inav_premium(naive, D_LAST, ["amc_motilal"])


def test_inav_cross_levels_and_times() -> None:
    # 0.20 -> 0.30 crosses 25% at 10:15; 0.30 -> 0.60 crosses 50% at 10:30.
    alerts = evaluate(
        None, config=only("inav_premium_cross"), now=ist(D_LAST, 10, 40),
        snapshots=snaps_for([120, 130, 160]),
    )  # fmt: skip
    assert [a.key for a in alerts] == [
        "inav_premium_cross|MONQ50|2026-11-13|up|25%",
        "inav_premium_cross|MONQ50|2026-11-13|up|50%",
    ]
    assert "rose above 25% at 10:15 IST" in alerts[0].message
    assert "= 60.0%" in alerts[1].message and "iNAV Rs 100.00" in alerts[1].message


def test_inav_cross_back_and_forth_same_level_is_one_alert_per_direction() -> None:
    # 0.60 -> 0.40 -> 0.60 -> 0.40: 50% is crossed down twice and up once; one alert each way.
    alerts = evaluate(
        None, config=only("inav_premium_cross"), now=ist(D_LAST, 11, 0),
        snapshots=snaps_for([160, 140, 160, 140]),
    )  # fmt: skip
    assert [a.key.split("|", 3)[3] for a in alerts] == ["down|50%", "up|50%"]


def test_inav_cross_only_today_and_tracked() -> None:
    snaps = snaps_for([120, 130])
    assert (
        evaluate(None, config=only("inav_premium_cross"), now=ist(D_PREV, 11, 0), snapshots=snaps)
        == []
    )
    out = evaluate(
        None,
        config=only("inav_premium_cross"),
        now=ist(D_LAST, 11, 0),
        snapshots=snaps,
        tracked={"X"},
    )
    assert out == []


# -- data failures ----
def test_data_stale_cases() -> None:
    cfg = only("data_failure")
    fresh = etf_day([{"symbol": "A", "date": D_LAST}])
    behind = etf_day([{"symbol": "A", "date": D_PREV}])
    # Fri 22:00: Friday's data is due by 21:00.
    assert run(fresh, cfg) == []
    late = run(behind, cfg)
    assert len(late) == 1 and late[0].key == "data_stale|*|2026-11-13"
    assert (
        "latest ETF data is for 2026-11-12" in late[0].message and "2026-11-13" in late[0].message
    )
    # Fri 20:00: not due yet, Thursday's data is enough.
    assert run(behind, cfg, now=ist(D_LAST, 20, 0)) == []
    # Saturday: Friday is the last session, so Thursday is stale.
    sat = run(behind, cfg, now=ist(date(2026, 11, 14), 12, 0))
    assert len(sat) == 1 and sat[0].key == "data_stale|*|2026-11-14"
    assert run(fresh, cfg, now=ist(date(2026, 11, 14), 12, 0)) == []
    # Nothing built at all.
    empty = run(pd.DataFrame(), cfg)
    assert len(empty) == 1 and "etfp build" in empty[0].message
    assert len(evaluate(None, config=cfg, now=NOW)) == 1


def test_data_stale_not_affected_by_tracked_filter() -> None:
    behind = etf_day([{"symbol": "A", "date": D_PREV}])
    assert len(run(behind, only("data_failure"), tracked={"ZZZ"})) == 1


def heartbeat(finished: str, sources: dict | None = None) -> dict:
    return {"last_tick_started": finished, "last_tick_finished": finished, "sources": sources or {}}


def test_recorder_heartbeat_checks() -> None:
    cfg = only("data_failure")
    collect = load_collect_settings()  # 9:15-15:30, heartbeat limit 20 minutes
    at = ist(D_LAST, 11, 0)

    def go(hb, now=at):
        return evaluate(
            pd.DataFrame({"symbol": ["A"], "date": [D_LAST]}),
            config=cfg,
            now=now,
            heartbeat=hb,
            collect=collect,
        )

    assert go(heartbeat("2026-11-13T10:50:00+05:30")) == []  # 10 minutes old: fine
    stale = go(heartbeat("2026-11-13T10:20:00+05:30"))  # 40 minutes old
    assert [a.rule for a in stale] == ["recorder_stale"] and "40 minutes ago" in stale[0].message
    assert [a.rule for a in go(None)] == ["recorder_stale"]  # no heartbeat while open
    assert go(None, now=ist(D_LAST, 9, 20)) == []  # too early: recorder had no time to tick yet
    assert go(None, now=ist(D_LAST, 16, 0)) == []  # after the post-close snapshot
    assert go(None, now=ist(date(2026, 11, 14), 11, 0)) == []  # Saturday


def test_recorder_failed_source_alert() -> None:
    cfg = only("data_failure")
    collect = load_collect_settings()
    hb = heartbeat(
        "2026-11-13T10:55:00+05:30",
        {
            "amc_motilal": {"ok": True, "last_ok_at": "2026-11-13T10:55:00+05:30"},
            "amc_mirae": {
                "ok": False,
                "error_type": "SourceError",
                "last_ok_at": None,
                "error": "secret?",
            },
        },
    )
    out = evaluate(
        pd.DataFrame({"symbol": ["A"], "date": [D_LAST]}),
        config=cfg,
        now=ist(D_LAST, 11, 0),
        heartbeat=hb,
        collect=collect,
    )
    assert [a.key for a in out] == ["source_failed|amc_mirae|2026-11-13"]
    assert "SourceError" in out[0].message and "never" in out[0].message
    assert "secret?" not in out[0].message  # raw error text stays out of messages
    no_collect = evaluate(
        pd.DataFrame({"symbol": ["A"], "date": [D_LAST]}),
        config=cfg,
        now=ist(D_LAST, 11, 0),
        heartbeat=hb,
    )
    assert no_collect == []


def test_evaluate_requires_aware_now() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        evaluate(pd.DataFrame(), config=make_config(), now=datetime(2026, 11, 13, 22, 0))  # noqa: DTZ001
