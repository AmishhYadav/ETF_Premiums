"""etf_master: one row per ETF, from NSE's ETF list plus config/etfs.yaml.

NSE's list (``nse_etf_seclist``, DECISIONS D8) names every listed ETF with its
ISIN, listing date and underlying. Our config adds what NSE does not publish:
fund house, AMFI scheme code, underlying market, the "group" hypothesis and
which funds are tracked. Where both have a value, config wins (it was checked by
hand); a disagreement on the ISIN is logged as a warning.

The list file is not dated (NSE overwrites it in place), so we use the most
recent copy in the raw store.
"""

from __future__ import annotations

import logging

import pandas as pd

from etfp.clean.common import CleanContext, list_raw_files, parse_file
from etfp.collect import nse_etf_listfile

logger = logging.getLogger("etfp.clean")

TABLE = "etf_master"


def build(ctx: CleanContext) -> pd.DataFrame:
    """Build the etf_master table."""
    listed = _latest_list(ctx)
    rows: dict[str, dict] = {}

    if listed is not None:
        frame, list_date, list_path = listed
        for r in frame.itertuples(index=False):
            symbol = str(r.symbol)
            if symbol in rows:
                logger.warning(
                    "%s: symbol %s appears twice in %s; keeping the first", TABLE, symbol, list_path
                )
                continue
            rows[symbol] = {
                "symbol": symbol,
                "name": r.name,
                "isin": r.isin,
                "listing_date": r.listing_date,
                "face_value": r.face_value,
                "market_lot": r.market_lot,
                "nse_underlying_asset": r.underlying_asset,
                "nse_underlying_type": r.underlying_type,
                "list_file_date": list_date,
                "source_file": list_path,
            }

    for etf in ctx.universe.etfs:
        row = rows.setdefault(etf.symbol, {"symbol": etf.symbol, "source_file": ctx.config_file})
        listed_isin = row.get("isin")
        if listed_isin is not None and not pd.isna(listed_isin) and listed_isin != etf.isin:
            logger.warning(
                "%s: ISIN for %s is %s in config but %s on NSE's list; using config",
                TABLE,
                etf.symbol,
                etf.isin,
                listed_isin,
            )
        row.update(
            name=etf.name,
            isin=etf.isin,
            amc=etf.amc,
            amfi_scheme_code=etf.amfi_scheme_code,
            underlying_index=etf.underlying_index,
            underlying_market=etf.underlying_market,
            currency=etf.currency,
            asset_class=etf.asset_class,
            group_label=etf.group,
            tracked=etf.tracked,
            futures_proxy=etf.futures_proxy,
            creation_unit_size=etf.creation_unit_size,
            config_file=ctx.config_file,
        )

    columns = [
        "symbol", "name", "isin", "amc", "amfi_scheme_code", "underlying_index",
        "underlying_market", "currency", "asset_class", "group_label", "tracked",
        "futures_proxy", "creation_unit_size", "listing_date", "face_value", "market_lot",
        "nse_underlying_asset", "nse_underlying_type", "list_file_date", "config_file",
        "source_file",
    ]  # fmt: skip
    out = pd.DataFrame(list(rows.values()), columns=columns)
    # ETFs that are not in config are not tracked (an explicit False, not "unknown").
    out["tracked"] = out["tracked"].where(out["tracked"].notna(), False)
    return out


def _latest_list(ctx: CleanContext) -> tuple[pd.DataFrame, object, str] | None:
    files = list_raw_files(ctx.raw_root, nse_etf_listfile.SOURCE)
    if not files:
        logger.warning("%s: no NSE ETF list in the raw store; using config/etfs.yaml only", TABLE)
        return None
    rf = files[-1]  # sorted oldest first
    frame = parse_file(TABLE, rf, lambda b: nse_etf_listfile.parse(b, source_file=rf.relpath))
    return frame, rf.day, rf.relpath
