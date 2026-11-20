"""Tests for etfp.config (ETF universe, price-band rules, collector settings)."""

from __future__ import annotations

import itertools
from datetime import date, time
from pathlib import Path

import pytest

from etfp.config import load_collect_settings, load_etfs, load_rules

# --------------------------------------------------------------------------
# helpers: small yaml builders
# --------------------------------------------------------------------------
ETF_TEMPLATE = """
  - symbol: {symbol}
    name: Test ETF
    amc: Test AMC
    isin: {isin}
    amfi_scheme_code: {code}
    underlying_index: Test Index
    underlying_market: {market}
    currency: {currency}
    asset_class: equity
    group: control
    tracked: false
"""


def etf_yaml(*entries: dict[str, object], extra: str = "") -> str:
    body = "etfs:\n"
    for e in entries:
        params = {
            "symbol": "AAA",
            "isin": "INF000000001",
            "code": 1,
            "market": "IN",
            "currency": "INR",
        } | e
        body += ETF_TEMPLATE.format(**params) + extra
    return body


def write(tmp_path: Path, text: str, name: str = "x.yaml") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# ETF universe
# --------------------------------------------------------------------------
def test_real_etf_file_loads():
    universe = load_etfs()
    assert len(universe.symbols()) == 30  # 12 + 18 domestic controls (R20, D42)
    assert len(universe.tracked()) == 7
    assert [e.symbol for e in universe.tracked()] == [
        "MONQ50",
        "MON100",
        "MASPTOP50",
        "MAFANG",
        "MAHKTECH",
        "HNGSNGBEES",
        "NIFTYBEES",
    ]


VERIFIED = [
    # symbol, name, amc, scheme code, isin, underlying index, market, group
    (
        "MONQ50",
        "Motilal Oswal Nasdaq Q 50 ETF",
        "Motilal Oswal",
        149438,
        "INF247L01AU3",
        "Nasdaq Q-50",
        "US",
        "blocked",
    ),
    (
        "MON100",
        "Motilal Oswal Nasdaq 100 ETF",
        "Motilal Oswal",
        114984,
        "INF247L01AP3",
        "Nasdaq-100",
        "US",
        "unclear",
    ),
    (
        "MASPTOP50",
        "Mirae Asset S&P 500 Top 50 ETF",
        "Mirae Asset",
        149169,
        "INF769K01HP3",
        "S&P 500 Top 50",
        "US",
        "blocked",
    ),
    (
        "MAFANG",
        "Mirae Asset NYSE FANG+ ETF",
        "Mirae Asset",
        148927,
        "INF769K01HF4",
        "NYSE FANG+",
        "US",
        "blocked",
    ),
    (
        "MAHKTECH",
        "Mirae Asset Hang Seng TECH ETF",
        "Mirae Asset",
        149379,
        "INF769K01HS7",
        "Hang Seng TECH",
        "HK",
        "blocked",
    ),
    (
        "HNGSNGBEES",
        "Nippon India ETF Hang Seng BeES",
        "Nippon India",
        140095,
        "INF204KB19I1",
        "Hang Seng Index",
        "HK",
        "possibly_open",
    ),
    (
        "NIFTYBEES",
        "Nippon India ETF Nifty 50 BeES",
        "Nippon India",
        140084,
        "INF204KB14I2",
        "Nifty 50",
        "IN",
        "control",
    ),
    (
        "GOLDBEES",
        "Nippon India ETF Gold BeES",
        "Nippon India",
        140088,
        "INF204KB17I5",
        "Domestic gold price",
        "IN",
        "bullion",
    ),
]


@pytest.mark.parametrize(
    ("symbol", "name", "amc", "code", "isin", "index", "market", "group"), VERIFIED
)
def test_verified_etf_rows(symbol, name, amc, code, isin, index, market, group):
    etf = load_etfs().by_symbol(symbol)
    assert etf.name == name
    assert etf.amc == amc
    assert etf.amfi_scheme_code == code
    assert etf.isin == isin
    assert etf.underlying_index == index
    assert etf.underlying_market == market
    assert etf.group == group
    assert etf.currency == {"US": "USD", "HK": "HKD", "IN": "INR"}[market]


