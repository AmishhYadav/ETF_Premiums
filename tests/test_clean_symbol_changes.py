"""NSE symbol-change list: collector parsing and the build-time check against config (D45).

The fixture ``tests/fixtures/nse_symbolchange/symbolchange.csv`` is a trim of the real file
NSE served on 2026-11-18 (all rows touching a configured ETF plus a few unrelated ones).
No network.
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from etfp.clean import make_context, symbol_changes
from etfp.collect import nse_symbolchange
from etfp.config import load_etfs
from test_clean_helpers import save_raw

FIXTURE = Path(__file__).parent / "fixtures" / "nse_symbolchange" / "symbolchange.csv"
DAY = date(2026, 11, 18)
SRC = "nse_symbolchange/2026-11-18/symbolchange.csv"


@pytest.fixture(scope="module")
def parsed() -> pd.DataFrame:
    return nse_symbolchange.parse(FIXTURE.read_bytes(), source_file=SRC)


def test_parse_gives_typed_rows_with_iso_dates(parsed: pd.DataFrame):
    assert list(parsed.columns) == list(nse_symbolchange.OUTPUT_COLUMNS)
    row = parsed[parsed["old_symbol"] == "N100"].iloc[0]
    assert (row["new_symbol"], row["change_date"], row["source_file"]) == (
        "MON100",
        date(2021, 6, 16),
        SRC,
    )
    assert row["change_date"].isoformat() == "2021-06-16"
    assert "NASDAQ 100" in row["company_name"]


def test_parse_failures_name_the_source_and_line():
    with pytest.raises(ValueError, match="header is missing column"):
        nse_symbolchange.parse(b"A,B\n1,2\n", source_file="f")
    bad = b"NAME OF THE COMPANY,OLD SYMBOL,NEW SYMBOL,EFFECTIVE DATE\nX,A,B,2021-06-16\n"
    with pytest.raises(ValueError, match=r"nse_symbolchange: line 2.*EFFECTIVE DATE"):
        nse_symbolchange.parse(bad, source_file="f")


def test_real_renames_in_config_are_recognised_as_consistent(parsed: pd.DataFrame):
    out = symbol_changes.check(parsed, load_etfs().etfs)
    consistent = out[out["config_status"] == "consistent"]
    # the 13 renames config/etfs.yaml took from symbolchange.csv (D42)
    assert len(consistent) == 13
    assert set(consistent["old_symbol"]) == {
        "N100", "M100", "NETFSILVER", "NETFIT", "NETFMID150", "NETFPHARMA", "ICICINXT50",
        "HDFCNIFETF", "MAN50ETF", "MAM150ETF", "KOTAKNIFTY", "UTINIFTETF", "UTIBANKETF",
    }  # fmt: skip
    # renames from before our data starts need no mapping; none is flagged
    early = out[out["config_status"] == "before_data_window"]
    assert {"NIFTYIWIN", "SETFNIFTY", "RETFMID150"} <= set(early["old_symbol"])
    assert symbol_changes.warnings_in(out).empty
    # rows about other companies are not kept
    assert not out["old_symbol"].isin(["RELNV20", "MBSWITCH", "KOTAKBKETF"]).any()


def test_a_fake_unconfigured_rename_of_a_configured_etf_is_flagged(parsed: pd.DataFrame):
    fake = pd.DataFrame(
        [
            {
                "company_name": "Fake rename of a configured ETF",
                "old_symbol": "MON100",
                "new_symbol": "MON100NEW",
                "change_date": date(2026, 12, 1),
                "source_file": SRC,
            },
            {  # an older name of a configured ETF that config does not list
                "company_name": "Fake earlier name",
                "old_symbol": "OLDITBEES",
                "new_symbol": "NETFIT",
                "change_date": date(2021, 3, 1),
                "source_file": SRC,
            },
        ]
    )
    out = symbol_changes.check(pd.concat([parsed, fake], ignore_index=True), load_etfs().etfs)
    bad = symbol_changes.warnings_in(out)
    assert set(bad["old_symbol"]) == {"MON100", "OLDITBEES"}
    assert "still treats it as current" in bad.set_index("old_symbol").loc["MON100", "detail"]
    assert "not in former_symbols" in bad.set_index("old_symbol").loc["OLDITBEES", "detail"]


def test_wrong_until_date_is_flagged(parsed: pd.DataFrame):
    etfs = load_etfs().etfs
    shifted = parsed.copy()
    shifted.loc[shifted["old_symbol"] == "N100", "change_date"] = date(2021, 6, 22)
    out = symbol_changes.check(shifted, etfs).set_index("old_symbol")
    assert out.loc["N100", "config_status"] == "unconfigured"
    assert "until=2021-06-15" in out.loc["N100", "detail"]


def test_build_warns_loudly_and_writes_the_table(tmp_path: Path, caplog: pytest.LogCaptureFixture):
    extra = FIXTURE.read_bytes() + b"Fake Fund,SBINIFTY50,SBINIFTYNEW,01-DEC-2026\n"
    save_raw(tmp_path, nse_symbolchange.SOURCE, DAY, "symbolchange.csv", extra)
    ctx = make_context(tmp_path)
    with caplog.at_level(logging.WARNING, logger="etfp.clean"):
        out = symbol_changes.build(ctx)
    assert len(symbol_changes.warnings_in(out)) == 1
    messages = [r.getMessage() for r in caplog.records]
    assert any("UNCONFIGURED symbol change for SBINIFTY50" in m and SRC in m for m in messages)
    assert list(out.columns) == symbol_changes.COLUMNS
    assert (out["source_file"] == SRC).all()


def test_build_without_a_raw_file_says_the_check_did_not_run(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    ctx = make_context(tmp_path)
    with caplog.at_level(logging.WARNING, logger="etfp.clean"):
        out = symbol_changes.build(ctx)
    assert out.empty
    assert any("NOT being checked" in r.getMessage() for r in caplog.records)
