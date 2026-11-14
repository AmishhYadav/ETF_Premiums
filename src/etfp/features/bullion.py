"""Bullion comparison group: gold/silver ETFs measured against the metal they hold.

The finance in plain words
--------------------------
International ETFs in this project trade far above NAV because their "creation valve"
is shut: the fund house cannot buy more foreign shares (overseas-investment cap), so
nobody can make new units to meet demand. Gold and silver ETFs have no such cap. They
buy physical metal in India, so when the price runs above NAV a market maker can hand
the fund metal (or cash), get new units, sell them, and pocket the gap. That pulls the
price back to NAV. Their premium should therefore stay near zero and their units
outstanding should grow when demand rises. They are the control group.

Silver is the interesting case. In October 2025 physical silver ran short in India and
the DOMESTIC metal price itself rose well above London price x exchange rate. That is a
premium caused by the underlying being scarce, not by a closed valve. To tell the two
apart we compare three things:

* ``prem_nav``   = close / NAV - 1                       (the usual ETF premium)
* ``prem_metal`` = close / metal_value - 1               (premium over the metal held,
  priced at the Indian reference rate; known at the IBJA PM poll, before NAV is out)
* ``nav_check``  = NAV / metal_value - 1                 (does the official NAV match
  "metal per unit x Indian price"? Large values mean the fund values its metal with a
  different price than IBJA.)

where ``metal_value`` (the "metal-implied NAV") = grams per unit known YESTERDAY x
today's Indian price per gram. Using yesterday's grams per unit avoids peeking at
today's NAV, so ``prem_metal`` is a value someone could have computed at the time.

Grams of metal per unit. Each unit is a claim on a fixed amount of metal, slowly eaten
by the yearly fee (the fund sells a little metal to pay costs). We do not know the exact
figure, so we DERIVE it from history: grams_per_unit = NAV / price per gram, both for the
same date. A stable ratio says the NAV and the reference price agree; its slow downward
drift should be close to the fund's expense ratio.

All functions here are pure: data frames in, data frames or dicts out, no I/O.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

DAYS_PER_YEAR = 365.25


def _by_date(series: pd.Series, name: str) -> pd.Series:
    s = series.copy()
    s.index = pd.Index([pd.Timestamp(d).date() for d in s.index], name="date")
    if not s.index.is_unique:
        dup = s.index[s.index.duplicated()][0]
        raise ValueError(f"{name}: date {dup} appears more than once")
    return s.astype("float64").sort_index()


def implied_metal_per_unit(nav: pd.Series, price_per_gram: pd.Series) -> pd.DataFrame:
    """Grams of metal behind one ETF unit, from NAV and price per gram of the same date.

    ``nav`` and ``price_per_gram`` are indexed by date (rupees per unit, rupees per gram).
    Only dates with both values are returned, with columns date, nav, price_inr_per_gram,
    grams_per_unit. Example: NAV 81.96 and price 10,000 per gram -> 0.008196 g per unit.
    """
    nav_s = _by_date(nav, "nav")
    px = _by_date(price_per_gram, "price_per_gram")
    if (px <= 0).any():
        raise ValueError("price_per_gram: prices must be positive")
    both = pd.concat({"nav": nav_s, "price_inr_per_gram": px}, axis=1, join="inner").dropna()
    both["grams_per_unit"] = both["nav"] / both["price_inr_per_gram"]
    return both.reset_index()


def metal_per_unit_stability(grams: pd.Series) -> dict[str, float]:
    """How steady the derived grams per unit is.

    Returns n, mean, std (sample, ddof=1), cv (= std / mean), min, max,
    max_abs_dev (largest |value / median - 1|) and drift_per_year (slope of
    ln(grams) against time in years, by least squares; about minus the expense ratio).
    std, cv and drift are NaN with fewer than two values (drift also when all dates are
    the same).
    """
    g = _by_date(grams, "grams").dropna()
    n = len(g)
    nan = float("nan")
    if n == 0:
        return dict.fromkeys(
            ("n", "mean", "std", "cv", "min", "max", "max_abs_dev", "drift_per_year"), nan
        ) | {"n": 0.0}
    mean = float(g.mean())
    std = float(g.std(ddof=1)) if n > 1 else nan
    median = float(g.median())
    days = np.array([(d - g.index[0]).days for d in g.index], dtype="float64")
    years = days / DAYS_PER_YEAR
    if n > 1 and years.max() > 0:
        y = np.log(g.to_numpy())
        drift = float(np.polyfit(years, y, 1)[0])
    else:
        drift = nan
    return {
        "n": float(n),
        "mean": mean,
        "std": std,
        "cv": std / mean if n > 1 else nan,
        "min": float(g.min()),
        "max": float(g.max()),
        "max_abs_dev": float((g / median - 1).abs().max()),
        "drift_per_year": drift,
    }


def bullion_premium_frame(day: pd.DataFrame, price_per_gram: pd.Series) -> pd.DataFrame:
    """Daily premium of a bullion ETF over NAV and over the metal it holds.

    ``day`` has one row per trading date with columns ``date``, ``close`` and ``nav``
    (same-date NAV; may be missing) and optionally ``units`` (units outstanding).
    ``price_per_gram`` is the Indian reference price indexed by date.

    Output, one row per date of ``day`` (sorted): the inputs plus
    ``price_inr_per_gram``, ``grams_per_unit`` (NAV / price, same date),
    ``grams_per_unit_prev`` (latest value from an EARLIER date), ``metal_value``
    (= grams_per_unit_prev x today's price), ``prem_nav``, ``prem_metal`` and
    ``nav_check`` (formulas in the module docstring). Missing inputs give NaN, never 0.
    """
    need = {"date", "close", "nav"}
    missing = need - set(day.columns)
    if missing:
        raise ValueError(f"day: missing column(s) {sorted(missing)}")
    out = day.copy()
    out["date"] = [pd.Timestamp(d).date() for d in out["date"]]
    if out["date"].duplicated().any():
        raise ValueError(f"day: date {out.loc[out['date'].duplicated(), 'date'].iloc[0]} repeats")
    out = out.sort_values("date").reset_index(drop=True)
    px = _by_date(price_per_gram, "price_per_gram")
    out["price_inr_per_gram"] = out["date"].map(px).astype("float64")
    out["grams_per_unit"] = out["nav"] / out["price_inr_per_gram"]
    # Latest grams-per-unit known BEFORE this date: carry forward, then step one row back.
    out["grams_per_unit_prev"] = out["grams_per_unit"].ffill().shift(1)
    out["metal_value"] = out["grams_per_unit_prev"] * out["price_inr_per_gram"]
    out["prem_nav"] = out["close"] / out["nav"] - 1
    out["prem_metal"] = out["close"] / out["metal_value"] - 1
    out["nav_check"] = out["nav"] / out["metal_value"] - 1
    return out


def bullion_monthly_summary(
    frame: pd.DataFrame, *, premium_col: str = "prem_nav", threshold: float = 0.01
) -> pd.DataFrame:
    """Per calendar month: how big the premium was and whether units were being created.

    ``frame`` needs ``date`` and ``premium_col``; ``units`` is optional. Columns out:
    month (first day of the month), n_days (days with a premium), prem_mean, prem_min,
    prem_max, share_abs_gt (share of those days with |premium| > ``threshold``),
    units_last (last units figure in the month), units_growth (units_last / the previous
    row's units_last - 1; a month with no rows is skipped, not treated as zero). Units
    growing means new units were created: the valve is open. NaN where a figure cannot
    be computed.
    """
    if "date" not in frame.columns or premium_col not in frame.columns:
        raise ValueError(f"frame: needs 'date' and '{premium_col}'")
    if not (isinstance(threshold, float | int) and math.isfinite(threshold) and threshold >= 0):
        raise ValueError(f"threshold must be a finite number >= 0, got {threshold!r}")
    f = frame.copy()
    f["date"] = pd.to_datetime(f["date"])
    f = f.sort_values("date")
    f["month"] = f["date"].dt.to_period("M").dt.to_timestamp().dt.date
    prem = f[premium_col].astype("float64")
    big = (prem.abs() > threshold).astype("float64").where(prem.notna())
    g = f.assign(_p=prem, _big=big).groupby("month")
    out = pd.DataFrame(
        {
            "n_days": g["_p"].count(),
            "prem_mean": g["_p"].mean(),
            "prem_min": g["_p"].min(),
            "prem_max": g["_p"].max(),
            "share_abs_gt": g["_big"].mean(),
        }
    )
    if "units" in f.columns:
        out["units_last"] = g["units"].agg(
            lambda s: s.dropna().iloc[-1] if s.notna().any() else np.nan
        )
    else:
        out["units_last"] = np.nan
    out["units_last"] = out["units_last"].astype("float64")
    out["units_growth"] = out["units_last"] / out["units_last"].shift(1) - 1
    return out.reset_index()