def test_etf_extras_futures_units_tracking():
    u = load_etfs()
    proxies = {s: u.by_symbol(s).futures_proxy for s in u.symbols()}
    assert {s: p for s, p in proxies.items() if p is not None} == {
        "MONQ50": "NQ",
        "MON100": "NQ",
        "MASPTOP50": "ES",
        "MAFANG": "ES",
    }
    assert u.by_symbol("HNGSNGBEES").creation_unit_size == 25000
    assert u.by_symbol("MAHKTECH").creation_unit_size == 800000
    assert "creation_unit_size" not in u.by_symbol("MAHKTECH").verify  # confirmed (D36)
    assert u.by_symbol("GOLDBEES").tracked is False
    assert u.by_symbol("GOLDBEES").asset_class == "gold"
    assert u.by_symbol("SILVERBEES").asset_class == "silver"
    assert u.by_symbol("SILVERBEES").group == "bullion"


def test_controls_added():
    u = load_etfs()
    for symbol in ("JUNIORBEES", "BANKBEES", "NIFTYIETF"):
        etf = u.by_symbol(symbol)
        assert etf.group == "control"
        assert etf.tracked is False
        assert etf.underlying_market == "IN"


def test_by_symbol_unknown_is_clear_keyerror():
    with pytest.raises(KeyError, match="unknown ETF symbol 'NOPE'"):
        load_etfs().by_symbol("NOPE")


def test_valid_small_yaml_loads(tmp_path: Path):
    path = write(
        tmp_path, etf_yaml({"symbol": "AAA"}, {"symbol": "BBB", "isin": "INF000000002", "code": 2})
    )
    assert load_etfs(path).symbols() == ["AAA", "BBB"]


def test_duplicate_symbol_fails(tmp_path: Path):
    path = write(
        tmp_path,
        etf_yaml({"symbol": "AAA"}, {"symbol": "AAA", "isin": "INF000000002", "code": 2}),
    )
    with pytest.raises(ValueError, match="duplicate symbol"):
        load_etfs(path)


def test_duplicate_isin_fails(tmp_path: Path):
    path = write(tmp_path, etf_yaml({"symbol": "AAA"}, {"symbol": "BBB", "code": 2}))
    with pytest.raises(ValueError, match="duplicate isin"):
        load_etfs(path)


def test_duplicate_scheme_code_fails(tmp_path: Path):
    path = write(tmp_path, etf_yaml({"symbol": "AAA"}, {"symbol": "BBB", "isin": "INF000000002"}))
    with pytest.raises(ValueError, match="duplicate amfi_scheme_code"):
        load_etfs(path)


@pytest.mark.parametrize("bad_isin", ["US0378331005", "INF12345", "inf000000001", "INF0000000012"])
def test_bad_isin_fails(tmp_path: Path, bad_isin: str):
    path = write(tmp_path, etf_yaml({"isin": bad_isin}))
    with pytest.raises(ValueError, match="ISIN"):
        load_etfs(path)


def test_wrong_currency_fails(tmp_path: Path):
    path = write(tmp_path, etf_yaml({"market": "US", "currency": "INR"}))
    with pytest.raises(ValueError, match="does not match underlying market"):
        load_etfs(path)


def test_unknown_field_fails(tmp_path: Path):
    path = write(tmp_path, etf_yaml({}, extra="    colour: blue\n"))
    with pytest.raises(ValueError, match="colour"):
        load_etfs(path)


def test_models_are_frozen():
    etf = load_etfs().by_symbol("NIFTYBEES")
    with pytest.raises(ValueError):
        etf.symbol = "OTHER"  # type: ignore[misc]


