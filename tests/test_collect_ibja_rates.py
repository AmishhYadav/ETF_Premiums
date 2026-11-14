"""IBJA gold/silver rates collector. Parsing uses a trimmed copy of the real page; no network."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import httpx
import pandas as pd
import pytest

from etfp.collect import ibja_rates as ibja
from etfp.http import PoliteClient, SourceBlockedError
from etfp.rawstore import RawStore

FIXTURE = Path(__file__).parent / "fixtures" / "ibja_rates" / "ibjarates_home_20261114.html"
DAY = date(2026, 11, 14)
SRC = "ibja_rates/2026-11-14/ibjarates_home.html"


@pytest.fixture(scope="module")
def raw() -> bytes:
    return FIXTURE.read_bytes()


@pytest.fixture(scope="module")
def df(raw: bytes) -> pd.DataFrame:
    return ibja.parse(raw, source_file=SRC)


def one(frame: pd.DataFrame, day: date, metal: str, purity: str, session: str) -> pd.Series:
    rows = frame[
        (frame["date"] == day)
        & (frame["metal"] == metal)
        & (frame["purity"] == purity)
        & (frame["session"] == session)
    ]
    assert len(rows) == 1, (day, metal, purity, session)
    return rows.iloc[0]


# -- values read off the live page on 2026-11-14 ----------------------------------------


def test_gold_999_am_13_nov_from_table(df):
    r = one(df, date(2026, 11, 13), "gold", "999", "AM")
    assert r["quote_inr"] == 135884 and r["quote_unit"] == "INR per 10 g"
    assert r["price_inr_per_gram"] == pytest.approx(13588.4)  # 135,884 / 10 g
    assert r["origin"] == "table" and r["source_file"] == SRC


def test_silver_pm_13_nov_converted_from_per_kg(df):
    r = one(df, date(2026, 11, 13), "silver", "999", "PM")
    assert r["quote_inr"] == 178000 and r["quote_unit"] == "INR per 1 kg"
    assert r["price_inr_per_gram"] == pytest.approx(178.0)  # 178,000 / 1,000 g


def test_chart_gives_pm_history_back_to_july(df):
    r = one(df, date(2026, 7, 14), "gold", "999", "PM")
    assert r["quote_inr"] == 141882 and r["origin"] == "chart"
    s = one(df, date(2026, 7, 14), "silver", "999", "PM")
    assert s["quote_inr"] == 219975
    pm999 = df[(df["metal"] == "gold") & (df["purity"] == "999") & (df["session"] == "PM")]
    assert len(pm999) == 84 and pm999["date"].is_unique


def test_table_wins_over_chart_where_both_exist(df):
    # 12 Nov PM is in both the PM table and the chart, with the same price.
    assert one(df, date(2026, 11, 12), "gold", "999", "PM")["origin"] == "table"


def test_day_without_pm_rate_has_no_pm_row(df):
    # 10 Nov 2026 had only an AM poll (IBJA's 30-day PDF shows '-' for PM).
    assert one(df, date(2026, 11, 10), "gold", "999", "AM")["quote_inr"] == 142420
    pm = df[(df["date"] == date(2026, 11, 10)) & (df["session"] == "PM")]
    assert pm.empty


def test_dash_cell_is_skipped_not_zero(raw):
    edited = raw.replace(b'data-label="Gold 750">101913', b'data-label="Gold 750">-', 1)
    assert edited != raw
    out = ibja.parse(edited, source_file=SRC)
    hit = out[(out["date"] == date(2026, 11, 13)) & (out["purity"] == "750")]
    assert list(hit["session"]) == ["PM"]  # the AM 750 row is gone, nothing is zero
    assert (out["quote_inr"] > 0).all()


def test_platinum_is_skipped_and_keys_unique(df):
    assert set(df["metal"]) == {"gold", "silver"}
    assert not df.duplicated(["date", "metal", "purity", "session"]).any()
    assert set(df["purity"][df["metal"] == "gold"]) == {"999", "995", "916", "750", "585"}
    assert list(df.columns) == list(ibja.OUTPUT_COLUMNS)


# -- loud failures --------------------------------------------------------------------------


def test_layout_change_is_loud():
    with pytest.raises(ValueError, match="ibja_rates: no rate table"):
        ibja.parse(b"<html><body>maintenance</body></html>", source_file=SRC)


def test_table_and_chart_disagreeing_is_loud(raw):
    bad = raw.replace(b'data-label="Gold 999">135809', b'data-label="Gold 999">135810', 1)
    assert bad != raw
    with pytest.raises(ValueError, match="table and chart disagree"):
        ibja.parse(bad, source_file=SRC)


def test_non_numeric_price_is_loud(raw):
    bad = raw.replace(b'data-label="Gold 995">135340', b'data-label="Gold 995">13x340', 1)
    with pytest.raises(ValueError, match="not a positive whole-rupee price"):
        ibja.parse(bad, source_file=SRC)


# -- fetch -------------------------------------------------------------------------------


def make_client(handler) -> PoliteClient:
    return PoliteClient(transport=httpx.MockTransport(handler), sleep=lambda s: None)


def test_fetch_stores_untouched_bytes(tmp_path, raw):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=raw)

    store = RawStore(tmp_path / "raw")
    record = ibja.fetch(make_client(handler), store, DAY)
    assert len(seen) == 1 and str(seen[0].url) == ibja.URL
    assert record.relpath == SRC
    assert record.path.read_bytes() == raw


def test_fetch_block_is_not_retried_and_stores_nothing(tmp_path):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(403)

    store = RawStore(tmp_path / "raw")
    with pytest.raises(SourceBlockedError):
        ibja.fetch(make_client(handler), store, DAY)
    assert len(calls) == 1
    assert not (tmp_path / "raw" / ibja.SOURCE).exists()
