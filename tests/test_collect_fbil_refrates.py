"""FBIL reference rates collector (D43). The fixture is a REAL answer saved 2026-11-18
for fromDate=2026-11-02&toDate=2026-11-13 (the public feed returned dates up to 9 Nov)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import httpx
import pandas as pd
import pytest

from etfp.collect import fbil_refrates as fb
from etfp.http import PoliteClient, SourceError
from etfp.rawstore import RawStore
from etfp.timeutil import IST

FIX = (
    Path(__file__).parent
    / "fixtures"
    / "fbil_refrates"
    / "fbil_refrates_2026-11-02_2026-11-13.json"
)
RAW = FIX.read_bytes()


def client(handler) -> PoliteClient:
    return PoliteClient(transport=httpx.MockTransport(handler), sleep=lambda s: None, max_retries=0)


def test_parse_real_answer_hand_checked_values() -> None:
    """From the raw JSON: 9 Nov 2026 'INR / 1 USD' 93.857900 -> USDINR 93.8579 rupees per USD;
    'INR / 100 JPY' 59.310000 -> JPYINR 59.31 / 100 = 0.5931 rupees per yen. Published 13:00 IST."""
    df = fb.parse(RAW, source_file="fbil_refrates/2026-11-18/x.json")
    usd = df[(df["pair"] == "USDINR") & (df["date"] == date(2026, 11, 9))].iloc[0]
    assert usd["rate"] == 93.8579 and usd["quote_units"] == 1
    assert usd["published_ist"].tz is not None
    assert usd["published_ist"] == pd.Timestamp("2026-11-09 13:00", tz=IST)
    jpy = df[(df["pair"] == "JPYINR") & (df["date"] == date(2026, 11, 9))].iloc[0]
    assert jpy["rate_quoted"] == 59.31 and jpy["quote_units"] == 100
    assert jpy["rate"] == pytest.approx(0.5931)
    assert set(df["date"]) == {date(2026, 11, d) for d in (2, 3, 4, 5, 6, 9)}
    assert "HKDINR" not in set(df["pair"])  # FBIL publishes no HKD rate
    assert df["source_file"].eq("fbil_refrates/2026-11-18/x.json").all()


def test_parse_rejects_changed_format() -> None:
    with pytest.raises(ValueError, match="not like 'INR / 1 USD'"):
        fb.parse(json.dumps([{"processRunDate": "2026-11-09 00:00:00", "subProdName": "USD",
                              "rate": 1.0}]).encode(), source_file="f")  # fmt: skip
    with pytest.raises(ValueError, match="positive"):
        fb.parse(json.dumps([{"processRunDate": "2026-11-09 00:00:00",
                              "subProdName": "INR / 1 USD", "rate": 0}]).encode(),
                 source_file="f")  # fmt: skip
    with pytest.raises(ValueError, match="two different rates"):
        row = {"processRunDate": "2026-11-09 00:00:00", "subProdName": "INR / 1 USD"}
        fb.parse(
            json.dumps([{**row, "rate": 93.1}, {**row, "rate": 93.2}]).encode(), source_file="f"
        )
    assert fb.parse(b"[]", source_file="f").empty


def test_windows_split_by_calendar_year() -> None:
    assert fb.windows(date(2024, 12, 20), date(2025, 1, 10)) == [
        (date(2024, 12, 20), date(2024, 12, 31)),
        (date(2025, 1, 1), date(2025, 1, 10)),
    ]
    assert len(fb.windows(date(2021, 1, 1), date(2026, 11, 18))) == 6
    with pytest.raises(ValueError):
        fb.windows(date(2026, 1, 2), date(2026, 1, 1))


def test_fetch_saves_untouched_and_refuses_html(tmp_path: Path) -> None:
    seen = []

    def ok(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        return httpx.Response(200, content=RAW)

    store = RawStore(tmp_path)
    with client(ok) as c:
        rec = fb.fetch(c, store, date(2026, 11, 18), date(2026, 11, 2), date(2026, 11, 13))
    assert rec.relpath == "fbil_refrates/2026-11-18/fbil_refrates_2026-11-02_2026-11-13.json"
    assert rec.path.read_bytes() == RAW
    assert seen[0].params["fromDate"] == "2026-11-02" and seen[0].params["toDate"] == "2026-11-13"
    with (
        client(lambda r: httpx.Response(200, content=b"<html>down</html>")) as c,
        pytest.raises(SourceError, match="not stored"),
    ):
        fb.fetch(c, store, date(2026, 11, 18), date(2026, 10, 1), date(2026, 10, 31))
    assert len(list(tmp_path.rglob("*.json"))) == 1
    with pytest.raises(ValueError, match="longer"):
        fb.fetch(None, store, date(2026, 11, 18), date(2024, 1, 1), date(2025, 6, 1))  # type: ignore[arg-type]