# --------------------------------------------------------------------------
# Price-band rules
# --------------------------------------------------------------------------
def test_real_rules_file_loads():
    rules = load_rules()
    assert [r.name for r in rules.regimes] == ["nav_t2", "prev_close", "nav_t1"]
    assert [r.status for r in rules.regimes] == ["confirmed", "confirmed", "planned"]
    assert rules.tick_size == pytest.approx(0.01)
    assert rules.rounding.band_low == "up"
    assert rules.rounding.band_high == "down"
    assert rules.at_band_tolerance == pytest.approx(0.001)
    assert len(rules.sources) == 4
    assert any("NSE/CMTR/76101" in src for src in rules.sources)
    equity = rules.regime_for(date(2026, 11, 9)).band_spec("equity")
    assert equity.initial_pct == pytest.approx(10)
    assert equity.step_pct == pytest.approx(5)
    assert equity.max_widenings == 2
    assert equity.cooling_off_minutes == 15
    assert equity.cooling_off_minutes_last_30 == 5
    assert equity.trigger_pct == pytest.approx(9.90)
    gold = rules.regime_for(date(2026, 11, 9)).band_spec("gold")
    assert gold.initial_pct == pytest.approx(6)
    assert gold.step_pct == pytest.approx(3)
    assert gold.pre_open_call_auction is True


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (date(2021, 1, 1), "nav_t2"),
        (date(2026, 9, 4), "nav_t2"),
        (date(2026, 9, 7), "prev_close"),
        (date(2026, 11, 9), "prev_close"),
        (date(2027, 3, 31), "prev_close"),
        (date(2027, 4, 1), "nav_t1"),
        (date(2029, 1, 1), "nav_t1"),
    ],
)
def test_regime_for(day: date, expected: str):
    assert load_rules().regime_for(day).name == expected


@pytest.mark.parametrize("day", [date(2026, 9, 5), date(2026, 9, 6)])
def test_regime_for_weekend_gap_raises(day: date):
    with pytest.raises(ValueError, match="no price-band regime covers"):
        load_rules().regime_for(day)


@pytest.mark.parametrize("name", ["nav_t2", "prev_close", "nav_t1"])
def test_max_pct_equity_is_20_everywhere(name: str):
    regime = next(r for r in load_rules().regimes if r.name == name)
    assert regime.max_pct("equity") == pytest.approx(20)
    assert regime.max_pct("debt") == pytest.approx(20)  # debt uses the equity spec


def test_max_pct_gold():
    rules = load_rules()
    assert rules.regime_for(date(2026, 9, 4)).max_pct("gold") == pytest.approx(20)
    assert rules.regime_for(date(2026, 11, 9)).max_pct("gold") is None
    assert rules.regime_for(date(2027, 4, 1)).max_pct("silver") is None


RULES_HEAD = """
tick_size: 0.01
rounding: {band_low: up, band_high: down}
at_band_tolerance: 0.001
regimes:
"""


def regime_yaml(name: str, start: str | None, end: str | None) -> str:
    lines = [f"  - name: {name}", "    base: prev_close", "    status: confirmed"]
    if start:
        lines.append(f"    start: {start}")
    if end:
        lines.append(f"    end: {end}")
    spec = "{initial_pct: 20, step_pct: 0, max_widenings: 0}"
    lines.append(f"    bands: {{equity: {spec}, gold: {spec}, silver: {spec}}}")
    return "\n".join(lines) + "\n"


def test_small_valid_rules_load(tmp_path: Path):
    text = RULES_HEAD + regime_yaml("a", None, "2026-01-31") + regime_yaml("b", "2026-02-01", None)
    rules = load_rules(write(tmp_path, text))
    assert rules.regime_for(date(2026, 2, 1)).name == "b"


