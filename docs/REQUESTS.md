# Requests from the research side

The research analysis runs in a separate chat that only reads the files under `exports/research/`. When it needs something from the pipeline (a new column, a fix, a different definition), the request is written here.

At the start of every build session: read this file, implement the open items, and mark each one done with the commit hash.

## Format

```
### R<number> — <short title>
- Asked: YYYY-MM-DD
- Need: what is needed and why, in one or two sentences
- Status: open | done (<commit hash>, YYYY-MM-DD) | declined (<reason>)
- Notes: schema_version change, DECISIONS.md entry, anything the research side must know
```

Changing what an existing column means needs a `schema_version` bump and a `docs/DECISIONS.md` entry.

## Open

### R1 — Daily units outstanding and creation/redemption events
- Asked: 2026-11-10
- Need: Carry `IssdCptl` from the NSE security master as a daily `units_outstanding` column for every ETF, from the earliest available file. Add `units_change` and `creation_units_change` (change divided by the fund's creation-unit size) and an event row whenever units change. The research dates every creation and redemption from this; it replaces the monthly 1% rule in SPEC 6.5.
- Status: done (298d62a, 2026-11-14): `units_daily` from `IssdCptl` (security master from 2024-02-05, D22), `units_outstanding`, `units_change`, `creation_units_change` (= `creation_event`) in etf_day, `units_change` event rows; AUM/NAV cross-check kept as `dq_units_aum_mismatch`. schema_version 1.0.0
- Notes: Keep the monthly AUM/NAV cross-check as a data-quality flag, not as the valve definition. schema_version bump.

### R2 — Official band beside the computed band
- Asked: 2026-11-10
- Need: Store `band_low_official` and `band_high_official` from `PricRg`, with the file date and the session they apply to, next to the computed band, plus a mismatch flag. The research treats days at the upper band as censored demand and needs the exact ceiling.
- Status: done (298d62a, 2026-11-14): `band_low_official`, `band_high_official`, `band_file_date`, `band_session_date` (next NSE session, D17), `band_mismatch` (one tick). Note D24: Muhurat sessions are sessions, so the file dated 6 Nov 2026 applies to Sun 8 Nov
- Notes: DECISIONS D4 says the file dated t gives the band for the next session; keep both dates explicit.

### R3 — NAV timing columns
- Asked: 2026-11-10
- Need: For each ETF-day keep `nav_same_date`, `nav_known_at_close` with `nav_known_date`, and `nav_publication_lag_days`. Compute `prem_raw` on the first, `prem_nav_known` on the second, and build fair value from the second (DECISIONS D10).
- Status: done (298d62a, 2026-11-14): `nav_same_date`, `nav_known_at_close`, `nav_known_date`, `nav_known_underlying_date`, `nav_publication_lag_days`, `prem_raw`, `prem_nav_known`; fair value from the known NAV. SPEC 6.1-6.3 rewritten (D18)
- Notes: SPEC 6.1 and 6.2 need rewriting to match D10; log it.

### R4 — Feeder-fund (fund of funds) table
- Asked: 2026-11-10
- Need: New table `fof_monthly`: fund of funds name, AMFI scheme code, month end, AUM, units of the underlying ETF held (from the AMC monthly portfolio), and subscription status (open / lump sum shut / SIP shut) with notice date. Feeder funds value the ETF at the exchange price and buy on the exchange, so their flows are price-insensitive demand.
- Status: partly done (e898930, 2026-11-14): `fof_monthly` loader, validation, template, docs/manual_data.md. Data and AMFI confirmation of the feeder list still to enter
- Notes: Manual CSV under data/manual/ is fine for v1. Feeders: Motilal Oswal Nasdaq 100 FoF; Mirae Asset NYSE FANG+ ETF FoF, S&P 500 Top 50 ETF FoF, Hang Seng TECH ETF FoF. Confirm the list from AMFI.

### R5 — Seed the events table
- Asked: 2026-11-10
- Need: Seed `events` with the dated events in the research dossier (overseas-cap history 2020-2026, price-band steps Feb-Sep 2026, BSE and NSE cautions, AMC notices), each with a source URL and `verified = false` until the primary document is saved under data/raw/rules/.
- Status: partly done (e898930, 2026-11-14): loader + `verified` flag; 6 seed rows from repo docs. Waiting for the research-side CSV at data/manual/events.csv
- Notes: The research side will supply the CSV; the pipeline needs the loader and the verified flag.

### R6 — Creation-unit sizes
- Asked: 2026-11-10
- Need: Set `creation_unit_size` in config/etfs.yaml: MONQ50 600000, MON100 250000 (scheme documents). Confirm MASPTOP50 and MAFANG from the latest Mirae factsheets; keep them in `verify` until then.
- Status: partly done (e898930, 2026-11-14): MONQ50 600000 and MON100 250000 set; MASPTOP50 and MAFANG null and listed in `verify` until Mirae factsheets are checked
- Notes: Units changes in DECISIONS D7 are exact multiples of these sizes, which supports both the sizes and the reading of `IssdCptl`.

### R7 — Band rules: history and widening test
- Asked: 2026-11-10
- Need: Find the exchange circular that introduced the T-2 NAV base and record the regime before it in config/rules.yaml (the sample starts in Jan 2021 and the T-2 rule may date from about 2022). Add the breadth test for widening if the final SEBI circular kept it: at least 50 trades from 10 client codes and 3 trading members on each side at or beyond 9.90%.
- Status: partly done (e898930, 2026-11-14): `nav_t2` start marked VERIFY; final-circular widening rules recorded (no breadth test, moving side only). Circular introducing T-2 still to find; `regime_inferred` (R10) will date it from data
- Notes: Mark the `nav_t2` start date as VERIFY until found. Update 2026-11-12: the final circular (HO/47/11/11(1)2026-MRD-POD3/I/13804/2026, 15 Jun 2026, para 5.1) did not keep the breadth test. Widening needs only trades at 9.90% from the base and a cooling-off of 15 minutes (5 in the last half hour); the band then widens by 5% of the base, at most twice per direction, on the moving side only. NSE's implementing circular is NSE/CMTR/76101 (ref 122/2026), 1 Sep 2026.

### R8 — Securities lending data
- Asked: 2026-11-10
- Need: Collect NSE's SLB eligible-securities list (latest and history) and SLB trade data for the six international ETFs. The paper must show, not assume, whether these units could be borrowed and shorted.
- Status: open
- Notes: MON100 was admitted to SLB in Sep 2013; current status unknown.

### R9 — BSE daily prices and volumes
- Asked: 2026-11-11
- Need: Daily BSE close, units traded, value and trades for every tracked ETF, stored beside the NSE figures. Add `turnover` = (NSE units + BSE units) / `units_outstanding`. Without BSE the turnover of these ETFs is understated, and a rupee turnover mixes in the premium.
- Status: partly done (298d62a, 2026-11-14): `turnover` = units traded / `units` with NSE volume; BSE columns exist and are empty until a BSE collector is built
- Notes: Needed for paper A. Keep NSE-only columns unchanged; new columns only. schema_version bump.

### R10 — Ceiling on the premium, limit flags and inferred regime
- Asked: 2026-11-11
- Need: Per ETF-day: `cap_pi` = ln(`band_high_official` / `fv`); `hit_up` (high >= initial upper limit minus one tick), `hit_up_max` (high >= maximum limit minus one tick), `last_at_up` (last trade at the limit), and the same three for the lower limit. Also `regime_inferred`: which base reproduces the midpoint of the official band within one tick (NAV of t-2, previous close, NAV of t-1, other). For dates before the first security-master file, set it from prices: does any trade fall outside 0.8 to 1.2 x NAV(t-2)?
- Status: done (298d62a, 2026-11-14): `cap_pi` (official), `cap_pi_computed`, `hit_up`, `hit_up_max`, `last_at_up` and lower twins, `regime_inferred` (+ `regime_inferred_method`; price test before 2024-02-05)
- Notes: Needed for paper A. The old ceiling on the quoted premium was 1.2 x NAV(t-2) / NAV(t) - 1, so it moved every day; the research treats `cap_pi` as a moving censoring point. `regime_inferred` answers R7's "when did the T-2 rule start" from data.

### R11 — Units outstanding before the first security-master file
- Asked: 2026-11-11
- Need: Monthly `units` = AUM / NAV for each tracked ETF from Jan 2021 to the first available security-master date, from AMFI and fund-house monthly disclosures, in `units_monthly` with a `source` column. DECISIONS D4 found HTTP 404 for the file of 2 Jan 2024, so `IssdCptl` cannot date the Jan 2022 suspension or the Jun 2022 reopening.
- Status: partly done (e898930, 2026-11-14): `units_monthly` loader + template. Security master starts 2024-02-05 (D22), so monthly data needed Jan 2021 to Feb 2024 — to enter by hand
- Notes: Needed for paper A. Manual CSV under data/manual/ is fine. Check whether MONQ50's 3,275,018 units (1 Jul 2024) equal its units at launch (Dec 2021).

### R12 — Beta-adjusted fair value
- Asked: 2026-11-11
- Need: `fv_beta`: the D10 fair value with the futures return scaled by each fund's beta, where beta is the slope of the fund's NAV log return (net of the currency return) on its futures proxy's close-to-close log return, estimated on the 250 NAV dates before 2026-09-07. Store `fv_beta_value` (the beta) and its standard error per ETF. Keep `fv` (beta = 1) as it is.
- Status: done (298d62a, 2026-11-14): `fv_beta`, `fv_beta_value`, `fv_beta_se`, `fv_beta_n`; NaN until futures history exists (Bloomberg import or Yahoo)
- Notes: Needed for paper A. The Nasdaq Q-50, NYSE FANG+ and S&P 500 Top 50 are not the indices the NQ and ES futures track. Every result is reported with both `fv` and `fv_beta`.

### R13 — Limit-day diagnostics from intraday data
- Asked: 2026-11-11
- Need: Per ETF-day where a limit was touched: time of first touch, minutes spent at the limit, number of band widenings, and total sell quantity (upper limit) or buy quantity (lower limit) in the book at the limit, from the iNAV/depth snapshots and from Bloomberg minute bars where imported. Flag `locked_up` / `locked_down` when the opposite side of the book is empty at the limit.
- Status: open
- Notes: Separates a touched limit from a locked one. For Sep-Oct 2026 this depends on the Bloomberg minute-bar import; say in the manifest which days have it.

### R14 — Feeder holdings and net float
- Asked: 2026-11-11
- Need: Extend R4's `fof_monthly` with `etf_units_held` (from the feeder's monthly portfolio), `fof_net_flow` (AMFI monthly) and the feeder's daily NAV. Add `feeder_units` (sum over feeders, carried forward daily) and `float_net` = (`units_outstanding` - `feeder_units`) x `nav_known_at_close` to the ETF-day table.
- Status: partly done (298d62a, 2026-11-14): `feeder_units` (carried forward, 10-day publication lag) and `float_net` in etf_day; empty until fof_monthly has data. Feeder daily NAV not built
- Notes: Research finding of 2026-11-11: Motilal Oswal Nasdaq 100 FoF stopped new SIPs on 2024-12-10 and paused existing SIPs from 2025-01-05, citing exhausted overseas limits (addendum dated 2024-12-24), which implies its inflows had been met by creations; Mirae's feeders take only SIPs registered before 2024-01-05. The Mirae FANG+ feeder (about Rs 3,085 crore at market price) may hold 43-50% of the ETF's units. Feeders are mainly a supply channel, not a demand channel; R4's rationale is corrected by this.

