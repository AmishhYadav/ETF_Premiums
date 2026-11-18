"""Dry run of the Dec 2026 Bloomberg session with SYNTHETIC files (D52).

Every number is invented (sentinel-looking: futures near 91,357, rupee rates near 71.13).
Nothing here is Bloomberg data. The files are written in the documented template format
(docs/bloomberg_template.md) into a throwaway folder, then read back through the same paths
the real build uses: importer, coverage check, fair-value inputs, beta, minute bars, China table
and the open-dataset licence guard.
"""

from __future__ import annotations

import math
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from etfp import build, calendars
from etfp.clean import china_etf
from etfp.collect import bloomberg as bb
from etfp.config import load_etfs
from etfp.export import ETF_DAY_COLUMNS
from etfp.export_dataset import assert_no_denied, load_dataset_config, select_table
from etfp.features import limit_days

HEADER = "date,time,timezone,instrument,field,value,note\n"
START, END = date(2025, 8, 1), date(2026, 9, 4)
NQ, SPOT = 91357.0, 71.1357


def _path(base: float, i: float, vol: float) -> float:
    return base * math.exp(vol * math.sin(i / 7.0) + 0.0002 * i)


def synthetic_daily_rows(*, fx_at_us_close: bool = True) -> list[str]:
    """Daily rows: future and rupee at 15:30 IST, future (and optionally rupee) at the US close."""
    rows: list[str] = []
    for i, d in enumerate(calendars.sessions("IN", START, END)):
        rows.append(f"{d},15:30,Asia/Kolkata,NQ1,PX_LAST,{_path(NQ, i, 0.03):.4f},SYNTHETIC")
        rows.append(f"{d},15:30,Asia/Kolkata,ES1,PX_LAST,{_path(NQ + 2218, i, 0.02):.4f},SYNTHETIC")
        rows.append(f"{d},15:30,Asia/Kolkata,USDINR,PX_LAST,{_path(SPOT, i, 0.005):.4f},SYNTHETIC")
    for i, d in enumerate(calendars.sessions("US", START, END)):
        close = calendars.market_close("US", d).strftime("%H:%M")
        rows.append(f"{d},{close},America/New_York,NQ1,PX_LAST,{_path(NQ, i + 0.5, 0.03):.4f},S")
        rows.append(
            f"{d},{close},America/New_York,ES1,PX_LAST,{_path(NQ + 2218, i + 0.5, 0.02):.4f},S"
        )
        if fx_at_us_close:
            fx = _path(SPOT, i + 0.3, 0.005)
            rows.append(f"{d},{close},America/New_York,USDINR,PX_LAST,{fx:.4f},SYNTHETIC")
    return rows


def write_csv(root: Path, name: str, rows: list[str]) -> None:
    folder = root / "data" / "raw" / "bloomberg" / "2026-12-01"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(HEADER + "\n".join(rows) + "\n", encoding="utf-8")


@pytest.fixture(scope="module")
def universe():
    return load_etfs()


@pytest.fixture(scope="module")
def us_only_universe(universe):
    """Only the US ETFs with a futures proxy (the HK rows are not part of this dry run)."""

    class _U:
        def tracked(self):
            return [e for e in universe.tracked() if e.underlying_market == "US"]

    return _U()


