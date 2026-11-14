"""Live premium tracker (SPEC section 9). Run it with:

    uv run streamlit run dashboard/app.py

(needs the dashboard extra once: ``uv sync --extra dashboard``).

This file is only the screen layout. Every number comes from ``dashboard/data.py`` (plain
functions with tests) and every figure from ``dashboard/charts.py``.

The dashboard only reads. It opens the Parquet files in ``data/clean`` and never the DuckDB
file, so it cannot lock out ``etfp build``. It never calls a data source itself: what you see is
what the last build wrote.

Text on this page describes the data. It does not say what anyone should do (CLAUDE.md).
"""

from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # lets "from dashboard import ..." work when Streamlit runs this file
    sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from dashboard import charts  # noqa: E402
from dashboard import data as dd  # noqa: E402
from etfp.alerts.rules import load_alert_config  # noqa: E402
from etfp.config import load_rules  # noqa: E402
from etfp.recorder import read_heartbeat  # noqa: E402
from etfp.store import Store  # noqa: E402
from etfp.timeutil import now_ist  # noqa: E402


def _stretch() -> dict:
    """Full-width option under both the old and the new Streamlit spelling."""
    major_minor = tuple(int(x) for x in st.__version__.split(".")[:2])
    return {"width": "stretch"} if major_minor >= (1, 50) else {"use_container_width": True}


DEFAULT_INAV_SOURCES = ("amc_motilal", "amc_mirae", "nse_quote")
HISTORY_RANGES = {"All": None, "1 year": 365, "6 months": 183, "3 months": 92, "1 month": 31}


# --------------------------------------------------------------------------
# Loading (cached; the cache key changes whenever a table file changes)
# --------------------------------------------------------------------------
def _file_stamp(clean_dir: Path) -> tuple:
    stamp = []
    for name in dd.TABLE_NAMES:
        path = clean_dir / f"{name}.parquet"
        stamp.append((name, path.stat().st_mtime_ns if path.exists() else None))
    return tuple(stamp)


@st.cache_data(show_spinner="Reading the clean tables...", ttl=600)
def _load(clean_dir: str, stamp: tuple) -> dd.Tables:
    del stamp  # only part of the cache key
    return dd.load_tables(Store(Path(clean_dir)))


@st.cache_data(ttl=3600)
def _markers() -> list[dd.Marker]:
    try:
        return dd.regime_markers(load_rules().regimes)
    except (FileNotFoundError, ValueError):
        return []


def _inav_settings() -> tuple[list[str], float]:
    """Which iNAV sources to trust and the pairing gap: the same settings the alerts use."""
    try:
        cfg = load_alert_config().inav_premium_cross
        return list(cfg.inav_sources), float(cfg.max_pair_gap_minutes)
    except (FileNotFoundError, ValueError):
        return list(DEFAULT_INAV_SOURCES), 10.0


def _fmt_pct(value: float | None, digits: int = 1) -> str:
    return "n/a" if value is None or pd.isna(value) else f"{value * 100:.{digits}f}%"


def _fmt_rs(value: float | None) -> str:
    return "n/a" if value is None or pd.isna(value) else f"Rs {value:,.2f}"


# --------------------------------------------------------------------------
# Page
# --------------------------------------------------------------------------
st.set_page_config(page_title="India international ETF premiums", layout="wide")
st.title("India-listed international ETFs: premium over fair value")
st.warning(dd.DISCLAIMER)

store = Store()
tables = _load(str(store.dir), _file_stamp(store.dir))
now = now_ist()
today = now.date()

if not tables.has_data:
    st.info(
        "There is no ETF data yet. Run `uv run etfp build` to create the analysis table "
        "(`etf_day`), then reload this page."
    )
    built = [name for name, frame in tables.frames.items() if frame is not None]
    st.caption("Tables found: " + (", ".join(built) if built else "none"))
    st.stop()

etf_day = tables.etf_day
master = tables.get("etf_master")
symbols = dd.tracked_symbols(etf_day, master)
latest_date = max(pd.to_datetime(etf_day["date"]).dt.date)