def test_overlapping_regimes_fail(tmp_path: Path):
    text = RULES_HEAD + regime_yaml("a", None, "2026-02-10") + regime_yaml("b", "2026-02-01", None)
    path = write(tmp_path, text)
    with pytest.raises(ValueError, match="overlap"):
        load_rules(path)


def test_out_of_order_regimes_fail(tmp_path: Path):
    text = RULES_HEAD + regime_yaml("b", "2026-02-01", None) + regime_yaml("a", None, "2026-01-31")
    path = write(tmp_path, text)
    with pytest.raises(ValueError):
        load_rules(path)


def test_unknown_rules_field_fails(tmp_path: Path):
    text = RULES_HEAD + regime_yaml("a", None, None) + "tick_sizes: 1\n"
    path = write(tmp_path, text)
    with pytest.raises(ValueError, match="tick_sizes"):
        load_rules(path)


def test_regime_missing_asset_class_fails(tmp_path: Path):
    text = (
        RULES_HEAD
        + "  - name: a\n    base: nav_t2\n    status: confirmed\n"
        + "    bands: {equity: {initial_pct: 20, step_pct: 0, max_widenings: 0}}\n"
    )
    path = write(tmp_path, text)
    with pytest.raises(ValueError, match="missing band specs"):
        load_rules(path)


# --------------------------------------------------------------------------
# Collector settings
# --------------------------------------------------------------------------
COLLECT_TEMPLATE = """
inav:
  start: "{start}"
  end: "{end}"
  every_minutes: {every}
  post_close: "{post}"
  timezone: "Asia/Kolkata"
  heartbeat_stale_minutes: 20
http:
  nse_min_interval_s: {nse}
  default_interval_s: 0.5
  max_retries: 3
"""


def collect_yaml(**overrides: object) -> str:
    params = {"start": "09:15", "end": "15:30", "every": 15, "post": "15:45", "nse": 1.5}
    return COLLECT_TEMPLATE.format(**(params | overrides))


def test_real_collect_file_loads():
    s = load_collect_settings()
    assert s.inav.timezone == "Asia/Kolkata"
    assert s.inav.every_minutes == 15
    assert s.inav.heartbeat_stale_minutes == 20
    assert s.http.nse_min_interval_s == pytest.approx(1.5)
    assert s.http.default_interval_s == pytest.approx(0.5)
    assert s.http.max_retries == 3


def test_inav_tick_times():
    ticks = load_collect_settings().inav_tick_times()
    assert len(ticks) == 27
    assert ticks[0] == time(9, 15)
    assert ticks[1] == time(9, 30)
    assert ticks[-2] == time(15, 30)
    assert ticks[-1] == time(15, 45)
    assert ticks == sorted(ticks)


def test_tick_times_without_extra_post_close(tmp_path: Path):
    path = write(tmp_path, collect_yaml(post="15:30"))
    ticks = load_collect_settings(path).inav_tick_times()
    assert len(ticks) == 26
    assert ticks[-1] == time(15, 30)


def test_nse_interval_below_one_second_fails(tmp_path: Path):
    path = write(tmp_path, collect_yaml(nse=0.9))
    with pytest.raises(ValueError, match="nse_min_interval_s"):
        load_collect_settings(path)


def test_nse_interval_exactly_one_second_ok(tmp_path: Path):
    assert (
        load_collect_settings(write(tmp_path, collect_yaml(nse=1.0))).http.nse_min_interval_s == 1.0
    )


@pytest.mark.parametrize("every", [0, -5])
def test_non_positive_every_minutes_fails(tmp_path: Path, every: int):
    with pytest.raises(ValueError, match="every_minutes"):
        load_collect_settings(write(tmp_path, collect_yaml(every=every)))


def test_start_after_end_fails(tmp_path: Path):
    with pytest.raises(ValueError, match="start < end <= post_close"):
        load_collect_settings(write(tmp_path, collect_yaml(start="16:00")))