### R15 — Google Trends daily series
- Asked: 2026-11-11
- Need: Daily search interest for India for a fixed term list ("MONQ50", "Nasdaq Q50 ETF", "MON100", "Motilal Oswal Nasdaq 100 ETF", "MAFANG", "Mirae FANG ETF", "international ETF premium"), pulled in fixed 90-day windows that overlap by 30 days, from 2024-01-01, raw responses saved under data/raw/trends/<pull date>/. Chain the windows on the overlaps into one series per term.
- Status: partly done (e898930, 2026-11-14): `trends_daily` loader and window chaining. Raw CSVs still to pull by hand into data/raw/trends/<pull date>/
- Notes: Trends rescales each window to 0-100, so a series pulled later will not match one pulled now. Rate-limit; manual CSV download from the Trends site is acceptable for v1.

### R16 — Event timestamps
- Asked: 2026-11-11
- Need: Add to `events` (R5): `ts_ist` (publication time, IST, null if unknown), `first_tradable_session` (the first NSE session that could react), `category` (rule / cap / caution / amc_notice / fund_status / media), `direction` (premium_reducing / premium_preserving / neutral, set before prices are looked at) and `source_primary` (URL or raw-file path of the original document).
- Status: done (e898930, 2026-11-14): `ts_ist`, `first_tradable_session` (computed if blank), `category`, `direction` (left blank until classified), `source_primary`. See DECISIONS D23
- Notes: BSE's caution of 2026-09-25 was reported after the close, so its first tradable session is 2026-09-28. The April 2027 registration classifies events by `direction`.

