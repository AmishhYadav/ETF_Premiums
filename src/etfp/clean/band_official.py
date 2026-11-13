"""band_official: the price band NSE published for each ETF, and the session it applies to (R2).

A price band is the range outside which the exchange will not accept orders for
the day. NSE writes the *initial* band for the next session in the ``PricRg``
column of the security master as "low-high", for example "206.71-252.63".

Timing (DECISIONS D4): the file dated ``t`` holds the band for the **next NSE
trading day**, not for ``t`` itself. Example: the file dated 9 Nov 2026 gives
MONQ50 206.71-252.63, which is the 9 Nov close (229.67) times 0.90 and 1.10, and
it applies to the next session. 10 Nov 2026 is an NSE holiday, so that session
is 11 Nov. The next session is looked up in the trading calendar
(``etfp.calendars``), never by adding one day.

Both dates are stored: ``file_date`` (the file) and ``session_date`` (the day the
band is in force). Join prices to bands on ``session_date``.

This is the band at the OPEN. Widening during the day (cooling-off rule) is not
in this file. A fund with no band in the file ("No Band") keeps a row with empty
limits so the absence is visible.
"""

from __future__ import annotations

import logging

import pandas as pd

from etfp import calendars
from etfp.clean import _secmaster
from etfp.clean.common import CleanContext, CleanError

logger = logging.getLogger("etfp.clean")

TABLE = "band_official"
COLUMNS = [
    "symbol", "file_date", "session_date", "series", "band_low_official",
    "band_high_official", "price_range_raw", "source_file",
]  # fmt: skip


def build(ctx: CleanContext) -> pd.DataFrame:
    """Build the band_official table from every security-master file."""
    rows = _secmaster.load(ctx, TABLE)
    out = rows.rename(
        columns={"band_low": "band_low_official", "band_high": "band_high_official"}
    ).copy()

    sessions = {d: _next_session(d) for d in sorted(set(out["file_date"]))}
    out["session_date"] = pd.Series([sessions[d] for d in out["file_date"]], dtype=object)
    _warn_on_shared_sessions(out)
    return out[COLUMNS].reset_index(drop=True)


def _next_session(file_date) -> object:
    try:
        return calendars.next_session("IN", file_date)
    except ValueError as exc:
        raise CleanError(
            f"{TABLE}: no NSE trading day after file date {file_date} in the calendar ({exc})"
        ) from exc


def _warn_on_shared_sessions(df: pd.DataFrame) -> None:
    """Two files can point at one session (a file dated on a holiday). Say so, keep both."""
    shared = df.duplicated(["symbol", "session_date"], keep=False)
    if shared.any():
        first = df[shared].iloc[0]
        logger.warning(
            "%s: %d row(s) share a (symbol, session_date) with another file, e.g. %s for %s; "
            "use the latest file_date for each session",
            TABLE,
            int(shared.sum()),
            first["symbol"],
            first["session_date"],
        )
