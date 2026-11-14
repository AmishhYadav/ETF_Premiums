"""inav_snapshots: every intraday snapshot the recorder took, one row per source per tick.

iNAV ("indicative NAV") is the fund house's running estimate, during the trading
day, of what one unit's holdings are worth. Nobody publishes an archive of it, so
these snapshots (taken every 15 minutes by ``etfp.recorder``) are unique data.

Sources (the ``source`` column) and what each one fills:

=============  ==========================================================
nse_etf_api    NSE ETF table: last price, volume, value, previous NAV
amc_motilal    Motilal Oswal's own page: iNAV, previous NAV
amc_mirae      Mirae Asset's own page: iNAV, previous NAV
amc_nippon     Nippon India's own page: iNAV, previous NAV (values doubtful, D15)
nse_quote      NSE single-ETF quote: last price, iNAV, order book (see below)
=============  ==========================================================

Columns a source does not give stay empty; they are never zero.

Time (important): ``ts`` is when WE fetched the answer, in IST, taken from the raw
file name (``etf_HHMMSS.json`` / ``inav_HHMMSS.json``) and its date folder. The
fund houses' own time stamps look like server clocks (DECISIONS D15), so they are
kept only as text in ``amc_ts_raw``. NSE's own data time goes in ``data_ts``; on a
holiday or after the close it is older than ``ts`` (that snapshot is stale).

Order book (``nse_quote``): CLAUDE.md requires keeping total buy/sell quantity
and the depth from the raw quote JSON. This table does: ``total_buy_qty`` and
``total_sell_qty``, the best bid/ask, and ``depth_json`` with every level in the
book. The collector (``etfp.collect.nse_quote``, a real browser session, DECISIONS
D12) saves the two answers NSE's quote page receives as two raw files with the same
time: ``SYM_HHMMSS.json`` (price block: last price, iNAV) and
``SYM_trade_info_HHMMSS.json`` (order book). They are paired here and become ONE row;
its ``source_file`` lists both files separated by ``;``. The reader has only been
tested on hand-made samples shaped like NSE's public quote API: its field names are
UNVERIFIED until the first real capture. If a file does not look as expected the
build stops and names the file; nothing is guessed.

Only ETFs listed in config/etfs.yaml are kept (the raw files hold hundreds more).
"""

from __future__ import annotations

import json
import logging
import math
import re
from datetime import date, datetime, time
from typing import Any

import pandas as pd

from etfp.clean.common import CleanContext, CleanError, RawFile, list_raw_files, parse_file
from etfp.collect import amc_inav, nse_etf_api
from etfp.timeutil import IST

logger = logging.getLogger("etfp.clean")

TABLE = "inav_snapshots"
NSE_QUOTE_SOURCE = "nse_quote"  # raw-store folder expected for the future quote collector

COLUMNS = [
    "symbol", "ts", "source", "last_price", "inav", "prev_nav", "bid", "ask", "bid_qty",
    "ask_qty", "total_buy_qty", "total_sell_qty", "depth_json", "volume", "value", "data_ts",
    "amc_ts_raw", "market_status", "source_file",
]  # fmt: skip
_FLOATS = ["last_price", "inav", "prev_nav", "bid", "ask", "value"]
_INTS = ["bid_qty", "ask_qty", "total_buy_qty", "total_sell_qty", "volume"]
_STRINGS = ["depth_json", "amc_ts_raw", "market_status"]

_FETCH_TIME = re.compile(r"(\d{2})(\d{2})(\d{2})\.json\Z")
_QUOTE_FILE = re.compile(r"(?P<prefix>.+?)(?P<kind>_trade_info)?_(?P<hms>\d{6})\.json\Z")


def build(ctx: CleanContext) -> pd.DataFrame:
    """Build the inav_snapshots table from every recorder file in the raw store."""
    symbols = ctx.config_symbols()
    frames: list[pd.DataFrame] = []

    for rf in list_raw_files(ctx.raw_root, nse_etf_api.SOURCE):
        ts = _fetch_time(rf)
        frames.append(_nse_etf_api(rf, ts, symbols))
    for amc in amc_inav.AMCS:
        for rf in list_raw_files(ctx.raw_root, amc_inav.source_name(amc)):
            ts = _fetch_time(rf)
            frames.append(_amc(rf, amc, ts, symbols))
    for rf, companion, hint in _pair_quote_files(list_raw_files(ctx.raw_root, NSE_QUOTE_SOURCE)):
        ts = _fetch_time(rf)
        frames.append(_nse_quote(rf, ts, symbols, companion, hint))

    frames = [f for f in frames if not f.empty]
    if not frames:
        return pd.DataFrame({c: [] for c in COLUMNS})
    out = pd.concat(frames, ignore_index=True)
    return _one_row_per_key(out)[COLUMNS]


