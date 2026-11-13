"""fof_monthly: feeder funds (funds of funds) that buy these ETFs (requests R4, R14).

A "feeder" or fund of funds (FoF) is a mutual fund whose only holding is one ETF.
When investors put money into the feeder, the feeder buys ETF units on the
exchange at the market price (it cannot create new ETF units because the
overseas caps are used up). So a feeder is a price-insensitive buyer, and once it
holds a big slice of the ETF it also takes those units out of the free-floating
supply ("float").

The facts come from the fund houses' monthly portfolio disclosures and AMFI's
monthly data, typed into ``data/manual/fof_monthly.csv`` (one row per feeder per
month end). Money is in RUPEES, not crores.

Columns: amfi_scheme_code, month_end (last day of the month), fund_name,
etf_symbol, aum, etf_units_held, fof_net_flow, subscription_status (open,
lump_sum_shut, sip_shut or shut), notice_date (date of the notice that set the
status). Only the first four are required.

``feeder_units_daily`` turns the monthly rows into a daily series (needed later
for ``float_net`` = (units outstanding - feeder units) x NAV).
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import date

import numpy as np
import pandas as pd

from etfp.clean.common import CleanContext, CleanError, concat_frames
from etfp.clean.manual_csv import Row, is_month_end, read_rows

logger = logging.getLogger("etfp.clean")

TABLE = "fof_monthly"
FILENAME = "fof_monthly.csv"
COLUMNS = [
    "amfi_scheme_code", "month_end", "fund_name", "etf_symbol", "aum", "etf_units_held",
    "fof_net_flow", "subscription_status", "notice_date", "source_file",
]  # fmt: skip
SUBSCRIPTION_STATUSES = ("open", "lump_sum_shut", "sip_shut", "shut")


def _parse_row(row: Row, known: set[str]) -> dict[str, object]:
    month_end = row.get_date("month_end", required=True)
    assert month_end is not None
    if not is_month_end(month_end):
        raise row.fail(f"column 'month_end': {month_end} is not the last day of a month")
    symbols = row.get_symbols("etf_symbol", known)
    if len(symbols) != 1:
        raise row.fail("column 'etf_symbol': needs exactly one ETF symbol")
    notice = row.get_date("notice_date")
    return {
        "amfi_scheme_code": row.get_int("amfi_scheme_code", required=True, positive=True),
        "month_end": month_end,
        "fund_name": row.get_text("fund_name", required=True),
        "etf_symbol": symbols[0],
        "aum": row.get_float("aum", minimum=0),
        "etf_units_held": row.get_float("etf_units_held", minimum=0),
        # A net flow can be negative (more redemptions than subscriptions).
        "fof_net_flow": row.get_float("fof_net_flow"),
        "subscription_status": row.get_choice("subscription_status", SUBSCRIPTION_STATUSES),
        "notice_date": notice,
        "source_file": row.source_file,
    }


def build(ctx: CleanContext) -> pd.DataFrame:
    """Build fof_monthly from data/manual/fof_monthly.csv (empty table if the file is absent)."""
    rows = read_rows(
        ctx,
        FILENAME,
        TABLE,
        required=["amfi_scheme_code", "month_end", "fund_name", "etf_symbol"],
        optional=[
            "aum", "etf_units_held", "fof_net_flow", "subscription_status", "notice_date",
        ],
    )  # fmt: skip
    if not rows:
        return concat_frames([], COLUMNS)
    known = ctx.etf_symbols()
    parsed = [_parse_row(r, known) for r in rows]

    first_seen: dict[tuple[object, object], str] = {}
    held_symbol: dict[object, tuple[object, str]] = {}
    for rec in parsed:
        key = (rec["amfi_scheme_code"], rec["month_end"])
        if key in first_seen:
            raise CleanError(
                f"{TABLE}: feeder {key[0]} has two rows for {key[1]}: "
                f"{first_seen[key]} and {rec['source_file']}"
            )
        first_seen[key] = str(rec["source_file"])
        # One feeder holds one ETF; a scheme code that switches ETF is a typing mistake.
        code = rec["amfi_scheme_code"]
        if code in held_symbol and held_symbol[code][0] != rec["etf_symbol"]:
            raise CleanError(
                f"{TABLE}: feeder {code} holds {held_symbol[code][0]} at {held_symbol[code][1]} "
                f"but {rec['etf_symbol']} at {rec['source_file']}"
            )
        held_symbol.setdefault(code, (rec["etf_symbol"], str(rec["source_file"])))
    out = pd.DataFrame(parsed, columns=COLUMNS)
    logger.info("%s: %d rows for %d feeder(s)", TABLE, len(out), len(held_symbol))
    return out


_NAT = np.datetime64("NaT", "D")  # "no date", with an explicit day unit
FEEDER_UNITS_COLUMNS = ["symbol", "date", "feeder_units", "feeders_counted", "oldest_month_end"]


def feeder_units_daily(
    fof_monthly: pd.DataFrame, dates: Iterable[date], *, lag_days: int = 0
) -> pd.DataFrame:
    """Total ETF units held by feeder funds, for each ETF and each requested date.

    For every feeder, take its latest disclosed ``etf_units_held`` on or before
    the date (carry-forward: a monthly number stays in force until the next one).
    Then add the feeders of the same ETF together. Months where a feeder did not
    report units (blank) are skipped, so the previous reported number carries on.

    Returns one row per (ETF symbol, date): ``symbol``, ``date``, ``feeder_units``
    (float, NaN when no feeder has reported yet: "unknown", not "zero"),
    ``feeders_counted`` (how many feeders contribute) and ``oldest_month_end`` (the
    oldest disclosure among them, so a reader can see how stale the sum is).

    ``lag_days``: a monthly portfolio is published some days after month end
    (up to 10 under SEBI rules). With the default 0 the number counts from the
    month-end date itself, which uses information the market did not have yet; use
    e.g. ``lag_days=10`` for a no-look-ahead series.

    Pure function: nothing is read from disk, so it is easy to test.
    """
    days = sorted({d for d in dates})
    if lag_days < 0:
        raise ValueError("lag_days must be 0 or more")
    columns = FEEDER_UNITS_COLUMNS
    if fof_monthly.empty or not days:
        return pd.DataFrame({c: [] for c in columns})

    needed = {"amfi_scheme_code", "month_end", "etf_symbol", "etf_units_held"}
    missing = needed - set(fof_monthly.columns)
    if missing:
        raise ValueError(f"fof_monthly is missing column(s): {sorted(missing)}")
    known = fof_monthly.dropna(subset=["etf_units_held"])

    day_idx = np.array(days, dtype="datetime64[D]")
    as_of = day_idx - np.timedelta64(lag_days, "D")  # the disclosure date that is public by then
    frames = []
    for symbol, group in known.groupby("etf_symbol", sort=True):
        total = np.zeros(len(days))
        counted = np.zeros(len(days), dtype=int)
        oldest = np.full(len(days), _NAT, dtype="datetime64[D]")
        for _, feeder in group.groupby("amfi_scheme_code", sort=True):
            feeder = feeder.sort_values("month_end")
            ends = np.array(list(feeder["month_end"]), dtype="datetime64[D]")
            units = feeder["etf_units_held"].to_numpy(dtype=float)
            pos = np.searchsorted(ends, as_of, side="right") - 1  # latest disclosure <= as_of
            have = pos >= 0
            total[have] += units[pos[have]]
            counted[have] += 1
            this_end = np.where(have, ends[np.clip(pos, 0, None)], _NAT)
            oldest = np.where(
                have & (np.isnat(oldest) | (this_end < oldest)),
                this_end,
                oldest,
            )
        frames.append(
            pd.DataFrame(
                {
                    "symbol": symbol,
                    "date": days,
                    "feeder_units": np.where(counted > 0, total, np.nan),
                    "feeders_counted": pd.array(counted, dtype="Int64"),
                    "oldest_month_end": [
                        None if np.isnat(x) else x.astype("datetime64[D]").item() for x in oldest
                    ],
                }
            )
        )
    if not frames:
        return pd.DataFrame({c: [] for c in columns})
    return pd.concat(frames, ignore_index=True)[columns]
