"""Tests for the NSE security-master collector. Parsing uses real saved samples; no network."""

from __future__ import annotations

import gzip
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pandas as pd
import pytest

from etfp.collect import nse_etf_listfile
from etfp.collect import nse_secmaster as sm
from etfp.http import PoliteClient, SourceNotFoundError
from etfp.rawstore import RawStore

FIXTURES = Path(__file__).parent / "fixtures" / "nse_secmaster"
ETF_LIST = Path(__file__).parent / "fixtures" / "nse_etf_listfile" / "eq_etfseclist.csv"

D_NOV26 = date(2026, 11, 9)
D_SEP26 = date(2026, 9, 4)
D_JUL24 = date(2024, 7, 1)
D_JAN25 = date(2025, 1, 2)


def load(day: date) -> bytes:
    return (FIXTURES / sm.raw_name(day)).read_bytes()


def parse_day(day: date) -> pd.DataFrame:
    return sm.parse(
        load(day),
        file_date=day,
        source_file=f"nse_security_master/{day.isoformat()}/{sm.raw_name(day)}",
    )


@pytest.fixture(scope="module")
def nov26() -> pd.DataFrame:
    return parse_day(D_NOV26)


def row(frame: pd.DataFrame, symbol: str, series: str = "EQ") -> pd.Series:
    rows = frame[(frame["symbol"] == symbol) & (frame["series"] == series)]
    assert len(rows) == 1, f"{symbol} {series}: {len(rows)} rows"
    return rows.iloc[0]


def gz_replace(raw: bytes, old: bytes, new: bytes, count: int = 1) -> bytes:
    return gzip.compress(gzip.decompress(raw).replace(old, new, count))


# -- names -----------------------------------------------------------------------------


def test_url_and_raw_name():
    assert sm.url_for(D_NOV26) == (
        "https://nsearchives.nseindia.com/content/cm/NSE_CM_security_09112026.csv.gz"
    )
    assert sm.raw_name(D_NOV26) == "NSE_CM_security_09112026.csv.gz"
    assert sm.SOURCE == "nse_security_master"


def test_file_date_from_name():
    assert sm.file_date_from_name("NSE_CM_security_09112026.csv.gz") == D_NOV26
    assert sm.file_date_from_name("NSE_CM_security_09112026.v2.csv.gz") == D_NOV26
    relpath = "nse_security_master/2026-11-09/NSE_CM_security_09112026.v12.csv.gz"
    assert sm.file_date_from_name(relpath) == D_NOV26
    for bad in (
        "NSE_CM_security_9112026.csv.gz",
        "other.csv.gz",
        "NSE_CM_security_31022026.csv.gz",
    ):
        with pytest.raises(ValueError, match="nse_security_master"):
            sm.file_date_from_name(bad)


# -- parsing: known values ------------------------------------------------------------------


def test_monq50_9_nov_2026(nov26):
    r = row(nov26, "MONQ50")
    assert r["file_date"] == D_NOV26
    assert r["issued_units"] == 14675018  # from 1.46750180000000E+07, exactly
    assert r["band_low"] == pytest.approx(206.71)
    assert r["band_high"] == pytest.approx(252.63)
    assert r["price_range_raw"] == "206.71-252.63"
    assert r["isin"] == "INF247L01AU3"
    assert r["name"] == "MOTILALAMC - MONQ50"
    assert r["par_value_raw"] == 1000
    assert r["delete_flag"] == "N"


def test_band_is_ten_percent_around_the_close(nov26):
    # 229.67 (the 9 Nov close) x 0.90 and x 1.10, rounded in the exchange's favour
    r = row(nov26, "MONQ50")
    assert r["band_low"] == pytest.approx(229.67 * 0.9, abs=0.01)
    assert r["band_high"] == pytest.approx(229.67 * 1.1, abs=0.01)


def test_sep_2026_bands():
    df = parse_day(D_SEP26)
    assert row(df, "MONQ50")["band_low"] == pytest.approx(127.74)
    assert row(df, "MONQ50")["band_high"] == pytest.approx(156.12)
    assert row(df, "GOLDBEES")["band_low"] == pytest.approx(119.54)
    assert row(df, "GOLDBEES")["band_high"] == pytest.approx(134.80)


def test_jul_2024_units_and_bands():
    df = parse_day(D_JUL24)
    assert row(df, "MONQ50")["issued_units"] == 3275018
    assert row(df, "NIFTYBEES")["issued_units"] == 1101166386
    assert row(df, "NIFTYBEES")["band_low"] == pytest.approx(213.60)
    assert row(df, "NIFTYBEES")["band_high"] == pytest.approx(320.41)


