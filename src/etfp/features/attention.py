"""``attention``: how much the public was searching for an ETF, from Google Trends (R15).

Plain words. Google Trends gives each search term a daily number, 0 to 100, where 100 is that
term's own busiest day. ``etfp.clean.manual_trends`` chains the downloaded windows into one
series per term. Here we turn "a few terms per ETF" into ONE number per ETF and day.

Rule (config/trends_terms.yaml): the plain average of the ETF's terms on that calendar day,
but only if EVERY configured term of the ETF has a number that day; otherwise the day is
empty (NaN). Why so strict: each term is scaled to its own maximum, so averaging "whatever
terms exist" would make the series jump on the day a term starts or stops.

An ETF with no term configured gets NaN on every day. A missing value is never turned into 0.

Nothing here reads the clock or the files: pass in the trends table and the dates.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

import numpy as np
import pandas as pd


def attention_series(
    trends: pd.DataFrame, terms: Sequence[str], days: Sequence[date], *, geo: str = "India"
) -> np.ndarray:
    """One attention value per entry of ``days`` (NaN where it cannot be computed).

    ``trends`` has the columns ``term``, ``geo``, ``date``, ``value`` (the clean
    ``trends_daily`` table). Only rows of region ``geo`` count.
    """
    out = np.full(len(days), np.nan)
    if not terms or len(days) == 0 or trends is None or len(trends) == 0:
        return out
    rows = trends[(trends["geo"] == geo) & trends["term"].isin(list(terms))]
    per_term = []
    for term in dict.fromkeys(terms):  # each term once, order kept
        sub = rows[rows["term"] == term].drop_duplicates("date").set_index("date")["value"]
        per_term.append(sub.reindex(list(days)).astype("float64").to_numpy())
    stacked = np.vstack(per_term)
    complete = ~np.isnan(stacked).any(axis=0)
    out[complete] = stacked[:, complete].mean(axis=0)
    return out
