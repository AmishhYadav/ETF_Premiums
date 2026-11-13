"""units_daily: how many units of each ETF exist, read from the NSE security master (R1).

``IssdCptl`` ("issued capital") in the security master is the number of ETF units
outstanding (DECISIONS D4, D7). When an ETF cannot create new units, this number
stays flat. A jump up is a creation (a market maker handed in a basket of
shares or cash and got new units); a drop is a redemption.

``date`` is the date in the security-master file name, kept as published.
How soon after a creation or redemption NSE updates the number is still
unconfirmed (DECISIONS D4, OPEN), so a change dated ``date`` may really have
happened a day or two earlier. Changes and creation-unit counts are worked out
later from consecutive rows; this table only records what each file said.
"""

from __future__ import annotations

import logging

import pandas as pd

from etfp.clean import _secmaster
from etfp.clean.common import CleanContext, log_gaps

logger = logging.getLogger("etfp.clean")

TABLE = "units_daily"
COLUMNS = ["symbol", "date", "series", "isin", "units_outstanding", "source_file"]


def build(ctx: CleanContext) -> pd.DataFrame:
    """Build the units_daily table from every security-master file."""
    rows = _secmaster.load(ctx, TABLE)
    log_gaps(TABLE, set(rows["file_date"]))
    out = rows.rename(columns={"file_date": "date", "issued_units": "units_outstanding"})
    empty = out["units_outstanding"].isna()
    if empty.any():
        logger.warning(
            "%s: %d row(s) have no IssdCptl and were skipped, e.g. %s in %s",
            TABLE,
            int(empty.sum()),
            out[empty].iloc[0]["symbol"],
            out[empty].iloc[0]["source_file"],
        )
        out = out[~empty]
    return out[COLUMNS].reset_index(drop=True)
