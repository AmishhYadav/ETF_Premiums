# ruff: noqa: E501  (column definitions are long plain-words text on purpose)
"""Research export: the one folder the analysis side reads (CLAUDE.md "Research handoff", R18).

``export_research(store)`` writes ``exports/research/<YYYY-MM-DD>/`` (the date is the build
date in India) with the parquet tables, ``schema.json``, ``DATA_DICTIONARY.md`` and
``manifest.json``, then writes the folder's absolute path to ``exports/research/LATEST.txt``.

Single source of truth for column meanings
------------------------------------------
``ETF_DAY_COLUMNS`` below holds, for every column of the ``etf_day`` table, its type,
a plain-words definition, units, formula and source. Three things are generated from it:

* the registered ``etf_day`` table schema (``etf_day_schema``) that the store enforces,
* ``schema.json`` and ``DATA_DICTIONARY.md`` in the export,
* a test that fails if the table has a column with no definition (or the reverse).

Changing what a column MEANS needs a ``SCHEMA_VERSION`` bump, a new ``SCHEMA_CHANGELOG``
entry and a note in docs/DECISIONS.md (CLAUDE.md rule).

The full ``etf_day`` schema is registered only temporarily (``etf_day_schema_registered``):
``etfp.store`` keeps ``etf_day`` as an open-ended placeholder so other code and tests that
read or write it without this module keep working.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, time
from itertools import pairwise
from pathlib import Path
from typing import Any

import pandas as pd

from etfp import calendars, paths
from etfp import store as store_mod
from etfp.store import SCHEMAS, Column, Store, TableSchema
from etfp.timeutil import IST, now_ist

logger = logging.getLogger("etfp.export")

SCHEMA_VERSION = "1.6.0"
SCHEMA_CHANGELOG: list[dict[str, str]] = [
    {
        "version": "1.0.0",
        "date": "2026-11-13",
        "changes": (
            "First export. etf_day with prices, NAV timing (R3), fair value (D10), beta fair "
            "value (R12), premiums, computed and official bands (R2), limit flags and cap_pi "
            "(R10), units and creation events (R1), turnover (R9, NSE part), feeder float (R14), "
            "returns and data-quality flags. BLUEPRINT alias columns included."
        ),
    },
    {
        "version": "1.1.0",
        "date": "2026-11-14",
        "changes": (
            "BSE columns filled (R9): bse_close, bse_volume, bse_value, bse_trades come from "
            "BSE's daily bhavcopy, matched to the fund by ISIN. MEANING CHANGE: turnover is now "
            "(NSE + BSE units traded) / units; in 1.0.0 it was NSE units / units. turnover_nse "
            "is unchanged. turnover_is_nse_only is now True only on days with no BSE row "
            "(1.0.0: always True). A day with no BSE row counts BSE as zero in turnover, so "
            "filter on turnover_is_nse_only when the BSE part matters. bse_close is adjusted "
            "for unit splits like close (price_adj_factor)."
        ),
    },
    {
        "version": "1.2.0",
        "date": "2026-11-14",
        "changes": (
            "New files only; no etf_day column changes meaning. limit_day.parquet (R13): one "
            "row per ETF-day a price limit was touched, with first touch time, minutes at the "
            "limit (estimate; obs_source says snapshots_15m, bloomberg_1m or none), inferred "
            "widenings, book quantity at the limit and locked_up/locked_down. "
            "slb_eligible.parquet and slb_daily.parquet (R8): NSE securities-lending "
            "eligibility and daily lending trades. bse_prices.parquet (R9): the BSE rows "
            "behind the bse_* columns."
        ),
    },
    {
        "version": "1.3.0",
        "date": "2026-11-14",
        "changes": (
            "MEANING CHANGE: x_april is now ln(close / (1.2 x nav_known_at_close)) (the "
            "BLUEPRINT definition; the April 2027 band is anchored on NAV). In 1.0.0-1.2.0 it "
            "was ln(close / (1.2 x fv)); that version is now x_april_fv. The column "
            "x_april_nav_known is removed (it is the new x_april). "
            "New etf_day column attention (R15): Google Trends search interest for the ETF's "
            "configured terms on the same calendar day, empty unless every configured term has "
            "a value (config/trends_terms.yaml). "
            "New files: feeder_nav_daily.parquet (R14): daily AMFI NAV of each feeder fund "
            "(Regular and Direct plan are separate schemes). china_etf_daily.parquet (R17): "
            "close, NAV, shares outstanding and premium of China-listed QDII ETFs, from "
            "Bloomberg (licensed: not for the open dataset). inav_snapshots.parquet rows from "
            "the NSE quote capture (source nse_quote) join the price block and the order book "
            "of one page visit into one row; their source_file lists both raw files "
            "separated by ';'. MEANING CHANGE: nav_known_at_close is now empty when the "
            "freshest known NAV is more than 7 calendar days old (a hole in the NAV history, "
            "not a publication lag); nav_known_date and nav_publication_lag_days still show "
            "it and dq_nav_stale flags it. Premiums, fv and x_april are then empty "
            "(fv_method no_nav) instead of being computed from an old NAV."
        ),
    },
    {
        "version": "1.4.0",
        "date": "2026-11-14",
        "changes": (
            "New file only; no existing column changes meaning. valve_monthly.parquet (M5, "
            "DECISIONS D33): one row per ETF and calendar month from 2021-01 with units at the "
            "start and end of the month, number of days units rose / fell, net creation units "
            "and valve_status (open = units rose at least once; closed = data present and units "
            "never moved; redeeming_only = fell, never rose; unknown = no data or nothing to "
            "compare with). Daily NSE security-master counts where present (from 2024-02-05), "
            "else the hand-entered monthly AUM / NAV table with a +-1% threshold, else unknown."
        ),
    },
    {
        "version": "1.5.0",
        "date": "2026-11-14",
        "changes": (
            "MEANING CHANGES (DECISIONS D38, D39). Computed band: the base NAV is rounded to "
            "the paisa before the percentage, the lower limit rounds down through 2025-12-08 "
            "and up from 2025-12-09; special sessions 2024-03-02 and 2024-05-18 use +-5%; when "
            "no NAV is dated T-2, NSE's fallback is applied (previous close through "
            "2025-10-23, latest earlier NAV from 2025-10-24). band_base_kind now names the base "
            "actually used (prev_close on 246 nav_t2-regime rows); band_base_price, "
            "band_*_initial, band_*_max and band_mismatch follow (pre-Sep-2026 mismatches "
            "414 -> 20). nav_t2_date can be a non-session NAV day (31 March). Budget sessions "
            "2025-02-01 and 2026-02-01 are sessions. Look-ahead fix: nav_known_at_close "
            "(and nav_known_date, prem_nav_known, fv, x_april, every column built on it) "
            "never uses a NAV dated after t; 43 rows changed, mostly around 31 March. "
            "Official band columns are unchanged. New files: notices.parquet (D37; SEBI and "
            "NSE notice listings with keyword tags; relevant = keyword candidate only, never "
            "a verified event; published_ts is 00:00 IST when published_time_known is False) "
            "and bullion_ref_daily.parquet (D35; IBJA gold/silver reference rates, INR per "
            "gram, from Jul 2026)."
        ),
    },
    {
        "version": "1.6.0",
        "date": "2026-11-18",
        "changes": (
            "No existing column changes meaning. MORE ROWS: NSE symbol changes are now "
            "followed (DECISIONS D42, config former_symbols), so NIFTYIETF gains its history "
            "before 2023-12-20 (as ICICINIFTY), MON100 before 2021-06-16 (as N100) and "
            "SILVERBEES before 2022-05-10 (as NETFSILVER); re-run results using these funds "
            "before those dates. 18 more domestic control ETFs (R20, D42; group control). "
            "New etf_day column usdinr_ref: FBIL USD/INR reference rate dated the same day "
            "(published about 13:00-13:30 IST, before the close), empty if missing, never "
            "carried forward (D43). New file fx_ref_daily.parquet: all FBIL reference rates, "
            "rupees per one unit of the foreign currency. fv_method stays nav_only until free "
            "futures bars can be fetched; when they can, nights with a possible futures "
            "contract switch fall back to nav_only (D43)."
        ),
    },
]


@dataclass(frozen=True)
class ColumnDoc:
    """What one exported column is. ``units`` and ``formula`` may be empty."""

    dtype: str  # string | float | int | date | timestamp | bool (the store's logical types)
    definition: str
    units: str = ""
    formula: str = ""
    source: str = ""


def _c(
    dtype: str, definition: str, units: str = "", formula: str = "", source: str = ""
) -> ColumnDoc:
    return ColumnDoc(dtype, definition, units, formula, source)


_PRICE_SRC = "NSE bhavcopy (prices_daily)"
_NAV_SRC = "AMFI NAV (nav_daily), adjusted to today's unit scale"
_FV_SRC = "nav_known_at_close x futures x FX (D10); inputs Bloomberg, else Yahoo, else none"
_BAND_SRC = "bands.compute_band_frame with config/rules.yaml"
_OFF_SRC = "NSE security master PricRg (band_official), session = next NSE day (D4)"

# Order here is the column order of the table.
ETF_DAY_COLUMNS: dict[str, ColumnDoc] = {
    # ---- keys and labels -------------------------------------------------------------
    "symbol": _c("string", "NSE trading symbol.", source="config/etfs.yaml"),
    "date": _c("date", "NSE trading day (IST calendar date).", "date", source=_PRICE_SRC),
    "underlying_market": _c(
        "string", "Market of the holdings: US, HK or IN.", source="config/etfs.yaml"
    ),
    "asset_class": _c(
        "string", "equity, gold or silver (decides the band shape).", source="config/etfs.yaml"
    ),
    "group_label": _c(
        "string",
        "Hypothesis label (blocked, unclear, possibly_open, control, bullion). Not a fact: the units data decides.",
        source="config/etfs.yaml",
    ),
    # ---- prices ------------------------------------------------------------------------
    "open": _c(
        "float",
        "Opening price, adjusted to today's unit scale.",
        "rupees per unit",
        "open_raw x price_adj_factor",
        _PRICE_SRC,
    ),
    "high": _c(
        "float", "Day high, adjusted.", "rupees per unit", "high_raw x price_adj_factor", _PRICE_SRC
    ),
    "low": _c(
        "float", "Day low, adjusted.", "rupees per unit", "low_raw x price_adj_factor", _PRICE_SRC
    ),
    "close": _c(
        "float",
        "Official NSE close = volume-weighted average of the last 30 minutes (D3), adjusted. NOT the last trade.",
        "rupees per unit",
        "close_raw x price_adj_factor",
        _PRICE_SRC,
    ),
    "last": _c(
        "float",
        "Price of the very last trade (LTP), adjusted.",
        "rupees per unit",
        "last_raw x price_adj_factor",
        _PRICE_SRC,
    ),
    "avg_price": _c(
        "float",
        "Volume-weighted average price of the whole day, adjusted.",
        "rupees per unit",
        "avg_price_raw x price_adj_factor",
        _PRICE_SRC,
    ),
    "prev_close": _c(
        "float",
        "Previous official close as published by NSE, adjusted with the factor of the previous session. The base of the prev_close band regime.",
        "rupees per unit",
        "NSE PREV_CLOSE x factor(previous session)",
        _PRICE_SRC,
    ),
    "close_raw": _c(
        "float", "Close exactly as published (unadjusted).", "rupees per unit", "", _PRICE_SRC
    ),
    "high_raw": _c("float", "Day high exactly as published.", "rupees per unit", "", _PRICE_SRC),
    "low_raw": _c("float", "Day low exactly as published.", "rupees per unit", "", _PRICE_SRC),
    "last_raw": _c("float", "Last trade exactly as published.", "rupees per unit", "", _PRICE_SRC),
    "price_adj_factor": _c(
        "float",
        "Multiplier that puts the price of this date on today's unit scale (1 unless a split happened later; MON100 10:1 split 2021-06-17 gives 0.1 before it).",
        "ratio",
        "corporate_actions.adjustment_factor(kind='price')",
        "config/corporate_actions.yaml",
    ),
    "volume": _c("int", "Units traded on NSE.", "units", "", _PRICE_SRC),
    "value": _c("float", "Traded value on NSE.", "rupees", "", _PRICE_SRC),
    "trades": _c("int", "Number of trades on NSE.", "count", "", _PRICE_SRC),
    "deliv_qty": _c("int", "Units taken as delivery.", "units", "", _PRICE_SRC),
    "deliv_pct": _c(
        "float",
        "Delivered units as a percent of units traded, as published.",
        "percent 0-100",
        "",
        _PRICE_SRC,
    ),
    "deliv_share": _c(
        "float", "Delivered share as a fraction.", "fraction 0-1", "deliv_pct / 100", _PRICE_SRC
    ),
    "avg_trade": _c(
        "float", "Average rupees per trade.", "rupees per trade", "value / trades", _PRICE_SRC
    ),
    "source_file": _c(
        "string",
        "Raw-store path of the NSE price file this row came from.",
        source="prices_daily.source_file",
    ),
    # ---- NAV timing (R3, D10) -----------------------------------------------------------
    "nav_same_date": _c(
        "float",
        "NAV dated t (the one the press quotes). NaN when AMFI has no NAV for t (US holidays at Mirae, D-note 13 Nov). Not known at the Indian close for US funds.",
        "rupees per unit",
        "",
        _NAV_SRC,
    ),
    "nav_known_at_close": _c(
        "float",
        "Freshest NAV whose underlying market had closed before 15:30 IST on t. For US funds normally the NAV dated t-1.",
        "rupees per unit",
        "latest NAV d with d <= t and underlying close(u(d)) <= India close on t",
        _NAV_SRC,
    ),
    "nav_known": _c(
        "float",
        "Alias of nav_known_at_close (BLUEPRINT name).",
        "rupees per unit",
        "= nav_known_at_close",
        _NAV_SRC,
    ),
    "nav_known_date": _c("date", "Date of the NAV in nav_known_at_close.", "date", "", _NAV_SRC),
    "nav_known_underlying_date": _c(
        "date",
        "Underlying-market session whose close is embedded in nav_known_at_close (the 'a' in fair value).",
        "date",
        "",
        "calendars",
    ),
    "nav_publication_lag_days": _c(
        "int",
        "Calendar days from nav_known_date to t. Large values mean NAVs are missing for a stretch.",
        "days",
        "t - nav_known_date",
        "calendars",
    ),
    "nav_t2_date": _c(
        "date",
        "Day before the previous Indian session (old band base date): normally the session two before t; a NAV the fund dated on a non-session day in between counts as a day (31 March year-end NAV, D38).",
        "date",
        "",
        "calendars",
    ),
    "nav_t2_nav_date": _c(
        "date",
        "Date of the NAV actually used for nav_t2. Equals nav_t2_date unless the fund published no NAV that day (Mirae skips US holidays), then the latest earlier NAV (at most 5 calendar days older).",
        "date",
        "",
        _NAV_SRC,
    ),
    "nav_t2": _c(
        "float",
        "NAV for the Indian session two trading days before t (the NAV dated nav_t2_nav_date; the latest earlier NAV when none is dated exactly then). Base of the pre-7-Sep-2026 band.",
        "rupees per unit",
        "",
        _NAV_SRC,
    ),
    "nav_t1": _c(
        "float",
        "NAV dated the previous Indian trading day. Base of the planned nav_t1 band (from 2027-04-01).",
        "rupees per unit",
        "",
        _NAV_SRC,
    ),
    "nav_adj_factor": _c(
        "float",
        "Multiplier putting NAVs dated t on today's unit scale (differs from price_adj_factor between the price and NAV ex-dates).",
        "ratio",
        "corporate_actions.adjustment_factor(kind='nav')",
        "config/corporate_actions.yaml",
    ),
    "nav_same_source_file": _c(
        "string", "Raw file of nav_same_date.", source="nav_daily.source_file"
    ),
    "nav_known_source_file": _c(
        "string", "Raw file of nav_known_at_close.", source="nav_daily.source_file"
    ),
    # ---- fair value (D10) ------------------------------------------------------------------
    "fv": _c(
        "float",
        "Fair value: the known NAV moved to 15:30 IST with the index future and the rupee rate (beta 1). Equals NAV when inputs are missing (fv_method says so).",
        "rupees per unit",
        "nav_known_at_close x fv_futures_ratio x fv_fx_ratio",
        _FV_SRC,
    ),
    "fv_method": _c(
        "string",
        "bloomberg | free_futures | nav_only | no_nav. nav_only: a needed input was missing (or the fund needs none, e.g. domestic).",
        source="fair_value.py",
    ),
    "fv_futures_ratio": _c(
        "float",
        "F_t[15:30 IST] / F[US close of a]. NaN when not applied.",
        "ratio",
        "fv_in_futures_t / fv_in_futures_a",
        _FV_SRC,
    ),
    "fv_fx_ratio": _c(
        "float",
        "S_t[15:30 IST] / S_a. NaN when not applied.",
        "ratio",
        "fv_in_fx_t / fv_in_fx_a",
        _FV_SRC,
    ),
    "fv_in_nav_a": _c("float", "NAV used (echo of nav_known_at_close).", "rupees per unit", "", ""),
    "fv_in_futures_t": _c(
        "float",
        "Index future at 15:30 IST on t.",
        "index points",
        "",
        "Bloomberg (NQ1/ES1) or Yahoo (NQ=F/ES=F)",
    ),
    "fv_in_futures_a": _c(
        "float",
        "Index future at the underlying close of nav_known_underlying_date.",
        "index points",
        "",
        "Bloomberg or Yahoo",
    ),
    "fv_in_fx_t": _c(
        "float", "Rupees per USD/HKD at 15:30 IST on t.", "rupees", "", "Bloomberg or Yahoo"
    ),
    "fv_in_fx_a": _c(
        "float",
        "Rupees per USD/HKD at the NAV time of nav_known_underlying_date (approximated by the rate at that market's close; NAV-time rate unverified, D10).",
        "rupees",
        "",
        "Bloomberg or Yahoo",
    ),
    "fv_input_files": _c(
        "string",
        "Raw files the four fair-value inputs came from, separated by ';'.",
        source="lineage",
    ),
    # ---- beta fair value (R12) -------------------------------------------------------------
    "fv_beta": _c(
        "float",
        "Fair value with the future's log return scaled by the fund's beta. Equals fv where fv itself fell back to NAV; NaN where an adjustment was due but no beta exists.",
        "rupees per unit",
        "nav_a x (F_t/F_a)^beta x S_t/S_a",
        "features/beta.py",
    ),
    "fv_beta_value": _c(
        "float",
        "Beta: slope of NAV log return (net of FX return) on the future's close-to-close log return over the 250 NAV dates before 2026-09-07. NaN if inputs are missing or the fund has no futures term.",
        "ratio",
        "OLS slope with intercept",
        "features/beta.py",
    ),
    "fv_beta_se": _c(
        "float",
        "Standard error of fv_beta_value.",
        "ratio",
        "sqrt(SSR/(n-2)/Sxx)",
        "features/beta.py",
    ),
    "fv_beta_n": _c(
        "int", "Number of NAV dates used in the beta regression.", "count", "", "features/beta.py"
    ),
    "fv_beta_method": _c(
        "string",
        "beta_scaled | no_futures_term (fund has no future, fv_beta = fv) | no_beta (estimation failed, see fv_beta_n).",
        source="build.py",
    ),
    # ---- rupee reference rate (covariate, D43) --------------------------------------------
    "usdinr_ref": _c(
        "float",
        "FBIL USD/INR reference rate for t (the official daily rate many fund houses use for NAV; published about 13:00-13:30 IST, before the NSE close, so known at 15:30 IST). NaN when FBIL has no rate dated t (bank holidays, special sessions, or not yet public: the public feed lags about a week).",
        "rupees per USD",
        "",
        "FBIL (fx_ref_daily)",
    ),
    # ---- premiums ---------------------------------------------------------------------------
    "prem_raw": _c(
        "float",
        "close / nav_same_date - 1. What the press quotes; not synchronous for US funds. Percent as a fraction (1.0 = 100%).",
        "fraction",
        "close / nav_same_date - 1",
        "derived",
    ),
    "prem_nav_known": _c(
        "float",
        "close / nav_known_at_close - 1.",
        "fraction",
        "close / nav_known_at_close - 1",
        "derived",
    ),
    "prem_known": _c(
        "float",
        "Alias of prem_nav_known (BLUEPRINT name).",
        "fraction",
        "= prem_nav_known",
        "derived",
    ),
    "prem_band_ref": _c(
        "float",
        "close / nav_t2 - 1: what the pre-Sep-2026 band constrained.",
        "fraction",
        "close / nav_t2 - 1",
        "derived",
    ),
    "prem_fv": _c("float", "close / fv - 1.", "fraction", "close / fv - 1", "derived"),
    "log_prem_fv": _c(
        "float",
        "ln(close / fv). Main dependent variable of the paper.",
        "log points",
        "ln(close / fv)",
        "derived",
    ),
    "pi": _c(
        "float", "Alias of log_prem_fv (BLUEPRINT name).", "log points", "= log_prem_fv", "derived"
    ),
    "x_april": _c(
        "float",
        "Excess over the planned April-2027 cap, on the known NAV. From 1 April 2027 the band is "
        "anchored on NAV, so the cap is 1.2 x NAV; this is how far the price sits above it "
        "(positive = price would be above the new cap). MEANING CHANGE in 1.3.0 (was on fv).",
        "log points",
        "ln(close / (1.2 x nav_known_at_close))",
        "derived",
    ),
    "x_april_fv": _c(
        "float",
        "Same excess, but measured against the fair value instead of the known NAV (this was "
        "the meaning of x_april before 1.3.0).",
        "log points",
        "ln(close / (1.2 x fv))",
        "derived",
    ),
    # ---- band regime and computed band -------------------------------------------------------
    "regime": _c(
        "string",
        "Band regime in force on this date from config/rules.yaml: nav_t2, prev_close, nav_t1. (BLUEPRINT's 'regime', the base that reproduces the band, is regime_inferred.)",
        source="config/rules.yaml",
    ),
    "band_base_kind": _c(
        "string",
        "Base the computed band is actually centred on (nav_t2, prev_close, nav_t1). In the nav_t2 regime it is prev_close when the fund published no NAV dated T-2 and NSE fell back to the previous close (through 2025-10-23, D38).",
        source="config/rules.yaml",
    ),
    "band_base_price": _c(
        "float",
        "Base price used for the computed band, current unit scale.",
        "rupees per unit",
        "",
        _BAND_SRC,
    ),
    "band_low_initial": _c(
        "float", "Computed lowest allowed price at the open.", "rupees per unit", "", _BAND_SRC
    ),
    "band_high_initial": _c(
        "float", "Computed highest allowed price at the open.", "rupees per unit", "", _BAND_SRC
    ),
    "band_low_max": _c(
        "float",
        "Computed lowest limit after all widenings (0 for uncapped gold/silver).",
        "rupees per unit",
        "",
        _BAND_SRC,
    ),
    "band_high_max": _c(
        "float",
        "Computed highest limit after all widenings (inf for uncapped).",
        "rupees per unit",
        "",
        _BAND_SRC,
    ),
    "hit_up": _c(
        "bool",
        "Day high reached the initial upper limit minus one tick (R10).",
        "",
        "high >= band_high_initial - tick",
        _BAND_SRC,
    ),
    "hit_up_max": _c(
        "bool",
        "Day high reached the maximum upper limit minus one tick (R10).",
        "",
        "high >= band_high_max - tick",
        _BAND_SRC,
    ),
    "last_at_up": _c(
        "bool",
        "Last trade within 0.1% of the upper limit in force at the end of the day (R10).",
        "",
        "",
        _BAND_SRC,
    ),
    "hit_down": _c(
        "bool",
        "Day low reached the initial lower limit plus one tick.",
        "",
        "low <= band_low_initial + tick",
        _BAND_SRC,
    ),
    "hit_down_max": _c(
        "bool",
        "Day low reached the maximum lower limit plus one tick.",
        "",
        "low <= band_low_max + tick",
        _BAND_SRC,
    ),
    "last_at_down": _c(
        "bool",
        "Last trade within 0.1% of the lower limit in force at the end of the day.",
        "",
        "",
        _BAND_SRC,
    ),
    "widened_up": _c(
        "bool",
        "High above the initial upper limit, so the band must have widened upward.",
        "",
        "high > band_high_initial",
        _BAND_SRC,
    ),
    "widened_down": _c(
        "bool", "Low below the initial lower limit.", "", "low < band_low_initial", _BAND_SRC
    ),
    "widenings_min_up": _c(
        "int", "Fewest upward widenings consistent with the day's high.", "count", "", _BAND_SRC
    ),
    "widenings_min_down": _c(
        "int", "Fewest downward widenings consistent with the day's low.", "count", "", _BAND_SRC
    ),
    "close_at_upper": _c(
        "bool",
        "NSE close within 0.1% of the upper limit in force (weak evidence: the close is a 30-minute average, D3).",
        "",
        "",
        _BAND_SRC,
    ),
    "close_at_lower": _c(
        "bool",
        "NSE close within 0.1% of the lower limit in force (weak evidence).",
        "",
        "",
        _BAND_SRC,
    ),
    # ---- official band (R2) -------------------------------------------------------------------
    "band_low_official": _c(
        "float",
        "Official initial lower price limit for session t from NSE's security master (PricRg).",
        "rupees per unit",
        "",
        _OFF_SRC,
    ),
    "band_high_official": _c(
        "float",
        "Official initial upper price limit for session t (the censoring ceiling).",
        "rupees per unit",
        "",
        _OFF_SRC,
    ),
    "band_low": _c(
        "float",
        "Alias of band_low_official (BLUEPRINT name).",
        "rupees per unit",
        "= band_low_official",
        _OFF_SRC,
    ),
    "band_high": _c(
        "float",
        "Alias of band_high_official (BLUEPRINT name).",
        "rupees per unit",
        "= band_high_official",
        _OFF_SRC,
    ),
    "band_file_date": _c(
        "date",
        "Date in the name of the security-master file the official band came from (the session before t).",
        "date",
        "",
        _OFF_SRC,
    ),
    "band_session_date": _c(
        "date", "Session the official band applies to (equals date).", "date", "", _OFF_SRC
    ),
    "band_source_file": _c(
        "string",
        "Raw security-master file of the official band.",
        source="band_official.source_file",
    ),
    "band_low_diff": _c(
        "float",
        "Computed minus official lower limit.",
        "rupees per unit",
        "band_low_initial - band_low_official",
        "derived",
    ),
    "band_high_diff": _c(
        "float",
        "Computed minus official upper limit.",
        "rupees per unit",
        "band_high_initial - band_high_official",
        "derived",
    ),
    "band_mismatch": _c(
        "bool",
        "Computed and official initial band differ by more than one tick on either side. Null if either is missing.",
        "",
        "",
        "derived",
    ),
    "hit_up_official": _c(
        "bool",
        "Day high reached the OFFICIAL initial upper limit minus one tick.",
        "",
        "high >= band_high_official - tick",
        "derived",
    ),
    "hit_down_official": _c(
        "bool",
        "Day low reached the OFFICIAL initial lower limit plus one tick.",
        "",
        "low <= band_low_official + tick",
        "derived",
    ),
    "cap_pi": _c(
        "float",
        "Ceiling on the log premium: how high pi can go before the official upper limit stops the price (R10). Moves daily under the NAV-anchored band.",
        "log points",
        "ln(band_high_official / fv)",
        "derived",
    ),
    "cap_pi_computed": _c(
        "float",
        "Same using the computed initial upper limit; available before the security-master files begin.",
        "log points",
        "ln(band_high_initial / fv)",
        "derived",
    ),
    "regime_inferred": _c(
        "string",
        "Which base reproduces the middle of the official band within one tick: nav_t2, prev_close, nav_t1, ambiguous, other. Before the first security-master file: nav_t2_consistent (no trade outside 0.8-1.2 x nav_t2) or not_nav_t2. unknown if no data.",
        source="bands.infer_regime_from_band / price test",
    ),
    "regime_inferred_method": _c(
        "string",
        "official_band or price_test (how regime_inferred was obtained).",
        source="build.py",
    ),
    # ---- units (R1, R11) --------------------------------------------------------------------------
    "units_outstanding": _c(
        "int",
        "Units issued per the security master (IssdCptl), from the latest file dated on or before t.",
        "units",
        "",
        "units_daily",
    ),
    "units_file_date": _c(
        "date",
        "Date of the security-master file that units_outstanding came from.",
        "date",
        "",
        "units_daily",
    ),
    "units_stale_days": _c(
        "int",
        "Calendar days between date and units_file_date.",
        "days",
        "date - units_file_date",
        "derived",
    ),
    "units_source_file": _c(
        "string", "Raw file of units_outstanding.", source="units_daily.source_file"
    ),
    "units": _c(
        "float",
        "units_outstanding where available, else the latest monthly AUM/NAV estimate within 45 days (R11).",
        "units",
        "",
        "units_daily / units_monthly",
    ),
    "units_source": _c(
        "string", "security_master or monthly_aum_nav: where units came from.", source="build.py"
    ),
    "units_change": _c(
        "int",
        "Change in units outstanding seen in the security-master file(s) dated since the previous row up to this row; 0 if none; null before the first file. A change over a long file gap lands on the date of the file that showed it.",
        "units",
        "",
        "features/units.py",
    ),
    "creation_units_change": _c(
        "float",
        "units_change / creation_unit_size: positive = creation, negative = redemption. Null if the size is not configured.",
        "creation units",
        "units_change / creation_unit_size",
        "features/units.py",
    ),
    "creation_event": _c(
        "float",
        "Alias of creation_units_change (BLUEPRINT name).",
        "creation units",
        "= creation_units_change",
        "features/units.py",
    ),
    "creation_multiple_ok": _c(
        "bool",
        "True if the change is a whole number of creation units (supports the size and the reading of IssdCptl, D7).",
        "",
        "",
        "features/units.py",
    ),
    "units_change_days_since_prev": _c(
        "int",
        "Longest gap in days between the file that showed a change and the previous file; large = not a one-day event.",
        "days",
        "",
        "features/units.py",
    ),
    # ---- BSE and turnover (R9) --------------------------------------------------------------------
    "bse_close": _c(
        "float",
        "BSE official close on the same date as the NSE row, on the same unit scale as close "
        "(adjusted for unit splits). Empty when BSE has no row for that fund and day.",
        "rupees per unit",
        "bse_prices.close x price_adj_factor",
        "BSE daily bhavcopy (raw bse_bhavcopy), matched by ISIN",
    ),
    "bse_volume": _c(
        "int",
        "Units traded on BSE that day (not split-adjusted, like volume). Empty when BSE has no "
        "row for that fund and day.",
        "units",
        "",
        "BSE daily bhavcopy (raw bse_bhavcopy), matched by ISIN",
    ),
    "bse_value": _c(
        "float",
        "Rupees traded on BSE that day. Empty when BSE has no row.",
        "rupees",
        "",
        "BSE daily bhavcopy (raw bse_bhavcopy), matched by ISIN",
    ),
    "bse_trades": _c(
        "int",
        "Number of trades on BSE that day. Empty when BSE has no row.",
        "count",
        "",
        "BSE daily bhavcopy (raw bse_bhavcopy), matched by ISIN",
    ),
    "turnover_nse": _c(
        "float", "Units traded on NSE / units.", "fraction of units", "volume / units", "derived"
    ),
    "turnover": _c(
        "float",
        "(NSE + BSE units traded) / units. CHANGED in schema 1.1.0 (was NSE only). A day with no "
        "BSE row counts BSE as zero, so it equals turnover_nse and turnover_is_nse_only is true.",
        "fraction of units",
        "(volume + bse_volume) / units; bse_volume treated as 0 when empty",
        "derived",
    ),
    "turnover_is_nse_only": _c(
        "bool",
        "True when turnover has no BSE part because there is no BSE row for the fund and day "
        "(understated if the fund really traded on BSE; R9).",
        "",
        "bse_volume is null",
        "derived",
    ),
    # ---- feeders (R14) ------------------------------------------------------------------------------
    "feeder_units": _c(
        "float",
        "ETF units held by feeder funds, summed over feeders. Each feeder contributes its latest monthly disclosure published on or before t (month end + 10 days, so no look-ahead); a blank month keeps the previous figure. Null before any feeder has reported or when there is no feeder data.",
        "units",
        "manual_fof.feeder_units_daily(lag_days=10)",
        "fof_monthly",
    ),
    "feeder_units_asof": _c(
        "date",
        "Oldest month-end among the feeder figures used (how stale the sum is).",
        "date",
        "",
        "fof_monthly",
    ),
    "float_net": _c(
        "float",
        "Value of the units not held by feeders at the known NAV.",
        "rupees",
        "(units - feeder_units) x nav_known_at_close",
        "derived",
    ),
    # ---- paper variables --------------------------------------------------------------------------------
    "rationed": _c(
        "bool",
        "pi > 0.02 and no creation (creation_units_change > 0) in the previous 20 rows. Threshold 0.02 is the paper's default; vary it in analysis.",
        "",
        "",
        "derived",
    ),
    "excess_rupees": _c(
        "float",
        "Rupees paid above the old-rule ceiling: units traded x max(0, avg_price - 1.2 x nav_t2).",
        "rupees",
        "volume x max(0, avg_price - 1.2 x nav_t2)",
        "derived",
    ),
    # ---- returns --------------------------------------------------------------------------------------------
    "ret_close_1": _c(
        "float",
        "Log price return from the previous row to t.",
        "log points",
        "ln(close_t / close_t-1)",
        "derived",
    ),
    "ret_close_5": _c(
        "float", "Log price return over 5 rows.", "log points", "ln(close_t / close_t-5)", "derived"
    ),
    "ret_own_1": _c(
        "float",
        "Fund's log price return of the previous session (known before t).",
        "log points",
        "ret_close_1 shifted by one row",
        "derived",
    ),
    "ret_own_5": _c(
        "float",
        "Fund's log price return over the 5 sessions ending the previous session.",
        "log points",
        "ret_close_5 shifted by one row",
        "derived",
    ),
    "ret_nav_1": _c(
        "float",
        "One-row log return of nav_known_at_close (zero when no new NAV arrived).",
        "log points",
        "",
        "derived",
    ),
    "ret_fv_1": _c("float", "One-row log return of fv.", "log points", "", "derived"),
    # ---- attention (R15) --------------------------------------------------------------------------------
    "attention": _c(
        "float",
        "Google Trends search interest in India for this ETF's configured terms on the same calendar day (0-100 scale; each term is scaled to its own maximum). Empty when any configured term has no number that day, and for ETFs with no term configured. Includes searches after the 15:30 close, so it is not known at the close.",
        "index 0-100",
        "mean of the ETF's terms (config/trends_terms.yaml), only when every term has a value",
        "trends_daily (Google Trends, pulled by hand)",
    ),
    # ---- data-quality flags (SPEC 12). True = a human should look; never dropped. ----------------------
    "dq_duplicate_row": _c(
        "bool", "Another row has the same symbol and date.", source="features/quality.py"
    ),
    "dq_prem_fv_extreme": _c("bool", "|prem_fv| > 3.0 (300%).", source="features/quality.py"),
    "dq_prem_fv_jump": _c(
        "bool",
        "prem_fv changed by more than 0.5 (50 points) from the previous row.",
        source="features/quality.py",
    ),
    "dq_fv_far_from_nav": _c(
        "bool",
        "fv differs from the known NAV by more than 5% without a futures move above 5%.",
        source="features/quality.py",
    ),
    "dq_negative_units": _c("bool", "units_outstanding below zero.", source="features/quality.py"),
    "dq_missing_close": _c("bool", "close is missing.", source="features/quality.py"),
    "dq_missing_nav_known": _c(
        "bool", "nav_known_at_close is missing.", source="features/quality.py"
    ),
    "dq_nav_stale": _c(
        "bool", "nav_publication_lag_days above 5 or negative.", source="features/quality.py"
    ),
    "dq_nav_same_date_missing": _c(
        "bool",
        "No NAV dated t in AMFI (for example Mirae on US holidays); prem_raw is empty and NAV is carried forward for alignment.",
        source="build.py",
    ),
    "dq_nav_t2_missing": _c(
        "bool",
        "No NAV usable for the T-2 session (none dated then and none within 5 days before), so the old band base and prem_band_ref are empty.",
        source="build.py",
    ),
    "dq_units_aum_mismatch": _c(
        "bool",
        "For the month ending on the month-end of t: AMC AUM / NAV differs from the exchange unit count by more than 2% (data quality only, not the valve definition).",
        source="features/units.py",
    ),
}

# Tables exported as copies of the clean store, plus two derived ones.
UNITS_DAILY_EXPORT_DOCS: dict[str, str] = {
    "symbol": "NSE trading symbol",
    "date": "Date in the security-master file name",
    "units_outstanding": "Units issued (IssdCptl)",
    "units_prev": "Units at the previous file for this symbol",
    "units_prev_date": "Date of the previous file",
    "days_since_prev": "Calendar days since the previous file",
    "units_change": "units_outstanding - units_prev",
    "creation_unit_size": "Units per creation basket (config)",
    "creation_units_change": "units_change / creation_unit_size",
    "creation_multiple_ok": "True if the change is a whole number of creation units",
    "event_type": "creation, redemption, or empty when unchanged",
    "source_file": "Raw security-master file",
}
UNIT_EVENT_DOCS: dict[str, str] = {
    "symbol": "NSE trading symbol",
    "date": "Date of the file that showed the change",
    "prev_date": "Date of the previous file",
    "units_prev": "Units before",
    "units_new": "Units after",
    "units_change": "units_new - units_prev",
    "creation_units_change": "units_change / creation_unit_size",
    "creation_multiple_ok": "True if a whole number of creation units",
    "event_type": "creation or redemption",
    "days_since_prev": "Calendar days between the two files",
    "source_file": "Raw security-master file",
}


def etf_day_schema() -> TableSchema:
    """The full ``etf_day`` table schema generated from ``ETF_DAY_COLUMNS``."""
    cols = [Column(name, doc.dtype, doc.definition) for name, doc in ETF_DAY_COLUMNS.items()]  # type: ignore[arg-type]
    return TableSchema(
        name="etf_day",
        primary_key=("symbol", "date"),
        columns=tuple(cols),
        doc="One row per ETF per NSE trading day with a price. Rebuilt from scratch by etfp build.",
    )


@contextlib.contextmanager
def etf_day_schema_registered() -> Iterator[TableSchema]:
    """Register the full ``etf_day`` schema for the duration of the block, then restore.

    Restoring keeps the open-ended placeholder in ``etfp.store`` intact for everyone else.
    """
    previous = SCHEMAS["etf_day"]
    schema = etf_day_schema()
    store_mod.register_schema(schema, replace=True)
    try:
        yield schema
    finally:
        store_mod.register_schema(previous, replace=True)


# ==========================================================================
# The export
# ==========================================================================
COPIED_TABLES = (
    "inav_snapshots",
    "units_monthly",
    "fof_monthly",
    "trends_daily",
    "limit_day",
    "slb_eligible",
    "slb_daily",
    "bse_prices",
    "feeder_nav_daily",
    "china_etf_daily",
    "valve_monthly",
    "notices",
    "bullion_ref_daily",
    "fx_ref_daily",
)
"""Clean tables copied as they are (their parquet files already have the registered types)."""
MARKET_HOURS = (time(9, 10), time(15, 35))
"""Snapshots inside this IST window count as intraday coverage (the recorder runs 09:15-15:30)."""
INTRADAY_GAP_MINUTES = 30


def _git_info(repo_dir: Path) -> dict[str, Any]:
    """Commit hash and dirty flag. No repo or no commit gives ``'uncommitted'``."""

    def run(*args: str) -> subprocess.CompletedProcess[str] | None:
        try:
            return subprocess.run(
                ["git", *args],
                cwd=repo_dir,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None

    head = run("rev-parse", "HEAD")
    if head is None or head.returncode != 0:
        return {"commit": "uncommitted", "dirty": True}
    status = run("status", "--porcelain")
    dirty = True if status is None or status.returncode != 0 else bool(status.stdout.strip())
    return {"commit": head.stdout.strip(), "dirty": dirty}


def parse_open_verify_items(decisions_path: Path) -> list[dict[str, str]]:
    """Rows of the "Open VERIFY items" table in docs/DECISIONS.md (empty list if absent)."""
    if not decisions_path.exists():
        return []
    items: list[dict[str, str]] = []
    in_section = in_table = False
    for line in decisions_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            in_section = line.strip().lower().startswith("## open verify items")
            in_table = False
            continue
        if not in_section:
            continue
        if line.startswith("|"):
            # a pipe inside a cell is written \| in markdown
            cells = [
                c.strip().replace("\\|", "|")
                for c in re.split(r"(?<!\\)\|", line.strip().strip("|"))
            ]
            if not in_table:  # header row
                in_table = True
                continue
            if set("".join(cells)) <= set("-: "):  # the |---|---| separator
                continue
            if len(cells) >= 3:
                items.append({"item": cells[0], "where_it_matters": cells[1], "status": cells[2]})
        elif in_table and line.strip():
            in_table = False
    return items


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _infer_type(series: pd.Series) -> str:
    dtype = str(series.dtype).lower()
    if dtype.startswith(("int", "uint")):
        return "int"
    if dtype.startswith("float"):
        return "float"
    if dtype.startswith("bool"):
        return "bool"
    if "datetime" in dtype:
        return "timestamp"
    sample = series.dropna()
    if len(sample) and isinstance(sample.iloc[0], date):
        return "date"
    return "string"


def _rows(table: str, df: pd.DataFrame) -> list[dict[str, str]]:
    """Column documentation rows for one exported table."""
    if table == "etf_day":
        return [
            {
                "name": n,
                "type": d.dtype,
                "definition": d.definition,
                "units": d.units,
                "formula": d.formula,
                "source": d.source,
            }
            for n, d in ETF_DAY_COLUMNS.items()
        ]
    if table in SCHEMAS and table != "units_daily":  # exported units_daily has extra columns
        return [
            {
                "name": c.name,
                "type": c.dtype,
                "definition": c.doc,
                "units": "",
                "formula": "",
                "source": "features/valve.py (D33); see source_file"
                if table == "valve_monthly"
                else "clean table; see source_file",
            }
            for c in SCHEMAS[table].columns
        ]
    docs = {"units_daily": UNITS_DAILY_EXPORT_DOCS, "unit_events": UNIT_EVENT_DOCS}[table]
    return [
        {
            "name": c,
            "type": _infer_type(df[c]),
            "definition": docs.get(c, ""),
            "units": "",
            "formula": "",
            "source": "features/units.py",
        }
        for c in df.columns
    ]


def _schema_json(frames: dict[str, pd.DataFrame], build_time: datetime) -> dict[str, Any]:
    keys = {
        "etf_day": ["symbol", "date"],
        "units_daily": ["symbol", "date"],
        "unit_events": ["symbol", "date"],
    }
    tables = {}
    for name, df in frames.items():
        pk = keys.get(name) or list(SCHEMAS[name].primary_key)
        tables[name] = {"primary_key": pk, "columns": _rows(name, df)}
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at_ist": build_time.isoformat(timespec="seconds"),
        "changelog": SCHEMA_CHANGELOG,
        "conventions": (
            "Dates are ISO calendar dates in India. Timestamps are timezone-aware (IST). Prices, "
            "NAV and values are rupees, on today's unit scale where the column says adjusted. "
            "Premiums are fractions (1.0 = 100%); log columns are natural logs. Empty = unknown, "
            "never zero. dq_* flags mean 'look at this row'; no row is dropped or corrected."
        ),
        "tables": tables,
    }


def _md_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _dictionary_md(schema: dict[str, Any]) -> str:
    lines = [
        "# Data dictionary",
        "",
        f"Schema version **{schema['schema_version']}**, generated {schema['generated_at_ist']}.",
        "",
        schema["conventions"],
        "",
        "## Changelog",
        "",
    ]
    for entry in schema["changelog"]:
        lines.append(f"- **{entry['version']}** ({entry['date']}): {entry['changes']}")
    for name, table in schema["tables"].items():
        lines += ["", f"## {name}", "", f"Primary key: {', '.join(table['primary_key'])}", ""]
        lines.append("| column | type | definition | units | formula | source |")
        lines.append("|---|---|---|---|---|---|")
        for c in table["columns"]:
            cells = [c["name"], c["type"], c["definition"], c["units"], c["formula"], c["source"]]
            lines.append("| " + " | ".join(_md_cell(x) for x in cells) + " |")
    return "\n".join(lines) + "\n"


def _date_range(values: pd.Series) -> dict[str, str | None]:
    values = values.dropna()
    if values.empty:
        return {"first": None, "last": None}
    first, last = min(values), max(values)
    iso = lambda v: v.isoformat() if hasattr(v, "isoformat") else str(v)  # noqa: E731
    return {"first": iso(first), "last": iso(last)}


def recorder_gaps(snapshots: pd.DataFrame) -> dict[str, Any]:
    """Where the intraday recorder has no data (D16: the laptop being off is expected).

    * ``sessions_without_intraday_snapshots``: NSE sessions between the first and last
      snapshot with no snapshot inside market hours.
    * ``intraday_gaps``: days where two consecutive in-hours snapshot times are more than
      30 minutes apart.
    """
    if snapshots.empty:
        return {
            "snapshot_rows": 0,
            "first_day": None,
            "last_day": None,
            "rows_by_source": {},
            "sessions_without_intraday_snapshots": [],
            "intraday_gaps": [],
        }
    ts = snapshots["ts"].dt.tz_convert(IST)
    days = ts.dt.date
    in_hours = ts.dt.time.map(lambda t: MARKET_HOURS[0] <= t <= MARKET_HOURS[1])
    hours_days = set(days[in_hours])
    first, last = min(days), max(days)
    try:
        expected = calendars.sessions("IN", first, last)
    except ValueError:
        expected = []
    missing = [d.isoformat() for d in expected if d not in hours_days]
    gaps = []
    for day, grp in ts[in_hours].groupby(days[in_hours]):
        stamps = sorted(set(grp))
        if len(stamps) < 2:
            continue
        worst = max((b - a).total_seconds() / 60 for a, b in pairwise(stamps))
        if worst > INTRADAY_GAP_MINUTES:
            gaps.append({"date": day.isoformat(), "max_gap_minutes": round(worst, 1)})
    return {
        "snapshot_rows": len(snapshots),
        "first_day": first.isoformat(),
        "last_day": last.isoformat(),
        "rows_by_source": {str(k): int(v) for k, v in snapshots["source"].value_counts().items()},
        "sessions_without_intraday_snapshots": missing,
        "intraday_gaps": gaps,
    }


def _atomic_text(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def export_research(
    store: Store,
    out_root: Path | None = None,
    *,
    root: Path | None = None,
    now: datetime | None = None,
    repo_dir: Path | None = None,
    decisions_path: Path | None = None,
) -> Path:
    """Write ``<out_root>/<YYYY-MM-DD>/`` for the research side and point LATEST.txt at it.

    ``now`` (timezone-aware) is the build time; its India date names the folder. The folder is
    written under a temporary name and swapped in at the end, so a failed export never leaves
    a half-written folder and ``LATEST.txt`` still names the previous good one.
    """
    from etfp import build  # lazy: build imports this module (it also imports etfp.clean
    # and etfp.features.limit_days, which register the bse/slb/limit_day schemas)

    root = build.resolve_root(store, root)
    cfg = build.load_config(root)
    now = now.astimezone(IST) if now is not None else now_ist()
    out_root = Path(out_root) if out_root is not None else root / "exports" / "research"
    repo_dir = repo_dir if repo_dir is not None else paths.project_root()
    decisions_path = decisions_path or (repo_dir / "docs" / "DECISIONS.md")

    if not store.has_table("etf_day"):
        raise FileNotFoundError("etf_day has not been built; run `etfp build` before exporting")
    with etf_day_schema_registered():
        etf_day = store.read_table("etf_day")

    changes, unit_events = build.build_unit_tables(store, cfg.universe)
    frames: dict[str, pd.DataFrame] = {"etf_day": etf_day}
    for name in COPIED_TABLES:
        if store.has_table(name):
            frames[name] = store.read_table(name)
    frames["events"] = build.build_events(store, universe=cfg.universe)
    frames["units_daily"] = changes
    frames["unit_events"] = unit_events

    out_root.mkdir(parents=True, exist_ok=True)
    folder = out_root / now.date().isoformat()
    tmp = Path(tempfile.mkdtemp(prefix=f".{folder.name}.", dir=out_root))
    try:
        files: dict[str, dict[str, Any]] = {}

        def record(name: str) -> None:
            path = tmp / name
            files[name] = {"sha256": _sha256(path), "bytes": path.stat().st_size}

        for name, df in frames.items():
            target = tmp / f"{name}.parquet"
            if name in COPIED_TABLES or name == "etf_day":
                shutil.copyfile(store.parquet_path(name), target)  # already typed on disk
            else:
                df.to_parquet(target, index=False, compression="zstd")
            record(target.name)

        schema = _schema_json(frames, now)
        (tmp / "schema.json").write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
        record("schema.json")
        (tmp / "DATA_DICTIONARY.md").write_text(_dictionary_md(schema), encoding="utf-8")
        record("DATA_DICTIONARY.md")

        manifest = _manifest(store, cfg, frames, now, repo_dir, decisions_path, folder, files)
        (tmp / "manifest.json").write_text(
            json.dumps(manifest, indent=2, default=str) + "\n", encoding="utf-8"
        )

        old = None
        if folder.exists():
            old = out_root / f".{folder.name}.old.{os.getpid()}"
            folder.rename(old)
        tmp.rename(folder)
        if old is not None:
            shutil.rmtree(old, ignore_errors=True)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    _atomic_text(out_root / "LATEST.txt", str(folder.resolve()) + "\n")
    logger.info("research export written to %s", folder)
    return folder


def _limit_day_coverage(ld: pd.DataFrame | None) -> dict[str, Any] | None:
    """Which limit days have minute bars, snapshots or no intraday data (R13 asks for this)."""
    if ld is None:
        return None
    return {
        "rows": len(ld),
        "by_obs_source": {str(k): int(v) for k, v in ld["obs_source"].value_counts().items()},
        "days_with_bloomberg_minute_bars": sorted(
            ld.loc[ld["obs_source"] == "bloomberg_1m", "date"].astype(str).unique()
        ),
        "days_without_intraday": sorted(
            ld.loc[ld["obs_source"] == "none", "date"].astype(str).unique()
        ),
    }


def write_valve_status(store: Store, out_path: Path | None = None) -> Path:
    """Regenerate docs/VALVE_STATUS.md from the built ``valve_monthly`` table (M5, D33).

    Uses only the table (no clock, no git), so the same build always gives the same file.
    """
    from etfp import build  # lazy, as in export_research (registers the valve schema)
    from etfp.features import valve

    if not store.has_table(valve.TABLE):
        raise FileNotFoundError("valve_monthly has not been built; run `etfp build` first")
    root = build.resolve_root(store)
    cfg = build.load_config(root)
    tracked = [e.symbol for e in cfg.universe.etfs if e.tracked]
    text = valve.valve_status_markdown(store.read_table(valve.TABLE), tracked=tracked)
    out_path = Path(out_path) if out_path is not None else root / "docs" / "VALVE_STATUS.md"
    _atomic_text(out_path, text)
    return out_path


def _valve_counts(vm: pd.DataFrame | None) -> dict[str, dict[str, int]] | None:
    """valve_status counts per symbol (M5), so the research side sees coverage at a glance."""
    if vm is None:
        return None
    return {
        str(sym): {str(k): int(v) for k, v in g["valve_status"].value_counts().sort_index().items()}
        for sym, g in vm.groupby("symbol", sort=True)
    }


def _manifest(
    store: Store,
    cfg: Any,
    frames: dict[str, pd.DataFrame],
    now: datetime,
    repo_dir: Path,
    decisions_path: Path,
    folder: Path,
    files: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    from etfp.clean.common import find_gaps
    from etfp.collect import bloomberg
    from etfp.features import quality, units

    etf_day = frames["etf_day"]
    date_cols = {
        "prices_daily": "date",
        "nav_daily": "nav_date",
        "units_daily": "date",
        "band_official": "session_date",
        "units_monthly": "month_end",
        "fof_monthly": "month_end",
        "trends_daily": "date",
    }
    ranges: dict[str, Any] = {"etf_day": _date_range(etf_day["date"])}
    for table, col in date_cols.items():
        if table in frames:
            df = frames[table]
        elif store.has_table(table):
            df = store.read_table(table, columns=[col])
        else:
            continue
        ranges[table] = _date_range(df[col])
    snaps = frames.get("inav_snapshots")
    if snaps is not None and len(snaps):
        ranges["inav_snapshots"] = _date_range(snaps["ts"].dt.tz_convert(IST).dt.date)
    ranges["events"] = _date_range(frames["events"]["date"])

    flag_cols = [c for c in etf_day.columns if c.startswith("dq_")]
    flags = lambda df: {c: int(df[c].fillna(False).astype(bool).sum()) for c in flag_cols}  # noqa: E731
    missing_cols = {c: int(n) for c, n in etf_day.isna().sum().items() if int(n) > 0}
    sessions: dict[str, Any] = {}
    methods: dict[str, Any] = {}
    for sym, grp in etf_day.groupby("symbol", sort=True):
        gaps = find_gaps(list(grp["date"]))
        sessions[str(sym)] = {"count": len(gaps), "first_20": [d.isoformat() for d in gaps[:20]]}
        methods[str(sym)] = {str(k): int(v) for k, v in grp["fv_method"].value_counts().items()}

    imported = bloomberg.import_directory(cfg.raw_root / bloomberg.SOURCE, on_conflict="latest")
    bloomberg_files = [
        {
            "source_file": f.source_file,
            "rows": f.rows,
            "first_date": f.first_date.isoformat(),
            "last_date": f.last_date.isoformat(),
            "sha256": f.sha256,
        }
        for f in imported.files
    ]
    sizes = units.creation_unit_sizes(cfg.universe)
    needed = [e.symbol for e in cfg.universe.etfs if e.symbol in set(etf_day["symbol"])]
    return {
        "schema_version": SCHEMA_VERSION,
        "build_time_ist": now.isoformat(timespec="seconds"),
        "export_dir": str(folder.resolve()),
        "git": _git_info(repo_dir),
        "date_range": ranges,
        "row_counts": {
            "tables": {name: len(df) for name, df in frames.items()},
            "etf_day_by_symbol": {
                str(k): int(v) for k, v in etf_day["symbol"].value_counts().sort_index().items()
            },
        },
        "dq_flag_counts": {
            "all": flags(etf_day),
            "by_symbol": {str(s): flags(g) for s, g in etf_day.groupby("symbol", sort=True)},
        },
        "dq_summary_core": quality.dq_summary(etf_day),
        "missing_etf_days_per_column": {
            "etf_day_rows": len(etf_day),
            "null_counts": missing_cols,
        },
        "missing_nse_sessions_by_symbol": sessions,
        "fv_method_counts": methods,
        "bloomberg_files": bloomberg_files,
        "recorder_gaps": recorder_gaps(frames.get("inav_snapshots", pd.DataFrame())),
        "limit_day": _limit_day_coverage(frames.get("limit_day")),
        "valve_status_counts_by_symbol": _valve_counts(frames.get("valve_monthly")),
        "creation_unit_sizes_missing": units.missing_creation_unit_sizes(needed, sizes),
        "open_verify_items": parse_open_verify_items(decisions_path),
        "files": files,
    }