def test_post_close_before_end_fails(tmp_path: Path):
    with pytest.raises(ValueError, match="start < end <= post_close"):
        load_collect_settings(write(tmp_path, collect_yaml(post="15:00")))


def test_unquoted_time_is_rejected(tmp_path: Path):
    text = collect_yaml().replace('end: "15:30"', "end: 15:30")
    with pytest.raises(ValueError, match="quoted"):
        load_collect_settings(write(tmp_path, text))


def test_missing_file_is_clear(tmp_path: Path):
    with pytest.raises(FileNotFoundError, match="config file not found"):
        load_collect_settings(tmp_path / "nope.yaml")


# --------------------------------------------------------------------------
# Creation-unit sizes (request R6)
# --------------------------------------------------------------------------
def test_creation_unit_sizes_from_scheme_documents():
    u = load_etfs()
    assert u.by_symbol("MONQ50").creation_unit_size == 600_000
    assert u.by_symbol("MON100").creation_unit_size == 250_000
    # Mirae SIDs (D36): 5,50,000 and 2,00,000 units; every IssdCptl change since
    # Feb 2024 is a whole multiple of these (D33).
    assert u.by_symbol("MASPTOP50").creation_unit_size == 550_000
    assert u.by_symbol("MAFANG").creation_unit_size == 200_000
    for symbol in ("MONQ50", "MON100", "MASPTOP50", "MAFANG", "MAHKTECH"):  # confirmed
        assert "creation_unit_size" not in u.by_symbol(symbol).verify


# DECISIONS D7: units outstanding (IssdCptl) read from the NSE security master.
D7_UNITS = {
    "MONQ50": [3_275_018, 8_075_018, 14_675_018, 14_675_018, 14_675_018],
    "MON100": [498_384_740, 504_884_740, 504_134_740, 504_134_740, 504_134_740],
    "MAHKTECH": [192_911_852, 192_911_852, 193_711_852, 193_711_852, 193_711_852],
    "HNGSNGBEES": [19_111_680, 23_586_680, 23_586_680, 23_586_680, 23_586_680],
}


@pytest.mark.parametrize("symbol", sorted(D7_UNITS))
def test_d7_unit_changes_are_whole_numbers_of_creation_units(symbol: str):
    size = load_etfs().by_symbol(symbol).creation_unit_size
    assert size
    series = D7_UNITS[symbol]
    changes = [b - a for a, b in itertools.pairwise(series)]
    assert any(changes)  # the test must have something to check
    assert all(change % size == 0 for change in changes), (symbol, size, changes)


def test_d7_monq50_baskets_are_8_then_11():
    size = load_etfs().by_symbol("MONQ50").creation_unit_size
    assert (8_075_018 - 3_275_018) == 4_800_000 == 8 * size
    assert (14_675_018 - 8_075_018) == 6_600_000 == 11 * size


def test_a_wrong_size_would_be_caught_by_the_d7_check():
    # Mutation check: a wrong size (700,000) must fail the whole-basket test above.
    series = D7_UNITS["MONQ50"]
    changes = [b - a for a, b in itertools.pairwise(series)]
    assert not all(change % 700_000 == 0 for change in changes)


def test_unknown_size_on_a_tracked_foreign_etf_must_be_flagged(tmp_path: Path):
    def text(extra: str) -> str:
        return (
            etf_yaml({"symbol": "USA", "isin": "INF000000002", "code": 2,
                      "market": "US", "currency": "USD"})
            .replace("tracked: false", "tracked: true")
            + extra
        )  # fmt: skip

    with pytest.raises(ValueError, match="creation_unit_size"):
        load_etfs(write(tmp_path, text("")))
    ok = load_etfs(write(tmp_path, text("    verify: [creation_unit_size]\n"), "ok.yaml"))
    assert ok.by_symbol("USA").creation_unit_size is None


def test_domestic_or_untracked_etf_may_leave_size_unknown(tmp_path: Path):
    u = load_etfs(write(tmp_path, etf_yaml({"symbol": "AAA"})))
    assert u.by_symbol("AAA").creation_unit_size is None


