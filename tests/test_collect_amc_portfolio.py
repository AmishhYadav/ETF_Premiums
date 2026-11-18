"""AMC monthly portfolio files: parsing real (trimmed) workbooks, listings and downloads.

The fixtures in tests/fixtures/amc_portfolio/ are real files from the three fund houses with
all sheets removed except the schemes we use (values only). All rupee amounts in a file are
"Rs. in Lakhs/Lacs": 1 lakh = 100,000 rupees. Numbers below were read off the Excel files by
hand.
"""

# ruff: noqa: E501

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from etfp.collect import amc_portfolio as ap
from etfp.http import PoliteClient, SourceBlockedError
from etfp.rawstore import RawStore

FIX = Path(__file__).parent / "fixtures" / "amc_portfolio"
DAY = date(2026, 11, 18)


def parse_fixture(name: str, amc: str):
    return ap.parse((FIX / name).read_bytes(), amc=amc, source_file=f"fx/{name}")


def one(frame, symbol: str, role: str):
    sub = frame[(frame["etf_symbol"] == symbol) & (frame["role"] == role)]
    assert len(sub), (symbol, role)
    return sub


# -- Motilal Oswal ------------------------------------------------------------------------


def test_motilal_feeder_holds_250_million_etf_units_at_30_sep_2026() -> None:
    """Sheet YO13 (Nasdaq 100 Fund of Fund): one holding, INF247L01AP3, 250,248,956 units.

    Net assets (GRAND TOTAL) 803,183.82 lakh = 803,183.82 x 100,000 = Rs 80,318,382,000.
    The ETF line is valued at 803,499.35 lakh and is 100.04% of net assets.
    """
    fof = one(parse_fixture("motilal_scheme_portfolio_2026-09.xlsx", "motilal"), "MON100", "fof")
    assert len(fof) == 1
    r = fof.iloc[0]
    assert r["month_end"] == date(2026, 9, 30)
    assert r["isin"] == "INF247L01AP3" and r["holding_name"] == "Motilal Oswal NASDAQ 100 ETF"
    assert r["quantity"] == 250_248_956
    assert r["market_value_inr"] == pytest.approx(803_499.35 * 100_000)
    assert r["pct_net_assets"] == pytest.approx(100.04)
    assert r["aum_inr"] == pytest.approx(80_318_382_000)
    assert r["scheme_name"] == "Motilal Oswal Nasdaq 100 Fund of Fund"


def test_motilal_etf_aum_and_first_holding() -> None:
    """MON100 total net assets 1,435,905.07 lakh = Rs 143,590,507,000; 101 equity holdings.

    The largest holding is Nvidia, 548,267 shares worth 119,991.83 lakh (8.36%).
    """
    etf = one(parse_fixture("motilal_scheme_portfolio_2026-09.xlsx", "motilal"), "MON100", "etf")
    assert len(etf) == 101
    assert etf["aum_inr"].nunique() == 1
    assert etf["aum_inr"].iloc[0] == pytest.approx(143_590_507_000)
    first = etf.iloc[0]
    assert first["holding_name"] == "Nvidia Corp Com" and first["quantity"] == 548_267
    assert first["market_value_inr"] == pytest.approx(119_991.83 * 100_000)


def test_motilal_q50_is_found_and_not_confused_with_nifty_500() -> None:
    q50 = one(parse_fixture("motilal_scheme_portfolio_2026-09.xlsx", "motilal"), "MONQ50", "etf")
    assert q50["scheme_name"].iloc[0] == "Motilal Oswal Nasdaq Q50 ETF"
    assert q50["aum_inr"].iloc[0] == pytest.approx(1_756_241_000, rel=1e-3)  # 17,562.41 lakh


def test_motilal_old_file_has_old_isin_and_fraction_percentages() -> None:
    """Dec 2020: the feeder held 17,739,747 units of the OLD ISIN INF247L01031 (pre 10:1 split).

    Older files print % to net assets as a fraction (0.9658 = 96.58%); parse returns percent.
    Net assets 170,201.66 lakh = Rs 17,020,166,000.
    """
    fof = one(parse_fixture("motilal_month_end_2020-12.xlsx", "motilal"), "MON100", "fof").iloc[0]
    assert fof["month_end"] == date(2020, 12, 31)
    assert fof["isin"] == "INF247L01031"
    assert fof["quantity"] == pytest.approx(17_739_747.0011)
    assert fof["pct_net_assets"] == pytest.approx(96.5858, abs=1e-3)
    assert fof["aum_inr"] == pytest.approx(17_020_166_000)


