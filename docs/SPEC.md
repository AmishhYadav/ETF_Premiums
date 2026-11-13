# ETF Premium Tracker — Technical Spec

Version 1 · 10 Nov 2026 · Owner: Amish Yadav

Items marked **VERIFY** are believed correct but must be confirmed against the live source before use. Record the result in `docs/DECISIONS.md`.

---

## 1. Background in one page

**ETF basics.** An ETF holds a basket of securities and trades on the exchange like a share.
- **NAV** (net asset value): value of holdings per unit, published once a day by the fund house (AMC) via AMFI.
- **iNAV** (indicative NAV): the AMC's real-time estimate of NAV during market hours, broadcast via the exchange.
- **Premium** = price / NAV − 1. A ₹120 price on a ₹100 NAV is a 20% premium.

**Why premiums normally stay near zero.** Market makers can create new units (give the AMC the underlying stocks/cash, get units, sell them) when price > NAV, and redeem units when price < NAV. This arbitrage keeps price ≈ NAV.

**Why it broke in India.** International ETFs must buy foreign stocks to create units. Indian mutual funds have per-fund-house (US$1 bn) and industry (US$7 bn) caps on overseas investment, and those caps are used up. So for most of these ETFs, no new units can be created; redemptions still work. Demand can only raise the price.

**The price band.** Exchanges limit how far an ETF can trade in a day:
- Until 4 Sep 2026: ±20% around the closing NAV of two days earlier (T-2). This capped premiums near 20%.
- From 7 Sep 2026: band around the **previous day's closing price**, so the ceiling ratchets up daily. Premiums ran to 80–235%.
- From 1 Apr 2027 (planned): band around the previous day's closing NAV (T-1). Premiums capped near 20% again.

## 2. What the research needs from this system

1. A daily ETF-level panel from Jan 2021 to Jun 2027: price, volume, trades, delivery %, NAV, fair value, three premium measures, band limits, units outstanding.
2. Whether each ETF could create units in each month (units outstanding rising = yes).
3. Live iNAV snapshots from now on (no public archive exists — this is unique data).
4. The daily "excess over the April 2027 cap" for the run-up to 1 Apr 2027.
5. Reproducibility: any figure in the paper regenerates from raw files with one command.

## 3. Scope

**v1 (must have):** ETF universe config; daily collectors (NSE, AMFI); iNAV snapshot collector; Bloomberg CSV importer; trading calendars; clean ETF-day table; fair value; premiums; band calculator; units-outstanding tracker; Streamlit dashboard; Telegram alerts; tests.

**v2 (later):** BSE data; backfill to 2021; minute-bar import from Bloomberg; notice reader (LLM agent logging SEBI/AMFI/exchange/AMC notices); silver ETF module; public hosting; dataset export with DOI.

**Out of scope:** trading signals, order placement, buy/sell recommendations, any user accounts.

## 4. ETF universe (`config/etfs.yaml`)

| Symbol (NSE) | Name | AMC | Underlying index | Underlying market | Group |
|---|---|---|---|---|---|
| MONQ50 | Motilal Oswal Nasdaq Q 50 ETF | Motilal Oswal | Nasdaq Q-50 | US | blocked |
| MON100 | Motilal Oswal Nasdaq 100 ETF | Motilal Oswal | Nasdaq-100 | US | unclear |
| MASPTOP50 | Mirae Asset S&P 500 Top 50 ETF | Mirae Asset | S&P 500 Top 50 | US | blocked |
| MAFANG | Mirae Asset NYSE FANG+ ETF | Mirae Asset | NYSE FANG+ | US | blocked |
| MAHKTECH | Mirae Asset Hang Seng TECH ETF | Mirae Asset | Hang Seng TECH | HK | blocked |
| HNGSNGBEES | Nippon India ETF Hang Seng BeES | Nippon India | Hang Seng Index | HK | possibly open |
| NIFTYBEES | Nippon India ETF Nifty 50 BeES | Nippon India | Nifty 50 | IN | control |

