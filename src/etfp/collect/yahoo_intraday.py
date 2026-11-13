"""Yahoo Finance intraday bars for the INTERIM free fair value (docs/DECISIONS.md D13).

Why this exists (plain words)
-----------------------------
An India-listed international ETF (say the Nasdaq-100 one) is quoted on the NSE until
15:30 India time, but the US market it holds closes ~10 hours later. The ETF's published
NAV therefore describes the US close, not the Indian close. To estimate what the holdings
are worth *at 15:30 India time* ("fair value", SPEC 6.2) we move the last known NAV by how
much the US market has moved since its close, using two things we can observe at 15:30 IST:

* an index future (NQ=F is the Nasdaq-100 future, ES=F the S&P 500 future) that trades
  almost around the clock, so it shows where the US market is "now";
* the exchange rate (USDINR=X, HKDINR=X), because the fund's rupee value moves with the
  dollar (or Hong Kong dollar).

Bloomberg gives clean 15:30 IST values but is not available yet, so as an interim we use
Yahoo's free 5-minute chart data and label the result ``fv_method = 'free_futures'``.

Limits you must know
--------------------
* Yahoo is an unofficial, undocumented endpoint: it can change or block us (it answered
  HTTP 429 to our first test request on 2026-11-13). Failures are loud, never skipped.
* Yahoo keeps only about 60 days of 5-minute history (30 days for 1-minute). A day older
  than that CANNOT be fetched later: every day not recorded is lost for the free method.
* Futures prices are for the *continuous front contract*; a contract roll can show as a
  jump. Index futures are a proxy for the fund's real index (SPEC 6.2, D13).

Bar timestamps: Yahoo gives the time a bar STARTS. A "5m" bar stamped 15:25 covers
15:25:00-15:29:59 and its ``close`` is the last price seen at ~15:30:00. See `value_at`.

Design: `fetch()` stores the untouched JSON; `parse()` reads bytes and never touches the
network.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

import pandas as pd

from etfp import calendars
from etfp.http import PoliteClient, SourceError
from etfp.rawstore import RawRecord, RawStore
from etfp.timeutil import now_ist, require_aware

SOURCE = "yahoo_intraday"

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
YAHOO_HEADERS = {"Accept": "application/json"}

# Index futures and FX pairs needed for the free fair value.
FUTURES: tuple[str, ...] = ("NQ=F", "ES=F")
FX: tuple[str, ...] = ("USDINR=X", "HKDINR=X")
SYMBOLS: tuple[str, ...] = FUTURES + FX

DEFAULT_INTERVAL = "5m"
LOOKBACK_DAYS = 5  # a day's file starts this many days before the day (covers long weekends)
LOOKAHEAD_DAYS = 2  # ...and runs to the end of the next day (Yahoo clamps this to "now")

# How far back Yahoo serves each bar size, minus a safety margin of one day.
MAX_HISTORY_DAYS: dict[str, int] = {"1m": 29, "2m": 59, "5m": 59, "15m": 59, "30m": 59}

OUTPUT_COLUMNS: tuple[str, ...] = (
    "symbol",
    "ts",
    "interval_minutes",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "source_file",
)

_INTERVAL = re.compile(r"(\d+)m\Z")


class IntradayHistoryUnavailable(SourceError):
    """The requested day is older than Yahoo's intraday history. It cannot be recovered."""


# -- names and URLs -----------------------------------------------------------------


def safe_symbol(symbol: str) -> str:
    """Symbol as used in file names: 'NQ=F' -> 'NQ_F' (letters and digits only)."""
    return re.sub(r"[^A-Za-z0-9]+", "_", symbol).strip("_")


def raw_name(symbol: str, interval: str = DEFAULT_INTERVAL) -> str:
    """Raw file name for one symbol and bar size under the day's folder."""
    return f"{safe_symbol(symbol)}_{interval}.json"


def raw_names(day: date, interval: str = DEFAULT_INTERVAL) -> list[str]:
    """All raw files `collect daily` expects for a day (one per symbol)."""
    del day
    return [raw_name(symbol, interval) for symbol in SYMBOLS]


def url_for(symbol: str) -> str:
    return CHART_URL.format(symbol=symbol)