head = st.columns(3)
head[0].metric("Latest data date", latest_date.isoformat())
head[1].metric("Page loaded (IST)", now.strftime("%d %b %Y %H:%M"))
head[2].metric(
    "Days to 1 Apr 2027",
    dd.days_to_april(now),
    help="Planned date when the price-band anchor returns to the previous day's NAV.",
)

if not symbols:
    st.warning("No tracked ETFs have data. Check `tracked: true` in `config/etfs.yaml`.")
    st.stop()

events = tables.get("events")
tab_overview, tab_detail, tab_april, tab_inav, tab_events, tab_quality, tab_about = st.tabs(
    [
        "Overview",
        "ETF detail",
        "April 2027",
        "Intraday iNAV",
        "Events log",
        "Data quality",
        "About",
    ]
)

# ---- Overview ---------------------------------------------------------------------------------
with tab_overview:
    overview = dd.latest_overview(etf_day, master)
    shown = dd.display_overview(overview).dropna(axis=1, how="all")  # hide columns with no data
    missing = dd.overview_missing_columns(etf_day)
    st.subheader("Latest row for each tracked ETF")
    st.caption(
        "Premium = price above (or below) a reference value. Fair value = the latest known NAV "
        "moved to the Indian close using the index future and the rupee rate. "
        "Sorted by premium over fair value; click a column header to re-sort. "
        "A premium is blank until the NAV it needs has been collected; "
        '"Premium as of" shows the date of the newest premium.'
    )
    money = st.column_config.NumberColumn(format="%.2f")
    one_dp = st.column_config.NumberColumn(format="%.1f")
    rupee_fields = ("close", "nav_known", "fv", "band_low", "band_high")
    percent_fields = (
        "prem_raw", "prem_nav_known", "prem_fv", "prem_fv_chg_1d", "prem_fv_chg_1w",
        "pct_to_upper", "pct_to_lower",
    )  # fmt: skip
    config = {dd.DISPLAY_LABELS[c]: money for c in rupee_fields}
    config |= {dd.DISPLAY_LABELS[c]: one_dp for c in percent_fields}
    config[dd.DISPLAY_LABELS["band_position"]] = st.column_config.NumberColumn(format="%.2f")
    st.dataframe(shown, hide_index=True, column_config=config, **_stretch())
    if missing:
        st.caption("Not in the data yet, so some columns are hidden: " + ", ".join(missing))
    st.caption(
        "Fair value method `nav_only` means no market adjustment was possible, so fair value "
        "equals the latest known NAV. The band position is where the close sits between the "
        "lower (0) and upper (1) price limit; the limits are NSE's official ones when available."
    )