Add 3–5 more domestic equity ETFs as controls, plus GOLDBEES and SILVERBEES for the bullion module (v2). Each entry stores: symbol, name, AMC, AMFI scheme code (**VERIFY** per ETF), ISIN, underlying index, underlying market, currency, Bloomberg tickers (ETF, index, futures, FX), creation unit size, group label. The `group` label is a hypothesis; the units-outstanding data (§6.5) decides the real status.

## 5. Data sources

| Source | What | How | Frequency | Notes |
|---|---|---|---|---|
| NSE ETF archives | OHLC, close, traded qty/value, no. of trades, deliverable qty, delivery % | NSE historical archives ("ETFs: price, volume and delivery position") and full bhavcopy; libraries `nselib` or `jugaad-data` | Daily, after ~7 pm IST | **VERIFY** current file names/URLs (NSE moved to the UDiFF bhavcopy format in 2024). NSE rejects plain scripts; needs browser-like headers + session cookies. Run from an Indian residential IP. |
| NSE quote / iNAV | Last price, iNAV, bid/ask if available | NSE quote endpoint or AMC iNAV pages | Every 15 min, 9:15–15:30 IST | **VERIFY** which source exposes iNAV reliably. Save each snapshot raw as JSON. |
| AMFI | Daily NAV per scheme | AMFI NAV history download / daily `NAVAll.txt` | Daily, evening | Match by AMFI scheme code. **VERIFY** URL/format. |
| AMC disclosures | Monthly AUM (→ units outstanding), factsheets, addenda (suspension notices) | AMC websites, AMFI monthly data | Monthly | Mostly PDFs; v1 can be a manual CSV `data/manual/units.csv` |
| Bloomberg (manual export) | Underlying index closes, index futures at 15:30 IST, USD/INR and HKD/INR at 15:30 IST and at NAV time, optionally ETF NAV/shares | Excel BDH export on the college terminal → CSV dropped into `data/raw/bloomberg/` | Weekly batch | Importer must validate column names and date coverage. Never commit. |
| FBIL | USD/INR reference rate | FBIL website | Daily | Fallback FX if Bloomberg missing |
| Trading calendars | NSE, NYSE, HKEX holidays | `exchange_calendars` (XBOM or XNSE for India — **VERIFY** which exists, XNYS, XHKG) | — | Critical for NAV alignment |
| Rules | Price-band regimes | `config/rules.yaml`, hand-maintained | On change | See §7 |

## 6. Calculations (`src/etfp/features/`)

### 6.1 NAV alignment (rewritten 2026-11-13 to match DECISIONS D10, D18)
- A fund publishes one NAV per date. The NAV dated *d* is struck from the close of the last underlying-market session on or before *d*; on an underlying-market holiday it is carried forward.
- **US-linked ETFs:** the NAV dated *t* uses the US close of *t*, about 10 hours after the Indian close on *t*, and is published by 10:00 IST on *t+1* (SEBI circular of 29 Mar 2023). At the Indian close on *t* the freshest known NAV is normally the one dated *t−1*.
- **HK-linked ETFs:** Hong Kong closes 13:30 IST, before the Indian close, so the NAV dated *t* is already struck at 15:30 IST (timing still VERIFY).
- For each Indian trading day *t* store: `nav_same_date` (NAV dated *t*), `nav_known_at_close` with `nav_known_date` and `nav_known_underlying_date` (the freshest NAV whose underlying close is at or before 15:30 IST on *t*; on a US holiday this is the carried-forward NAV dated *t*), `nav_publication_lag_days` (calendar days from `nav_known_date` to *t*) and `nav_t2` (NAV dated two Indian trading days earlier, the old band base).
- The rule is re-estimated per ETF and year because it may differ before 1 Jul 2023 or by AMC.

### 6.2 Fair value (moves the freshest known NAV to the Indian close)