### R17 — China comparison (Bloomberg)
- Asked: 2026-11-11
- Need: Daily price, NAV and shares outstanding from Jan 2024 for six China-listed quota (QDII) ETFs on the Nasdaq-100 and S&P 500, imported through the Bloomberg CSV importer into `china_etf_daily`. Candidate codes, to confirm on the terminal: 513100, 159941, 513300, 159509 (Nasdaq), 513500, 159612 (S&P 500).
- Status: open
- Notes: Outside control for a global rush into US technology shares. Chinese ETFs have a 10% limit on the previous close and issuer trading halts. Never commit the raw export.

### R18 — Research export
- Asked: 2026-11-11
- Need: `uv run etfp export research` writes `exports/research/etf_day.parquet` (all ETFs, all columns), `events.parquet`, `units_monthly.parquet`, `fof_monthly.parquet`, a `schema.json` with `schema_version` and one line per column (name, type, definition, source), and `manifest.json` (build time, date range per source, count of missing ETF-days per column, recorder gaps).
- Status: done (298d62a, 2026-11-14): `uv run etfp export research` (also runs after every `etfp build`) writes exports/research/<date>/ with etf_day, inav_snapshots, units_monthly, units_daily, unit_events, fof_monthly, trends_daily, events (.parquet), schema.json, DATA_DICTIONARY.md, manifest.json, and LATEST.txt
- Notes: The research side reads only this folder. Column names the analysis expects are listed in docs/BLUEPRINT.md, section "Measures" (same text as the project doc research/BLUEPRINT.md).