# ---- ETF detail -------------------------------------------------------------------------------
with tab_detail:
    measures = dd.available_measures(etf_day)
    left, mid, right = st.columns([1, 2, 1])
    chosen = left.selectbox("ETF", symbols, key="detail_symbol")
    compare = mid.multiselect("Compare ETFs", symbols, default=[chosen], key="detail_compare")
    span = right.selectbox("Period", list(HISTORY_RANGES), key="detail_span")
    start = None
    if HISTORY_RANGES[span]:
        start = latest_date - timedelta(days=HISTORY_RANGES[span])

    row = overview[overview["symbol"] == chosen].iloc[0]
    cards = st.columns(5)
    cards[0].metric("Close", _fmt_rs(row["close"]))
    cards[1].metric("NAV known at close", _fmt_rs(row["nav_known"]))
    cards[2].metric("Fair value", _fmt_rs(row["fv"]))
    cards[3].metric("Premium, fair value", _fmt_pct(row["prem_fv"]))
    cards[4].metric("Premium, same-day NAV", _fmt_pct(row["prem_raw"]))
    st.caption(f"{row['name']} ({chosen}); data for {row['data_date']}.")

    st.subheader("Premium history")
    if not measures:
        st.info("No premium columns in the data yet.")
    else:
        measure = st.selectbox(
            "Measure", list(measures), format_func=measures.get, key="detail_measure"
        )
        series = dd.premium_series(etf_day, compare or [chosen], measure, start=start)
        if series.empty:
            st.info("No values for this measure and period.")
        else:
            first_day, last_day = series["date"].min(), series["date"].max()
            in_range = dd.markers_in_range(_markers(), first_day, last_day)
            show_events = st.checkbox("Show events", value=True, key="detail_events")
            event_marks = []
            if show_events:
                event_marks = dd.markers_in_range(
                    dd.event_markers(
                        events,
                        compare or [chosen],
                        skip_rule_days=[m.day for m in _markers()],
                    ),
                    first_day,
                    last_day,
                )
            fig = charts.premium_figure(
                series,
                label=measures[measure],
                log_measure=measure in dd.LOG_MEASURES,
                markers=in_range + event_marks,
            )
            st.plotly_chart(fig, **_stretch())
            if event_marks:
                st.caption(
                    "Pink dotted lines: entries of the events log for these ETFs, placed on the "
                    "first session that could react (see the Events log tab)."
                )
            later = [m for m in _markers() if m not in in_range]
            if later:
                st.caption(
                    "Other rule changes not on this chart: "
                    + "; ".join(f"{m.day.isoformat()} {m.label}" for m in later)
                )

    st.subheader("Price, known NAV and fair value")
    levels = dd.level_frame(etf_day, chosen)
    if start is not None:
        levels = levels[levels["date"] >= start]
    if levels.drop(columns="date").dropna(how="all").empty:
        st.info("No price or NAV values for this ETF and period.")
    else:
        st.plotly_chart(charts.level_figure(levels, chosen), **_stretch())

    st.subheader("Close against the price limits")
    bands = dd.band_frame(etf_day, chosen)
    if start is not None:
        bands = bands[bands["date"] >= start]
    band_cols = [c for c in bands.columns if c.startswith("band_")]
    if not band_cols or bands[band_cols].dropna(how="all").empty:
        st.info("No band limits in the data for this ETF and period.")
    else:
        in_range = dd.markers_in_range(_markers(), bands["date"].min(), bands["date"].max())
        st.plotly_chart(charts.band_figure(bands, chosen, in_range), **_stretch())
        st.caption(
            "Shaded area: NSE's official initial limits. Dotted: computed initial limits; "
            "dashed: computed widest limits (gold and silver have no widest limit)."
        )

