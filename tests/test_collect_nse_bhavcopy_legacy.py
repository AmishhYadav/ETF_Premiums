"""Old-format NSE bhavcopy (repair source for 4 Nov 2021, DECISIONS D54). No network.

The CSV below is built in code from two rows of the real file
``cm04NOV2021bhav.csv`` (MON100 and NIFTYBEES, read 18 Nov 2026).
"""

from __future__ import annotations

import io
import math
import zipfile
from datetime import date
from pathlib import Path

import httpx
import pytest

from etfp.clean import make_context, prices_daily
from etfp.clean.etf_master import build as build_master
from etfp.collect import nse_bhavcopy_legacy as legacy
from etfp.http import PoliteClient
from etfp.rawstore import RawStore
from test_clean_helpers import fixture_bytes, make_raw_root, save_raw

CSV = (
    "SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,TIMESTAMP,"
    "TOTALTRADES,ISIN,\n"
    "MON100,EQ,124.6,124.6,118,120.09,120.14,117.57,527567,63130002.86,04-NOV-2021,10093,"
    "INF247L01AP3,\n"
    "NIFTYBEES,EQ,207.88,207.88,185.11,193.59,193.6,192.58,671760,130195880.06,04-NOV-2021,"
    "23193,INF204KB14I2,\n"
)
DAY = date(2021, 11, 4)


def make_zip(text: str = CSV) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("cm04NOV2021bhav.csv", text)
    return buf.getvalue()


def test_names_and_urls() -> None:
    assert legacy.raw_name(DAY) == "cm04NOV2021bhav.csv.zip"
    assert legacy.url_for(DAY) == (
        "https://nsearchives.nseindia.com/content/historical/EQUITIES/2021/NOV/"
        "cm04NOV2021bhav.csv.zip"
    )
    assert legacy.mto_raw_name(DAY) == "MTO_04112021.DAT"
    assert legacy.mto_url_for(DAY).endswith("/archives/equities/mto/MTO_04112021.DAT")


def test_parse_reads_the_real_4_nov_values() -> None:
    df = legacy.parse(make_zip(), source_file="x/cm04NOV2021bhav.csv.zip")
    m = df[df["symbol"] == "MON100"].iloc[0]
    assert m["date"] == DAY
    assert (m["open"], m["high"], m["low"]) == (124.6, 124.6, 118.0)
    assert m["close"] == 120.09 and m["last"] == 120.14
    # PREVCLOSE is the real 3 Nov close (the stale full file said 117.54 for the close)
    assert m["prev_close"] == 117.57
    assert m["volume"] == 527_567 and m["trades"] == 10_093
    assert m["value"] == 63_130_002.86  # rupees already, not lacs
    assert math.isnan(m["vwap"]) and math.isnan(m["deliv_pct"]) and m["deliv_qty"] is not None
    assert m["source_file"] == "x/cm04NOV2021bhav.csv.zip"
    assert list(df.columns)[:3] == ["symbol", "series", "date"]


def test_plain_csv_bytes_also_parse() -> None:
    assert len(legacy.parse(CSV.encode(), source_file="x")) == 2


def test_wrong_header_names_the_source() -> None:
    bad = CSV.replace("TOTTRDVAL", "TURNOVER")
    with pytest.raises(ValueError, match=r"nse_bhavcopy_legacy.*TOTTRDVAL"):
        legacy.parse(make_zip(bad), source_file="x")


def test_bad_number_names_the_line() -> None:
    bad = CSV.replace("120.09", "abc")
    with pytest.raises(ValueError, match=r"nse_bhavcopy_legacy: line 2.*CLOSE"):
        legacy.parse(make_zip(bad), source_file="x")


def test_fetch_saves_untouched_bytes(tmp_path: Path) -> None:
    payload = make_zip()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/2021/NOV/cm04NOV2021bhav.csv.zip")
        return httpx.Response(200, content=payload)

    client = PoliteClient(transport=httpx.MockTransport(handler), sleep=lambda s: None)
    rec = legacy.fetch(client, RawStore(tmp_path), DAY)
    assert rec.relpath == "nse_bhavcopy_legacy/2021-11-04/cm04NOV2021bhav.csv.zip"
    assert rec.path.read_bytes() == payload


def test_clean_uses_legacy_rows_only_where_the_day_is_otherwise_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_raw_root(tmp_path)
    # The stale NSE file: stored under 4 Nov but holding 3 Nov data (here: the 4 Jan 2021 sample)
    stale = "nse_bhavcopy_full/2021-11-04/sec_bhavdata_full_04112021.csv"
    save_raw(
        root,
        "nse_bhavcopy_full",
        DAY,
        "sec_bhavdata_full_04112021.csv",
        fixture_bytes("nse_bhavcopy/sec_bhavdata_full_04012021.csv"),
    )
    save_raw(root, "nse_bhavcopy_legacy", DAY, "cm04NOV2021bhav.csv.zip", make_zip())
    monkeypatch.setattr(prices_daily, "skipped_raw_files", lambda: {stale: "stale copy of 3 Nov"})
    ctx = make_context(root)
    ctx.master = build_master(ctx)
    df = prices_daily.build(ctx)

    on_day = df[df["date"] == DAY]
    assert set(on_day["symbol"]) == {"MON100", "NIFTYBEES"}
    r = on_day[on_day["symbol"] == "NIFTYBEES"].iloc[0]
    assert r["close"] == 193.59 and r["prev_close"] == 192.58
    assert r["value_source"] == "bhavcopy_legacy" and r["value"] == 130_195_880.06
    assert r["source_file"] == "nse_bhavcopy_legacy/2021-11-04/cm04NOV2021bhav.csv.zip"
    # other days are untouched and still come from the full bhavcopy
    other = df[(df["symbol"] == "NIFTYBEES") & (df["date"] == date(2021, 1, 4))].iloc[0]
    assert other["value_source"] == "bhavcopy"
    assert not df.duplicated(["symbol", "date"]).any()
