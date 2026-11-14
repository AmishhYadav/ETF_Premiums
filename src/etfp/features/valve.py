"""valve_monthly (M5): was each ETF's creation "valve" open in each month? (DECISIONS D33)

Plain words
-----------
An ETF's price can only be pulled back to its NAV if market makers can make new units:
they buy the underlying shares, hand them to the fund house and get fresh ETF units to
sell (a "creation"). For these international ETFs the fund house stops accepting
creations when it hits its overseas investment cap. We call that switch the *valve*:
open = new units can be made (arbitrage caps the premium), closed = they cannot (demand
can only push the price up).

We never see the fund house's switch directly. We see its footprint: the number of units
outstanding. It can only rise through a creation and only fall through a redemption, so
a month in which the count rose is a month in which the valve was open on at least one
day. A month with a flat count is *consistent with* a closed valve, but it does not prove
it: a fund can be open and simply get no creation orders (e.g. a domestic ETF whose price
is already at NAV). Read ``closed`` as "no creation happened", which for a fund trading at
a large premium is the evidence that creation was not possible.

Classification rule (one row per symbol x calendar month)
---------------------------------------------------------
Source priority: the daily NSE security-master count (``units_daily``, ``IssdCptl``, from
2024-02-05) if the month has at least one daily observation; else the hand-entered
month-end table (``units_monthly``, AUM / NAV, request R11); else nothing.

Daily months (``source = daily_secmaster``). ``units_start`` is the last count *before*
the month (the previous month's last file), so a change on the first file of the month is
counted; every later file is compared with the file before it. Then:

* ``open``            at least one file shows units up        (``status_rule = daily_rose``)
* ``redeeming_only``  no rise, at least one file shows a fall (``daily_fell_only``)
* ``closed``          the count never moved, and NSE did refresh the counts in the
                      window (some ETF's count changed)       (``daily_flat``)
* ``unknown``         flat, but NSE refreshed no fund's count in the window, so flat is
                      no evidence                             (``daily_no_refresh``)
* ``unknown``         the month's only file is the very first file we have, so there is
                      nothing to compare with                 (``daily_no_baseline``)

Important (found in the first real build, D33): ``IssdCptl`` is NOT updated every day.
NSE refreshes it in batches, about weekly (usually the first session of a week), for all
funds at once; NIFTYBEES and GOLDBEES change at every batch. So a "creation day" here is
a publication batch with a net rise, netting a week of creations and redemptions, and a
creation late in a month can be dated to the next month (the batch date). Between the
first file (5 Feb 2024) and 14 Mar 2024 there was no batch at all, which is why that
window is ``daily_no_refresh``.

A month with both rises and falls is ``open`` (creation was possible). When the first
daily month has no earlier count (Feb 2024: files from 5 Feb), ``units_start`` is the first
file in the month and changes before that file are invisible; ``units_start_date`` shows it.
A change across a gap in the files is dated to the later file (same rule as R1 events).

Monthly months (``source = monthly_manual``). Units = AUM / NAV are estimates (rounding,
NAV date vs AUM date), so a small move is noise. Compared with the previous calendar
month's row (SPEC 6.5 rule, threshold ``MONTHLY_THRESHOLD`` = 1%):

* ``open``            units_end / units_start - 1 >  +1%      (``monthly_up``)
* ``redeeming_only``  units_end / units_start - 1 <  -1%      (``monthly_down``)
* ``closed``          within +-1%                             (``monthly_flat``)
* ``unknown``         no row for the previous month           (``monthly_no_baseline``)

Monthly rows cannot see days, so ``n_creation_days`` / ``n_redemption_days`` are empty,
and a month with a creation and an equal redemption looks ``closed``. A creation smaller
than 1% of the fund is missed. Callers must pass monthly units already adjusted for unit
splits (``build.py`` does this with ``config/corporate_actions.yaml``), or the MON100 10:1
split of June 2021 would look like a 900% creation.

No data (``source = none``): ``unknown`` (``no_data``). A month is never guessed from its
neighbours.

Creation units: ``creation_units_net`` = ``units_change`` / creation-unit size (one basket,
config/etfs.yaml). Empty where the size is unknown (MASPTOP50, MAFANG and the domestic
funds at the time of writing); the status does not need it.

Hand example (made-up numbers, basket 600,000): the last file of December reads 3,275,018
and the four files of January read 3,275,018, 3,875,018, 3,875,018, 4,475,018. Then for
January units_start = 3,275,018 (December's last file), two files show a rise, units_end =
4,475,018, units_change = +1,200,000 = +2 creation units, status ``open``.

Pure functions only; the build writes the table.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date

import numpy as np
import pandas as pd

from etfp.store import SCHEMAS, Column, TableSchema, register_schema

TABLE = "valve_monthly"
MONTHLY_THRESHOLD = 0.01
"""Monthly AUM/NAV rule: a month-on-month move larger than this counts (SPEC 6.5)."""
START_MONTH = "2021-01"
"""First month of the grid (the research panel starts Jan 2021)."""

STATUSES = ("open", "closed", "redeeming_only", "unknown")
SOURCES = ("daily_secmaster", "monthly_manual", "none")


def _schema() -> TableSchema:
    cols = (
        Column("symbol", "string", "NSE trading symbol"),
        Column("month", "string", "Calendar month, YYYY-MM"),
        Column(
            "valve_status",
            "string",
            "open (units rose at least once: creation happened), closed (data present, units "
            "never moved: no creation happened), redeeming_only (units fell, never rose), "
            "unknown (no data, or nothing to compare with). Rule in DECISIONS D33",
        ),
        Column(
            "status_rule",
            "string",
            "Which rule set the status: daily_rose, daily_fell_only, daily_flat, "
            "daily_no_refresh, daily_no_baseline, monthly_up, monthly_down, "
            "monthly_flat (threshold +-1%), "
            "monthly_no_baseline, no_data",
        ),
        Column(
            "source",
            "string",
            "daily_secmaster (NSE security master IssdCptl, units_daily), monthly_manual "
            "(units_monthly, AUM / NAV), none",
        ),
        Column(
            "units_start",
            "float",
            "Units at the start: daily = last file before the month (or the first file in the "
            "month if none earlier); monthly = previous month-end AUM / NAV (split-adjusted)",
        ),
        Column("units_start_date", "date", "Date of the units_start observation"),
        Column("units_end", "float", "Units at the last observation in the month"),
        Column("units_end_date", "date", "Date of the units_end observation"),
        Column("units_change", "float", "units_end - units_start"),
        Column("units_change_pct", "float", "units_end / units_start - 1 (fraction)"),
        Column("n_obs", "int", "Daily files in the month (monthly source: 1; none: 0)"),
        Column(
            "n_creation_days",
            "int",
            "Daily files whose count is above the previous file (empty for monthly/none). NSE "
            "refreshes IssdCptl in about-weekly batches, so this counts publication batches "
            "with a net rise, not trading days with a creation (D33)",
        ),
        Column(
            "n_redemption_days",
            "int",
            "Daily files whose count is below the previous file (empty for monthly/none); "
            "batches, as for n_creation_days",
        ),
        Column(
            "n_refresh_dates",
            "int",
            "NSE refresh batches (dates any tracked ETF's count changed) after units_start_date "
            "and up to units_end_date. 0 with a flat count -> unknown, not closed",
        ),
        Column("creation_unit_size", "int", "Units per creation basket (config/etfs.yaml)"),
        Column(
            "creation_units_net",
            "float",
            "units_change / creation_unit_size; empty when the size is unknown",
        ),
        Column(
            "all_changes_whole_cu",
            "bool",
            "Daily: every change in the month is a whole number of baskets (True if no change); "
            "empty for monthly rows or when the size is unknown. False = look at this month",
        ),
        Column(
            "source_file",
            "string",
            "Lineage: raw files of units_start, of every change day and of units_end, "
            "';'-separated (monthly: the manual CSV path; none: 'none')",
        ),
    )
    return TableSchema(
        name=TABLE,
        primary_key=("symbol", "month"),
        columns=cols,
        doc="Creation-valve status per ETF and calendar month from units outstanding (M5, D33).",
    )


if TABLE not in SCHEMAS:
    register_schema(_schema())

COLUMNS = [c.name for c in _schema().columns]


def month_range(first: str, last: str) -> list[str]:
    """['2021-01', '2021-02', ...] inclusive."""
    return [p.strftime("%Y-%m") for p in pd.period_range(first, last, freq="M")]


def _month(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _prev_month(m: str) -> str:
    return (pd.Period(m, freq="M") - 1).strftime("%Y-%m")


def _row(symbol: str, month: str, size: int | None, **kw: object) -> dict[str, object]:
    row: dict[str, object] = {c: None for c in COLUMNS}
    row.update(symbol=symbol, month=month, creation_unit_size=size)
    row.update(kw)
    return row


def refresh_dates(units_daily: pd.DataFrame) -> list[date]:
    """Dates on which NSE visibly refreshed ``IssdCptl``: any ETF's count changed vs its
    previous file. Busy funds (NIFTYBEES, GOLDBEES) change at every refresh, so this is the
    calendar of update batches (about weekly; D33)."""
    d = units_daily.sort_values(["symbol", "date"], kind="stable")
    chg = d.groupby("symbol", sort=False)["units_outstanding"].diff()
    return sorted(set(d.loc[chg.fillna(0) != 0, "date"]))


def _daily_rows(
    symbol: str, d: pd.DataFrame, size: int | None, refreshes: np.ndarray
) -> dict[str, dict[str, object]]:
    """Rows for every month that has daily files. ``d``: one symbol, valid units only.
    ``refreshes``: sorted refresh dates (numpy datetime64[D])."""
    d = d.sort_values("date", kind="stable").reset_index(drop=True)
    d["_m"] = [_month(x) for x in d["date"]]
    units = d["units_outstanding"].astype("float64").to_numpy()
    dates = list(d["date"])
    files = list(d["source_file"])
    out: dict[str, dict[str, object]] = {}
    for month, idx in d.groupby("_m", sort=True).indices.items():
        i0, i1 = int(idx[0]), int(idx[-1])
        base = i0 - 1 if i0 > 0 else i0  # previous file (last of an earlier month), if any
        diffs = np.diff(units[base : i1 + 1])  # change at each file vs the file before it
        n_up, n_down = int((diffs > 0).sum()), int((diffs < 0).sum())
        start, end = units[base], units[i1]
        change = end - start
        # Refresh batches published after units_start and up to units_end: only these could
        # have shown a creation for this month.
        lo = np.searchsorted(refreshes, np.datetime64(dates[base], "D"), side="right")
        hi = np.searchsorted(refreshes, np.datetime64(dates[i1], "D"), side="right")
        n_refresh = int(hi - lo)
        if base == i0 and i1 == i0:
            status, rule = "unknown", "daily_no_baseline"
        elif n_up > 0:
            status, rule = "open", "daily_rose"
        elif n_down > 0:
            status, rule = "redeeming_only", "daily_fell_only"
        elif n_refresh == 0:
            # Flat, but NSE did not refresh the count for ANY fund in this window (e.g.
            # 5 Feb - 13 Mar 2024): flat says nothing about this fund. Never call it closed.
            status, rule = "unknown", "daily_no_refresh"
        else:
            status, rule = "closed", "daily_flat"
        moved = [base + 1 + k for k in np.flatnonzero(diffs != 0)]
        lineage = list(dict.fromkeys([files[base], *(files[k] for k in moved), files[i1]]))
        if size:
            nz = diffs[diffs != 0] / size
            whole: bool | None = bool(np.all(np.abs(nz - np.round(nz)) < 1e-9))
            cu = change / size
        else:
            whole, cu = None, np.nan
        out[month] = _row(
            symbol,
            month,
            size,
            valve_status=status,
            status_rule=rule,
            source="daily_secmaster",
            units_start=start,
            units_start_date=dates[base],
            units_end=end,
            units_end_date=dates[i1],
            units_change=change,
            units_change_pct=change / start if start > 0 else np.nan,
            n_obs=i1 - i0 + 1,
            n_creation_days=n_up,
            n_redemption_days=n_down,
            n_refresh_dates=n_refresh,
            creation_units_net=cu,
            all_changes_whole_cu=whole,
            source_file=";".join(lineage),
        )
    return out


def _monthly_row(
    symbol: str,
    month: str,
    cur: pd.Series,
    prev: pd.Series | None,
    size: int | None,
    threshold: float,
) -> dict[str, object]:
    end = float(cur["units"])
    common = dict(
        source="monthly_manual",
        units_end=end,
        units_end_date=cur["month_end"],
        n_obs=1,
    )
    if prev is None:
        return _row(
            symbol,
            month,
            size,
            valve_status="unknown",
            status_rule="monthly_no_baseline",
            source_file=str(cur["source_file"]),
            **common,
        )
    start = float(prev["units"])
    change = end - start
    pct = change / start if start > 0 else np.nan
    if pct > threshold:
        status, rule = "open", "monthly_up"
    elif pct < -threshold:
        status, rule = "redeeming_only", "monthly_down"
    else:
        status, rule = "closed", "monthly_flat"
    lineage = list(dict.fromkeys([str(prev["source_file"]), str(cur["source_file"])]))
    return _row(
        symbol,
        month,
        size,
        valve_status=status,
        status_rule=rule,
        units_start=start,
        units_start_date=prev["month_end"],
        units_change=change,
        units_change_pct=pct,
        creation_units_net=change / size if size else np.nan,
        source_file=";".join(lineage),
        **common,
    )


def valve_monthly(
    units_daily: pd.DataFrame,
    units_monthly: pd.DataFrame,
    sizes: Mapping[str, int | None],
    symbols: Iterable[str],
    *,
    start_month: str = START_MONTH,
    end_month: str | None = None,
    threshold: float = MONTHLY_THRESHOLD,
    refreshes: Iterable[date] | None = None,
) -> pd.DataFrame:
    """The ``valve_monthly`` table (see the module docstring for the rule).

    ``units_daily``: symbol, date, units_outstanding, source_file (one row per file).
    ``units_monthly``: symbol, month_end, units (split-adjusted), source_file.
    ``end_month`` defaults to the latest month in either input. Every symbol gets every
    month from ``start_month`` to ``end_month``; months without data are ``unknown``.
    ``refreshes``: dates NSE refreshed the counts (default :func:`refresh_dates` of
    ``units_daily``, so pass the whole table, not one symbol).
    """
    ud = units_daily[["symbol", "date", "units_outstanding", "source_file"]].copy()
    ud["units_outstanding"] = pd.to_numeric(ud["units_outstanding"], errors="coerce")
    ud = ud.dropna(subset=["units_outstanding"])
    if ud.duplicated(["symbol", "date"]).any():
        raise ValueError("duplicate (symbol, date) rows in units_daily")
    um = units_monthly[["symbol", "month_end", "units", "source_file"]].copy()
    um["units"] = pd.to_numeric(um["units"], errors="coerce")
    um = um.dropna(subset=["units"])
    um["_m"] = [_month(x) for x in um["month_end"]]
    if um.duplicated(["symbol", "_m"]).any():
        raise ValueError("more than one units_monthly row for a (symbol, month)")

    if end_month is None:
        last = [_month(max(ud["date"]))] if len(ud) else []
        last += [max(um["_m"])] if len(um) else []
        end_month = max(last) if last else start_month
    months = month_range(start_month, end_month)
    ref = refresh_dates(ud) if refreshes is None else sorted(set(refreshes))
    ref_arr = np.array([np.datetime64(x, "D") for x in ref], dtype="datetime64[D]")

    rows = []
    for sym in symbols:
        size = sizes.get(sym) or None
        daily = _daily_rows(sym, ud[ud["symbol"] == sym], size, ref_arr) if len(ud) else {}
        monthly = {r["_m"]: r for _, r in um[um["symbol"] == sym].iterrows()}
        for m in months:
            if m in daily:
                rows.append(daily[m])
            elif m in monthly:
                prev = monthly.get(_prev_month(m))
                rows.append(_monthly_row(sym, m, monthly[m], prev, size, threshold))
            else:
                rows.append(
                    _row(
                        sym,
                        m,
                        size,
                        valve_status="unknown",
                        status_rule="no_data",
                        source="none",
                        n_obs=0,
                        source_file="none",
                    )
                )
    out = pd.DataFrame(rows, columns=COLUMNS)
    return out.sort_values(["symbol", "month"], kind="stable").reset_index(drop=True)


# ==========================================================================
# docs/VALVE_STATUS.md (regenerated by `etfp export valve-status`)
# ==========================================================================
_CELL = {"open": "O", "closed": "·", "redeeming_only": "R", "unknown": " "}


def _fmt_units(x: object) -> str:
    return "" if x is None or pd.isna(x) else f"{x:,.0f}"


def valve_status_markdown(
    vm: pd.DataFrame,
    *,
    tracked: Iterable[str] = (),
) -> str:
    """Month-by-month status grid per ETF, plus a list of every non-flat month."""
    tracked = list(tracked)
    syms = sorted(vm["symbol"].unique(), key=lambda s: (s not in tracked, s))
    years = sorted({m[:4] for m in vm["month"]})
    ends = vm["units_end_date"].dropna()
    through = max(ends).isoformat() if len(ends) else "none"
    lines = [
        "# Valve status per ETF-month",
        "",
        "Generated by `uv run etfp export valve-status` from the `valve_monthly` table built "
        f"by `uv run etfp build` (units data through {through}). Do not edit by hand; rebuild "
        "and regenerate. Rule: DECISIONS D33 and `src/etfp/features/valve.py`.",
        "",
        "Legend: **O** open (units rose: a creation happened) · **·** closed (data present, "
        "units never moved although NSE refreshed the counts) · **R** redeeming only (units "
        "fell, never rose) · blank unknown (no units data, nothing to compare with, or NSE "
        "refreshed no fund's count that month). `closed` means *no "
        "creation happened*; for a fund at a large premium that is the evidence the valve was "
        "shut, but a fund with no premium can be `closed` just because nobody asked.",
        "",
        "Daily data (NSE security master) starts 2024-02-05, so Feb 2024 only sees changes "
        "from that file on. Months before Feb 2024 need the hand-entered `units_monthly` "
        "table (R11); where it is empty they are blank (and the years are left out).",
        "",
        "NSE refreshes units outstanding (`IssdCptl`) in batches about once a week, for all "
        "funds at once, not daily. A month's status is therefore dated by the batch that "
        "published the change: a creation in the last days of a month can show in the next "
        "month. No batch at all ran between 5 Feb and 14 Mar 2024, so Feb 2024 is unknown "
        "and the Mar 2024 changes may include February creations.",
        "",
        "## Grid",
        "",
        "| ETF | year | " + " | ".join(f"{i:02d}" for i in range(1, 13)) + " |",
        "|---|---|" + "---|" * 12,
    ]
    by = vm.set_index(["symbol", "month"])
    for s in syms:
        for y in years:
            cells = []
            for mo in range(1, 13):
                key = (s, f"{y}-{mo:02d}")
                cells.append(_CELL[by.loc[key, "valve_status"]] if key in by.index else "")
            if all(c.strip() == "" for c in cells):
                continue
            lines.append(f"| {s} | {y} | " + " | ".join(cells) + " |")

    lines += ["", "## Summary per ETF", ""]
    lines.append(
        "| ETF | months open | closed | redeeming only | unknown | last open month | "
        "units on last file | basket |"
    )
    lines.append("|---|---|---|---|---|---|---|---|")
    for s in syms:
        g = vm[vm["symbol"] == s]
        counts = g["valve_status"].value_counts()
        opened = g.loc[g["valve_status"] == "open", "month"]
        known = g[g["units_end"].notna()]
        last_units = _fmt_units(known["units_end"].iloc[-1]) if len(known) else ""
        size = g["creation_unit_size"].dropna()
        lines.append(
            f"| {s} | {counts.get('open', 0)} | {counts.get('closed', 0)} | "
            f"{counts.get('redeeming_only', 0)} | {counts.get('unknown', 0)} | "
            f"{opened.iloc[-1] if len(opened) else '-'} | {last_units} | "
            f"{_fmt_units(size.iloc[0]) if len(size) else 'unknown'} |"
        )

    lines += [
        "",
        "## Every month in which units moved",
        "",
        "| ETF | month | status | units start | units end | change | creation units | "
        "rise batches | fall batches | source |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    order = {s: i for i, s in enumerate(syms)}
    moved = vm[vm["units_change"].fillna(0) != 0].copy()
    moved["_o"] = moved["symbol"].map(order)
    for r in moved.sort_values(["_o", "month"]).itertuples():
        cu = "" if pd.isna(r.creation_units_net) else f"{r.creation_units_net:+.2f}"
        nd = lambda v: "" if v is None or pd.isna(v) else str(int(v))  # noqa: E731
        lines.append(
            f"| {r.symbol} | {r.month} | {r.valve_status} | {_fmt_units(r.units_start)} | "
            f"{_fmt_units(r.units_end)} | {r.units_change:+,.0f} | {cu} | "
            f"{nd(r.n_creation_days)} | {nd(r.n_redemption_days)} | {r.source} |"
        )
    return "\n".join(lines) + "\n"


__all__ = [
    "COLUMNS",
    "MONTHLY_THRESHOLD",
    "START_MONTH",
    "TABLE",
    "month_range",
    "valve_monthly",
    "valve_status_markdown",
]