def test_jan_2025_hngsngbees_units():
    df = parse_day(D_JAN25)
    assert row(df, "HNGSNGBEES")["issued_units"] == 23586680


def test_all_series_rows_of_an_etf_are_kept(nov26):
    series = set(nov26.loc[nov26["symbol"] == "MONQ50", "series"])
    assert {"EQ", "BL", "AF", "RL"} <= series


def test_dummy_inav_instrument_and_leading_space_name(nov26):
    inav = row(nov26, "MONQ50INAV")
    assert inav["isin"] == "DUMMY0000120"
    # the raw name is ' SREE RAYALSEEMA ALKALIES' (leading space); surrounding spaces are stripped
    assert row(nov26, "SREERAYALK")["name"] == "SREE RAYALSEEMA ALKALIES"


def test_delete_flag_values(nov26):
    assert {"N", "Y"} <= set(nov26["delete_flag"].dropna())


# -- the listing-date code -------------------------------------------------------------------


def test_listing_date_code_is_seconds_since_1980_01_01():
    """Finding: ListgDt counts seconds from 1980-01-01 (not 1970).

    It reproduces the listing dates of the ETF list file for all 11 fixture ETFs.
    parse() still keeps the raw number; this test documents the evidence.
    """
    etfs = nse_etf_listfile.parse(ETF_LIST.read_bytes(), source_file="x").set_index("symbol")
    master = parse_day(D_NOV26)
    epoch = datetime(1980, 1, 1, tzinfo=UTC)
    checked = 0
    for symbol in etfs.index:
        match = master[(master["symbol"] == symbol) & (master["series"] == "EQ")]
        if match.empty or symbol == "NXT100CASE":
            continue
        code = int(match["listing_date_raw"].iloc[0])
        assert (epoch + timedelta(seconds=code)).date() == etfs.loc[symbol, "listing_date"], symbol
        checked += 1
    assert checked >= 11


# -- parsing: shape, dtypes, lineage ------------------------------------------------------------


def test_columns_and_dtypes(nov26):
    assert tuple(nov26.columns) == sm.OUTPUT_COLUMNS
    for name in ("issued_units", "par_value_raw", "listing_date_raw"):
        assert nov26[name].dtype == "Int64"
    for name in ("band_low", "band_high"):
        assert nov26[name].dtype == "float64"
    for name in (
        "symbol",
        "series",
        "name",
        "isin",
        "price_range_raw",
        "call_auction_ind",
        "delete_flag",
        "slb_eligible_raw",
        "source_file",
    ):
        assert nov26[name].dtype == "string"
    assert nov26["file_date"].dtype == object
    assert all(type(d) is date for d in nov26["file_date"])


def test_source_file_on_every_row(nov26):
    assert (nov26["source_file"] == f"nse_security_master/2026-11-09/{sm.raw_name(D_NOV26)}").all()


def test_no_duplicate_symbol_series_in_any_fixture():
    for day in (D_NOV26, D_SEP26, D_JUL24, D_JAN25):
        assert not parse_day(day).duplicated(["symbol", "series"]).any(), day


def test_empty_slb_flag_is_missing(nov26):
    assert nov26["slb_eligible_raw"].isna().all()


def test_returns_every_data_row():
    lines = gzip.decompress(load(D_NOV26)).decode().splitlines()
    assert len(parse_day(D_NOV26)) == len([x for x in lines if x.strip()]) - 1


# -- parsing: unusual values ------------------------------------------------------------------


def make_gz(rows: list[dict[str, str]]) -> bytes:
    cols = list(sm._REQUIRED)  # the columns the parser needs
    lines = [",".join(cols)]
    base = dict.fromkeys(cols, "")
    base.update(TckrSymb="TEST", SctySrs="EQ", FinInstrmNm="TEST ETF", ISIN="INF000000000")
    for r in rows:
        lines.append(",".join({**base, **r}[c] for c in cols))
    return gzip.compress(("\n".join(lines) + "\n").encode())


def test_band_missing_when_empty_or_not_low_dash_high():
    df = sm.parse(
        make_gz([{"PricRg": ""}, {"PricRg": "No Band"}, {"PricRg": "10.5-20"}]),
        file_date=D_NOV26,
        source_file="x",
    )
    assert df["band_low"].isna().tolist() == [True, True, False]
    assert df["band_high"].isna().tolist() == [True, True, False]
    assert df["price_range_raw"].tolist() == [pd.NA, "No Band", "10.5-20"]
    assert df.loc[2, "band_low"] == pytest.approx(10.5)
    assert df.loc[2, "band_high"] == pytest.approx(20.0)