def window(day: date) -> tuple[datetime, datetime]:
    """UTC start/end of the data requested for `day`.

    Wide on purpose: the fair value for Monday needs the US close of the previous Friday,
    or Thursday if Friday was a US holiday, so we ask for 5 days back. The end is
    two days ahead so a run on the following morning also catches the US close of `day`.
    """
    start = datetime.combine(day - timedelta(days=LOOKBACK_DAYS), time.min, tzinfo=UTC)
    end = datetime.combine(day + timedelta(days=LOOKAHEAD_DAYS), time.min, tzinfo=UTC)
    return start, end


def _interval_minutes(interval: str) -> int:
    match = _INTERVAL.match(interval)
    if match is None or int(match.group(1)) == 0:
        raise ValueError(f"{SOURCE}: interval {interval!r} is not like '5m'")
    return int(match.group(1))


# -- fetching -----------------------------------------------------------------------


def fetch(
    client: PoliteClient,
    store: RawStore,
    day: date,
    symbol: str,
    *,
    interval: str = DEFAULT_INTERVAL,
    now: datetime | None = None,
) -> RawRecord:
    """Download bars around `day` for one symbol and store the untouched JSON.

    Refuses (before any request) when the window starts further back than Yahoo keeps
    intraday data. A body that is not a chart result (for example Yahoo's JSON error with
    HTTP 200) is never stored.
    """
    _interval_minutes(interval)
    now = require_aware(now, "now") if now is not None else now_ist()
    start, end = window(day)
    limit = MAX_HISTORY_DAYS.get(interval)
    if limit is not None and start < now.astimezone(UTC) - timedelta(days=limit):
        raise IntradayHistoryUnavailable(
            f"{SOURCE} for {day.isoformat()}: Yahoo keeps only ~{limit + 1} days of "
            f"{interval} bars; {symbol} bars for this day can no longer be fetched",
            source=SOURCE,
            day=day,
            url=url_for(symbol),
        )
    params = {
        "interval": interval,
        "period1": str(int(start.timestamp())),
        "period2": str(int(end.timestamp())),
        "includePrePost": "false",
    }
    result = client.get(
        url_for(symbol), source=SOURCE, day=day, headers=YAHOO_HEADERS, params=params
    )
    problem = _body_problem(result.content)
    if problem is not None:
        raise SourceError(
            f"{SOURCE} for {day.isoformat()}: {symbol} response is not chart data ({problem}); "
            f"not stored: {url_for(symbol)}",
            source=SOURCE,
            day=day,
            url=url_for(symbol),
            status_code=result.status_code,
        )
    return store.save_result(source=SOURCE, day=day, name=raw_name(symbol, interval), result=result)


def _body_problem(content: bytes) -> str | None:
    """Why this body is not a chart result, or None if it looks right."""
    try:
        body = json.loads(content)
    except ValueError:
        return f"not JSON, starts with {content[:30]!r}"
    chart = body.get("chart") if isinstance(body, dict) else None
    if not isinstance(chart, dict):
        return 'JSON without a "chart" object'
    error = chart.get("error")
    if error:
        return f"Yahoo error {error.get('code')!r}: {error.get('description')!r}"
    result = chart.get("result")
    if not isinstance(result, list) or not result:
        return 'no "result" in chart'
    return None


# -- parsing ------------------------------------------------------------------------


