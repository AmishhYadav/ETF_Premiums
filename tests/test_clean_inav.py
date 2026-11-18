"""inav_snapshots: the recorder's raw files turned into one tidy table."""

from __future__ import annotations

import json
import logging
import math
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from etfp.clean import CleanError, inav_snapshots, make_context
from etfp.config import load_etfs
from test_clean_helpers import build_sample, fixture_bytes, make_raw_root, save_raw

DAY = date(2026, 11, 10)


@pytest.fixture(scope="module")
def snaps(tmp_path_factory: pytest.TempPathFactory) -> pd.DataFrame:
    _, store = build_sample(tmp_path_factory)
    return store.read_table("inav_snapshots")


def one(df: pd.DataFrame, symbol: str, source: str) -> pd.Series:
    sel = df[(df["symbol"] == symbol) & (df["source"] == source)]
    assert len(sel) == 1, f"{symbol}/{source}: {len(sel)} rows"
    return sel.iloc[0]


def build_snaps(root: Path) -> pd.DataFrame:
    return inav_snapshots.build(make_context(root))


# -- fund-house pages -----------------------------------------------------------


def test_motilal_inav_for_monq50(snaps: pd.DataFrame) -> None:
    r = one(snaps, "MONQ50", "amc_motilal")
    assert r["inav"] == pytest.approx(110.678132)
    assert r["prev_nav"] == pytest.approx(110.8852)
    assert r["amc_ts_raw"] == "11/10/2026 15:47:00"  # evidence only, never parsed (D15)
    assert r["source_file"] == "amc_inav_motilal/2026-11-10/inav_155000.json"
    assert one(snaps, "MON100", "amc_motilal")["inav"] == pytest.approx(247.179716)


def test_mirae_and_nippon_rows(snaps: pd.DataFrame) -> None:
    assert one(snaps, "MASPTOP50", "amc_mirae")["inav"] == pytest.approx(62.8733)
    assert one(snaps, "MAFANG", "amc_mirae")["inav"] == pytest.approx(155.0547)
    assert one(snaps, "HNGSNGBEES", "amc_nippon")["inav"] == pytest.approx(325.2129)
    assert one(snaps, "NIFTYBEES", "amc_nippon")["inav"] == pytest.approx(250.7151)


def test_timestamp_is_our_fetch_time_in_ist_not_the_fund_houses(snaps: pd.DataFrame) -> None:
    r = one(snaps, "MONQ50", "amc_motilal")
    assert r["ts"] == pd.Timestamp("2026-11-10 15:50:00+05:30")  # from file name inav_155000
    assert str(snaps["ts"].dt.tz) == "Asia/Kolkata"
    assert pd.isna(r["data_ts"])  # the AMC gives no trustworthy data time


def test_columns_a_source_does_not_give_are_empty_not_zero(snaps: pd.DataFrame) -> None:
    r = one(snaps, "MONQ50", "amc_motilal")
    for col in ("last_price", "bid", "ask", "value"):
        assert math.isnan(r[col]), col
    for col in ("bid_qty", "ask_qty", "total_buy_qty", "total_sell_qty", "volume", "depth_json"):
        assert pd.isna(r[col]), col


# -- NSE ETF table ----------------------------------------------------------------


def test_nse_etf_api_row(snaps: pd.DataFrame) -> None:
    r = one(snaps, "MONQ50", "nse_etf_api")
    assert r["last_price"] == 231.76 and r["volume"] == 274_573
    assert r["value"] == pytest.approx(63_626_801.29)
    assert r["prev_nav"] == pytest.approx(110.8852)  # the PREVIOUS published NAV, not a live iNAV
    assert math.isnan(r["inav"])
    assert r["market_status"] == "Close"
    assert r["ts"] == pd.Timestamp("2026-11-10 21:20:00+05:30")
    # NSE's own data time is older than our fetch: that snapshot is stale and says so.
    assert r["data_ts"] == pd.Timestamp("2026-11-09 16:00:00+05:30")