# ---- April 2027 countdown ---------------------------------------------------------------------
with tab_april:
    left_days = dd.days_to_april(now)
    st.subheader("April 2027: the price-band base returns to NAV")
    st.metric(
        "Days to 1 Apr 2027 (IST)",
        left_days,
        help="Calendar days from today's date in India to 1 April 2027.",
    )
    st.markdown(
        """
**What is planned for 1 April 2027.** Since 7 Sep 2026 each day's price limits are drawn around
the previous day's closing *price*, so the limits can move up with the price day after day. From
1 April 2027 the base is planned to go back to the previous day's *NAV* (the fund's published
value per unit). For equity ETFs the limits then start at 10% either side of that NAV and can
widen twice by 5%, so in one day the price cannot go more than **20% above NAV**. Gold and silver
ETFs widen without a cap, so the 20% line does not bind them. The rule is planned, not in force,
and may change before then (`config/rules.yaml`).

**What `x_april` measures.** `x_april` = ln(close / (1.2 x the NAV known at the close)).

* `x_april` = 0: the close is exactly 1.2 x the known NAV.
* `x_april` > 0: the close sits **above** 1.2 x the known NAV, i.e. above where the April upper
  limit would be if it were computed on today's known NAV. For example 0.10 means the close is
  about 10.5% above that level (e^0.10 - 1).
* `x_april` < 0: the close sits below that level.

This describes where prices are relative to a planned rule. It is not a forecast of what prices
will do. The NAV used is the latest one published by the close (for US funds, the previous US
day's NAV); the April rule will use the previous day's NAV, which is usually the same one.
"""
    )
    x_cols = {
        c: lab
        for c, lab in {
            "x_april": "On the known NAV (x_april)",
            "x_april_fv": "On fair value (x_april_fv)",
        }.items()
        if c in etf_day.columns
    }
    if not x_cols:
        st.info("etf_day has no `x_april` column yet. Rebuild with `uv run etfp build`.")
    else:
        a_left, a_mid, a_right = st.columns([1, 2, 1])
        x_col = a_left.selectbox("Reference", list(x_cols), format_func=x_cols.get, key="x_col")
        x_syms = a_mid.multiselect("ETFs", symbols, default=symbols, key="x_symbols")
        x_span = a_right.selectbox("Period", list(HISTORY_RANGES), index=1, key="x_span")
        x_start = None
        if HISTORY_RANGES[x_span]:
            x_start = latest_date - timedelta(days=HISTORY_RANGES[x_span])
        x_series = dd.x_april_series(etf_day, x_syms, column=x_col, start=x_start)
        if x_series.empty:
            st.info("No values for these ETFs and this period.")
        else:
            x_marks = dd.markers_in_range(
                _markers(), x_series["date"].min(), x_series["date"].max()
            )
            st.plotly_chart(
                charts.x_april_figure(
                    x_series, label=f"{x_cols[x_col]}: above 0 = close above 1.2 x NAV",
                    markers=x_marks,
                ),
                **_stretch(),
            )  # fmt: skip
            latest_x = dd.x_april_latest(x_series)
            shown_x = latest_x.assign(close_vs_cap=latest_x["close_vs_cap"] * 100.0).rename(
                columns={
                    "symbol": "Symbol",
                    "date": "Date",
                    "value": "x_april (log)",
                    "close_vs_cap": "Close vs 1.2 x NAV (%)",
                    "above_cap": "Above 1.2 x NAV",
                }
            )
            st.subheader("Latest value for each ETF")
            st.dataframe(
                shown_x,
                hide_index=True,
                column_config={
                    "x_april (log)": st.column_config.NumberColumn(format="%.3f"),
                    "Close vs 1.2 x NAV (%)": st.column_config.NumberColumn(format="%.1f"),
                },
                **_stretch(),
            )
            st.caption(
                "Blank days: no NAV known at the close, or the known NAV was more than 7 days "
                "old and was left out (data-quality flag `dq_nav_stale`)."
            )

# ---- Intraday iNAV ----------------------------------------------------------------------------
with tab_inav:
    snaps = tables.get("inav_snapshots")
    st.subheader("Intraday premium over iNAV")
    st.caption(
        "iNAV is the fund house's running estimate of what one unit is worth during the day. "
        "Each iNAV reading is paired with the nearest exchange price of the same ETF."
    )
    days = dd.snapshot_days(snaps)
    if not days:
        st.info(
            "No iNAV snapshots recorded yet. Start the recorder with `uv run etfp collect inav`."
        )
    else:
        sym = st.selectbox("ETF", symbols, key="inav_symbol")
        default_day = today if today in days else days[0]
        day = st.selectbox("Day (IST)", days, index=days.index(default_day), key="inav_day")
        if day != today:
            st.info(f"No snapshots for today ({today.isoformat()}) yet; showing {day.isoformat()}.")
        sources, gap = _inav_settings()
        paired = dd.intraday_premium(snaps, sym, day, sources, gap)
        if paired.empty:
            st.info(
                "No paired readings for this ETF and day. A pair needs an iNAV from "
                f"{', '.join(sources)} and an exchange price fetched within {gap:g} minutes."
            )
        else:
            last = paired.iloc[-1]
            cols = st.columns(4)
            cols[0].metric("Latest reading (IST)", f"{last['ts']:%H:%M}")
            cols[1].metric("Last price", _fmt_rs(last["last_price"]))
            cols[2].metric("iNAV", _fmt_rs(last["inav"]))
            cols[3].metric("Premium over iNAV", _fmt_pct(last["prem"]))
            st.plotly_chart(charts.intraday_figure(paired, sym, day), **_stretch())
            st.caption(f"iNAV source: {last['source']}. Premium over iNAV = last price / iNAV - 1.")