def test_motilal_binary_xls_is_read_by_content_not_by_name() -> None:
    """Jan 2023 is an old binary .xls (xlrd). Feeder: 337,020,103 units; net assets 323,900.87 lakh."""
    frame = parse_fixture("motilal_month_end_2023-01.xls", "motilal")
    fof = one(frame, "MON100", "fof").iloc[0]
    assert fof["quantity"] == 337_020_103
    assert fof["aum_inr"] == pytest.approx(32_390_087_000)
    assert one(frame, "MONQ50", "etf")["month_end"].iloc[0] == date(2023, 1, 31)


# -- Mirae Asset --------------------------------------------------------------------------


def test_mirae_feeder_and_etf_of_october_2026() -> None:
    """S&P 500 Top 50 FoF holds 102,882,370 ETF units (ISIN INF769K01HP3).

    FoF net assets 100,676.41 lakh = Rs 10,067,641,000; ETF net assets 110,680.20 lakh
    = Rs 11,068,020,000.
    """
    fof = parse_fixture("mirae_maspf_2026-10.xlsx", "mirae")
    r = one(fof, "MASPTOP50", "fof").iloc[0]
    assert r["month_end"] == date(2026, 10, 31)
    assert (r["isin"], r["quantity"]) == ("INF769K01HP3", 102_882_370)
    assert r["aum_inr"] == pytest.approx(10_067_641_000)
    etf = one(parse_fixture("mirae_matpf_2026-10.xlsx", "mirae"), "MASPTOP50", "etf")
    assert etf["aum_inr"].iloc[0] == pytest.approx(11_068_020_000)
    assert len(etf) == 51


def test_mirae_old_layout_has_a_security_code_column_before_the_name() -> None:
    """Aug 2021 FANG+ ETF: 10 holdings, the first is Alphabet Inc A, 5,975 shares worth
    12,649.25 lakh; net assets 112,586.21 lakh."""
    etf = one(parse_fixture("mirae_maftf_2021-08.xlsx", "mirae"), "MAFANG", "etf")
    assert len(etf) == 10
    first = etf.iloc[0]
    assert (first["holding_name"], first["isin"]) == ("Alphabet Inc A", "US02079K3059")
    assert first["quantity"] == 5975
    assert first["market_value_inr"] == pytest.approx(12_649.245928 * 100_000)
    assert etf["aum_inr"].iloc[0] == pytest.approx(11_258_620_780, rel=1e-6)
    assert etf["pct_net_assets"].sum() == pytest.approx(99.9985, abs=1e-3)


# -- Nippon India -------------------------------------------------------------------------


def test_nippon_hang_seng_bees_oct_2026() -> None:
    """Sheet HS: ISIN comes BEFORE the name in Nippon files. 95 holdings; the largest is
    Alibaba, 644,036 shares worth 7,565.01 lakh (7.63%). Net assets 99,128.724 lakh
    = Rs 9,912,872,407 (the file's xlsx content has a '.xls' name)."""
    etf = one(parse_fixture("nippon_monthly_2026-10.xlsx", "nippon"), "HNGSNGBEES", "etf")
    assert len(etf) == 95 and etf["month_end"].iloc[0] == date(2026, 10, 31)
    first = etf.iloc[0]
    assert first["holding_name"] == "Alibaba Group Holding Limited"
    assert (first["isin"], first["quantity"]) == ("KYG017191142", 644_036)
    assert first["market_value_inr"] == pytest.approx(756_501_000)
    assert etf["aum_inr"].iloc[0] == pytest.approx(9_912_872_406.75)


# -- failure modes ------------------------------------------------------------------------


def test_html_error_page_is_rejected_by_name() -> None:
    with pytest.raises(ap.PortfolioParseError, match="not an Excel file"):
        ap.parse(b"<html>Access denied</html>", amc="motilal", source_file="x/y.xlsx")


def test_workbook_without_our_schemes_gives_an_empty_frame() -> None:
    import io

    import openpyxl

    wb = openpyxl.Workbook()
    wb.active.append(["Motilal Oswal Nifty 50 Index Fund"])
    wb.active.append(["Portfolio as on December 31, 2020"])
    buf = io.BytesIO()
    wb.save(buf)
    out = ap.parse(buf.getvalue(), amc="motilal", source_file="x.xlsx")
    assert out.empty and list(out.columns) == list(ap.HOLDINGS_COLUMNS)