@pytest.fixture(scope="module")
def root(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("bbg_dry_run")
    write_csv(root, "daily.csv", synthetic_daily_rows())
    return root


def test_import_and_coverage_pass_for_a_complete_export(root, us_only_universe) -> None:
    imported = bb.import_directory(root / "data" / "raw" / "bloomberg")
    lines, problems = bb.describe_import(imported, us_only_universe)
    assert [p for p in problems if "China" not in p] == []  # no China rows in this file
    assert any("NQ1" in line for line in lines)


def test_rupee_only_at_indian_close_is_reported_not_silently_skipped(
    tmp_path, us_only_universe
) -> None:
    """The build reads the NAV-time rate between the US open and one hour after the US close.
    A file with the rupee only at 15:30 IST used to pass the coverage check and then give no
    Bloomberg fair value; now the check names the missing US-day rows."""
    write_csv(tmp_path, "daily.csv", synthetic_daily_rows(fx_at_us_close=False))
    imported = bb.import_directory(tmp_path / "data" / "raw" / "bloomberg")
    _, problems = bb.describe_import(imported, us_only_universe)
    assert any("USDINR" in p and "America/New_York" in p for p in problems)


def test_fair_value_inputs_come_from_bloomberg_and_match_hand_computation(root, universe) -> None:
    data = build.load_underlying(root / "data" / "raw")
    etf = universe.by_symbol("MONQ50")
    t = date(2026, 9, 4)
    a = calendars.previous_session("US", t)  # NAV known at the Indian close of t is dated a
    got = build.fair_value_inputs(etf, [t], [a], data)
    row = got.iloc[0]
    assert row.source == "bloomberg"
    sessions = calendars.sessions("IN", START, END)
    us = calendars.sessions("US", START, END)
    want_ft = round(_path(NQ, sessions.index(t), 0.03), 4)
    want_fa = round(_path(NQ, us.index(a) + 0.5, 0.03), 4)
    want_st = round(_path(SPOT, sessions.index(t), 0.005), 4)
    want_sa = round(_path(SPOT, us.index(a) + 0.3, 0.005), 4)
    assert (row.futures_t, row.futures_a, row.fx_t, row.fx_a) == pytest.approx(
        (want_ft, want_fa, want_st, want_sa)
    )
    assert "bloomberg/2026-12-01/daily.csv" in row.files


def test_beta_uses_250_nav_dates_and_recovers_a_known_beta(root, universe) -> None:
    """NAV built to move 0.8 x the future (plus the rupee), so the regression must return 0.8."""
    data = build.load_underlying(root / "data" / "raw")
    etf = universe.by_symbol("MONQ50")
    us = calendars.sessions("US", START, END)
    nav, level = {}, 100.0
    prev_f = prev_s = None
    for i, d in enumerate(us):
        f, s = _path(NQ, i + 0.5, 0.03), _path(SPOT, i + 0.3, 0.005)
        if prev_f is not None:
            level *= math.exp(0.8 * math.log(f / prev_f) + math.log(s / prev_s))
        prev_f, prev_s = f, s
        nav[d] = level
    est = build.estimate_beta(etf, pd.Series(nav), data)
    assert est.ok, est.reason
    assert est.n_obs == 250
    assert est.beta == pytest.approx(0.8, abs=1e-3)


def test_minute_bars_become_the_limit_day_timeline(universe) -> None:
    rows = []
    for k in range(375):
        hh, mm = divmod(9 * 60 + 15 + k, 60)
        for field, value in (("PX_LAST", 150.0 + k * 0.01), ("VOLUME", 1000.0)):
            rows.append(
                f"2026-09-02,{hh:02d}:{mm:02d},Asia/Kolkata,MONQ50.1M,{field},{value:.4f},SYNTHETIC"
            )
    frame = bb.parse(HEADER + "\n".join(rows) + "\n", "bloomberg/2026-12-01/bars.csv")
    bars = bb.etf_minute_bars(frame, "MONQ50")
    tl = limit_days.bar_timeline(bars, date(2026, 9, 2))
    assert tl.source == "bloomberg_1m" and len(tl) == 375


def test_minute_bars_in_the_wrong_time_zone_are_flagged(universe) -> None:
    # A terminal set to New York time would stamp the 09:15 IST bar at 23:45 the day before.
    rows = [
        f"2026-09-01,23:{45 + k:02d},Asia/Kolkata,MONQ50.1M,PX_LAST,150.0,SYNTHETIC"
        for k in range(5)
    ]
    frame = bb.parse(HEADER + "\n".join(rows) + "\n", "bloomberg/x.csv")
    imported = bb.BloombergImport(frame, (), 0)
    _, problems = bb.describe_import(imported, universe)
    assert any("MONQ50" in p and "outside 09:15-15:29" in p for p in problems)


def test_china_etf_table_fills_from_synthetic_rows() -> None:
    rows = []
    for i, d in enumerate(pd.bdate_range("2026-11-02", "2026-11-06")):
        px, nav = 3.1357 + 0.01 * i, 3.0
        rows.append(f"{d.date()},15:00,Asia/Shanghai,513100 CH,PX_LAST,{px:.4f},SYNTHETIC")
        rows.append(f"{d.date()},15:00,Asia/Shanghai,513100 CH,FUND_NET_ASSET_VAL,{nav:.4f},S")
        rows.append(f"{d.date()},15:00,Asia/Shanghai,513100 CH,EQY_SH_OUT,9137,millions (VERIFY)")
    frame = bb.parse(HEADER + "\n".join(rows) + "\n", "bloomberg/2026-12-01/cn.csv")
    out = china_etf.from_frame(frame)
    assert len(out) == 5 and set(out["code"]) == {"513100"}
    assert out["premium"].iloc[0] == pytest.approx(3.1357 / 3.0 - 1)
    assert out["shares_outstanding"].eq(9137.0).all()


def test_open_dataset_guard_drops_every_fair_value_column() -> None:
    """Fill every etf_day column with a sentinel; nothing Bloomberg-derived may come out."""
    cfg = load_dataset_config()
    sentinel = 91357.0
    frame = pd.DataFrame({c: [sentinel] for c in ETF_DAY_COLUMNS})
    out, _ = select_table("etf_day", frame, cfg)
    assert not [c for c in out.columns if cfg.column_denied("etf_day", c)]
    assert not [c for c in out.columns if "fv" in c or "futures" in c or c in {"pi", "cap_pi"}]
    assert_no_denied({"etf_day": out}, cfg)
    for table in ("china_etf_daily", "underlying_daily", "fx_daily"):
        assert cfg.table_denied(table)
    limits = pd.DataFrame({"obs_source": ["bloomberg_1m", "none"]})
    assert cfg.deny_rows["limit_day"]["obs_source"] == ["bloomberg_1m"]
    assert limits["obs_source"].isin(cfg.deny_rows["limit_day"]["obs_source"]).sum() == 1


def test_build_report_warns_when_bloomberg_did_not_reach_the_tables() -> None:
    etf_day = pd.DataFrame(
        {
            "symbol": ["MONQ50", "MONQ50"],
            "underlying_market": ["US", "US"],
            "fv_method": ["nav_only", "bloomberg"],
            "fv_beta_method": ["beta_scaled", "beta_scaled"],
            "fv_beta_value": [0.8, 0.8],
            "fv_beta_n": [250.0, 250.0],
        }
    )
    limit_day = pd.DataFrame({"obs_source": ["none", "bloomberg_1m"]})
    china = pd.DataFrame({"code": ["513100"]})
    _, warns = bb.build_report(etf_day, limit_day, china)
    assert warns == []
    bad_etf = etf_day.assign(fv_method="nav_only", fv_beta_n=120.0)
    _, warns = bb.build_report(bad_etf, limit_day.assign(obs_source="none"), china.iloc[0:0])
    assert len(warns) == 4  # no bloomberg fv, beta n, no minute bars, empty China table