### R19 — Regression targets from the paper's spot checks
- Asked: 2026-11-12
- Need: Pipeline tests that reproduce, from `tests/fixtures`, the file-based numbers in the second draft of the paper (Tables 5, 7, 8). Reference implementation: `paper/draft2/analysis/spot_checks.py` (standard library only), expected values in `paper/draft2/analysis/spot_checks.json`. At minimum: (a) 2026-09-04 closes recovered from the next-session band equal floor(1.2 x NAV(2 Sep), 2 dp) for MONQ50 141.93, MON100 324.67, MASPTOP50 79.86, MAFANG 207.56, MAHKTECH 22.10, and not for HNGSNGBEES (471.09 vs 546.57); (b) 2026-11-09: six funds worth Rs 19,451 crore at NAV and Rs 4,875 crore more at the close; `prem_raw` / `prem_known` for MONQ50 100.8% / 107.1%; (c) `creation_event` for MONQ50 between 2024-07-01 and 2026-09-04 sums to +19.
- Status: done with two findings (298d62a, 2026-11-14): (a) and (c) reproduce exactly; (b) aggregates and MONQ50 prem_raw 100.8% reproduce; prem_known 107.1% only reproduces once AMFI has the NAV dated 6 Nov (fixtures lack it; strict xfail). **For the research side:** the pipeline counts 9 Nov 2026 as session 44, not 43, because Sun 8 Nov 2026 was a Diwali Muhurat session that NSE treats as a session (its close is the 9 Nov PREV_CLOSE and band base; DECISIONS D24). 4 Nov is still session 40, so the P2 window is unaffected. Please confirm which count the paper should use
- Notes: The trading calendar must treat 2026-09-14 as an NSE holiday (18 Sep is session 9 after the change, 4 Nov session 40, 9 Nov session 43); the paper's P2 window (sessions 16-40) depends on it. If the pipeline's numbers differ from these, tell the research side before changing either.

## Done

_None yet._