def _fetch_time(rf: RawFile) -> datetime:
    """When we fetched the file (IST) from its date folder and its HHMMSS name."""
    match = _FETCH_TIME.search(rf.base_name)
    if match is None:
        raise CleanError(f"{TABLE}: cannot read a fetch time (HHMMSS) from file name {rf.relpath}")
    hh, mm, ss = (int(g) for g in match.groups())
    try:
        return datetime.combine(rf.day, time(hh, mm, ss), tzinfo=IST)
    except ValueError as exc:
        raise CleanError(f"{TABLE}: {rf.relpath} has an impossible time in its name") from exc


def _stamps(values: list[Any]) -> pd.Series:
    """Timezone-aware IST timestamps from aware datetimes (or missing values)."""
    index = pd.DatetimeIndex(pd.to_datetime(values, utc=True))
    return pd.Series(index.tz_convert(IST)).dt.as_unit("us")


def _frame(
    ts: datetime, source: str, source_file: str, symbols: list[Any], **given: list[Any]
) -> pd.DataFrame:
    """One block of rows in the final layout. Columns not given are missing (never zero).

    Each value in ``given`` is a list (or Series) with one entry per symbol.
    """
    n = len(symbols)
    cols: dict[str, pd.Series] = {}
    for name in COLUMNS:
        values = list(given[name]) if name in given else [None] * n
        if name in _FLOATS:
            cols[name] = pd.Series(pd.to_numeric(pd.Series(values, dtype=object)), dtype="float64")
        elif name in _INTS:
            cols[name] = pd.Series(pd.array(values, dtype="Int64"))
        elif name in _STRINGS:
            cols[name] = pd.Series(pd.array(values, dtype="string"))
        elif name == "data_ts":
            cols[name] = _stamps(values)
    cols["symbol"] = pd.Series(pd.array(list(symbols), dtype="string"))
    cols["ts"] = _stamps([ts] * n)
    cols["source"] = pd.Series(pd.array([source] * n, dtype="string"))
    cols["source_file"] = pd.Series(pd.array([source_file] * n, dtype="string"))
    return pd.DataFrame(cols)[COLUMNS]


# -- NSE ETF table ------------------------------------------------------------


def _nse_etf_api(rf: RawFile, ts: datetime, symbols: set[str]) -> pd.DataFrame:
    df = parse_file(
        TABLE, rf, lambda b: nse_etf_api.parse(b, source_file=rf.relpath, fetched_at=ts)
    )
    df = df[df["symbol"].isin(symbols)]
    return _frame(
        ts, "nse_etf_api", rf.relpath, list(df["symbol"]),
        last_price=df["last_price"], prev_nav=df["nav_prev"],
        volume=df["volume"], value=df["value"], data_ts=df["api_ts"],
        market_status=df["market_status"],
    )  # fmt: skip


# -- fund-house pages ---------------------------------------------------------


def _amc(rf: RawFile, amc: str, ts: datetime, symbols: set[str]) -> pd.DataFrame:
    df = parse_file(
        TABLE, rf, lambda b: amc_inav.parse(b, amc=amc, source_file=rf.relpath, fetched_at=ts)
    )
    df = df[df["symbol"].isin(symbols)]  # rows without an NSE symbol (Nippon) drop out here
    return _frame(
        ts, f"amc_{amc}", rf.relpath, list(df["symbol"]),
        inav=df["inav"], prev_nav=df["prev_nav"], amc_ts_raw=df["amc_ts_raw"],
    )  # fmt: skip


# -- NSE single-ETF quote (shape UNVERIFIED, see module docstring) -----------------

_INAV_KEYS = ("iNavValue", "iNAVValue", "inav", "iNav", "iNAV", "indicativeNav")
_QUOTE_STAMP = re.compile(r"(\d{1,2})-([A-Za-z]{3})-(\d{4}) (\d{2}):(\d{2}):(\d{2})\Z")
_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]


