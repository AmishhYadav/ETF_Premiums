"""R19: reproduce the file-based numbers of the paper's second draft from tests/fixtures.

Expected values are the paper's (paper/draft2/analysis/spot_checks.json and the R19 text).
They are NEVER adjusted to fit the pipeline: if the pipeline disagrees, the test is marked
xfail with the reason and the research side is told (REQUESTS R19).
"""

from __future__ import annotations

import json
import math
from datetime import date
from decimal import ROUND_FLOOR, Decimal

import pytest

from etfp import calendars, paths
from etfp.build import (
    build_all,
    build_unit_tables,
    implied_base_prices,
    load_config,
)
from etfp.export import etf_day_schema_registered
from etfp.features import bands, nav_align
from etfp.store import Store
from test_clean_helpers import make_raw_root

SPOT = paths.project_root() / "paper" / "draft2" / "analysis" / "spot_checks.json"
SIX = ["MONQ50", "MON100", "MASPTOP50", "MAFANG", "MAHKTECH", "HNGSNGBEES"]
D4, D9 = date(2026, 9, 4), date(2026, 11, 9)

# R19 (a): the 4 Sep 2026 close read back from the next-session band equals the NAV-anchored
# limit floor(1.2 x NAV of 2 Sep, 2 dp) for five funds, and does not for HNGSNGBEES.
EXPECTED_LIMIT = {
    "MONQ50": 141.93, "MON100": 324.67, "MASPTOP50": 79.86, "MAFANG": 207.56, "MAHKTECH": 22.10,
}  # fmt: skip


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    root = make_raw_root(tmp_path_factory.mktemp("regression"))
    build_all(root)
    store = Store(root / "data" / "clean")
    with etf_day_schema_registered():
        etf_day = store.read_table("etf_day")
    return root, store, etf_day, load_config(root)


@pytest.fixture(scope="module")
def paper() -> dict:
    assert SPOT.exists(), f"missing {SPOT}"
    return json.loads(SPOT.read_text())


def test_calendar_matches_the_paper_session_counts() -> None:
    # 14 Sep 2026 is an NSE holiday; sessions are counted from 7 Sep (the rule change) as 1.
    assert not calendars.is_session("IN", date(2026, 9, 14))
    sessions = calendars.sessions("IN", date(2026, 9, 7), D9)
    number = {d: i + 1 for i, d in enumerate(sessions)}
    assert number[date(2026, 9, 18)] == 9
    assert number[date(2026, 11, 4)] == 40


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Paper counts 9 Nov 2026 as session 43; the pipeline counts 44 because the "
        "Sunday 8 Nov Diwali Muhurat session is a real NSE session (NSE's 9 Nov "
        "PREV_CLOSE and the 8 Nov security-master band use its close; DECISIONS D24). "
        "Flagged to the research side in REQUESTS R19; P2 window sessions 16-40 are "
        "unaffected (4 Nov is still session 40)."
    ),
)
def test_paper_session_count_for_9_nov() -> None:
    sessions = calendars.sessions("IN", date(2026, 9, 7), D9)
    assert len(sessions) == 43


# ---- (a) 4 Sep 2026 ----
def _nav2(store: Store, symbol: str) -> float:
    nav = store.read_table("nav_daily")
    nav = nav[nav["symbol"] == symbol]
    got = nav_align.align_nav(nav, [D4], "US" if symbol not in ("MAHKTECH", "HNGSNGBEES") else "HK")
    assert got["nav_t2_date"][0] == date(2026, 9, 2)  # T-2 of 4 Sep is 2 Sep
    return float(got["nav_t2"][0])


@pytest.mark.parametrize("symbol", list(EXPECTED_LIMIT))
def test_band_implied_close_equals_nav_limit(built, paper, symbol) -> None:
    _, store, _, cfg = built
    etf = cfg.universe.by_symbol(symbol)
    nav2 = _nav2(store, symbol)
    assert nav2 == pytest.approx(paper["A"][symbol]["nav2"])
    # the pipeline's computed NAV-anchored limit: floor(1.2 x NAV(2 Sep), 2 dp)
    limit = bands.compute_band(D4, etf.asset_class, cfg.rules, nav_t2=nav2).high_initial
    assert limit == pytest.approx(EXPECTED_LIMIT[symbol])
    # the close of 4 Sep, read back from the official band in the file dated 4 Sep
    bo = store.read_table("band_official")
    row = bo[(bo["symbol"] == symbol) & (bo["file_date"] == D4)].iloc[0]
    assert row["session_date"] == date(2026, 9, 7)  # D4: the file gives the NEXT session
    bases = implied_base_prices(row["band_low_official"], row["band_high_official"], cfg.rules)
    assert bases == [pytest.approx(EXPECTED_LIMIT[symbol])]  # close == limit: at the ceiling
    floor2 = (Decimal(str(nav2)) * Decimal("1.2")).quantize(Decimal("0.01"), rounding=ROUND_FLOOR)
    assert float(floor2) == EXPECTED_LIMIT[symbol]


