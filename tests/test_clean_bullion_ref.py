"""bullion_ref_daily: IBJA domestic gold/silver rates, cleaned from saved pages."""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import pytest

from etfp.clean import make_context
from etfp.clean.bullion_ref import COLUMNS, TABLE, build, combine
from etfp.collect import ibja_rates
from etfp.store import SCHEMAS
from test_clean_helpers import fixture_bytes, save_raw

FIX = "ibja_rates/ibjarates_home_20261114.html"


def test_schema_registered_with_key_and_lineage():
    schema = SCHEMAS[TABLE]
    assert schema.primary_key == ("date", "metal", "purity", "session")
    assert list(schema.column_names) == COLUMNS


def test_build_from_one_page(tmp_path: Path):
    save_raw(tmp_path, ibja_rates.SOURCE, date(2026, 11, 14), "ibjarates_home.html",
             fixture_bytes(FIX))  # fmt: skip
    out = build(make_context(tmp_path))
    assert len(out) == 288
    r = out[(out["date"] == date(2026, 11, 13)) & (out["metal"] == "silver")
            & (out["session"] == "AM")].iloc[0]  # fmt: skip
    assert r["price_inr_per_gram"] == pytest.approx(177.5)  # 177,500 per kg / 1,000
    assert r["source"] == "ibja"
    assert r["source_file"] == "ibja_rates/2026-11-14/ibjarates_home.html"


def test_repeated_rate_keeps_newest_page_and_logs_revisions(tmp_path: Path, caplog):
    old = fixture_bytes(FIX)
    # A later download where IBJA "revised" the 14 Jul silver PM rate in the chart.
    new = old.replace(b"[219975,219711", b"[219000,219711", 1)
    assert new != old
    save_raw(tmp_path, ibja_rates.SOURCE, date(2026, 11, 14), "ibjarates_home.html", old)
    save_raw(tmp_path, ibja_rates.SOURCE, date(2026, 11, 16), "ibjarates_home.html", new)
    with caplog.at_level(logging.WARNING, logger="etfp.clean"):
        out = build(make_context(tmp_path))
    assert "revised 1 rate" in caplog.text
    assert len(out) == 288  # same keys, no duplicates
    r = out[(out["date"] == date(2026, 7, 14)) & (out["metal"] == "silver")].iloc[0]
    assert r["quote_inr"] == 219000
    assert r["source_file"] == "ibja_rates/2026-11-16/ibjarates_home.html"


def test_no_pages_gives_empty_table_with_columns():
    out = combine([])
    assert out.empty and list(out.columns) == COLUMNS