def test_sheet_with_our_scheme_but_no_grand_total_is_an_error() -> None:
    import io

    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Motilal Oswal Nasdaq 100 ETF"])
    ws.append(["Portfolio as on December 31, 2020"])
    ws.append(["Sr. No.", "Name", "ISIN", "Quantity", "Market value (Rs. In lakhs)", "%"])
    buf = io.BytesIO()
    wb.save(buf)
    with pytest.raises(ap.PortfolioParseError, match="GRAND TOTAL"):
        ap.parse(buf.getvalue(), amc="motilal", source_file="x.xlsx")


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Motilal Oswal Nasdaq 100 Fund of Fund", ("MON100", "fof")),
        ("Motilal Oswal Nasdaq 100 ETF", ("MON100", "etf")),
        ("Motilal Oswal Nasdaq Q50 ETF", ("MONQ50", "etf")),
        ("Mirae Asset NYSE FANG + ETF Fund of Fund", ("MAFANG", "fof")),
        ("Mirae Asset NYSE FANG+ETF", ("MAFANG", "etf")),
        ("Mirae Asset Hang Seng TECH ETF Fund of Fund", ("MAHKTECH", "fof")),
        (
            "Nippon India ETF Hang Seng BeES (An Open Ended Index Exchange Traded Fund)",
            ("HNGSNGBEES", "etf"),
        ),
        ("Motilal Oswal Nifty 500 Index Fund", None),
        ("Motilal Oswal Multi Asset Passive Fund of Fund", None),
    ],
)
def test_scheme_titles(title: str, expected) -> None:
    hit = ap.match_target(title)
    assert (None if hit is None else (hit.etf_symbol, hit.role)) == expected


# -- listings and downloads (mocked network) ----------------------------------------------


def client_for(handler) -> PoliteClient:
    return PoliteClient(transport=httpx.MockTransport(handler), sleep=lambda s: None, max_retries=0)


def test_mirae_listing_keeps_only_our_six_schemes_and_excel_files(tmp_path: Path) -> None:
    items = [
        {"Title": "Portfolio Details as on 31st October 2026 for Mirae Asset S&P 500 Top 50 ETF Fund of Fund",
         "URL": "/docs/default-source/portfolios/maspf_oct2026.xlsx", "PublishDate": "/Date(1794182400000)/"},
        {"Title": "Portfolio Details as on 31st October 2026 for Mirae Asset NYSE FANG + ETF",
         "URL": "/docs/default-source/portfolios/maftf_oct2026.xlsx", "PublishDate": "/Date(1794182400000)/"},
        {"Title": "Portfolio Details as on 31st October 2026 for Mirae Asset Nifty 50 Index Fund",
         "URL": "/docs/default-source/portfolios/n50.xlsx", "PublishDate": "/Date(1794182400000)/"},
        {"Title": "Mirae Asset Hang Seng TECH ETF", "URL": "/docs/x/mahtf.pdf", "PublishDate": "/Date(1794182400000)/"},
    ]  # fmt: skip
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200, json={"Data": items, "DataCount": 4, "ReturnCode": "0", "ReturnMsg": "success"}
        )

    store = RawStore(tmp_path / "raw")
    with client_for(handler) as client:
        refs = ap.mirae_refs(client, store, DAY)
    assert seen["body"]["request"]["modulename"] == "portfolio_tab1"
    assert [(r.name.split("_")[1], r.name.split("_")[2]) for r in refs] == [
        ("MASPTOP50", "fof"),
        ("MAFANG", "etf"),
    ]
    assert (
        refs[0].url
        == "https://www.miraeassetmf.co.in/docs/default-source/portfolios/maspf_oct2026.xlsx"
    )
    assert (
        tmp_path / "raw" / "amc_portfolio_mirae" / "2026-11-18" / "listing_monthly.json"
    ).exists()