def test_hngsngbees_closed_below_its_limit(built, paper) -> None:
    _, store, _, cfg = built
    nav2 = _nav2(store, "HNGSNGBEES")
    limit = bands.compute_band(D4, "equity", cfg.rules, nav_t2=nav2).high_initial
    assert limit == pytest.approx(546.57)
    bo = store.read_table("band_official")
    row = bo[(bo["symbol"] == "HNGSNGBEES") & (bo["file_date"] == D4)].iloc[0]
    bases = implied_base_prices(row["band_low_official"], row["band_high_official"], cfg.rules)
    assert bases == [pytest.approx(471.09)]
    assert pytest.approx(limit) != 471.09
    assert paper["A"]["HNGSNGBEES"]["at"] is False


# ---- (b) 9 Nov 2026 ----
def test_six_funds_value_at_nav_and_above_nav(built, paper) -> None:
    _, _, etf_day, _ = built
    d = etf_day[(etf_day["date"] == D9) & etf_day["symbol"].isin(SIX)]
    assert len(d) == 6
    units = d["units_outstanding"].astype("float64")
    at_nav = (units * d["nav_same_date"]).sum() / 1e7  # rupees -> crore
    above = (units * (d["close"] - d["nav_same_date"])).sum() / 1e7
    assert round(at_nav) == 19451
    assert round(above) == 4875
    # same figures from the paper's own file
    assert at_nav == pytest.approx(sum(paper["C"][s]["navval"] for s in SIX), rel=1e-9)
    assert above == pytest.approx(sum(paper["C"][s]["excess"] for s in SIX), rel=1e-9)


def test_monq50_premium_press_number(built) -> None:
    _, _, etf_day, _ = built
    row = etf_day[(etf_day["symbol"] == "MONQ50") & (etf_day["date"] == D9)].iloc[0]
    assert round(row["prem_raw"] * 100, 1) == 100.8


@pytest.mark.xfail(
    strict=True,
    reason=(
        "The fixtures hold no AMFI NAV dated 6 Nov 2026, so the known NAV is 10 Sep and "
        "prem_nav_known is not 107.1%. The paper's 110.8852 is the 'previous NAV' of NSE's ETF "
        "API (see the next test). With that NAV in the raw store the pipeline gives 107.1% "
        "(test_build_etf_day.py::test_fair_value_from_bloomberg_matches_hand_computation)."
    ),
)
def test_monq50_prem_nav_known_from_amfi_fixtures_alone(built) -> None:
    _, _, etf_day, _ = built
    row = etf_day[(etf_day["symbol"] == "MONQ50") & (etf_day["date"] == D9)].iloc[0]
    assert round(row["prem_nav_known"] * 100, 1) == 107.1


def test_paper_known_nav_is_nses_previous_nav(built, paper) -> None:
    # The paper's "NAV known" is the NAV shown by NSE's ETF API on 10 Nov (110.8852).
    _, store, _, _ = built
    snap = store.read_table("inav_snapshots")
    prev = snap[(snap["symbol"] == "MONQ50") & (snap["source"] == "nse_etf_api")]["prev_nav"]
    assert prev.iloc[0] == pytest.approx(paper["C"]["MONQ50"]["navk"])
    assert round((229.67 / prev.iloc[0] - 1) * 100, 1) == 107.1


@pytest.mark.parametrize("symbol", [*SIX, "NIFTYBEES"])
def test_cross_section_matches_the_paper_for_every_fund(built, paper, symbol) -> None:
    _, _, etf_day, _ = built
    row = etf_day[(etf_day["symbol"] == symbol) & (etf_day["date"] == D9)].iloc[0]
    p = paper["C"][symbol]
    assert row["close"] == pytest.approx(p["close"])
    assert row["nav_same_date"] == pytest.approx(p["nav9"])
    assert row["prem_raw"] * 100 == pytest.approx(p["q"])
    assert row["units_outstanding"] / 1e6 == pytest.approx(p["units"])
    assert row["turnover"] * 100 == pytest.approx(p["turn"])
    assert row["deliv_pct"] == pytest.approx(p["deliv"])
    assert row["avg_trade"] == pytest.approx(p["pertrade"], rel=1e-4)  # UDiFF value is exact


# ---- (c) creation units ----
def test_monq50_creation_units_sum_to_19(built) -> None:
    _, store, _, cfg = built
    changes, events = build_unit_tables(store, cfg.universe)
    mon = changes[
        (changes["symbol"] == "MONQ50")
        & (changes["date"] >= date(2024, 7, 1))
        & (changes["date"] <= D4)
    ]
    assert mon["creation_units_change"].sum() == pytest.approx(19.0)
    assert mon["units_change"].sum() == 14_675_018 - 3_275_018
    # the two steps seen in D7: +8 baskets (Jul 2024 -> Jan 2025), +11 more (Jan 2025 -> Sep 2026)
    steps = mon["creation_units_change"].dropna()
    assert sorted(steps[steps != 0]) == pytest.approx([8.0, 11.0])
    assert mon["creation_multiple_ok"].dropna().all()
    ev = events[(events["symbol"] == "MONQ50")]
    assert len(ev) == 2 and (ev["event_type"] == "creation").all()
    assert not math.isnan(float(ev["creation_units_change"].sum()))
