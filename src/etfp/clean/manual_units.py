"""units_monthly: month-end units outstanding before the daily file exists (request R11).

NSE's security master gives units outstanding every day, but only from 2024
(the file of 2 Jan 2024 returned HTTP 404, DECISIONS D4). For earlier months the
units are estimated from what the fund house reports each month:

    units = AUM / NAV        (AUM in rupees, NAV in rupees per unit)

Example: AUM Rs 1,000,000,000 and NAV Rs 100 means 10,000,000 units. It is an
estimate (AUM and NAV are rounded, and the NAV date may differ a little from the
AUM date), so ``units`` is not a whole number.

File ``data/manual/units_monthly.csv``. Columns: symbol, month_end (last day of the
month), aum (rupees, NOT crores), nav (rupees), units (blank = aum / nav),
source (where the figures came from, e.g. "amfi", "factsheet 2022-01").
If all of aum, nav and units are given they must agree within 1%.

Months with no typed row are filled automatically from the fund houses' monthly portfolio
files (``etfp.clean.amc_portfolio``, D46); a typed row always wins.
"""

from __future__ import annotations

import logging

import pandas as pd

from etfp.clean import amc_portfolio
from etfp.clean.common import CleanContext, CleanError, concat_frames
from etfp.clean.manual_csv import Row, is_month_end, read_rows

logger = logging.getLogger("etfp.clean")

TABLE = "units_monthly"
FILENAME = "units_monthly.csv"
COLUMNS = ["symbol", "month_end", "aum", "nav", "units", "source", "source_file"]
AGREE_TOLERANCE = 0.01  # typed units vs aum/nav: 1%


def _parse_row(row: Row, known: set[str]) -> dict[str, object]:
    symbols = row.get_symbols("symbol", known)
    if len(symbols) != 1:
        raise row.fail("column 'symbol': needs exactly one ETF symbol")
    month_end = row.get_date("month_end", required=True)
    assert month_end is not None
    if not is_month_end(month_end):
        raise row.fail(f"column 'month_end': {month_end} is not the last day of a month")
    aum = row.get_float("aum", positive=True)
    nav = row.get_float("nav", positive=True)
    units = row.get_float("units", positive=True)
    source = row.get_text("source", required=True)
    if units is None:
        if aum is None or nav is None:
            raise row.fail("give 'units', or both 'aum' and 'nav' so units = aum / nav")
        units = aum / nav
    elif aum is not None and nav is not None:
        implied = aum / nav
        if abs(units - implied) > AGREE_TOLERANCE * implied:
            raise row.fail(
                f"column 'units': {units:,.0f} does not match aum / nav = {implied:,.0f} "
                f"(more than {AGREE_TOLERANCE:.0%} apart); check aum is in rupees, not crores"
            )
    return {
        "symbol": symbols[0],
        "month_end": month_end,
        "aum": aum,
        "nav": nav,
        "units": units,
        "source": source,
        "source_file": row.source_file,
    }


def build(ctx: CleanContext) -> pd.DataFrame:
    """Build units_monthly from data/manual/units_monthly.csv (empty table if absent)."""
    rows = read_rows(
        ctx,
        FILENAME,
        TABLE,
        required=["symbol", "month_end", "source"],
        optional=["aum", "nav", "units"],
    )
    if not rows:
        return amc_portfolio.merge_units(ctx, concat_frames([], COLUMNS))
    known = ctx.etf_symbols()
    parsed = [_parse_row(r, known) for r in rows]
    seen: dict[tuple[object, object], str] = {}
    for rec in parsed:
        key = (rec["symbol"], rec["month_end"])
        if key in seen:
            raise CleanError(
                f"{TABLE}: {key[0]} has two rows for {key[1]}: {seen[key]} and {rec['source_file']}"
            )
        seen[key] = str(rec["source_file"])
    out = pd.DataFrame(parsed, columns=COLUMNS)
    logger.info("%s: %d typed rows for %d ETF(s)", TABLE, len(out), out["symbol"].nunique())
    # Months not typed by hand come from the AMCs' monthly portfolio files: AUM / NAV (D46).
    return amc_portfolio.merge_units(ctx, out)