@pytest.mark.parametrize("bad", ["0", "-5"])
def test_creation_unit_size_must_be_positive(tmp_path: Path, bad: str):
    path = write(tmp_path, etf_yaml({"symbol": "AAA"}, extra=f"    creation_unit_size: {bad}\n"))
    with pytest.raises(ValueError, match="creation_unit_size"):
        load_etfs(path)


# --------------------------------------------------------------------------
# Band rules: history and widening (request R7)
# --------------------------------------------------------------------------
def test_nav_t2_start_is_marked_unverified():
    rules = load_rules()
    nav_t2 = rules.regimes[0]
    assert nav_t2.name == "nav_t2" and nav_t2.start is None
    assert "start" in nav_t2.verify


def test_widening_rule_has_no_breadth_test():
    rules = load_rules()
    for name in ("prev_close", "nav_t1"):
        equity = next(r for r in rules.regimes if r.name == name).band_spec("equity")
        assert equity.breadth_test is False
        assert equity.widen_moving_side_only is True
        assert equity.trigger_pct == pytest.approx(9.90)
        assert equity.step_pct == pytest.approx(5)
        assert equity.max_widenings == 2
        assert (equity.cooling_off_minutes, equity.cooling_off_minutes_last_30) == (15, 5)
    gold = rules.regime_for(date(2026, 11, 9)).band_spec("gold")
    assert gold.breadth_test is None  # not recorded for gold/silver


def test_quote_capture_settings_default_off_the_shelf(tmp_path: Path):
    # An older collect.yaml without the nse_quote block must still load, with safe defaults.
    path = write(tmp_path, collect_yaml())
    cfg = load_collect_settings(path).nse_quote
    assert cfg.enabled == "auto" and cfg.headless is True
    assert cfg.page_gap_s == 1.5 and cfg.channel is None


def test_quote_capture_page_gap_below_project_minimum_fails(tmp_path: Path):
    path = write(tmp_path, collect_yaml() + "\nnse_quote:\n  page_gap_s: 1.0\n")
    with pytest.raises(Exception, match="page_gap_s"):
        load_collect_settings(path)


def test_quote_capture_unknown_key_fails(tmp_path: Path):
    path = write(tmp_path, collect_yaml() + "\nnse_quote:\n  enabeld: false\n")
    with pytest.raises(Exception, match="enabeld"):
        load_collect_settings(path)


def test_domestic_control_creation_unit_sizes_from_scheme_documents():
    """D50: sizes read from AMC documents (Nippon, HDFC, Mirae, Motilal, ICICI, Kotak).

    Left null on purpose where documents conflict or none was found (DECISIONS D50).
    """
    u = load_etfs()
    expected = {
        "ITBEES": 100_000,
        "HDFCSML250": 35_000,
        "MID150BEES": 35_000,
        "PHARMABEES": 75_000,
        "HDFCNIFTY": 20_000,  # w.e.f. 20 Aug 2025; 4,000 before (data only)
        "NIFTYETF": 50_000,
        "MIDCAPETF": 400_000,
        "NIFTY1": 12_500,
        "MOM100": 125_000,
        "SENSEXIETF": 10_000,
    }
    for symbol, size in expected.items():
        etf = u.by_symbol(symbol)
        assert etf.creation_unit_size == size, symbol
        assert "creation_unit_size" not in etf.verify, symbol
    for symbol in ("HDFCBSE500", "SBINIFTY50", "BANKBETA", "NIFTYBETA"):
        etf = u.by_symbol(symbol)
        assert etf.creation_unit_size is None and "creation_unit_size" in etf.verify, symbol


