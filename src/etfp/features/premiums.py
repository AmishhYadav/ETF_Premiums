"""Premium measures and simple derived columns (SPEC 6.3 and 6.6, REQUESTS R3).

Premium in plain words
----------------------
Premium = price / value - 1. A Rs 120 price on a Rs 100 value is a 20% premium.
The catch for these ETFs is "what is the value?". We keep several answers, each
with a different NAV, because they answer different questions:

``prem_raw``       close / NAV dated t - 1. What the press quotes. NOT synchronous:
                   for a US ETF the NAV dated t is struck 10 hours after the Indian
                   close (D10).
``prem_nav_known`` close / NAV known at the close - 1. Compares prices with the
                   freshest value that existed at 15:30 IST.
``prem_band_ref``  close / NAV dated two trading days ago - 1. What the old
                   (pre-7 Sep 2026) price band was anchored to (D5).
``prem_fv``        close / fair value - 1. Fair value is the known NAV moved to the
                   Indian close with futures and the rupee (``fair_value``).
``log_prem_fv``    ln(close / FV). Same information on a log scale; additive over
                   time and symmetric (a +20% and a -16.7% move are +/-0.182).
``x_april``        ln(close / (1.2 x NAV known at close)): the log distance above
                   the ceiling the April 2027 band (anchored on the previous NAV,
                   +20% at most) would allow. Positive means the price is above
                   what that band would permit. Owner's choice, 14 Nov 2026 (D30).
``x_april_fv``     ln(close / (1.2 x FV)): the same on fair value (was ``x_april``
                   before schema 1.3.0).
``prem_inav``      last price / iNAV - 1, from intraday snapshots only.

Every ratio returns NaN when a number is missing or not positive; nothing is
silently filled. Pure functions: no clock, no files.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

APRIL_CAP_MULTIPLE = 1.2
"""After 1 Apr 2027 the band is planned to reach +20% around NAV (D5/SPEC 7)."""

PREMIUM_COLUMNS = (
    "prem_raw",
    "prem_nav_known",
    "prem_band_ref",
    "prem_fv",
    "log_prem_fv",
    "x_april",
    "x_april_fv",
)


def _num(x: pd.Series | float) -> pd.Series | float:
    return pd.to_numeric(x, errors="coerce") if isinstance(x, pd.Series) else float(x)


def ratio_minus_one(price: pd.Series, value: pd.Series) -> pd.Series:
    """price / value - 1, NaN unless both are present and positive.

    Hand example: 120 / 100 - 1 = 0.20.
    """
    p, v = _num(price), _num(value)
    ok = p.notna() & v.notna() & (p > 0) & (v > 0)  # type: ignore[union-attr]
    return (p / v - 1.0).where(ok)  # type: ignore[operator]


def log_ratio(price: pd.Series, value: pd.Series) -> pd.Series:
    """ln(price / value), NaN unless both are present and positive.

    Hand example: ln(120 / 100) = ln 1.2 = 0.1823215568.
    """
    p, v = _num(price), _num(value)
    ok = p.notna() & v.notna() & (p > 0) & (v > 0)  # type: ignore[union-attr]
    safe_p = p.where(ok, 1.0)  # type: ignore[union-attr]
    safe_v = v.where(ok, 1.0)  # type: ignore[union-attr]
    return pd.Series(np.log(safe_p / safe_v), index=p.index).where(ok)  # type: ignore[union-attr]


def premium_columns(
    df: pd.DataFrame,
    *,
    close_col: str = "close",
    nav_same_col: str = "nav_same_date",
    nav_known_col: str = "nav_known_at_close",
    nav_t2_col: str = "nav_t2",
    fv_col: str = "fv",
    april_cap_multiple: float = APRIL_CAP_MULTIPLE,
) -> pd.DataFrame:
    """The premium columns for an ETF-day frame (same index; see ``PREMIUM_COLUMNS``).

    Hand example, MONQ50 on 4 Sep 2026: close 141.93, NAV dated 4 Sep 118.7371
    -> prem_raw = 141.93 / 118.7371 - 1 = 0.19533 (19.53%, the figure Value Research
    reported). With FV 130.00 (made up): prem_fv = 141.93 / 130 - 1 = 0.09177;
    log_prem_fv = ln(141.93 / 130) = 0.08780; x_april_fv = ln(141.93 / (1.2 x 130)) =
    ln(0.90981) = -0.09452 (price is 9.5% inside what a +20% band would allow).
    With NAV known 117.00 (made up): x_april = ln(141.93 / 140.40) = 0.010838.
    """
    close = df[close_col]
    fv = df[fv_col]
    out = pd.DataFrame(index=df.index)
    out["prem_raw"] = ratio_minus_one(close, df[nav_same_col])
    out["prem_nav_known"] = ratio_minus_one(close, df[nav_known_col])
    out["prem_band_ref"] = ratio_minus_one(close, df[nav_t2_col])
    out["prem_fv"] = ratio_minus_one(close, fv)
    out["log_prem_fv"] = log_ratio(close, fv)
    # From 1 Apr 2027 the band base is the previous NAV, so the cap the price will face
    # is 1.2 x the NAV known at the close (BLUEPRINT definition; D30).
    cap_nav = _num(df[nav_known_col]) * april_cap_multiple
    out["x_april"] = log_ratio(close, cap_nav)  # type: ignore[arg-type]
    out["x_april_fv"] = log_ratio(close, _num(fv) * april_cap_multiple)  # type: ignore[arg-type]
    return out[list(PREMIUM_COLUMNS)]


def inav_premium(last_price: pd.Series, inav: pd.Series) -> pd.Series:
    """prem_inav = last price / iNAV - 1 for snapshot rows (NaN if either is missing).

    Hand example: last price 126.00, iNAV 100.00 -> 0.26.
    """
    return ratio_minus_one(last_price, inav).rename("prem_inav")


# --------------------------------------------------------------------------
# Section 6.6: turnover, average trade size, returns
# --------------------------------------------------------------------------
def turnover_units(volume_units: pd.Series, units_outstanding: pd.Series) -> pd.Series:
    """Share of the fund's units that changed hands: units traded / units outstanding.

    This is the R9/BLUEPRINT definition (unit-based, so a rising premium does not
    inflate it). Pass NSE volume now; add BSE volume when R9 lands. NaN if units are
    missing or not positive.

    Hand example: 1,000,000 units traded of 14,675,018 outstanding = 0.0681.
    """
    v, u = _num(volume_units), _num(units_outstanding)
    ok = v.notna() & u.notna() & (u > 0) & (v >= 0)  # type: ignore[union-attr]
    return (v / u).where(ok)  # type: ignore[operator]


def turnover_value(traded_value_rs: pd.Series, aum_rs: pd.Series) -> pd.Series:
    """SPEC 6.6 version: rupees traded / rupees of assets (units x NAV). Rupees, not lakhs.

    Hand example: Rs 5 crore traded (50,000,000) against Rs 250 crore AUM = 0.02.
    """
    v, a = _num(traded_value_rs), _num(aum_rs)
    ok = v.notna() & a.notna() & (a > 0) & (v >= 0)  # type: ignore[union-attr]
    return (v / a).where(ok)  # type: ignore[operator]


def avg_trade_value(traded_value_rs: pd.Series, trades: pd.Series) -> pd.Series:
    """Average rupees per trade = traded value / number of trades (NaN if no trades).

    Hand example: Rs 1,20,00,000 over 400 trades = Rs 30,000 per trade. NSE publishes
    value in lakhs (1 lakh = 100,000): multiply by 1e5 first.
    """
    v, n = _num(traded_value_rs), _num(trades)
    ok = v.notna() & n.notna() & (n > 0) & (v >= 0)  # type: ignore[union-attr]
    return (v / n).where(ok)  # type: ignore[operator]


def log_returns(series: pd.Series, periods: int = 1) -> pd.Series:
    """ln(x_t / x_{t-periods}) by row position; NaN if either price is missing or <= 0.

    The caller must pass ONE symbol, sorted by date, with one row per trading day,
    otherwise a "1-day" return can span a gap. Hand example: 100 -> 110 is
    ln(1.1) = 0.0953101798.
    """
    if periods < 1:
        raise ValueError("periods must be >= 1")
    return log_ratio(series, series.shift(periods))


def add_return_columns(
    df: pd.DataFrame,
    *,
    symbol_col: str = "symbol",
    date_col: str = "date",
    price_col: str = "close",
    nav_col: str = "nav_known_at_close",
    fv_col: str = "fv",
) -> pd.DataFrame:
    """Add log-return columns per symbol (returns a copy, original row order kept).

    * ``ret_close_1`` / ``ret_close_5``  price return from t-1 / t-5 to t.
    * ``ret_own_1`` / ``ret_own_5``      the same returns ending one session EARLIER
      (t-1 vs t-1-k): the fund's "previous k sessions" return, known before t, for
      regressions of today's change on yesterday's move.
    * ``ret_nav_1``, ``ret_fv_1``        one-session log returns of the known NAV and of FV.
      NAV known changes only when a new NAV arrives (so it repeats over US holidays).

    Rows are ordered by (symbol, date) internally; "session" means the previous ROW of
    that symbol, so the frame must hold every trading day of each symbol.
    """
    for col in (symbol_col, date_col):
        if col not in df.columns:
            raise ValueError(f"add_return_columns needs column {col!r}")
    if df.duplicated([symbol_col, date_col]).any():
        raise ValueError("duplicate (symbol, date) rows; remove them before computing returns")
    work = df.sort_values([symbol_col, date_col], kind="stable")
    grp = work.groupby(symbol_col, sort=False)
    res = pd.DataFrame(index=work.index)
    for k in (1, 5):
        res[f"ret_close_{k}"] = grp[price_col].transform(lambda s, k=k: log_returns(s, k))
        res[f"ret_own_{k}"] = grp[price_col].transform(lambda s, k=k: log_returns(s, k).shift(1))
    res["ret_nav_1"] = grp[nav_col].transform(lambda s: log_returns(s, 1))
    res["ret_fv_1"] = grp[fv_col].transform(lambda s: log_returns(s, 1))
    return pd.concat([df, res.reindex(df.index)], axis=1)