```
FV_t = NAV_a × (F_t[15:30 IST] / F[US close of a]) × (S_t[15:30 IST] / S_a)
```
- *a* = `nav_known_underlying_date`; NAV_a = `nav_known_at_close`.
- F = front-month index future (E-mini Nasdaq-100 for Nasdaq ETFs; E-mini S&P 500 as proxy for S&P Top 50 and FANG+ — proxies logged in DECISIONS.md).
- S = rupee exchange rate (USD/INR or HKD/INR).
- For HK ETFs: futures term = 1, FX term only.
- If any required input is missing → FV = NAV_a and `fv_method = 'nav_only'`; otherwise `'bloomberg'` or `'free_futures'` (no NAV → `'no_nav'`).
- **Tests:** hand-computed example: NAV 100, F used 20,000, F at 15:30 20,200, S used 84.00, S at 15:30 84.42 → FV = 100 × 1.01 × 1.005 = 101.505.

### 6.3 Premium measures (store all)
- `prem_raw` = close / `nav_same_date` − 1 (what the press quotes; non-synchronous, because that NAV is struck after the Indian close)
- `prem_nav_known` = close / `nav_known_at_close` − 1
- `prem_band_ref` = close / `nav_t2` − 1 (what the pre-Sep-2026 band constrained; T-2 = two Indian trading days earlier)
- `prem_fv` = close / FV_t − 1, and `log_prem_fv` = ln(close / FV_t)
- `prem_inav` = last_price / iNAV − 1 (snapshots only)
- `x_april` = ln(close / (1.2 × FV_t)) — excess over the April 2027 cap; > 0 means price exceeds what will be allowed after 1 Apr 2027. `x_april_nav_known` uses `nav_known_at_close` instead of FV (BLUEPRINT definition); both kept until Amish picks one (D20).

### 6.4 Price band calculator
Given date and regime (from `config/rules.yaml`), return `base_price`, `band_low`, `band_high` (initial and maximum after widening), and flag `closed_at_upper` / `closed_at_lower` (close within 0.1% of the limit — tolerance in config).

### 6.5 Units outstanding / valve status
- units_outstanding = AUM / NAV at month end (from AMC/AMFI disclosures).
- `valve_open` for month m = units_outstanding rose by more than 1% vs previous month (threshold in config). Store the raw numbers, not just the flag.

### 6.6 Other derived columns
turnover = traded value / AUM; avg trade size = traded value / trades; returns of ETF price, NAV and FV (log).

## 7. Price-band regimes (`config/rules.yaml`)

| Regime | Dates | Base price | Equity/debt ETF band | Gold/silver ETF band |
|---|---|---|---|---|
| `nav_t2` | until 2026-09-04 | closing NAV of T-2 | fixed ±20% | fixed ±20% |
| `prev_close` | 2026-09-07 to 2027-03-31 | previous day's close = VWAP of last 30 min; else last traded price; else latest NAV | starts ±10%, widens by 5% of base after 15-min cooling-off (5 min in last 30 min), max 2 widenings → ±20% | starts ±6%, widens in 3% steps, no cap; pre-open call auction |
| `nav_t1` | from 2027-04-01 (planned) | previous day's closing NAV | same dynamic band as `prev_close` | same |

Sources: SEBI circular HO/47/11/11(1)2026-MRD-POD3/I/13804/2026 (15 Jun 2026); extension circular of 28 Aug 2026; NSE circular 122/2026 (1 Sep 2026). **VERIFY** details against the NSE circular PDF before coding; the April 2027 regime may change — keep regimes data-driven so a new one is a config edit, not a code change. Overnight/liquid ETFs (±5%) are out of scope.

## 8. Storage

DuckDB file `data/clean/etf.duckdb` plus Parquet exports. Core tables:

- `etf_master` — from `config/etfs.yaml`
- `prices_daily` — symbol, date, open, high, low, close, vwap_last30 (if available), prev_close, volume, value, trades, deliv_qty, deliv_pct, source_file
- `nav_daily` — symbol, scheme_code, nav_date, nav, source_file
- `inav_snapshots` — symbol, ts (tz-aware IST), last_price, inav, bid, ask, source_file
- `underlying_daily` — instrument, date, close, value_at_1530_ist, source_file
- `fx_daily` — pair, date, rate_1530_ist, rate_nav, source, source_file
- `units_monthly` — symbol, month_end, aum, nav, units, source_file
- `events` — date, type (rule/limit/suspension/warning/other), symbols, description, url, verified (bool)
- `etf_day` — the analysis table: one row per symbol per Indian trading day with every column in §6, plus `regime`, `valve_open`, `fv_method`, data-quality flags