def test_only_configured_etfs_are_kept(snaps: pd.DataFrame) -> None:
    assert set(snaps["symbol"]) <= set(load_etfs().symbols())
    assert not snaps.duplicated(["symbol", "ts", "source"]).any()
    assert snaps["source_file"].notna().all()


def test_duplicate_entry_in_one_nse_answer_keeps_the_real_one(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    root = make_raw_root(tmp_path, skip_sources=("nse_etf_api",))
    body = json.loads(fixture_bytes("nse_etf_api/api_etf.json"))
    real = next(x for x in body["data"] if x["symbol"] == "MONQ50")
    empty = {**real, "ltP": "0", "qty": "0", "trdVal": "0"}
    body["data"] = [empty, *body["data"]]  # NSE has been seen listing a symbol twice
    save_raw(root, "nse_etf_api", DAY, "etf_212000.json", json.dumps(body).encode())
    with caplog.at_level(logging.WARNING, logger="etfp.clean"):
        df = build_snaps(root)
    r = one(df, "MONQ50", "nse_etf_api")
    assert r["last_price"] == 231.76 and r["volume"] == 274_573
    assert "duplicate" in caplog.text


# -- NSE quote with order book (shape unverified; synthetic sample) ------------------

QUOTE = {
    "info": {"symbol": "MONQ50", "companyName": "Motilal Oswal Nasdaq Q 50 ETF"},
    "metadata": {"lastUpdateTime": "12-Nov-2026 11:30:15"},
    "priceInfo": {"lastPrice": 240.5, "iNavValue": 112.25},
    "marketDeptOrderBook": {
        "totalBuyQuantity": 1200,
        "totalSellQuantity": 34500,
        "bid": [
            {"price": 240.0, "quantity": 100},
            {"price": 239.5, "quantity": 300},
            {"price": 0, "quantity": 0},
        ],
        "ask": [{"price": 241.0, "quantity": 5000}, {"price": 241.5, "quantity": 7000}],
    },
}


def add_quote(root: Path, body: object, name: str = "quote_MONQ50_113020.json") -> None:
    save_raw(root, "nse_quote", date(2026, 11, 12), name, json.dumps(body).encode())


def test_quote_keeps_total_quantities_and_every_depth_level(tmp_path: Path) -> None:
    root = make_raw_root(tmp_path)
    add_quote(root, QUOTE)
    r = one(build_snaps(root), "MONQ50", "nse_quote")
    assert r["total_buy_qty"] == 1200 and r["total_sell_qty"] == 34500
    assert (r["bid"], r["bid_qty"], r["ask"], r["ask_qty"]) == (240.0, 100, 241.0, 5000)
    assert json.loads(r["depth_json"]) == {
        "bid": [[240.0, 100], [239.5, 300]],  # the empty padding level (price 0) is dropped
        "ask": [[241.0, 5000], [241.5, 7000]],
    }
    assert r["last_price"] == 240.5 and r["inav"] == 112.25
    assert r["ts"] == pd.Timestamp("2026-11-12 11:30:20+05:30")
    assert r["data_ts"] == pd.Timestamp("2026-11-12 11:30:15+05:30")
    assert r["source_file"] == "nse_quote/2026-11-12/quote_MONQ50_113020.json"


def test_quote_with_an_empty_book_keeps_the_row_without_inventing_numbers(
    tmp_path: Path,
) -> None:
    root = make_raw_root(tmp_path)
    body = json.loads(json.dumps(QUOTE))
    body["marketDeptOrderBook"].update(bid=[], ask=[], totalBuyQuantity=0, totalSellQuantity=0)
    add_quote(root, body)
    r = one(build_snaps(root), "MONQ50", "nse_quote")
    assert r["total_buy_qty"] == 0 and r["total_sell_qty"] == 0  # a real zero: the book is empty
    assert math.isnan(r["bid"]) and math.isnan(r["ask"]) and pd.isna(r["bid_qty"])
    assert json.loads(r["depth_json"]) == {"bid": [], "ask": []}


def test_quote_in_an_unexpected_shape_stops_the_build_naming_the_file(tmp_path: Path) -> None:
    root = make_raw_root(tmp_path)
    add_quote(root, {"info": {"symbol": "MONQ50"}, "somethingNew": {}})
    with pytest.raises(CleanError) as err:
        build_snaps(root)
    assert "nse_quote/2026-11-12/quote_MONQ50_113020.json" in str(err.value)
    assert "marketDeptOrderBook" in str(err.value)


def test_file_name_without_a_time_is_refused(tmp_path: Path) -> None:
    root = make_raw_root(tmp_path)
    add_quote(root, QUOTE, name="quote_MONQ50.json")
    with pytest.raises(CleanError, match="fetch time"):
        build_snaps(root)


def test_not_json_amc_file_stops_the_build_naming_the_file(tmp_path: Path) -> None:
    root = make_raw_root(tmp_path, skip_sources=("amc_inav_mirae",))
    save_raw(root, "amc_inav_mirae", DAY, "inav_160000.json", b"<html>blocked</html>")
    with pytest.raises(CleanError) as err:
        build_snaps(root)
    assert "amc_inav_mirae/2026-11-10/inav_160000.json" in str(err.value)


def test_no_recorder_files_gives_an_empty_table(tmp_path: Path) -> None:
    df = build_snaps(tmp_path)
    assert df.empty and list(df.columns) == inav_snapshots.COLUMNS


# -- the collector's two-file layout: price block + order book (nse_quote) ----------------

PRICE_BLOCK = {
    "info": {"symbol": "MONQ50"},
    "metadata": {"lastUpdateTime": "12-Nov-2026 11:30:15"},
    "priceInfo": {"lastPrice": 240.5, "iNavValue": 112.25},
}
ORDER_BOOK = {"marketDeptOrderBook": QUOTE["marketDeptOrderBook"]}


def add_pair(root: Path, *, price: object | None = PRICE_BLOCK, book: object | None = ORDER_BOOK):
    if price is not None:
        add_quote(root, price, "MONQ50_113020.json")
    if book is not None:
        add_quote(root, book, "MONQ50_trade_info_113020.json")


def test_two_files_of_one_page_visit_become_one_row_with_both_in_the_lineage(
    tmp_path: Path,
) -> None:
    root = make_raw_root(tmp_path)
    add_pair(root)
    df = build_snaps(root)
    r = one(df, "MONQ50", "nse_quote")
    assert r["last_price"] == 240.5 and r["inav"] == 112.25
    assert r["total_buy_qty"] == 1200 and r["total_sell_qty"] == 34500
    assert (r["bid"], r["ask"]) == (240.0, 241.0)
    assert r["source_file"] == (
        "nse_quote/2026-11-12/MONQ50_113020.json;nse_quote/2026-11-12/MONQ50_trade_info_113020.json"
    )
    assert (df["source"] == "nse_quote").sum() == 1  # not two rows


def test_price_block_without_order_book_keeps_price_and_inav_with_empty_depth(
    tmp_path: Path,
) -> None:
    root = make_raw_root(tmp_path)
    add_pair(root, book=None)
    r = one(build_snaps(root), "MONQ50", "nse_quote")
    assert r["inav"] == 112.25
    assert pd.isna(r["total_buy_qty"]) and pd.isna(r["depth_json"]) and math.isnan(r["bid"])


def test_order_book_without_price_block_is_a_book_only_row(tmp_path: Path) -> None:
    root = make_raw_root(tmp_path)
    add_pair(root, price=None)
    r = one(build_snaps(root), "MONQ50", "nse_quote")
    assert r["total_sell_qty"] == 34500 and r["ask_qty"] == 5000
    assert math.isnan(r["last_price"]) and math.isnan(r["inav"])


def test_pairs_of_different_times_are_not_mixed(tmp_path: Path) -> None:
    root = make_raw_root(tmp_path)
    add_pair(root)
    early = {"marketDeptOrderBook": {**ORDER_BOOK["marketDeptOrderBook"], "totalBuyQuantity": 7}}
    add_quote(root, early, "MONQ50_trade_info_111500.json")  # a book with no price block
    df = build_snaps(root)
    q = df[df["source"] == "nse_quote"].set_index("ts")
    assert len(q) == 2
    assert q.loc[pd.Timestamp("2026-11-12 11:30:20+05:30"), "total_buy_qty"] == 1200
    assert q.loc[pd.Timestamp("2026-11-12 11:15:00+05:30"), "total_buy_qty"] == 7


def test_an_untracked_symbol_in_the_pair_is_dropped(tmp_path: Path) -> None:
    root = make_raw_root(tmp_path)
    add_pair(root, price={**PRICE_BLOCK, "info": {"symbol": "NOTMINE"}})
    assert (build_snaps(root)["source"] == "nse_quote").sum() == 0


def test_price_block_in_an_unknown_shape_still_stops_the_build(tmp_path: Path) -> None:
    root = make_raw_root(tmp_path)
    add_pair(root, price={"info": {"symbol": "MONQ50"}, "somethingNew": {}}, book=None)
    with pytest.raises(CleanError, match=r"MONQ50_113020\.json"):
        build_snaps(root)


# -- inav_stale flag (D49) ----------------------------------------------------------


def _stale_frame(rows: list[tuple[str, str, str, float | None]]) -> pd.DataFrame:
    """Minimal frame of (source, symbol, 'YYYY-MM-DD HH:MM' IST, inav) rows."""
    return pd.DataFrame(
        {
            "source": [r[0] for r in rows],
            "symbol": [r[1] for r in rows],
            "ts": pd.to_datetime([r[2] for r in rows]).tz_localize("Asia/Kolkata"),
            "inav": [r[3] for r in rows],
        }
    )


def test_inav_stale_flags_the_fourth_identical_value_onwards() -> None:
    values = [100.0, 100.0, 100.0, 100.0, 100.0, 101.0, 101.0]
    rows = [
        ("amc_x", "A", f"2026-11-16 {10 + i // 4}:{15 * (i % 4):02d}", v)
        for i, v in enumerate(values)
    ]
    out = inav_snapshots._flag_stale_inav(_stale_frame(rows))
    # runs: 100 x5 (4th and 5th flagged), 101 x2 (none flagged)
    assert list(out["inav_stale"]) == [False, False, False, True, True, False, False]


def test_inav_stale_is_per_source_symbol_and_day_and_ignores_off_hours_and_missing() -> None:
    rows = [("amc_x", "A", f"2026-11-16 10:{m:02d}", 5.0) for m in (0, 15, 30, 45)]  # stale at 4th
    rows += [("amc_x", "B", f"2026-11-16 10:{m:02d}", 5.0) for m in (0, 15, 30)]  # only 3
    rows += [("amc_x", "A", "2026-11-17 09:15", 5.0)]  # same value next day: a new run
    rows += [("amc_x", "C", f"2026-11-16 {h}:00", 7.0) for h in (7, 8, 16, 17, 18)]  # off-hours
    rows += [("nse_etf_api", "A", "2026-11-16 10:00", None)]  # price-only row: no iNAV
    out = inav_snapshots._flag_stale_inav(_stale_frame(rows))
    flags = list(out["inav_stale"])
    assert flags[:4] == [False, False, False, True]
    assert flags[4:7] == [False, False, False]
    assert not flags[7]
    assert flags[8:13] == [False] * 5
    assert pd.isna(flags[13])  # missing iNAV -> missing flag, never False


def test_inav_stale_in_the_built_table(snaps: pd.DataFrame) -> None:
    assert "inav_stale" in snaps.columns
    assert str(snaps["inav_stale"].dtype) == "boolean"
    # one sample snapshot per source: nothing can be a run of four
    assert not snaps["inav_stale"].fillna(False).any()
    assert snaps.loc[snaps["inav"].isna(), "inav_stale"].isna().all()