def parse(content: bytes, *, source_file: str) -> pd.DataFrame:
    """Parse one chart response into bars.

    Columns: see OUTPUT_COLUMNS. `ts` is the bar START time, timezone-aware UTC. Bars
    where Yahoo sent null prices (no trades in that slot) are dropped and counted in
    ``df.attrs["null_bars"]``. Rows are sorted by time; a repeated timestamp is an error.
    """
    problem = _body_problem(content)
    if problem is not None:
        raise ValueError(f"{SOURCE}: {source_file}: {problem}")
    result = json.loads(content)["chart"]["result"][0]
    meta = result.get("meta") or {}
    symbol = meta.get("symbol")
    granularity = meta.get("dataGranularity")
    if not isinstance(symbol, str) or not isinstance(granularity, str):
        raise ValueError(f"{SOURCE}: {source_file}: meta lacks 'symbol' or 'dataGranularity'")
    minutes = _interval_minutes(granularity)

    stamps = result.get("timestamp")
    quote_list = (result.get("indicators") or {}).get("quote") or []
    if not isinstance(stamps, list) or len(quote_list) != 1:
        raise ValueError(f"{SOURCE}: {source_file}: no 'timestamp' list or not exactly one quote")
    quote = quote_list[0]
    columns = ("open", "high", "low", "close", "volume")
    for name in columns:
        if not isinstance(quote.get(name), list) or len(quote[name]) != len(stamps):
            raise ValueError(
                f"{SOURCE}: {source_file}: '{name}' is missing or not as long as 'timestamp' "
                f"({len(stamps)} bars)"
            )

    frame = pd.DataFrame(
        {
            "symbol": pd.Series([symbol] * len(stamps), dtype="string"),
            "ts": pd.to_datetime(pd.Series(stamps, dtype="int64"), unit="s", utc=True),
            "interval_minutes": pd.Series([minutes] * len(stamps), dtype="int64"),
            **{name: pd.Series(quote[name], dtype="float64") for name in columns},
            "source_file": pd.Series([source_file] * len(stamps), dtype="string"),
        },
        columns=list(OUTPUT_COLUMNS),
    )
    null_bars = int(frame["close"].isna().sum())
    frame = frame.dropna(subset=["close"]).sort_values("ts", kind="stable").reset_index(drop=True)
    if frame["ts"].duplicated().any():
        raise ValueError(f"{SOURCE}: {source_file}: repeated bar timestamps for {symbol}")
    frame.attrs["null_bars"] = null_bars
    return frame


# -- picking a value at a moment ----------------------------------------------------


@dataclass(frozen=True)
class BarValue:
    """The price chosen for a moment, with the evidence of which bar it came from."""

    symbol: str
    close: float
    bar_start: datetime  # tz-aware UTC
    bar_end: datetime  # tz-aware UTC; never later than the requested moment
    stale: timedelta  # requested moment minus bar_end (0 = the bar ended exactly then)


def indian_close(day: date) -> datetime:
    """15:30 IST on an NSE session day, from the project calendar (tz-aware)."""
    return calendars.market_close("IN", day)


def us_close(day: date) -> datetime:
    """US cash-market close on a NYSE session day (16:00 New York; 13:00 on early-close days)."""
    return calendars.market_close("US", day)


def value_at(
    bars: pd.DataFrame,
    at: datetime,
    *,
    symbol: str | None = None,
    max_stale: timedelta = timedelta(minutes=30),
) -> BarValue | None:
    """Price as it stood at the moment `at`: the close of the last bar that had ENDED by then.

    Why "ended", not "started": a 5-minute bar stamped 15:30 covers 15:30-15:35, so using
    its close for "the price at 15:30" would peek up to five minutes into the future. The
    bar stamped 15:25 ends at 15:30, and its close is the last price before 15:30. (In
    the fixture the two differ: 21000.50 versus 21010.25.)

    Returns None, never a guess, when no bar ended in the `max_stale` window before `at`
    (market shut, weekend, data gap). The caller decides what to do.
    """
    require_aware(at, "at")
    frame = bars
    if symbol is not None:
        frame = frame[frame["symbol"] == symbol]
    names = set(frame["symbol"].astype(str))
    if len(names) > 1:
        raise ValueError(f"{SOURCE}: bars hold several symbols {sorted(names)}; pass symbol=")
    if frame.empty:
        return None
    ends = frame["ts"] + pd.to_timedelta(frame["interval_minutes"], unit="m")
    at_utc = pd.Timestamp(at).tz_convert("UTC")
    ready = frame[ends <= at_utc]
    if ready.empty:
        return None
    row = ready.iloc[-1]  # frame is sorted by time
    bar_start = row["ts"].to_pydatetime()
    bar_end = bar_start + timedelta(minutes=int(row["interval_minutes"]))
    stale = at_utc.to_pydatetime() - bar_end
    if stale > max_stale:
        return None
    return BarValue(
        symbol=str(row["symbol"]),
        close=float(row["close"]),
        bar_start=bar_start,
        bar_end=bar_end,
        stale=stale,
    )
