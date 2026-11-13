"""Tests for the AMFI NAV collector.

Parsing is tested on real saved files (tests/fixtures/amfi/), no network. Fetching is
tested with httpx.MockTransport, so nothing here touches the internet.

Plain words: NAV (net asset value) is what one unit of a fund is worth, published
once a day per scheme. AMFI is the industry body that collects them all.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import httpx
import pandas as pd
import pytest

from etfp.collect import amfi_nav as amfi
from etfp.http import PoliteClient
from etfp.rawstore import RawStore

FIXTURES = Path(__file__).parent / "fixtures" / "amfi"

# Scheme codes from docs/DECISIONS.md D9.
MONQ50, MON100, MASPTOP50 = 149438, 114984, 149169
MAFANG, MAHKTECH = 148927, 149379
HNGSNGBEES, NIFTYBEES, GOLDBEES = 140095, 140084, 140088
TRACKED = {MONQ50, MON100, MASPTOP50, MAFANG, MAHKTECH, HNGSNGBEES, NIFTYBEES, GOLDBEES}


@pytest.fixture(scope="module")
def navall() -> pd.DataFrame:
    return amfi.parse_navall((FIXTURES / "NAVAll.txt").read_bytes(), source_file="NAVAll.txt")


@pytest.fixture(scope="module")
def hist_sep() -> pd.DataFrame:
    name = "navhistory_20260901_20260910.txt"
    return amfi.parse_history((FIXTURES / name).read_bytes(), source_file=name)


@pytest.fixture(scope="module")
def hist_jan() -> pd.DataFrame:
    name = "navhistory_20210101_20210108.txt"
    return amfi.parse_history((FIXTURES / name).read_bytes(), source_file=name)


def nav(frame: pd.DataFrame, code: int, day: date) -> float:
    rows = frame[(frame["scheme_code"] == code) & (frame["nav_date"] == day)]
    assert len(rows) == 1, f"expected one row for {code} on {day}, got {len(rows)}"
    return float(rows.iloc[0]["nav"])


def raw(text: str) -> bytes:
    return text.encode("utf-8")


NAVALL_HEADER = (
    "Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;"
    "Plan;Option;Net Asset Value;Date\n"
)


# -- urls and names -------------------------------------------------------------------


def test_history_url_uses_english_month_abbreviations_and_zero_padded_days():
    url = amfi.history_url(date(2021, 1, 1), date(2026, 9, 10))
    assert url == (
        "https://portal.amfiindia.com/DownloadNAVHistoryReport_Po.aspx"
        "?frmdt=01-Jan-2021&todt=10-Sep-2026"
    )


def test_history_raw_name_is_sortable():
    assert amfi.history_raw_name(date(2026, 9, 1), date(2026, 9, 7)) == (
        "navhistory_20260901_20260907.txt"
    )


# -- NAVAll fixture: hand-checked values ----------------------------------------------


def test_navall_row_count_and_columns(navall: pd.DataFrame):
    assert len(navall) == 23
    assert tuple(navall.columns) == amfi.OUTPUT_COLUMNS
    assert navall.attrs["nav_missing"] == 0
    assert navall["scheme_code"].is_unique


def test_navall_tracked_etfs_navs_on_9_nov_2026(navall: pd.DataFrame):
    # Read by eye from the raw file (D9 scheme codes).
    day = date(2026, 11, 9)
    assert nav(navall, MON100, day) == 256.1057
    assert nav(navall, MONQ50, day) == 114.3849
    assert nav(navall, MASPTOP50, day) == 64.1521
    assert nav(navall, MAFANG, day) == 159.5331
    assert nav(navall, MAHKTECH, day) == 16.2818
    assert nav(navall, HNGSNGBEES, day) == 436.5722
    assert nav(navall, NIFTYBEES, day) == 285.8134
    assert nav(navall, GOLDBEES, day) == 116.6176


def test_navall_isins_match_decision_d9(navall: pd.DataFrame):
    isin = navall.set_index("scheme_code")["isin_growth"]
    assert isin[MONQ50] == "INF247L01AU3"
    assert isin[MON100] == "INF247L01AP3"
    assert isin[MASPTOP50] == "INF769K01HP3"
    assert isin[MAFANG] == "INF769K01HF4"
    assert isin[MAHKTECH] == "INF769K01HS7"
    assert isin[HNGSNGBEES] == "INF204KB19I1"
    assert isin[NIFTYBEES] == "INF204KB14I2"
    assert isin[GOLDBEES] == "INF204KB17I5"


def test_navall_dash_isin_is_missing_not_text(navall: pd.DataFrame):
    row = navall[navall["scheme_code"] == MON100].iloc[0]
    assert pd.isna(row["isin_reinvest"])  # file has "-"
    assert row["isin_growth"] == "INF247L01AP3"


def test_navall_blank_plan_and_option_are_missing(navall: pd.DataFrame):
    row = navall[navall["scheme_code"] == MON100].iloc[0]
    assert pd.isna(row["plan"]) and pd.isna(row["option"])
    gold = navall[navall["scheme_code"] == GOLDBEES].iloc[0]
    assert gold["plan"] == "Direct Plan" and pd.isna(gold["option"])


def test_navall_amc_and_category_come_from_lines_above(navall: pd.DataFrame):
    by_code = navall.set_index("scheme_code")
    assert by_code.loc[MON100, "amc"] == "Motilal Oswal Mutual Fund"
    assert by_code.loc[MON100, "category"] == "Open Ended Schemes(Other Scheme - Other  ETFs)"
    # Mirae's two overseas ETFs sit under the overseas category, the S&P one under equity ETF.
    assert "ETFs investing overseas" in by_code.loc[MAFANG, "category"]
    assert "Equity ETF" in by_code.loc[MASPTOP50, "category"]
    # A category line may follow an AMC line (Franklin, 118554): AMC is kept.
    assert by_code.loc[118554, "amc"] == "Franklin Templeton Mutual Fund"
    assert by_code.loc[118554, "category"].endswith("Medium Duration Fund)")
    # A new category without a new AMC line continues the same fund house (Nippon x5).
    assert by_code.loc[140085, "amc"] == "Nippon India Mutual Fund"
    assert by_code.loc[149758, "amc"] == "Nippon India Mutual Fund"


def test_navall_zero_nav_is_a_value_not_missing(navall: pd.DataFrame):
    # Franklin's segregated portfolios really have NAV 0.0000.
    assert nav(navall, 148304, date(2026, 11, 9)) == 0.0
    assert navall.attrs["nav_missing"] == 0


def test_navall_keeps_full_precision_and_old_dates(navall: pd.DataFrame):
    assert nav(navall, 146839, date(2019, 4, 17)) == 10.0087785
    assert nav(navall, 130565, date(2018, 12, 31)) == 1680494.0463  # NAV above a million
    # A stale NAV keeps its own (old) date; nothing is rolled forward.
    assert nav(navall, 120607, date(2018, 5, 25)) == 12.2962


def test_navall_source_file_lineage(navall: pd.DataFrame):
    assert set(navall["source_file"]) == {"NAVAll.txt"}


def test_navall_dtypes(navall: pd.DataFrame):
    assert str(navall["scheme_code"].dtype) == "Int64"
    assert str(navall["nav"].dtype) == "float64"
    assert all(isinstance(d, date) for d in navall["nav_date"])


def test_navall_non_utf8_apostrophe_survives(navall: pd.DataFrame):
    assert navall["category"].str.contains("Children\u2019s", regex=False).any()


# -- history fixtures -----------------------------------------------------------------


def test_history_september_hand_checked_navs(hist_sep: pd.DataFrame):
    assert nav(hist_sep, MONQ50, date(2026, 9, 4)) == 118.7371  # used in D4's 19.5% check
    assert nav(hist_sep, MON100, date(2026, 9, 1)) == 269.8926
    assert nav(hist_sep, MON100, date(2026, 9, 10)) == 271.5596
    assert nav(hist_sep, GOLDBEES, date(2026, 9, 2)) == 123.5036
    assert nav(hist_sep, MAFANG, date(2026, 9, 3)) == 175.3174


def test_history_name_column_comes_from_nav_name_header(hist_sep: pd.DataFrame):
    names = set(hist_sep.loc[hist_sep["scheme_code"] == MON100, "scheme_name"])
    assert names == {"Motilal Oswal Nasdaq 100 ETF (MOFN100)"}


def test_history_columns_found_by_name_not_position(hist_sep: pd.DataFrame):
    # In the history file the ISIN comes BEFORE the NAV and plan/option come before the ISINs,
    # unlike NAVAll. If positions were mixed up the ISIN would land in the wrong column.
    row = hist_sep[(hist_sep["scheme_code"] == GOLDBEES)].iloc[0]
    assert row["isin_growth"] == "INF204KB17I5"
    assert row["plan"] == "Direct Plan"
    assert pd.isna(row["option"])


def test_history_date_range_and_row_counts(hist_sep: pd.DataFrame, hist_jan: pd.DataFrame):
    assert (hist_sep["nav_date"].min(), hist_sep["nav_date"].max()) == (
        date(2026, 9, 1),
        date(2026, 9, 10),
    )
    assert (hist_jan["nav_date"].min(), hist_jan["nav_date"].max()) == (
        date(2021, 1, 1),
        date(2021, 1, 8),
    )
    assert len(hist_sep) == 142 and len(hist_jan) == 78


def test_history_skips_us_holiday_for_mirae_but_not_motilal(hist_sep: pd.DataFrame):
    """Finding for D10: on US Labor Day (Mon 7 Sep 2026) Mirae publishes NO NAV row for
    its US ETF (04 Sep then 08 Sep) whereas Motilal publishes a carried-forward NAV.
    Code that assumes one NAV per Indian session per fund will break on this."""
    mirae = set(hist_sep[hist_sep["scheme_code"] == MASPTOP50]["nav_date"])
    motilal = set(hist_sep[hist_sep["scheme_code"] == MON100]["nav_date"])
    assert date(2026, 9, 7) not in mirae
    assert date(2026, 9, 7) in motilal


def test_history_jan_2021_mon100_is_pre_split_price(hist_jan: pd.DataFrame):
    """The history is NOT adjusted for the 10:1 face-value split of 17 Jun 2021 (D9)."""
    assert nav(hist_jan, MON100, date(2021, 1, 1)) == 920.2743
    assert nav(hist_jan, MON100, date(2021, 1, 8)) == 928.3732


def test_history_each_scheme_date_pair_is_unique(hist_sep: pd.DataFrame):
    assert not hist_sep.duplicated(["scheme_code", "nav_date"]).any()


def test_history_category_with_inner_spaces_is_kept_verbatim(hist_sep: pd.DataFrame):
    assert "Open Ended Schemes ( Money Market )" in set(hist_sep["category"])


# -- synthetic edge cases -------------------------------------------------------------


def test_non_numeric_nav_is_nan_and_counted_never_zero():
    text = NAVALL_HEADER + (
        "\nOpen Ended Schemes(Debt Scheme - Gilt Fund)\n\nTest Mutual Fund\n\n"
        "1;INF000000001;-;Fund A;;;N.A.;09-Nov-2026\n"
        "2;INF000000002;-;Fund B;;;-;09-Nov-2026\n"
        "3;INF000000003;-;Fund C;;;;09-Nov-2026\n"
        "4;INF000000004;-;Fund D;;;12.5;09-Nov-2026\n"
    )
    df = amfi.parse_navall(raw(text), source_file="t")
    assert df.attrs["nav_missing"] == 3
    assert df["nav"].isna().sum() == 3
    assert df.loc[df["scheme_code"] == 4, "nav"].iloc[0] == 12.5


def test_crlf_line_endings_and_utf8_bom_are_handled():
    text = NAVALL_HEADER.replace("\n", "\r\n") + (
        "Open Ended Schemes(Other Scheme - Gold ETF)\r\n\r\nTest Mutual Fund\r\n\r\n"
        "7;INF000000007;-;Gold Fund;;;99.1234;09-Nov-2026\r\n"
    )
    df = amfi.parse_navall(b"\xef\xbb\xbf" + raw(text), source_file="t")
    assert df["scheme_code"].tolist() == [7]
    assert df["amc"].tolist() == ["Test Mutual Fund"]


def test_windows_1252_fallback_for_non_utf8_bytes():
    body = NAVALL_HEADER + "5;-;-;Caf\xe9 Fund;;;1.0;09-Nov-2026\n"
    df = amfi.parse_navall(body.encode("cp1252"), source_file="t")
    assert df["scheme_name"].tolist() == ["Café Fund"]


def test_scheme_before_any_amc_or_category_gets_missing_labels():
    text = NAVALL_HEADER + "9;-;-;Orphan;;;1.5;09-Nov-2026\n"
    df = amfi.parse_navall(raw(text), source_file="t")
    assert pd.isna(df.iloc[0]["amc"]) and pd.isna(df.iloc[0]["category"])


def test_amc_name_that_starts_with_digits_is_not_a_scheme_row():
    text = NAVALL_HEADER + (
        "Open Ended Schemes(Growth)\n360 ONE Mutual Fund\n8;-;-;Fund 360;;;1.5;09-Nov-2026\n"
    )
    df = amfi.parse_navall(raw(text), source_file="t")
    assert df["amc"].tolist() == ["360 ONE Mutual Fund"]


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("", "file is empty"),
        ("   \n\n", "file is empty"),
        (NAVALL_HEADER, "no data rows"),
        ("Scheme Code;Foo;Date\n1;x;09-Nov-2026\n", "header is missing"),
        (NAVALL_HEADER + "1;-;-;A;;;1.0\n", "expected 8 fields but found 7"),
        (NAVALL_HEADER + "1;-;-;A;;;1.0;9 Nov 2026\n", "not a date"),
        (NAVALL_HEADER + "1;-;-;A;;;1.0;31-Feb-2026\n", "not a real calendar date"),
        (NAVALL_HEADER + "1;-;-;A;;;1.0;09-Foo-2026\n", "not a date"),
        (NAVALL_HEADER + "abc;-;-;A;;;1.0;09-Nov-2026\n", "not a scheme code"),
    ],
)
def test_format_changes_fail_loudly_naming_the_source(body: str, message: str):
    with pytest.raises(ValueError, match=message) as exc:
        amfi.parse_navall(raw(body), source_file="t")
    assert "amfi_navall" in str(exc.value)


def test_history_error_names_the_history_source():
    with pytest.raises(ValueError, match="amfi_nav_history"):
        amfi.parse_history(raw("Scheme Code;Foo\n"), source_file="t")


def test_history_header_is_not_valid_for_navall():
    """NAVAll needs 'Scheme Name'; the history file has 'NAV Name' instead. Feeding the
    wrong file to the wrong parser must fail, not return empty names."""
    content = (FIXTURES / "navhistory_20260901_20260910.txt").read_bytes()
    with pytest.raises(ValueError, match="Scheme Name"):
        amfi.parse_navall(content, source_file="t")


# -- fetching (mock transport) --------------------------------------------------------


def make_client(handler) -> PoliteClient:
    return PoliteClient(
        transport=httpx.MockTransport(handler),
        sleep=lambda s: None,
        max_retries=0,
    )


def test_fetch_navall_saves_untouched_bytes(tmp_path: Path):
    body = (FIXTURES / "NAVAll.txt").read_bytes()
    seen: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        return httpx.Response(200, content=body)

    store = RawStore(tmp_path)
    with make_client(handler) as client:
        rec = amfi.fetch_navall(client, store, date(2026, 11, 13))
    assert rec.relpath == "amfi_navall/2026-11-13/NAVAll.txt"
    assert rec.path.read_bytes() == body
    assert str(seen[0]) == amfi.NAVALL_URL


def test_fetch_history_saves_under_end_date_and_rejects_reversed_range(tmp_path: Path):
    body = (FIXTURES / "navhistory_20260901_20260910.txt").read_bytes()
    seen: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        return httpx.Response(200, content=body)

    store = RawStore(tmp_path)
    with make_client(handler) as client:
        rec = amfi.fetch_history(client, store, date(2026, 9, 1), date(2026, 9, 10))
        assert rec.relpath == "amfi_nav_history/2026-09-10/navhistory_20260901_20260910.txt"
        assert seen[0].params["frmdt"] == "01-Sep-2026"
        assert seen[0].params["todt"] == "10-Sep-2026"
        with pytest.raises(ValueError, match="after end"):
            amfi.fetch_history(client, store, date(2026, 9, 11), date(2026, 9, 10))
    assert len(seen) == 1  # the reversed range never reached the network
