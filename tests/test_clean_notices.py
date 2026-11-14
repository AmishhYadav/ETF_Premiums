"""notices cleaner: keyword rules (hand-worked examples), merging, lineage, schema."""

from __future__ import annotations

import json
import logging
import shutil
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pytest
import yaml

from etfp import paths
from etfp.clean import make_context
from etfp.clean import notices as cn
from etfp.clean.common import CleanError
from etfp.config import load_etfs
from etfp.store import Store
from etfp.timeutil import IST

FIX = Path(__file__).parent / "fixtures" / "notices"


@pytest.fixture(scope="module")
def rules() -> cn.NoticeRules:
    return cn.load_rules(paths.config_dir() / "notices.yaml", known_symbols=load_etfs().symbols())


# -- keyword rules --------------------------------------------------------------------


def test_nse_etf_trading_parameters_is_a_rule_candidate(rules):
    g = cn.classify("Trading in Exchange Traded Funds (ETFs) - update in trading parameters", rules)
    assert g.category == "rule" and g.relevant
    # group 1 hit, then group 2 hits in keyword order; repeats from later rules dropped
    assert g.matched == (
        "trading parameters", "ETF", "exchange traded fund", "exchange traded funds",
    )  # fmt: skip
    assert g.symbols == ()


def test_sebi_aug_2026_band_circular_is_a_rule_candidate(rules):
    title = (
        "Extension of timeline for implementation of provisions of SEBI Circular dated June "
        "15, 2026 on norms for base price, price bands, call auction in pre-open session and "
        "Close-out procedure for Exchange Traded Funds (ETFs)"
    )
    g = cn.classify(title, rules)
    assert g.category == "rule" and g.relevant
    for kw in ("price band", "base price", "close-out", "pre-open", "call auction"):
        assert kw in g.matched


def test_overseas_limit_is_a_cap_candidate(rules):
    g = cn.classify("Investment in overseas securities by Mutual Funds - revised limits", rules)
    assert g.category == "cap" and g.relevant
    # yaml order; "overseas" also hits the amc_notice context group (listed, no effect)
    assert g.matched == ("investment in overseas", "overseas securities", "overseas")


def test_subscription_resumption_is_fund_status(rules):
    g = cn.classify(
        "Resumption of subscriptions in Baroda BNP Paribas Aqua Fund of Fund under Baroda BNP "
        "Paribas Mutual Fund on NSE MF Invest Platform",
        rules,
    )
    assert g.category == "fund_status" and g.relevant


def test_half_matches_are_logged_but_not_relevant(rules):
    # AMC name without an overseas/ETF word: only the first all_of group hits
    g = cn.classify("Launch of Motilal Oswal Nifty REITs & Realty Index Fund NFO", rules)
    assert g.category is None and not g.relevant and g.matched == ("Motilal Oswal",)
    # "suspension" without subscription/creation words
    g = cn.classify("Suspension of trading in units - on account of redemption", rules)
    assert g.category is None and not g.relevant and g.matched == ("suspension",)


def test_symbol_guesses(rules):
    g = cn.classify("Motilal Oswal Nasdaq 100 ETF - change in fund manager", rules)
    assert g.symbols == ("MON100",) and g.category == "amc_notice"
    assert cn.classify("Motilal Oswal Nasdaq Q 50 ETF", rules).symbols == ("MONQ50",)
    assert cn.classify("Mirae Asset NYSE FANG+ ETF", rules).symbols == ("MAFANG",)
    assert cn.classify("Mirae Asset Hang Seng TECH ETF", rules).symbols == ("MAHKTECH",)
    assert cn.classify("Nippon India ETF Hang Seng BeES", rules).symbols == ("HNGSNGBEES",)


def test_whole_words_only(rules):
    assert cn.classify("ETFX Limited - listing", rules).matched == ()
    assert cn.classify("Status of Nippon India arbitrage fund", rules).matched == ("Nippon India",)
    assert cn.classify("Mirae Asset Nasdaq  100\tETF", rules).symbols == ("MON100",)  # spacing


def test_exclude_keeps_the_match_but_not_relevance():
    r = cn.parse_rules(
        {"version": "t", "rules": [{"category": "rule", "any": ["ETF"]}], "exclude": ["NFO"]}
    )
    g = cn.classify("Launch of XYZ ETF NFO", r)
    assert g.category == "rule" and not g.relevant and g.matched == ("ETF", "NFO")


@pytest.mark.parametrize(
    ("data", "msg"),
    [
        ({"rules": [{"category": "rule", "any": ["x"]}]}, "version"),
        ({"version": 1, "rules": [{"category": "nope", "any": ["x"]}]}, "category"),
        ({"version": 1, "rules": [{"category": "rule"}]}, "exactly one"),
        ({"version": 1, "rules": [{"category": "rule", "any": []}]}, "non-empty"),
        ({"version": 1, "rules": [{"category": "rule", "all_of": [["a"], []]}]}, "group 2"),
        ({"version": 1, "rules": []}, "no rules"),
        (
            {
                "version": 1,
                "rules": [{"category": "rule", "any": ["x"]}],
                "symbols": {"ZZZ": ["z"]},
            },
            "not in config/etfs.yaml",
        ),
    ],
)
def test_bad_rules_fail_loudly(data, msg):
    with pytest.raises(ValueError, match=msg):
        cn.parse_rules(data, known_symbols={"MON100"})