def _number(value: Any) -> float:
    if value is None or isinstance(value, bool):
        return math.nan
    try:
        number = float(str(value).replace(",", "").strip())
    except ValueError:
        return math.nan
    return number if math.isfinite(number) else math.nan


def _whole(value: Any) -> int | None:
    number = _number(value)
    return None if math.isnan(number) else round(number)


def _levels(book: Any, side: str, rf: RawFile) -> list[tuple[float, int | None]]:
    """The price levels of one side of the book as (price, quantity), best first."""
    raw = book.get(side)
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise CleanError(f'{TABLE}: {rf.relpath}: marketDeptOrderBook["{side}"] is not a list')
    levels = []
    for item in raw:
        if not isinstance(item, dict):
            raise CleanError(f"{TABLE}: {rf.relpath}: order-book level {item!r} is not an object")
        price = _number(item.get("price"))
        if not math.isnan(price) and price > 0:  # NSE pads empty levels with price 0
            levels.append((price, _whole(item.get("quantity"))))
    return levels


def _pair_quote_files(
    files: list[RawFile],
) -> list[tuple[RawFile, RawFile | None, str | None]]:
    """Pair each price-block file with its order-book file (same day, symbol and time).

    Returns (main, companion, symbol_hint). A main file without companion is fine (the
    order book request failed that tick). An order-book file without a price block (that
    request failed) comes back as (book file, None, symbol taken from the file name) and
    is read as a book-only row.
    """
    mains: dict[tuple[date, str, str], RawFile] = {}
    books: dict[tuple[date, str, str], RawFile] = {}
    others: list[RawFile] = []
    for rf in files:
        m = _QUOTE_FILE.fullmatch(rf.base_name)
        if m is None:
            others.append(rf)  # unusual name: read alone; _fetch_time explains if unusable
            continue
        key = (rf.day, m["prefix"], m["hms"])
        (books if m["kind"] else mains)[key] = rf
    pairs: list[tuple[RawFile, RawFile | None, str | None]] = [(rf, None, None) for rf in others]
    pairs += [(rf, books.pop(key, None), None) for key, rf in mains.items()]
    pairs += [(rf, None, key[1]) for key, rf in books.items()]  # books with no price block
    return sorted(pairs, key=lambda p: p[0].relpath)


def _load_quote(content: bytes, rf: RawFile) -> dict[str, Any]:
    try:
        body = json.loads(content)
    except ValueError as exc:
        raise CleanError(
            f"{TABLE}: {rf.relpath} is not JSON (starts with {content[:30]!r})"
        ) from exc
    if not isinstance(body, dict):
        raise CleanError(f"{TABLE}: {rf.relpath} is not a JSON object; has {type(body).__name__}")
    return body


def parse_nse_quote(
    content: bytes,
    rf: RawFile,
    companion: tuple[bytes, RawFile] | None = None,
    symbol_hint: str | None = None,
) -> dict[str, Any]:
    """Read one saved NSE quote (JSON). Returns the fields of one inav_snapshots row.

    Expected shape (UNVERIFIED against a live capture)::

        price block   {"info": {"symbol": "MONQ50"},
                       "metadata": {"lastUpdateTime": "09-Nov-2026 15:30:00"},
                       "priceInfo": {"lastPrice": 231.76, "iNavValue": 114.38}}
        order book    {"marketDeptOrderBook": {"totalBuyQuantity": 1000,
                         "totalSellQuantity": 5000,
                         "bid": [{"price": 229.0, "quantity": 100}, ...],
                         "ask": [{"price": 230.0, "quantity": 500}, ...]}}

    The order book is either inside the same file (older hand-made samples) or in
    ``companion`` (what the collector saves). ``symbol_hint`` (the symbol in the file
    name) is only used when the file has no ``info`` of its own, i.e. an order book whose
    price block was never saved. A price block without any order book
    gives a row with empty depth columns (never zeros). A file with neither a price
    block nor an order book is an error: the format may have changed.
    """
    body = _load_quote(content, rf)
    book_body, book_rf = body, rf
    if companion is not None:
        book_body, book_rf = _load_quote(companion[0], companion[1]), companion[1]
    book = book_body.get("marketDeptOrderBook")
    price_info = body.get("priceInfo") if isinstance(body.get("priceInfo"), dict) else None
    info = body.get("info")
    symbol = info.get("symbol") if isinstance(info, dict) else None
    if symbol is None and symbol_hint:
        symbol = symbol_hint
    if not isinstance(symbol, str) or not symbol or (book is None and price_info is None):
        raise CleanError(
            f'{TABLE}: {rf.relpath} lacks info.symbol or both "priceInfo" and '
            f'"marketDeptOrderBook"; has keys {sorted(body)}. Has the NSE quote format changed?'
        )
    if book is not None and not isinstance(book, dict):
        raise CleanError(f'{TABLE}: {book_rf.relpath}: "marketDeptOrderBook" is not an object')
    price_info = price_info or {}
    inav = next((price_info[k] for k in _INAV_KEYS if k in price_info), None)

    row: dict[str, Any] = {
        "symbol": symbol.strip(),
        "last_price": _number(price_info.get("lastPrice")),
        "inav": _number(inav),
        "bid": math.nan,
        "ask": math.nan,
        "bid_qty": None,
        "ask_qty": None,
        "total_buy_qty": None,
        "total_sell_qty": None,
        "depth_json": None,
        "data_ts": _quote_time(body.get("metadata")),
        "market_status": None,
    }
    if book is not None:
        row.update(_book_fields(book, book_rf))
    return row


