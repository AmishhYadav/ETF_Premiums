"""Domestic control ETFs (request R20, DECISIONS D42) and former NSE symbols.

Controls are Indian equity index ETFs that can still create units, so their premium
should stay near zero; they are the comparison group for the blocked overseas ETFs.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from etfp.collect import amfi_nav
from etfp.config import load_etfs
from etfp.corporate_actions import load_corporate_actions

FIXTURES = Path(__file__).parent / "fixtures"
CONTROLS_NAVALL = FIXTURES / "amfi" / "NAVAll_controls_20261116.txt"


def controls():
    return [e for e in load_etfs().etfs if e.group == "control"]


def test_at_least_15_domestic_equity_controls():
    domestic = [e for e in controls() if e.underlying_market == "IN" and e.asset_class == "equity"]
    assert len(domestic) >= 15
    assert len(domestic) == len(controls())  # every control is a domestic equity fund


def test_controls_span_several_fund_houses_and_indices():
    cs = controls()
    assert len({e.amc for e in cs}) >= 6
    assert len({e.underlying_index for e in cs}) >= 8


def test_every_control_has_isin_and_amfi_code_not_in_verify():
    for etf in controls():
        assert etf.isin.startswith("INF"), etf.symbol  # mutual-fund units start INF
        assert etf.amfi_scheme_code > 0
        assert "isin" not in etf.verify and "amfi_scheme_code" not in etf.verify, etf.symbol


def test_every_control_amfi_code_matches_its_isin_in_amfi_navall():
    """Scheme code -> ISIN as AMFI publishes it (real extract of NAVAll.txt, 16 Nov 2026).

    A wrong scheme code would silently attach another fund's NAV to the ETF.
    """
    df = amfi_nav.parse_navall(CONTROLS_NAVALL.read_bytes(), source_file=CONTROLS_NAVALL.name)
    isin_by_code = dict(zip(df["scheme_code"].astype(int), df["isin_growth"], strict=True))
    for etf in controls():
        assert isin_by_code.get(etf.amfi_scheme_code) == etf.isin, etf.symbol


def test_controls_are_untracked_except_niftybees():
    tracked = {e.symbol for e in controls() if e.tracked}
    assert tracked == {"NIFTYBEES"}  # the dashboard rows stay as they were


def test_known_symbol_changes():
    m = load_etfs().former_symbols()
    assert m["SETFNIF50"] == ("SBINIFTY50", date(2026, 10, 29))
    assert m["ICICINIFTY"] == ("NIFTYIETF", date(2023, 12, 19))
    assert m["N100"] == ("MON100", date(2021, 6, 15))
    assert m["UTINIFTETF"] == ("NIFTYBETA", date(2025, 12, 23))
    for etf in load_etfs().etfs:
        for f in etf.former_symbols:
            assert f.source.strip(), (etf.symbol, f.symbol)


@pytest.mark.parametrize(
    ("symbol", "ex_date", "nav_ex_date"),
    [
        ("HDFCNIFTY", date(2021, 2, 17), date(2021, 2, 22)),
        ("BANKBETA", date(2021, 2, 17), date(2021, 2, 22)),
        ("NIFTYBETA", date(2023, 9, 25), date(2023, 9, 26)),
    ],
)
def test_control_splits_are_configured(symbol, ex_date, nav_ex_date):
    (action,) = load_corporate_actions().for_symbol(symbol)
    assert action.ratio == 10 and action.verified
    assert action.ex_date == ex_date
    assert action.nav_effective_date == nav_ex_date


# -- validation of former_symbols ---------------------------------------------------------

_ETF = """
  - symbol: {symbol}
    name: Test ETF
    amc: Test AMC
    isin: {isin}
    amfi_scheme_code: {code}
    underlying_index: Test Index
    underlying_market: IN
    currency: INR
    asset_class: equity
    group: control
    tracked: false
"""


def _yaml(tmp_path: Path, *entries: tuple[str, str]) -> Path:
    """entries: (symbol, former_symbols yaml block or '')."""
    body = "etfs:\n"
    for i, (symbol, former) in enumerate(entries, start=1):
        body += _ETF.format(symbol=symbol, isin=f"INF00000000{i}", code=i) + former
    path = tmp_path / "etfs.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def _former(*pairs: tuple[str, str]) -> str:
    out = "    former_symbols:\n"
    for sym, until in pairs:
        out += f"      - {{symbol: {sym}, until: {until}, source: test}}\n"
    return out


def test_former_symbols_load(tmp_path: Path):
    u = load_etfs(_yaml(tmp_path, ("NEW", _former(("OLD1", "2020-01-31"), ("OLD2", "2022-05-09")))))
    assert u.former_symbols() == {
        "OLD1": ("NEW", date(2020, 1, 31)),
        "OLD2": ("NEW", date(2022, 5, 9)),
    }


def test_former_symbols_must_be_oldest_first(tmp_path: Path):
    path = _yaml(tmp_path, ("NEW", _former(("OLD2", "2022-05-09"), ("OLD1", "2020-01-31"))))
    with pytest.raises(ValueError, match="oldest first"):
        load_etfs(path)


def test_former_symbol_cannot_be_a_current_symbol(tmp_path: Path):
    path = _yaml(tmp_path, ("AAA", _former(("BBB", "2022-05-09"))), ("BBB", ""))
    with pytest.raises(ValueError, match="equal to a current symbol"):
        load_etfs(path)


def test_former_symbol_used_by_two_funds_fails(tmp_path: Path):
    path = _yaml(
        tmp_path, ("AAA", _former(("OLD", "2022-05-09"))), ("BBB", _former(("OLD", "2023-01-02")))
    )
    with pytest.raises(ValueError, match="used twice"):
        load_etfs(path)
