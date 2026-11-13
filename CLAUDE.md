# CLAUDE.md — ETF Premium Tracker

Read this first, every session. The full technical spec is `docs/SPEC.md`; read the section you need before building any component.

## What this project is

A data pipeline and live dashboard that tracks how far India-listed international ETFs trade above the value of what they hold (their "premium" over NAV). These ETFs cannot create new units because Indian mutual funds have hit their overseas investment caps, so demand only pushes the price up. In September 2026 SEBI changed how ETF price bands are anchored and premiums jumped from ~20% to over 200%. On 1 April 2027 the band anchor moves back to NAV.

The pipeline serves three outputs from one clean table:
1. **A research paper** (empirical finance) — analysis scripts read the clean table.
2. **A live premium tracker** — public dashboard + alerts.
3. **An open dataset** — cleaned data + code, published with a DOI.

The owner (Amish) is a computer engineering student learning finance. Explain finance logic in code comments and PR summaries in plain words. He makes all external decisions (publishing, data sharing, anything public).

## Non-negotiables

- **Raw data is immutable.** Every download is saved untouched under `data/raw/<source>/<YYYY-MM-DD>/`. Never edit or overwrite a raw file. Cleaning reads raw and writes to `data/clean/`.
- **Every number must be traceable** to a raw file. Clean tables keep a `source_file` column or equivalent lineage.
- **Correctness over features.** The fair-value and price-band functions are research-critical: each needs unit tests with hand-computed examples (see SPEC §6, §7) before anything uses it.
- **Dates and time zones are explicit.** Store dates as ISO `YYYY-MM-DD`; timestamps as timezone-aware (`Asia/Kolkata` for Indian market data, `America/New_York` / `Asia/Hong_Kong` for underlying markets). Never use naive datetimes.
- **No Bloomberg raw data in git.** `data/raw/bloomberg/` is gitignored (licence forbids redistribution). Same for any paid data.
- **Secrets in `.env`** (gitignored). Never print or commit tokens.
- **No investment advice.** The dashboard describes; it never says buy/sell/undervalued/opportunity. Include the disclaimer from SPEC §9.
- **Be polite to data sources.** Rate-limit requests (≥1 s between NSE calls), cache, retry with backoff, identify failures loudly. Never hammer an endpoint.
- **Flag uncertainty.** Items marked `VERIFY` in the spec are not confirmed. Check them against the live source before relying on them, and record what you found in `docs/DECISIONS.md`.

## Stack

- Python 3.11+, managed with `uv`
- pandas (polars allowed for heavy transforms), DuckDB + Parquet for storage
- `exchange_calendars` for NSE/NYSE/HKEX trading days
- Streamlit + Plotly for the dashboard (v1)
- pytest for tests, ruff for lint/format
- Scheduling: OS scheduler (cron on macOS/Linux, Task Scheduler on Windows) calling CLI commands
- Alerts: Telegram bot (token in `.env`)

## Layout

```
etf-premium/
  CLAUDE.md
  docs/SPEC.md            technical spec (source of truth)
  docs/DECISIONS.md       log of decisions and VERIFY results
  config/etfs.yaml        ETF universe and metadata
  config/rules.yaml       price-band regimes with dates
  data/raw/<source>/<date>/   untouched downloads (gitignored)
  data/clean/             parquet tables + etf.duckdb (gitignored)
  src/etfp/collect/       one module per source
  src/etfp/clean/         raw -> clean transforms
  src/etfp/features/      fair value, premiums, bands, units
  src/etfp/alerts/        alert rules + Telegram sender
  src/etfp/cli.py         entry points
  dashboard/app.py        Streamlit app
  analysis/               research scripts/notebooks (read clean table only)
  tests/                  unit + fixture tests
```

## Commands (create these as you build)

```
uv run etfp collect daily --date YYYY-MM-DD     # pull all daily sources
uv run etfp collect inav                         # one iNAV snapshot
uv run etfp build                                # raw -> clean -> features
uv run etfp backfill --from 2021-01-01           # historical pull, resumable
uv run etfp alerts                               # evaluate + send alerts
uv run streamlit run dashboard/app.py
uv run pytest -q
uv run ruff check . && uv run ruff format .
```

## How to work

- Build in the milestone order in SPEC §11. Don't start the dashboard before the clean table and its tests exist.
- Small, testable modules. Each collector: `fetch()` -> saves raw, `parse()` -> returns a typed DataFrame. Network code and parsing are separate so parsing is testable from saved fixtures.
- When a source changes format or blocks access, fail with a clear message naming the source and date; don't silently skip.
- After each milestone, update `docs/DECISIONS.md` and summarise what was built, what was verified, and what is still uncertain.
- Research context (why, theory, hypotheses) lives in Amish's research doc; you don't need it to build, but SPEC §2 summarises what the research needs from the data.

## Research handoff
Analysis runs in a separate Claude chat that only reads files. The interface:

`uv run etfp export research` writes exports/research/<YYYY-MM-DD>/:
- etf_day.parquet, inav_snapshots.parquet, units_monthly.parquet, events.parquet
- manifest.json: git commit hash, build time (IST), date range, row counts per
  symbol, data-quality flag counts, open VERIFY items, schema_version
- DATA_DICTIONARY.md: every column with definition, units, formula, source
Then write that folder's path to exports/research/LATEST.txt. exports/ is gitignored.

Rules:
- Never change a column's meaning without bumping schema_version and logging it in docs/DECISIONS.md.
- Run the export after every build.
- iNAV snapshots must keep total buy/sell quantity and order-book depth from the raw quote JSON.
- At the start of each session, read docs/REQUESTS.md, implement open items, and mark each done with its commit hash.