# ---- Events log -------------------------------------------------------------------------------
with tab_events:
    st.subheader("Events log")
    st.caption(
        "Dated rule changes, limits, cautions and notices from the `events` table. "
        "`first_tradable_session` is the first NSE session whose prices could react; "
        "`ts_ist` is the publication time where known. `verified` is true only once the primary "
        "document has been saved and checked."
    )
    log = dd.events_table(events)
    if log.empty:
        st.info("No events loaded yet (`data/manual/events.csv`, then `uv run etfp build`).")
    else:
        st.dataframe(
            log,
            hide_index=True,
            column_config={
                "url": st.column_config.LinkColumn("url"),
                "verified": st.column_config.CheckboxColumn("verified"),
            },
            **_stretch(),
        )
        n_ok = int(log["verified"].sum()) if "verified" in log.columns else 0
        st.caption(f"{len(log)} events; {n_ok} verified.")

# ---- Data quality -----------------------------------------------------------------------------
with tab_quality:
    st.subheader("Latest data by source")
    sources_table = dd.last_data_dates(tables, today)
    st.dataframe(sources_table, hide_index=True, **_stretch())
    st.caption(
        "Age is in calendar days from today (IST); weekends and market holidays make 1 to 3 days "
        "normal for daily sources."
    )

    beat = read_heartbeat()
    if beat is None:
        st.caption("iNAV recorder: no heartbeat file yet.")
    else:
        st.caption(
            f"iNAV recorder: last tick finished {beat.get('last_tick_finished')}; "
            f"{beat.get('ok', 0)} sources ok, {beat.get('failed', 0)} failed."
        )

    st.subheader("Data-quality flags")
    st.caption("A flag means a person should look at the row. Rows are never dropped or corrected.")
    flags = dd.dq_summary(etf_day)
    if flags.empty:
        st.info("The data has no `dq_*` flag columns yet.")
    else:
        st.dataframe(flags, hide_index=True, **_stretch())

# ---- About ------------------------------------------------------------------------------------
with tab_about:
    st.subheader("What this shows")
    st.markdown(
        """
Some India-listed ETFs that track foreign markets cannot create new units, because their fund
houses have reached the overseas investment limits. Their exchange price can then sit above the
value of what they hold. The gap is the **premium**.

* **NAV** is the fund's published value per unit. A US-linked fund's NAV dated *t* is struck from
  the US close, which comes after the Indian close, so at the Indian close the freshest known NAV
  is the one dated the day before.
* **Fair value** moves that known NAV to the Indian close using the index future and the rupee
  rate. **Premium over fair value** = close / fair value - 1.
* **Price limits (bands).** NSE sets a daily range around a base price. Until 4 Sep 2026 the base
  was the NAV of two trading days earlier. From 7 Sep 2026 it is the previous day's close.
  From 1 Apr 2027 (planned) it returns to the previous day's NAV.
* **iNAV** is the fund house's intraday estimate of NAV.

Limits: values come from public sources and a free futures feed; some days use the latest NAV
without adjustment (`nav_only`). Check official NSE and AMFI data for anything that matters.
"""
    )
    st.subheader("Price-band regimes")
    try:
        regimes = load_rules().regimes
        st.dataframe(
            pd.DataFrame(
                {
                    "Regime": [r.name for r in regimes],
                    "From": [r.start.isoformat() if r.start else "earlier" for r in regimes],
                    "To": [r.end.isoformat() if r.end else "open" for r in regimes],
                    "Base price": [r.base for r in regimes],
                    "Status": [r.status for r in regimes],
                }
            ),
            hide_index=True,
            **_stretch(),
        )
    except (FileNotFoundError, ValueError):
        st.caption("Regime table unavailable (config/rules.yaml could not be read).")
    st.caption("The events log has its own tab.")

st.divider()
st.caption(dd.DISCLAIMER)