def test_issued_units_exact_conversion_and_missing():
    df = sm.parse(
        make_gz(
            [
                {"IssdCptl": "1.46750180000000E+07"},
                {"IssdCptl": "4.78627593500000E+09"},
                {"IssdCptl": "0.00000000000000E+00"},
                {"IssdCptl": "123"},
                {"IssdCptl": ""},
            ]
        ),
        file_date=D_NOV26,
        source_file="x",
    )
    assert df["issued_units"].tolist() == [14675018, 4786275935, 0, 123, pd.NA]


def test_fractional_units_raise():
    with pytest.raises(ValueError, match=r"line 2: column IssdCptl"):
        sm.parse(make_gz([{"IssdCptl": "1.5E+00"}]), file_date=D_NOV26, source_file="x")
    with pytest.raises(ValueError, match="IssdCptl"):
        sm.parse(make_gz([{"IssdCptl": "lots"}]), file_date=D_NOV26, source_file="x")


def test_non_integer_par_value_raises():
    with pytest.raises(ValueError, match="ParVal"):
        sm.parse(make_gz([{"ParVal": "10.5"}]), file_date=D_NOV26, source_file="x")


# -- parsing: malformed input ------------------------------------------------------------------


def test_html_instead_of_gzip_raises():
    html = (FIXTURES / "not_gzip_404.html").read_bytes()
    with pytest.raises(
        ValueError, match=r"nse_security_master: not a gzip file \(looks like an HTML"
    ):
        sm.parse(html, file_date=date(2024, 1, 2), source_file="x")


def test_truncated_gzip_raises():
    with pytest.raises(ValueError, match="nse_security_master"):
        sm.parse(load(D_NOV26)[:-40], file_date=D_NOV26, source_file="x")


def test_missing_column_raises():
    broken = gz_replace(load(D_NOV26), b"PricRg", b"PriceRange")
    with pytest.raises(ValueError, match=r"nse_security_master.*PricRg"):
        sm.parse(broken, file_date=D_NOV26, source_file="x")


def test_wrong_field_count_names_the_line():
    raw = gzip.decompress(load(D_NOV26))
    broken = gzip.compress(raw + b"1,SHORT,EQ\n")
    last = len(raw.decode().splitlines()) + 1
    with pytest.raises(ValueError, match=rf"nse_security_master: line {last}: expected 120"):
        sm.parse(broken, file_date=D_NOV26, source_file="x")


def test_header_only_and_empty_raise():
    header = gzip.decompress(load(D_NOV26)).split(b"\n", 1)[0] + b"\n"
    with pytest.raises(ValueError, match="no data rows"):
        sm.parse(gzip.compress(header), file_date=D_NOV26, source_file="x")
    with pytest.raises(ValueError, match="empty"):
        sm.parse(gzip.compress(b""), file_date=D_NOV26, source_file="x")


# -- fetch ------------------------------------------------------------------------------------


def make_client(handler) -> PoliteClient:
    return PoliteClient(transport=httpx.MockTransport(handler), sleep=lambda s: None)


def test_fetch_stores_untouched_bytes(tmp_path):
    raw = load(D_NOV26)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=raw)

    store = RawStore(tmp_path / "raw")
    record = sm.fetch(make_client(handler), store, D_NOV26)

    assert len(seen) == 1
    assert str(seen[0].url) == sm.url_for(D_NOV26)
    assert seen[0].headers["accept"] == "*/*"
    assert seen[0].headers["referer"] == "https://www.nseindia.com/"
    assert record.relpath == "nse_security_master/2026-11-09/NSE_CM_security_09112026.csv.gz"
    assert record.path.read_bytes() == raw
    parsed = sm.parse(
        record.path.read_bytes(),
        file_date=sm.file_date_from_name(record.name),
        source_file=record.relpath,
    )
    assert row(parsed, "MONQ50")["issued_units"] == 14675018


def test_fetch_404_propagates_and_stores_nothing(tmp_path):
    store = RawStore(tmp_path / "raw")
    client = make_client(lambda request: httpx.Response(404, text="<html>not found</html>"))
    with pytest.raises(SourceNotFoundError):
        sm.fetch(client, store, date(2024, 1, 2))
    assert not store.root.exists()