def test_motilal_listing_skips_fortnightly_and_non_excel(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "category=month%20end%20portfolio" in str(request.url)
        base = "/content/dam/motilal-mf/downloads/mf/month-end-portfolio/2026/oct/"
        return httpx.Response(200, json={"results": [
            {"path": base + "Scheme Portfolio Details September 2026.xlsx",
             "title": "scheme portfolio details september 2026", "publishDate": "08-10-2026"},
            {"path": base + "Fortnightly Portfolio Report 15th Oct 2026.xlsx",
             "title": "fortnightly portfolio report -15th oct 2026", "publishDate": "15-10-2026"},
            {"path": base + "note.pdf", "title": "month end portfolio", "publishDate": "08-10-2026"},
        ]})  # fmt: skip

    store = RawStore(tmp_path / "raw")
    with client_for(handler) as client:
        refs = ap.motilal_refs(client, store, DAY, date(2026, 10, 1), date(2026, 10, 31))
    assert len(refs) == 1
    assert refs[0].url.endswith("/oct/Scheme%20Portfolio%20Details%20September%202026.xlsx")
    assert refs[0].published == date(2026, 10, 8)


def test_nippon_page_links_skip_debt_fortnightly_and_old_files(tmp_path: Path) -> None:
    page = "".join(
        f'<a href="/InvestorServices/FactsheetsDocuments/{n}">x</a>'
        for n in (
            "NIMF-MONTHLY-PORTFOLIO-31-Oct-26.xls",
            "NIMF-FORTNIGHTLY-PORTFOLIO-31-Oct-26.xls",
            "Debt-Portfolio-as-on-31st-Oct-21.xls",
            "Reliance-Monthly-Portfolios-28-02-2019.xls",
            "RiskParameter-Oct-2026.xls",
            "Nippon-FS-October-2026.pdf",
        )
    )
    store = RawStore(tmp_path / "raw")
    with client_for(lambda r: httpx.Response(200, text=page)) as client:
        refs = ap.nippon_refs(client, store, DAY)
    assert [r.name for r in refs] == ["NIMF-MONTHLY-PORTFOLIO-31-Oct-26.xls"]


def test_download_saves_untouched_bytes_skips_known_urls_and_rejects_html(tmp_path: Path) -> None:
    good = (FIX / "mirae_maspf_2026-10.xlsx").read_bytes()
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("bad.xlsx"):
            return httpx.Response(200, content=b"<html>login</html>")
        return httpx.Response(200, content=good)

    refs = [
        ap.FileRef("mirae", "https://www.miraeassetmf.co.in/a/good.xlsx", "good.xlsx", "t", None),
        ap.FileRef("mirae", "https://www.miraeassetmf.co.in/a/bad.xlsx", "bad.xlsx", "t", None),
        ap.FileRef("mirae", "https://www.miraeassetmf.co.in/a/old.xlsx", "old.xlsx", "t", None),
    ]
    store = RawStore(tmp_path / "raw")
    with client_for(handler) as client:
        out = ap.download(
            client, store, DAY, refs, skip_urls={"https://www.miraeassetmf.co.in/a/old.xlsx"}
        )
    assert [o.status for o in out] == ["fetched", "failed", "exists"]
    assert "not an Excel file" in out[1].detail
    assert calls == ["/a/good.xlsx", "/a/bad.xlsx"]
    saved = tmp_path / "raw" / "amc_portfolio_mirae" / "2026-11-18" / "good.xlsx"
    assert saved.read_bytes() == good
    # the manifest remembers the URL, so a second run would skip it
    assert "https://www.miraeassetmf.co.in/a/good.xlsx" in ap.already_fetched(
        store, "amc_portfolio_mirae"
    )


def test_a_block_stops_the_run(tmp_path: Path) -> None:
    ref = ap.FileRef("nippon", "https://mf.nipponindiaim.com/a.xls", "a.xls", "t", None)
    store = RawStore(tmp_path / "raw")
    with client_for(lambda r: httpx.Response(403)) as client, pytest.raises(SourceBlockedError):
        ap.download(client, store, DAY, [ref])


def test_motilal_predicted_ref_for_unlisted_months() -> None:
    # May 2026 and June 2026 workbooks were on the server but not in the search listing (D54)
    from etfp.collect import amc_portfolio as ap

    may = ap.motilal_predicted_ref(2026, 5)
    assert may.url.endswith(
        "/month-end-portfolio/2026/jun/Scheme%20Portfolio%20Detail%20May%202026.xlsx"
    )
    assert may.name == "2026-jun__Scheme_Portfolio_Detail_May_2026.xlsx"
    assert (
        ap.motilal_predicted_ref(2026, 6).name == "2026-jul__Scheme_Portfolio_Detail_June_2026.xlsx"
    )
    dec = ap.motilal_predicted_ref(2025, 12)  # December is published in January of the next year
    assert "/2026/jan/Scheme%20Portfolio%20Detail%20December%202025.xlsx" in dec.url