def test_d58_creation_unit_sizes_and_dated_steps_load() -> None:
    """D58: the ten funds that were null now carry a size from a fund-house document."""
    u = load_etfs()
    current = {
        "NIFTYBEES": 25_000,
        "JUNIORBEES": 5_000,
        "BANKBEES": 5_000,
        "GOLDBEES": 120_000,
        "SILVERBEES": 31_000,
        "NIFTYIETF": 50_000,
        "PSUBNKBEES": 25_000,
        "CPSEETF": 25_000,
        "NEXT50IETF": 100_000,
        "ICICIB22": 4_300,
    }
    for symbol, size in current.items():
        etf = u.by_symbol(symbol)
        assert etf.creation_unit_size == size, symbol
        assert "creation_unit_size" not in etf.verify, symbol
    steps = {
        "NIFTYBEES": [(50_000, date(2024, 2, 5)), (25_000, date(2026, 9, 7))],
        "JUNIORBEES": [(10_000, date(2024, 2, 5)), (5_000, date(2026, 7, 20))],
        "BANKBEES": [(10_000, date(2024, 2, 5)), (5_000, date(2026, 7, 20))],
        "GOLDBEES": [(5_000, date(2024, 2, 5)), (120_000, date(2025, 6, 23))],
        "SILVERBEES": [(30_000, date(2024, 2, 5)), (31_000, date(2025, 6, 23))],
    }
    for symbol, want in steps.items():
        etf = u.by_symbol(symbol)
        got = [(s.size, s.from_date) for s in etf.creation_unit_history]
        assert got == want, symbol
        assert "creation_unit_history" in etf.verify, symbol
        # every step says where it comes from; inferred ones say so in the agreed words
        for s in etf.creation_unit_history:
            assert s.source
    for symbol in ("GOLDBEES", "SILVERBEES"):
        assert (
            "inferred from IssdCptl multiples"
            in u.by_symbol(symbol).creation_unit_history[0].source
        )
    for symbol in ("PSUBNKBEES", "CPSEETF", "NEXT50IETF", "NIFTYIETF", "ICICIB22"):
        assert not u.by_symbol(symbol).creation_unit_history


def _etf_dict(**kw):
    base = {
        "symbol": "AAA",
        "name": "A",
        "amc": "X",
        "isin": "INF000000001",
        "amfi_scheme_code": 1,
        "underlying_index": "Nifty 50",
        "underlying_market": "IN",
        "currency": "INR",
        "asset_class": "equity",
        "group": "control",
        "tracked": False,
    }
    base.update(kw)
    return base


def test_creation_unit_history_valid_and_backward_compatible() -> None:
    from etfp.config import Etf

    plain = Etf.model_validate(_etf_dict(creation_unit_size=100))
    assert plain.creation_unit_history == ()
    ok = Etf.model_validate(
        _etf_dict(
            creation_unit_size=200,
            creation_unit_history=[
                {"size": 100, "from": "2024-01-01"},
                {"size": 200, "from": "2025-01-01"},
            ],
        )
    )
    assert [s.size for s in ok.creation_unit_history] == [100, 200]


@pytest.mark.parametrize(
    "history, size, message",
    [
        (
            [{"size": 100, "from": "2025-01-01"}, {"size": 200, "from": "2024-01-01"}],
            200,
            "oldest first",
        ),
        (
            [{"size": 100, "from": "2024-01-01"}, {"size": 200, "from": "2024-01-01"}],
            200,
            "oldest first",
        ),
        ([{"size": 100, "from": "2024-01-01"}], 200, "must equal the last"),
        (
            [{"size": 100, "from": "2024-01-01"}, {"size": 100, "from": "2025-01-01"}],
            100,
            "repeats",
        ),
        ([{"size": 0, "from": "2024-01-01"}], 0, ""),
    ],
)
def test_creation_unit_history_rejects_bad_input(history, size, message) -> None:
    from etfp.config import Etf

    with pytest.raises(ValueError, match=message):
        Etf.model_validate(_etf_dict(creation_unit_size=size, creation_unit_history=history))