`etf_day` is rebuilt from scratch by `etfp build`; it is never edited by hand.

## 9. Dashboard (`dashboard/app.py`)

Pages:
1. **Overview** — table of all tracked ETFs: latest close, NAV, fair value, premium (fair value), premium (raw), change vs 1 day / 1 week, band high/low and % distance to band, `x_april`, last iNAV snapshot time. Sort by premium.
2. **ETF detail** — premium history (raw and fair value) with event markers from `events`; price vs NAV vs FV; volume and delivery %; band limits shaded.
3. **April 2027 countdown** — days to 1 Apr 2027; each ETF's `x_april` over time with the zero line; plain-English explainer of what happens on that date.
4. **Methodology** — how each number is computed, data sources, update times, known limitations.
5. **Events log** — table from `events`.

Every page shows: data as-of timestamp, and this disclaimer: *"For research and information only. Not investment advice. Figures may contain errors; check official NSE and AMFI data before acting."*

## 10. Alerts

Rules in `config/alerts.yaml`, evaluated after each build and each iNAV snapshot:
- premium (fair value) crosses configurable levels (e.g. 25%, 50%, 100%) up or down
- close at upper or lower band
- 1-day premium change > X points
- data failure: a collector failed or data is stale beyond expected time

Send via Telegram; de-duplicate (one alert per rule per symbol per day).

## 11. Milestones (build in this order)

| # | Milestone | Done when |
|---|---|---|
| M0 | Repo skeleton, uv, ruff, pytest, config files, calendars | `pytest` passes; `etfp --help` works |
| M1 | NSE + AMFI daily collectors with raw saving and parsers | Two weeks of data parsed from saved fixtures; tests on parsers |
| M2 | iNAV snapshot collector + scheduler | Runs every 15 min on market days for a full day without errors |
| M3 | Bloomberg CSV importer, fair value, premiums, band calculator, `etf_day` build | Hand-computed tests pass; spot-check 5 ETF-days against Value Research-reported premiums (e.g. Q50 ~19.5% on 4 Sep 2026, ~110% on 7 Oct 2026) |
| M4 | Dashboard v1 + alerts | Runs locally from `etf_day`; alerts reach Telegram |
| M5 | Backfill 2021–present, units-outstanding table, events table | Full panel built; valve status per ETF-month documented |
| v2 | BSE, minute bars, notice reader, silver module, hosting, dataset export | As prioritised later |

Priority note: start M2 (iNAV snapshots) as early as possible — every day not recorded is lost permanently.

## 12. Data quality checks (run in `etfp build`)

- No duplicate (symbol, date) rows.
- NAV and close present for every trading day an ETF traded; log gaps.
- |prem_fv| > 300% or day-over-day premium change > 50 points → flag for manual review, don't drop.
- NAV date alignment check against calendars (§6.1).
- FV within ±5% of NAV unless the underlying market moved > 5% (flag otherwise).
- Units outstanding non-negative and consistent with AUM/NAV.

## 13. Open questions (resolve and log in DECISIONS.md)

1. Exact current NSE archive URLs/format and whether `nselib`/`jugaad-data` still work.
2. Best reliable source of iNAV snapshots.
3. AMFI scheme codes for each ETF.
4. Which FX rate and US close each AMC uses in NAV (check scheme information documents).
5. Proxies for S&P 500 Top 50 and NYSE FANG+ futures.
6. Whether the closing price on NSE equals the last-30-minute VWAP in the bhavcopy, or needs computing.
7. Whether Bloomberg carries iNAV tickers for these ETFs.
8. Hosting choice for the public dashboard (data refreshed from the local machine).