def test_project_rules_file_is_valid_and_symbols_exist(rules):
    raw = yaml.safe_load((paths.config_dir() / "notices.yaml").read_text())
    assert set(rules.symbols) == set(raw["symbols"]) <= set(load_etfs().symbols())


# -- build ---------------------------------------------------------------------------


def _put(root: Path, source: str, day: str, fixture: str, name: str | None = None) -> str:
    folder = root / "data" / "raw" / source / day
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(FIX / fixture, folder / (name or fixture))
    return f"{source}/{day}/{name or fixture}"


def _manifest(root: Path, entries: list[tuple[str, str, datetime]]) -> None:
    with (root / "data" / "raw" / "_manifest.jsonl").open("a") as fh:
        for source, rel, ts in entries:
            fh.write(json.dumps({"source": source, "relpath": rel, "fetched_at": ts.isoformat()}))
            fh.write("\n")


def test_build_merges_repeats_and_keeps_lineage(tmp_path: Path, caplog):
    listing = "sebi_legal_20260825_20260905_p1.html"
    a = _put(tmp_path, "sebi_listing", "2026-09-05", listing)
    b = _put(tmp_path, "sebi_listing", "2026-09-04", listing,
             "sebi_legal_20260828_20260904_p1.html")  # fmt: skip
    _put(tmp_path, "sebi_listing", "2026-06-30", "sebi_legal_20260101_20260630_p1.html")
    _put(tmp_path, "sebi_rss", "2026-11-14", "sebirss_20261114.xml", "sebirss.xml")
    _put(tmp_path, "nse_circulars", "2026-09-01", "nse_circulars_20260901_20260901.json")
    t1 = datetime(2026, 9, 4, 19, 50, tzinfo=IST)
    t2 = datetime(2026, 9, 5, 19, 50, tzinfo=IST)
    _manifest(tmp_path, [("sebi_listing", a, t2), ("sebi_listing", b, t1)])

    with caplog.at_level(logging.WARNING, logger="etfp.clean"):
        df = cn.build(make_context(tmp_path))
    # page 1 of the Jan-Jun window says 86 records but only page 1 is stored
    assert any("states 86 records but its pages hold 25" in m for m in caplog.messages)

    assert list(df.columns) == cn.COLUMNS
    assert df["notice_id"].is_unique
    assert len(df) == 1 + 25 + 30 + 32  # listing (twice, merged) + H1 page + RSS + NSE

    band = df.set_index("notice_id").loc["sebi:104094"]
    assert band["n_source_files"] == 2
    assert band["source_file"] == b  # downloaded first (t1 < t2)
    assert band["first_seen_ts"] == t1
    assert band["published_date"] == date(2026, 8, 28)
    assert band["published_ts"] == datetime(2026, 8, 28, tzinfo=IST)
    assert not band["published_time_known"]
    assert band["category"] == "rule" and band["relevant"]
    assert band["feeds"] == "sebi_listing" and band["source"] == "sebi"

    emerge = df.set_index("notice_id").loc["nse:NSE/CML/76118"]
    assert emerge["section"] == "Emerge; Listing" and emerge["n_source_files"] == 1
    assert pd.isna(emerge["first_seen_ts"])  # file not in the manifest

    etf = df.set_index("notice_id").loc["nse:NSE/CMTR/76101"]
    assert etf["relevant"] and etf["category"] == "rule" and etf["issuer"] == "NSE"

    # writes through the registered schema (types, key, required columns)
    assert Store(tmp_path / "clean").write_table("notices", df) == len(df)


def test_build_reads_older_versions_too(tmp_path: Path):
    """A newer download of a 'latest items' feed must not hide items only the older had."""
    _put(tmp_path, "sebi_rss", "2026-11-14", "sebirss_20261114.xml", "sebirss.xml")
    folder = tmp_path / "data" / "raw" / "sebi_rss" / "2026-11-14"
    (folder / "sebirss.v2.xml").write_bytes(
        b'<?xml version="1.0"?><rss version="2.0"><channel><item><title>New item</title>'
        b"<link>https://www.sebi.gov.in/media/press-releases/nov-2026/x_999999.html</link>"
        b"<pubDate>14 Nov, 2026 +0530</pubDate></item></channel></rss>"
    )
    df = cn.build(make_context(tmp_path))
    assert len(df) == 31 and "sebi:999999" in set(df["notice_id"])


def test_build_with_no_raw_files_is_empty(tmp_path: Path):
    df = cn.build(make_context(tmp_path))
    assert df.empty and list(df.columns) == cn.COLUMNS


def test_build_names_the_bad_file(tmp_path: Path):
    folder = tmp_path / "data" / "raw" / "nse_circulars" / "2026-09-01"
    folder.mkdir(parents=True)
    (folder / "nse_circulars_20260901_20260901.json").write_bytes(b"<html>blocked</html>")
    with pytest.raises(CleanError, match="nse_circulars/2026-09-01/nse_circulars_2026"):
        cn.build(make_context(tmp_path))
