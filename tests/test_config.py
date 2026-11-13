"""Tests for etfp.config (ETF universe, price-band rules, collector settings)."""

from __future__ import annotations

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
    assert len(universe.symbols()) == 12
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
    assert {s: u.by_symbol(s).futures_proxy for s in u.symbols()} == {
        "MONQ50": "NQ",
        "MON100": "NQ",
        "MASPTOP50": "ES",
        "MAFANG": "ES",
        "MAHKTECH": None,
        "HNGSNGBEES": None,
        "NIFTYBEES": None,
        "GOLDBEES": None,
        "SILVERBEES": None,
        "JUNIORBEES": None,
        "BANKBEES": None,
        "NIFTYIETF": None,
    }
    assert u.by_symbol("HNGSNGBEES").creation_unit_size == 25000
    assert u.by_symbol("MAHKTECH").creation_unit_size == 800000
    assert "creation_unit_size" in u.by_symbol("MAHKTECH").verify
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
    assert len(rules.sources) == 3
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
