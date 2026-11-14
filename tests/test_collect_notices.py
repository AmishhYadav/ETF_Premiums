"""Notice reader collectors: parsers on real saved responses, fetchers on a fake server."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from etfp.collect import notices as n
from etfp.http import PoliteClient, SourceBlockedError, SourceError
from etfp.rawstore import RawStore

FIX = Path(__file__).parent / "fixtures" / "notices"


def _read(name: str) -> bytes:
    return (FIX / name).read_bytes()


# -- SEBI RSS -----------------------------------------------------------------------


def test_sebi_rss_parses_all_thirty_items():
    df = n.parse_sebi_rss(_read("sebirss_20261114.xml"), source_file="sebi_rss/x.xml")
    assert len(df) == 30
    assert list(df.columns) == list(n.COLUMNS)
    assert df["notice_id"].str.match(r"sebi:\d+$").all()
    assert set(df["published_date"]) == {date(2026, 11, 11), date(2026, 11, 12), date(2026, 11, 13)}
    first = df.iloc[0]
    assert first["notice_id"] == "sebi:105772"  # ..._105772.html
    assert first["section"] == "enforcement/recovery-proceedings"
    assert first["title"].startswith("General Remittance Order dated November 13, 2026")
    assert (df["source_file"] == "sebi_rss/x.xml").all()


def test_sebi_rss_rejects_a_web_page_and_bad_dates():
    with pytest.raises(ValueError, match="web page"):
        n.parse_sebi_rss(b"<!DOCTYPE html><html></html>", source_file="f")
    bad = (
        b"<rss><channel><item><title>T</title><link>https://www.sebi.gov.in/a/b/c_1.html</link>"
        b"<pubDate>sometime</pubDate></item></channel></rss>"
    )
    with pytest.raises(ValueError, match="item 1: date"):
        n.parse_sebi_rss(bad, source_file="f")


# -- SEBI listing -------------------------------------------------------------------


def test_sebi_listing_single_row_is_the_aug_2026_etf_band_circular():
    df = n.parse_sebi_listing(_read("sebi_legal_20260825_20260905_p1.html"), source_file="f")
    assert len(df) == 1
    row = df.iloc[0]
    assert row["notice_id"] == "sebi:104094"
    assert row["published_date"] == date(2026, 8, 28)
    assert row["section"] == "legal/circulars"
    assert "price bands" in row["title"] and "Exchange Traded Funds (ETFs)" in row["title"]


def test_sebi_listing_pages_and_meta():
    p1 = _read("sebi_legal_20260101_20260630_p1.html")
    p2 = _read("sebi_legal_20260101_20260630_p2.html")
    assert n.listing_meta(p1) == (1, 25, 86, 4)
    assert n.listing_meta(p2) == (26, 50, 86, 4)
    a = n.parse_sebi_listing(p1, source_file="p1")
    b = n.parse_sebi_listing(p2, source_file="p2")
    assert len(a) == len(b) == 25
    assert not set(a["notice_id"]) & set(b["notice_id"])
    assert a.iloc[0]["published_date"] == date(2026, 6, 24)
    assert b.iloc[-1]["published_date"] == date(2026, 3, 4)


def test_sebi_listing_fails_loudly_when_rows_cannot_be_read():
    page = _read("sebi_legal_20260825_20260905_p1.html").replace(b"<td>Aug 28", b"<td >Aug 28")
    with pytest.raises(ValueError, match=r"[01] could be read"):
        n.parse_sebi_listing(page, source_file="f")
    with pytest.raises(ValueError, match="not a listing page"):
        n.parse_sebi_listing(b"<html>maintenance</html>", source_file="f")


def test_sebi_listing_empty_window():
    assert n.listing_meta(b"<p>No Records Found</p>") == (0, 0, 0, 1)
    assert n.parse_sebi_listing(b"<p>No Records Found</p>", source_file="f").empty


# -- NSE circulars -------------------------------------------------------------------


def test_nse_circulars_keeps_every_listing_row():
    df = n.parse_nse_circulars(_read("nse_circulars_20260901_20260901.json"), source_file="f")
    assert len(df) == 37  # duplicates per department are kept here; the cleaner merges
    assert df["notice_id"].nunique() == 32  # 76118, 76117, 76114, 76112, 76109 twice
    etf = df[df["ref_no"] == "NSE/CMTR/76101"].iloc[0]
    assert etf["notice_id"] == "nse:NSE/CMTR/76101"
    assert etf["published_date"] == date(2026, 9, 1)
    assert etf["title"] == "Trading in Exchange Traded Funds (ETFs) - update in trading parameters"
    assert etf["section"] == "Capital Market (Equities) Trade"
    assert etf["issuer"] == "NSE"
    assert etf["url"].endswith("/CMTR76101.pdf")


@pytest.mark.parametrize(
    ("body", "msg"),
    [
        (b"<html>denied</html>", "web page"),
        (b"not json", "not JSON"),
        (b'{"rows": []}', "no 'data' list"),
        (b'{"data": [{"circDisplayNo": "X/1", "sub": "s", "cirDate": "2026-09-01"}]}', "date"),
    ],
)
def test_nse_circulars_bad_bodies(body: bytes, msg: str):
    with pytest.raises(ValueError, match=msg):
        n.parse_nse_circulars(body, source_file="f")


# -- fetchers (fake server) ----------------------------------------------------------


class Server:
    def __init__(self, pages: list[bytes] | None = None, nse_status: int = 200) -> None:
        self.requests: list[httpx.Request] = []
        self.pages = pages or [_read("sebi_legal_20260825_20260905_p1.html")]
        self.nse_status = nse_status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        if "getnewslistinfo" in url or "getArchiveCircularlistinfo" in url:
            form = parse_qs(request.content.decode())
            return httpx.Response(200, content=self.pages[int(form["nextValue"][0])])
        if "/api/circulars" in url:
            if self.nse_status != 200:
                return httpx.Response(self.nse_status, content=b"no")
            return httpx.Response(200, content=_read("nse_circulars_20260901_20260901.json"))
        if "exchange-communication-circulars" in url:
            return httpx.Response(200, content=b"<html>page</html>")
        if "sebirss.xml" in url:
            return httpx.Response(200, content=_read("sebirss_20261114.xml"))
        return httpx.Response(404)


def _client(server: Server) -> PoliteClient:
    return PoliteClient(
        transport=httpx.MockTransport(server), sleep=lambda s: None, rng=lambda: 0.0, max_retries=0
    )


def test_fetch_sebi_listing_stores_every_page_untouched(tmp_path: Path):
    p1 = _read("sebi_legal_20260101_20260630_p1.html")
    p2 = _read("sebi_legal_20260101_20260630_p2.html")
    # page 1 says 4 pages; reuse p2 bytes for pages 3-4 (only the count matters here)
    server = Server(pages=[p1, p2, p2, p2])
    store = RawStore(tmp_path)
    with _client(server) as c:
        rec = n.fetch_sebi_listing(c, store, date(2026, 1, 1), date(2026, 6, 30))
    assert rec.relpath == "sebi_listing/2026-06-30/sebi_legal_20260101_20260630_p1.html"
    assert rec.path.read_bytes() == p1
    folder = tmp_path / "sebi_listing" / "2026-06-30"
    assert sorted(p.name for p in folder.iterdir()) == [
        f"sebi_legal_20260101_20260630_p{i}.html" for i in range(1, 5)
    ]
    forms = [parse_qs(r.content.decode()) for r in server.requests]
    assert [(f["next"][0], f["nextValue"][0]) for f in forms] == [
        ("s", "0"), ("n", "1"), ("n", "2"), ("n", "3"),
    ]  # fmt: skip
    assert forms[0]["fromDate"] == ["01-01-2026"] and forms[0]["toDate"] == ["30-06-2026"]


def test_fetch_sebi_listing_stores_nothing_if_a_page_is_not_a_listing(tmp_path: Path):
    p1 = _read("sebi_legal_20260101_20260630_p1.html")
    server = Server(pages=[p1, b"<html>error</html>"])
    with _client(server) as c, pytest.raises(SourceError, match="page 2"):
        n.fetch_sebi_listing(c, RawStore(tmp_path), date(2026, 1, 1), date(2026, 6, 30))
    assert not (tmp_path / "sebi_listing").exists()


def test_fetch_nse_circulars_warms_then_stores(tmp_path: Path):
    server = Server()
    with _client(server) as c:
        rec = n.fetch_nse_circulars(c, RawStore(tmp_path), date(2026, 9, 1), date(2026, 9, 1))
    assert rec.relpath == "nse_circulars/2026-09-01/nse_circulars_20260901_20260901.json"
    urls = [str(r.url) for r in server.requests]
    assert "exchange-communication-circulars" in urls[0]
    assert urls[1].endswith("/api/circulars?fromDate=01-09-2026&toDate=01-09-2026")


def test_fetch_nse_circulars_block_is_not_retried(tmp_path: Path):
    server = Server(nse_status=403)
    with _client(server) as c, pytest.raises(SourceBlockedError):
        n.fetch_nse_circulars(c, RawStore(tmp_path), date(2026, 9, 1), date(2026, 9, 1))
    assert len(server.requests) == 2  # page + one API call, no retry
    assert not (tmp_path / "nse_circulars").exists()


def test_fetch_sebi_rss(tmp_path: Path):
    with _client(Server()) as c:
        rec = n.fetch_sebi_rss(c, RawStore(tmp_path), date(2026, 11, 14))
    assert rec.relpath == "sebi_rss/2026-11-14/sebirss.xml"


def test_window_must_be_in_order(tmp_path: Path):
    with _client(Server()) as c, pytest.raises(ValueError, match="after its end"):
        n.fetch_nse_circulars(c, RawStore(tmp_path), date(2026, 9, 2), date(2026, 9, 1))


# -- SEBI archive (circulars moved out of the Active listing, D38) -----------------------


def test_sebi_archive_page_parses_with_the_listing_parser():
    df = n.parse_sebi_listing(
        _read("sebi_archive_20200101_20210331_p1.html"), source_file="sebi_archive/x.html"
    )
    assert len(df) == 25  # "1 to 25 of 41 records"
    assert df["notice_id"].str.match(r"sebi:\d+$").all()
    assert df["published_date"].min() >= date(2020, 1, 1)
    assert df["published_date"].max() <= date(2021, 3, 31)
    assert (df["section"] == "legal/circulars").all()


def test_fetch_sebi_archive_uses_the_archive_endpoint_and_circulars_section(tmp_path: Path):
    p1 = _read("sebi_archive_20200101_20210331_p1.html")
    server = Server(pages=[p1, p1])  # page 1 says 2 pages
    with _client(server) as c:
        rec = n.fetch_sebi_archive(c, RawStore(tmp_path), date(2020, 1, 1), date(2021, 3, 31))
    assert rec.relpath == "sebi_archive/2021-03-31/sebi_archive_20200101_20210331_p1.html"
    assert (tmp_path / "sebi_archive/2021-03-31/sebi_archive_20200101_20210331_p2.html").exists()
    assert all("getArchiveCircularlistinfo" in str(r.url) for r in server.requests)
    form = parse_qs(server.requests[0].content.decode())
    assert form["ssid"] == ["7"] and form["fromDate"] == ["01-01-2020"]


def test_nse_entry_without_display_number_falls_back_to_circular_number():
    # real shape of 12 entries in 2020-2025 (e.g. circular 46162, 27 Oct 2020)
    body = (
        b'{"data":[{"cirDate":"20201027","circCompany":"NCL","circDepartment":"NSE Clearing",'
        b'"circDisplayNo":"","circNumber":"46162","circFilelink":"https://x/null46162.null",'
        b'"sub":"CM-Multiple Settlements scheduled on November 03, 2020"}]}'
    )
    df = n.parse_nse_circulars(body, source_file="f")
    assert df.iloc[0]["notice_id"] == "nse:46162"
    assert df.iloc[0]["published_date"] == date(2020, 10, 27)


def test_nse_entry_without_any_number_gets_a_stable_hash_id_and_blank_subject_is_set_aside(caplog):
    body = (
        b'{"data":['
        b'{"cirDate":"20201027","circCompany":"NSE","circDepartment":"Listing","circDisplayNo":"",'
        b'"circNumber":"","sub":"Some notice without a number"},'
        b'{"cirDate":"20201027","circCompany":"NSE","circDepartment":"Listing","circDisplayNo":"",'
        b'"circNumber":"","sub":""}]}'
    )
    with caplog.at_level("WARNING"):
        a = n.parse_nse_circulars(body, source_file="nse_circulars/x.json")
        b = n.parse_nse_circulars(body, source_file="nse_circulars/y.json")
    assert len(a) == 1
    assert a.iloc[0]["notice_id"].startswith("nse:20201027:")
    assert a.iloc[0]["notice_id"] == b.iloc[0]["notice_id"]  # stable across files
    assert a.iloc[0]["ref_no"] is None or a["ref_no"].isna().all()
    assert "nse_circulars/x.json" in caplog.text and "1 entries" in caplog.text
