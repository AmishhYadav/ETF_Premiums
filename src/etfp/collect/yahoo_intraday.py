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
* Yahoo keeps only about 60 days of 5-minute history (30 days for 1-minute), but about
  730 days of 60-minute bars. `fetch_history` (``etfp collect yahoo-history``) asks for
  728 days of 60m bars in one request per symbol, which reaches back to late 2024 and
  covers the R12 beta window; the daily 5m files keep the finer record going. Every
  Yahoo request from this machine so far (13, 14, 17-18 Nov 2026) got HTTP 429, so none
  of this has been checked against a live answer (DECISIONS D43).
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
from etfp.timeutil import IST, now_ist, require_aware

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


# -- history (backfill) mode ----------------------------------------------------------
#
# Yahoo serves 60-minute bars for about 730 days back (5-minute bars only ~60 days). One
# request per symbol therefore covers the whole Sep-Nov 2026 shock and goes back to late
# 2024: enough for the R12 beta window (250 NAV dates before 2026-09-07). This is NOT
# confirmed against a live answer yet: every request so far got HTTP 429 (D22, D29, D43).

HISTORY_INTERVAL = "60m"
HISTORY_DAYS = 728  # Yahoo refuses 60m requests that start more than 730 days back; keep 2 spare
HISTORY_SYMBOLS: tuple[str, ...] = SYMBOLS  # no Hang Seng future: HK ETFs have no futures term


def history_raw_name(symbol: str, interval: str = HISTORY_INTERVAL) -> str:
    """Raw file name of one history download: 'NQ=F' -> 'NQ_F_60m_history.json'.

    Distinct from the daily 5m names, so the daily job's "already have it" check is not
    fooled. Saved under the folder of the IST date the download was made.
    """
    return f"{safe_symbol(symbol)}_{interval}_history.json"


def history_window(now: datetime, *, days: int = HISTORY_DAYS) -> tuple[datetime, datetime]:
    """UTC start/end of a history request made at ``now``.

    The end is ``now`` rounded DOWN to the whole hour. Futures and FX 60m bars start on the
    hour, so every bar Yahoo returns has already ended: the hour still in progress (whose
    "close" would only be the price at download time) is never stored.
    """
    now_utc = require_aware(now, "now").astimezone(UTC)
    end = now_utc.replace(minute=0, second=0, microsecond=0)
    return end - timedelta(days=days), end


def fetch_history(
    client: PoliteClient,
    store: RawStore,
    symbol: str,
    *,
    now: datetime | None = None,
    interval: str = HISTORY_INTERVAL,
    days: int = HISTORY_DAYS,
) -> RawRecord:
    """One large request: ``days`` of ``interval`` bars for ``symbol``, stored untouched.

    Saved as ``yahoo_intraday/<IST date of now>/<history_raw_name>``. The build reads every
    file in ``yahoo_intraday`` and merges overlapping bars, so history and daily 5m files
    live side by side. A non-chart body (Yahoo's JSON error with HTTP 200) is not stored.
    HTTP 429 raises ``SourceBlockedError`` from the client and is never retried there.
    """
    _interval_minutes(interval)
    now = require_aware(now, "now") if now is not None else now_ist()
    day = now.astimezone(IST).date()
    start, end = history_window(now, days=days)
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
            f"{SOURCE} history: {symbol} response is not chart data ({problem}); "
            f"not stored: {url_for(symbol)}",
            source=SOURCE,
            day=day,
            url=url_for(symbol),
            status_code=result.status_code,
        )
    return store.save_result(
        source=SOURCE, day=day, name=history_raw_name(symbol, interval), result=result
    )


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
    ready = frame.assign(_end=ends)[ends <= at_utc]
    if ready.empty:
        return None
    # Latest END wins (with 5m and 60m bars mixed, the latest start is not always the
    # freshest price); on a tie the shorter bar wins (it is the finer measurement).
    ready = ready.sort_values(["_end", "interval_minutes"], ascending=[True, False], kind="stable")
    row = ready.iloc[-1]
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


# -- one value per day at a fixed moment (15:30 IST, US close) ------------------------

VALUE_COLUMNS: tuple[str, ...] = (
    "date",
    "moment",
    "symbol",
    "at_utc",
    "close",
    "bar_start_utc",
    "bar_end_utc",
    "interval_minutes",
    "stale_minutes",
    "source_file",
)
"""Columns of :func:`values_at`.

* ``moment``: ``'IN_1530'`` (15:30 IST on the NSE session ``date``) or ``'US_CLOSE'``
  (16:00 New York, 13:00 on early-close days, on the NYSE session ``date``).
* ``close``: close of the LAST BAR THAT HAD ENDED by ``at_utc``. For 60m futures/FX bars
  (which start on the hour) at 15:30 IST = 10:00 UTC that is the bar 09:00-10:00 UTC,
  whose close is the last trade before 10:00:00 UTC; nothing after 15:30 IST is used.
* ``stale_minutes``: ``at_utc - bar_end_utc`` in minutes (0 = the bar ended exactly then).
"""

MOMENT_IN_1530 = "IN_1530"
MOMENT_US_CLOSE = "US_CLOSE"


def values_at(
    bars: pd.DataFrame,
    days: list[date],
    *,
    moment: str = MOMENT_IN_1530,
    max_stale: timedelta = timedelta(minutes=30),
) -> pd.DataFrame:
    """One row per (day, symbol) that has a bar ended within ``max_stale`` before the moment.

    ``bars`` is one or more `parse` results concatenated (several symbols and bar sizes
    allowed). A day with no usable bar gives NO row (missing, never carried forward from
    further back); a day outside the calendar's range is skipped the same way.
    """
    if moment not in (MOMENT_IN_1530, MOMENT_US_CLOSE):
        raise ValueError(f"{SOURCE}: unknown moment {moment!r}")
    rows: list[dict[str, object]] = []
    if bars.empty:
        return pd.DataFrame({c: [] for c in VALUE_COLUMNS})
    for symbol, grp in bars.groupby("symbol", sort=True):
        grp = grp.drop_duplicates(["ts", "interval_minutes"], keep="last")
        for day in days:
            try:
                at = indian_close(day) if moment == MOMENT_IN_1530 else us_close(day)
            except ValueError:
                continue
            got = value_at(grp, at, max_stale=max_stale)
            if got is None:
                continue
            match = grp[
                (grp["ts"] == pd.Timestamp(got.bar_start))
                & (
                    grp["interval_minutes"]
                    == int((got.bar_end - got.bar_start).total_seconds() // 60)
                )
            ]
            rows.append(
                {
                    "date": day,
                    "moment": moment,
                    "symbol": str(symbol),
                    "at_utc": pd.Timestamp(at).tz_convert("UTC"),
                    "close": got.close,
                    "bar_start_utc": pd.Timestamp(got.bar_start),
                    "bar_end_utc": pd.Timestamp(got.bar_end),
                    "interval_minutes": int((got.bar_end - got.bar_start).total_seconds() // 60),
                    "stale_minutes": got.stale.total_seconds() / 60.0,
                    "source_file": str(match["source_file"].iloc[-1]) if len(match) else None,
                }
            )
    if not rows:
        return pd.DataFrame({c: [] for c in VALUE_COLUMNS})
    return pd.DataFrame(rows, columns=list(VALUE_COLUMNS))