def _book_fields(book: dict[str, Any], rf: RawFile) -> dict[str, Any]:
    bids = _levels(book, "bid", rf)
    asks = _levels(book, "ask", rf)
    depth = {"bid": [[p, q] for p, q in bids], "ask": [[p, q] for p, q in asks]}
    return {
        "bid": bids[0][0] if bids else math.nan,
        "ask": asks[0][0] if asks else math.nan,
        "bid_qty": bids[0][1] if bids else None,
        "ask_qty": asks[0][1] if asks else None,
        "total_buy_qty": _whole(book.get("totalBuyQuantity")),
        "total_sell_qty": _whole(book.get("totalSellQuantity")),
        "depth_json": json.dumps(depth, separators=(",", ":")),
    }


def _quote_time(metadata: Any) -> datetime | None:
    text = metadata.get("lastUpdateTime") if isinstance(metadata, dict) else None
    match = _QUOTE_STAMP.match(text.strip()) if isinstance(text, str) else None
    if match is None or match.group(2).lower() not in _MONTHS:
        return None
    day = date(int(match.group(3)), _MONTHS.index(match.group(2).lower()) + 1, int(match.group(1)))
    return datetime.combine(day, time(*(int(match.group(i)) for i in (4, 5, 6))), tzinfo=IST)


def _nse_quote(
    rf: RawFile,
    ts: datetime,
    symbols: set[str],
    companion: RawFile | None = None,
    symbol_hint: str | None = None,
) -> pd.DataFrame:
    pair = (companion.path.read_bytes(), companion) if companion is not None else None
    row = parse_file(TABLE, rf, lambda b: parse_nse_quote(b, rf, pair, symbol_hint))
    source_file = rf.relpath if companion is None else f"{rf.relpath};{companion.relpath}"
    if row["symbol"] not in symbols:
        return _frame(ts, "nse_quote", source_file, [])
    given = {k: [v] for k, v in row.items() if k != "symbol"}
    return _frame(ts, "nse_quote", source_file, [row["symbol"]], **given)


# -- duplicates -----------------------------------------------------------------


def _one_row_per_key(df: pd.DataFrame) -> pd.DataFrame:
    """Keep one row per (symbol, ts, source).

    NSE's ETF table is known to list a symbol twice in one answer (one entry
    all zeros, DECISIONS: nse_etf_api). We keep the entry with the larger volume
    (then the earlier one) and log it.
    """
    key = ["symbol", "ts", "source"]
    dup = df.duplicated(key, keep=False)
    if not dup.any():
        return df
    ranked = df.assign(_vol=pd.to_numeric(df["volume"]).fillna(-1)).sort_values(
        [*key, "_vol"], ascending=[True, True, True, False], kind="stable"
    )
    keep = ~ranked.duplicated(key, keep="first")
    first = ranked[dup.reindex(ranked.index)].iloc[0]
    logger.warning(
        "%s: %d duplicate (symbol, ts, source) row(s) dropped, e.g. %s at %s from %s",
        TABLE,
        int((~keep).sum()),
        first["symbol"],
        first["ts"],
        first["source_file"],
    )
    return ranked[keep].drop(columns="_vol").reset_index(drop=True)
