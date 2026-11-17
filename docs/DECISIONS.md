# Decisions and VERIFY log

Every design decision and every `VERIFY` result from `docs/SPEC.md` is recorded here, newest section last. Each entry says what was checked, how, and what is still not confirmed.

Status words: **CONFIRMED** (checked against the live source), **PRELIMINARY** (evidence found, needs a second check), **OPEN** (not checked yet).

---

## 2026-11-10 — Live source checks before any code

All checks were run from the project Mac (Indian IP, IST). 10 Nov 2026 was an NSE holiday, so nothing intraday could be observed. Sample files from these checks are the first test fixtures.

### D1. NSE daily prices and delivery — CONFIRMED
- URL: `https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_DDMMYYYY.csv`
- HTTP 200 for 04-01-2021 and 09-11-2026 with a browser-like User-Agent; no cookies needed.
- Columns: `SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, LAST_PRICE, CLOSE_PRICE, AVG_PRICE, TTL_TRD_QNTY, TURNOVER_LACS, NO_OF_TRADES, DELIV_QTY, DELIV_PER` (values have a leading space after each comma).
- All seven tracked ETFs are present in series `EQ`.
- Decision: this file is the primary daily price source for the whole 2021–2027 sample. `nselib` / `jugaad-data` are not needed (SPEC §13 question 1).

### D2. NSE UDiFF bhavcopy — CONFIRMED (start date OPEN)
- URL: `https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_YYYYMMDD_F_0000.csv.zip`
- Adds ISIN (`ISIN`), exact rupee turnover (`TtlTrfVal`), settlement price (`SttlmPric`).
- OPEN: earliest available date (expected mid-2024).

### D3. Is the NSE close the last-30-minute VWAP? — CONFIRMED in data (SPEC §13 question 6)
- MONQ50 on 9 Nov 2026: `CLOSE_PRICE` 229.67, `LAST_PRICE` 231.76. The published close is not the last trade.
- Decision: store both; do not compute a VWAP ourselves. "Closed at the band" cannot be read from the close alone (see D6).
- OPEN: read NSE's own definition in circular 122/2026.

### D4. Official price band and units outstanding — CONFIRMED source, PRELIMINARY interpretation
- URL: `https://nsearchives.nseindia.com/content/cm/NSE_CM_security_DDMMYYYY.csv.gz`
- HTTP 200 for 01-07-2024, 02-01-2025, 01-04-2026, 04-09-2026, 07-09-2026, 08-09-2026, 06-11-2026, 09-11-2026; **404 for 02-01-2024**. Earliest date OPEN.
- `PricRg` = the initial band for the **next** session. Evidence: file dated 9 Nov 2026 gives MONQ50 `206.71-252.63`, which is 229.67 (the 9 Nov close) × 0.90 and × 1.10. File dated 4 Sep 2026 gives `127.74-156.12`, midpoint 141.93, and 141.93 ÷ NAV 118.7371 = 19.53% (Value Research reported 19.5% for 4 Sep).
- `IssdCptl` = units outstanding (values move in round thousands, e.g. 3,275,018 → 8,075,018 → 14,675,018 for MONQ50).
- Rows exist per series; use series `EQ`.
- OPEN: how quickly `IssdCptl` is updated after a creation/redemption; reconcile with AMC month-end AUM ÷ NAV before using it to classify the valve.

### D5. Band regimes — CONFIRMED from official data
Ratio of band high to band low in `PricRg`:
- 1.50 (= 1.2 / 0.8, i.e. ±20%) on 1 Jul 2024, 2 Jan 2025, 1 Apr 2026: regime `nav_t2`.
- 1.2222 (= 1.1 / 0.9, i.e. ±10% around previous close) in the file dated 4 Sep 2026, which applies to 7 Sep: regime `prev_close`.
- GOLDBEES 1.1277 (= 1.06 / 0.94, i.e. ±6%) from the same date.
- Rounding seen on 9 Nov: band low rounded up, band high rounded down, to ₹0.01. PRELIMINARY (one day).
- `sec_list_DDMMYYYY.csv` shows "No Band" for ETFs and is not useful.

### D6. Band flags — DECISION
Because the close is a 30-minute average, three separate flags are stored: day high touched the initial limit; day high touched the maximum limit; last traded price at the limit. Widening is inferred from `high > initial band high`. Exact cooling-off timing needs minute bars (v2).

### D7. Units outstanding from the security master — PRELIMINARY finding
| ETF | 1 Jul 2024 | 2 Jan 2025 | 1 Apr 2026 | 4 Sep 2026 | 9 Nov 2026 |
|---|---|---|---|---|---|
| MONQ50 | 3,275,018 | 8,075,018 | 14,675,018 | 14,675,018 | 14,675,018 |
| MON100 | 498,384,740 | 504,884,740 | 504,134,740 | 504,134,740 | 504,134,740 |
| MASPTOP50 | 172,474,040 | 168,624,040 | 168,624,040 | 168,624,040 | 168,624,040 |
| MAFANG | 253,303,596 | 248,903,596 | 247,303,596 | 247,303,596 | 247,303,596 |
| MAHKTECH | 192,911,852 | 192,911,852 | 193,711,852 | 193,711,852 | 193,711,852 |
| HNGSNGBEES | 19,111,680 | 23,586,680 | 23,586,680 | 23,586,680 | 23,586,680 |
| NIFTYBEES | 1,101,166,386 | 1,379,216,385 | 2,239,266,385 | 2,427,116,385 | 2,586,291,385 |

Reading (not yet confirmed against AMC disclosures): MONQ50 units grew about 4.5 times between Jul 2024 and Apr 2026, so its creation was not fully shut in that period; HNGSNGBEES units have not changed since Jan 2025 although its premium is low. Both affect the `group` labels in `config/etfs.yaml`, which stay hypotheses until the units data is reconciled.

### D8. ETF universe — CONFIRMED source
`https://nsearchives.nseindia.com/content/equities/eq_etfseclist.csv`: 360 ETFs with ISIN, underlying and listing date. The clean table is built for all listed ETFs; `config/etfs.yaml` marks the tracked ones.

### D9. AMFI NAV — CONFIRMED (SPEC §13 question 3)
- Daily: `https://www.amfiindia.com/spages/NAVAll.txt` (redirects to `portal.amfiindia.com`). Semicolon-separated.
- History: `https://portal.amfiindia.com/DownloadNAVHistoryReport_Po.aspx?frmdt=DD-Mon-YYYY&todt=DD-Mon-YYYY`. Works for Jan 2021. An unfiltered 10-day window is about 9 MB. OPEN: filter parameters (`mf=`, `tp=`) and the maximum window.
- Scheme codes and ISINs: MONQ50 149438 / INF247L01AU3; MON100 114984 / INF247L01AP3; MASPTOP50 149169 / INF769K01HP3; MAFANG 148927 / INF769K01HF4; MAHKTECH 149379 / INF769K01HS7; HNGSNGBEES 140095 / INF204KB19I1; NIFTYBEES 140084 / INF204KB14I2; GOLDBEES 140088 / INF204KB17I5.
- The NAV history is not adjusted for face-value splits: MON100 was 920.27 on 1 Jan 2021 and 256.11 on 9 Nov 2026 (10:1 split, ex-date 17 Jun 2021 per NSE). Needs `config/corporate_actions.yaml`.

### D10. NAV timing — CONFIRMED for MON100, Sep 2026; SPEC §6.1 corrected
SPEC §6.1 says NAV(t) for US-linked ETFs reflects the *previous* US close. The data say otherwise. MON100 NAV log returns against the Nasdaq-100 close and USD/INR (FRED series `NASDAQ100`, `DEXINUS`):

| NAV dates | NAV return | Same-date index + FX | Previous-session index |
|---|---|---|---|
| 1→2 Sep 2026 | +0.247% | +0.248% | −1.298% |
| 2→3 Sep | +0.648% | +0.649% | +0.227% |
| 3→4 Sep | +0.220% | +0.210% | +1.156% |
| 4→7 Sep (US Labor Day) | −0.005% | no US session | +0.210% |
| 7→8 Sep | +0.227% | +0.226% (4→8 Sep) | 0.000% |
| 8→9 Sep | +0.009% | −0.008% | −0.123% |
| 9→10 Sep | −0.729% | −0.720% | −0.292% |

Conclusion: the NAV dated *t* uses the US close of date *t*, which happens about 10 hours after the Indian close on *t*. It is published the next morning (SEBI circular of 29 Mar 2023 allows overseas schemes until 10:00 on T+1, from 1 Jul 2023). On a US holiday the NAV is carried forward (the −0.005% is about three days of expenses).

Decisions:
- Fair value at the Indian close on *t* starts from the latest NAV whose underlying market had already closed, normally the NAV dated *t−1* for US ETFs and *t* for Hong Kong ETFs: `FV_t = NAV_a × F_t[15:30 IST] / F[US close of a] × S_t[15:30 IST] / S_a`.
- `prem_raw = close_t / NAV_t − 1` is kept because the press quotes it, and documented as non-synchronous. `prem_nav_known = close_t / NAV_a − 1` is added.
- The alignment is re-estimated per ETF and year during the build, because the rule may differ before 1 Jul 2023 or by AMC.
- Overseas NAVs for *t* are not on AMFI on the evening of *t*: the daily job has an evening run and a run at about 10:30 IST next morning.
- OPEN: timing for Hong Kong ETFs; timing before Jul 2023; the FX rate and time each AMC uses (SPEC §13 question 4).
- OPEN for April 2027: a T-1 NAV for US ETFs is only published after the 09:15 open, so how exchanges will apply a T-1 NAV base is unknown. `PricRg` will show it on the first day.

### D11. Trading calendars — CONFIRMED (SPEC §5)
- `exchange_calendars` 4.13.2 has `XBOM` (alias `BSE`) and no `XNSE`. XBOM is used for India.
- XBOM holidays are recorded only to the end of 2026; XNYS and XHKG extend to mid-2027. A hand-maintained `config/calendars/nse_holidays.yaml` covers 2027.
- 1,441 XBOM sessions between 1 Jan 2021 and 9 Nov 2026.
- Planned: days on which a bhavcopy exists override the library for the past.

### D12. NSE live endpoints — PARTLY CONFIRMED (SPEC §13 question 2)
- `https://www.nseindia.com/api/etf` returned HTTP 200 after one visit to the ETF page for cookies: last price, volume, value and the *previous* NAV for all 360 ETFs. It does not carry iNAV.
- `https://www.nseindia.com/api/quote-equity?symbol=...` returned **HTTP 403 Access Denied** to a plain script. It was not retried.
- Decision (Amish, 10 Nov): record iNAV, order-book depth and total buy/sell quantity through a real browser session on NSE quote pages, with the fund houses' own iNAV pages recorded in parallel as backup and cross-check.
- OPEN: which response carries iNAV and depth (needs an open market; first check on 11 Nov 2026).

### D13. Operating decisions (Amish, 10 Nov)
- The collector runs on the project Mac for now (`launchd`, kept awake in market hours). Gaps are expected when the laptop is off; they are counted in the export manifest. Moving to an always-on machine is a v2 item.
- Bloomberg exports are not available yet. The importer is built against a documented template; an interim fair value uses free futures/FX data and is labelled `fv_method = 'free_futures'`.
- Git is local only. No remote, no publishing, until Amish decides.

### D14. Layout
`SPEC.md` moved to `docs/SPEC.md`. The repo root is the `ETF_Premiums` folder. The research plan PDF stays at the root and is git-ignored until Amish decides whether it is public.

### D15. Fund-house (AMC) iNAV endpoints — CONFIRMED reachable, values NOT yet trusted
Found on 10 Nov 2026 (a market holiday) with a plain HTTP client; no cookies, login or tokens needed. One request per fund house returns all its ETFs. Samples are in `tests/fixtures/amc_inav/`.

| Fund house | Request | iNAV field | Our ETFs identified by |
|---|---|---|---|
| Motilal Oswal | `POST https://www.motilaloswalmf.com/mutualfund/api/v1/someFunc`, JSON body `{"apiName":"GetINAVandPrice"}` | `currNav` in rows whose `secname` ends with " iNAV" | `nseSymbol` (MONQ50, MON100) |
| Mirae Asset | `GET https://miraeassetetf.co.in/api/ticker` | `INAV` | `NSE_Symbol` (MASPTOP50, MAFANG, MAHKTECH) |
| Nippon India | `POST https://investeasy.nipponindiaim.com/Online/Realtime/DetailsFill`, empty body with `Content-Length: 0` | `CNav` | `SchName` (no symbol in the response) |

What is not confirmed:
- **Timestamps are not data times.** On the holiday the iNAV values stayed frozen while each fund house's timestamp kept advancing, so the stamp looks like the server clock at response time. Mirae's was about 5 minutes ahead of IST. Decision: our own fetch time (IST) is the observation time; the fund-house stamp is kept only as raw evidence.
- **Nippon's feed looked stale.** It showed Nifty 50 BeES 250.72, Hang Seng BeES 325.21 and Gold BeES 76.03 against official 9 Nov NAVs of 285.81, 436.57 and 116.62. It is recorded but not trusted until checked against a live session. If it stays wrong, NSE is the only iNAV source for HNGSNGBEES and NIFTYBEES.
- Motilal's page says its international iNAVs "consider only currency movement from the previous day's closing NAV". If so, that iNAV is not a futures-adjusted fair value, which matters for the planned robustness check of our fair value against iNAV. To confirm in a live session.
- Behaviour during market hours, and whether the sites' bot protection reacts to a script polling every 15 minutes, are untested.
- None of these endpoints carry market price, order-book depth or total buy/sell quantity. Those still need the NSE quote capture (D12).

## 2026-11-13 — Build session (clean layer, features, collectors)

### D16. Recorder scheduled
The iNAV recorder had been built on 10 Nov but never scheduled, so no snapshots exist for 11–13 Nov. Installed `~/Library/LaunchAgents/com.etfp.inav.plist` on 13 Nov (Amish approved); first run Mon 16 Nov 09:10 IST. The 11–13 Nov gap is permanent and is counted in the manifest.

### D17. Clean layer decisions
- **Storage.** `src/etfp/store.py`: one Parquet file per table in `data/clean/` plus `data/clean/etf.duckdb` rebuilt from the Parquet. Each table has a registered schema and primary key; a duplicate key, an empty key or an empty `source_file` stops the write and nothing is written. Do not run a build while the dashboard holds the DuckDB file open.
- **`band_official.session_date`** = next NSE session after the file date, from the trading calendar. The file dated 9 Nov 2026 applies to **11 Nov** because 10 Nov was an NSE holiday (D11). This refines D4 ("next session", not "next day").
- **NAV duplicates.** AMFI history beats `NAVAll.txt`; within a kind, the later download, then the higher raw version, wins. A numeric disagreement logs a warning naming both files. `nav_source` records the winner. "N.A." NAVs are skipped and logged, never set to 0. Scheme code → symbol from `config/etfs.yaml` only.
- **Revised raw files.** When a raw file has versions (`.v2`, …), the highest is used and its name is in `source_file`.
- **`prices_daily`.** All ETFs on the NSE list, not only tracked ones. Full bhavcopy (D1) is primary; UDiFF overlays ISIN, settlement price and exact rupee value (`value_source` says which). Close differences > 0.005 between them log a warning. One series per symbol-day: EQ, else the busiest. A bhavcopy whose internal date differs from its folder date stops the build. No `vwap_last30` column: `close` already is that VWAP (D3); `avg_price` is the whole-day VWAP.
- **Security master.** Series EQ, else BE. File date must match file name and folder. "No Band" keeps a row with empty limits. `units_daily.date` is the file date as published.
- **`inav_snapshots`.** Long format, one row per source per fetch. `ts` is our fetch time (IST) from the file name (D15); fund-house stamps kept as text in `amc_ts_raw`, NSE's own time in `data_ts`. Missing values are NA, never 0. Nippon rows are stored but untrusted (D15) — filter on `source`.
- **Corporate actions.** Clean prices and NAVs are unadjusted; adjustment happens through `config/corporate_actions.yaml` (MON100 10:1 split, ex-date 2021-06-17).
- OPEN: the NSE quote reader (`parse_nse_quote`) is written against an assumed JSON shape (`marketDeptOrderBook.totalBuyQuantity`, `bid[]`, `ask[]`, …) and must be checked against the first real capture (D12).

### D18. NAV alignment implemented (`features/nav_align.py`); SPEC §6.1–6.3 rewritten
- Rule: the NAV dated *d* is "known" at the Indian close of *t* when the close of the underlying session on or before *d* is at or before 15:30 IST on *t*; the latest such *d* wins. Comparison is `<=`, so domestic ETFs count their own-date NAV as known.
- Consequences: normal US day → NAV dated *t−1*; Monday → Friday's NAV (lag 3); US holiday (7 Sep 2026) → the carried-forward NAV dated 7 Sep, with underlying date 4 Sep; HK → same day.
- `nav_publication_lag_days` = calendar days from `nav_known_date` to *t*. It is not the observed AMFI posting delay (that would need fetch times; not built).
- `strike_lag_sessions` is a hook to test a different rule before Jul 2023; unused by default.
- OPEN (unchanged): HK timing, pre-Jul-2023 timing, FX rate/time used by each AMC.

### D19. Band calculator and flags (`features/bands.py`)
- Arithmetic in `Decimal` from `str(float)` to avoid float traps at the rounding boundary. Rounding and tick come from `config/rules.yaml` (low up, high down to ₹0.01 — still PRELIMINARY).
- "Touched" = within one tick of the limit (R10 `hit_up`, `hit_up_max` and lower twins). `ltp_at_*` = last trade within 0.1% of the limit in force at end of day. `widened_up` = `high > initial upper limit`. Gold/silver have no maximum (`high_max = inf`).
- `infer_regime_from_band` gives R10's `regime_inferred` (name, `ambiguous`, `other`, `unknown`).
- Two helpers produce the official band (`clean/band_official.py` and `bands.attach_official_band`); `etf_day` uses the clean table (`band_session_date`).

### D20. Premium and return definitions
- `x_april` follows SPEC (uses FV); BLUEPRINT defines it on `nav_known`, so `x_april_nav_known` is also stored. **Amish to pick one.**
- `ret_own_k` = log return over the k sessions ending at *t−1* (known before *t*); `ret_close_k` is the contemporaneous one.
- `fv_method` names the data tier (`bloomberg`, `free_futures`, `nav_only`, `no_nav`). Any missing required input → `nav_only`, never a partial adjustment. Zero/negative inputs raise.
- `avg_trade_value` is in rupees (bhavcopy turnover is in lakhs; converted).

### D21. Bloomberg importer (`collect/bloomberg.py`, template `docs/bloomberg_template.md`)
- Long format (date, instrument, field, value, time/zone). Strict column and coverage validation; conflicting values across files raise unless `on_conflict="latest"`.
- US-close coverage requires a stamp at or after 13:00 New York so a 15:30 IST row (06:00 New York) is not mistaken for the close.
- Weakness: coverage of the FX rate at NAV time is presence-only until each AMC's FX time is verified.
- Empty folder → empty result (no Bloomberg exports yet, D13).

### D22. Collector live checks (13 Nov 2026)
- **MON100 split (D9) — CONFIRMED from raw data.** Price ex-date 17 Jun 2021 (close 1015.62 on 16 Jun; open 107.00, close 100.87 on 17 Jun; `PREV_CLOSE` on 17 Jun unadjusted). AMFI NAV stays on the old scale through 18 Jun (1025.4998) and is on the new scale on 21 Jun (101.5224). `config/corporate_actions.yaml` therefore has separate `ex_date` (price, units) and `nav_ex_date`; set `verified: true` from the raw files (NSE circular itself not read). On 17–18 Jun 2021 the raw premium is meaningless without these factors.
- **AMFI on US holidays.** Motilal carries a NAV row forward on 7 Sep 2026 (US Labor Day); Mirae (MASPTOP50, 149169) has **no row** that day. D10's "carried forward" holds for Motilal only. Alignment code must not assume one NAV per fund per Indian session.
- **AMFI history window (D9).** 5 days ≈ 2.5 MB in 5 s; 12 days ≈ 9.3 MB within 100 s; 30 days timed out three times. Backfill uses 7-day windows on a fixed calendar grid. Max window and `mf=`/`tp=` filters still OPEN.
- **UDiFF earliest date (D2) — RESOLVED: 2024-01-01** (404 on 2023-12-29). Files of 1–2 Jan 2024 have a trailing comma in the header and `Rsvd01..04` names; parser now drops trailing empty header fields.
- **Security master earliest date (D4) — RESOLVED: 2024-02-05** (404 on 2 Jan, 1 Feb, 2 Feb 2024). `IssdCptl` units start Feb 2024; earlier units need R11's monthly AUM/NAV.
- **Band oddity.** NIFTYBEES band on 2024-03-01 was 231.05–255.38 (ratio ≈ 1.105), not the ±20% rule. Not investigated; relevant to R7/R10 `regime_inferred`.
- **Yahoo intraday (free futures, D13).** One live request returned HTTP 429; not retried. Fixtures are synthetic. Yahoo keeps ~60 days of 5-minute bars, so unrecorded days are lost; if 429 persists the interim fair value has no inputs (`nav_only`). OPEN: decide another free source or keep retrying.
- **NSE politeness.** Backfill keeps NSE requests ≥ 1.5 s apart; a 403/429 stops that website for the run; five failures in a row stop it; 404s are recorded in `data/state/backfill_state.json` and not re-asked.
- Backfill started 13 Nov 2026: UDiFF + security master from 2024-01-01, full bhavcopy + AMFI history from 2021-01-01.

### D23. Manual data loaders (`clean/manual_*.py`, formats in `docs/manual_data.md`)
- Strict CSV headers (missing or unknown columns are errors); every error names `data/manual/<file> line N`. `source_file` = `data/manual/<file>.csv:<line>`. Money columns are rupees. A missing CSV gives an empty table; a malformed one stops `etfp build`.
- **Events (R5, R16).** `date` = day the event became public; a `ts_ist` on another IST date is an error. First tradable session: before 15:30 IST on a session day → that day; at/after 15:30, weekend or holiday → next session; time unknown → same day if a session (optimistic). `direction` may be blank ("not yet classified") — it is a research judgement made before looking at prices, so the pipeline never fills it. `verified = true` requires `source_primary`. Blank `event_id` → `<date>_<category>_<sha1[:8]>` (changes if the description is edited; give explicit ids). Symbols `;`-separated. Seeded with 6 rows from repo docs, all unverified, no URLs, no direction.
- **Feeders (R4, R14).** One row per (scheme code, month end); a scheme cannot switch ETF. `feeder_units_daily` carries forward each feeder's latest holding and sums per ETF; NaN until a feeder reports. Use `lag_days=10` (portfolios publish up to ~10 days after month end) to avoid look-ahead.
- **units_monthly (R11).** `units = aum / nav` if blank; if all three typed they must agree within 1% (catches crores typed as rupees).
- **Trends (R15).** Windows chained in start-date order by the ratio of sums on shared days; earlier days keep their value; series rescaled to max 100; `<1` stored as 0.5. No overlap, zero overlap or weekly format stops the build.
- **Config.** Creation units MONQ50 600,000 and MON100 250,000 (scheme documents); MASPTOP50, MAFANG null and in `verify`. Every D7 unit change for MONQ50, MON100, MAHKTECH, HNGSNGBEES is a whole multiple of the size. `nav_t2.start` marked `verify`. Equity bands: `breadth_test: false`, `widen_moving_side_only: true` (final SEBI circular para 5.1).


### D24. Diwali Muhurat sessions are NSE sessions; NSE sometimes serves Excel (14 Nov 2026)
- **Finding.** The real build showed computed and official bands disagreeing on 9 Nov 2026 for all 12 ETFs, and nowhere else from 7 Sep 2026 (540 of 552 session-rows matched to the tick). NSE's 9 Nov `PREV_CLOSE` for MONQ50 was 231.90, not the 6 Nov close 227.58: it was the close of the **Diwali Muhurat session on Sunday 8 Nov 2026**, which our calendar did not know.
- **Evidence from NSE's own files (saved under data/raw):** the security master dated 8 Nov 2026 gives MONQ50 208.71–255.09 = 231.90 × 0.9 and × 1.1, so the 9 Nov band is based on the Muhurat close. In the T-2 regime, the band for Mon 4 Nov 2024 (file dated 1 Nov 2024) is 130.60–195.91 for MON100 = 1.2 / 0.8 × NAV dated 31 Oct (163.2638), i.e. T-2 counted the Muhurat session of Fri 1 Nov 2024. Full bhavcopy returned HTTP 200 for every Muhurat date 2021–2025.
- **Decision.** `config/calendars/nse_holidays.yaml` `extra_sessions`: 2021-11-04, 2022-10-24, 2023-11-12, 2024-11-01, 2025-10-21, 2026-11-08. The calendar stores 09:15–15:30 for them; the real session was about one hour in the evening (exact times VERIFY).
- **Consequences.** Session numbering after 8 Nov 2026 shifts by one (9 Nov = session 44 counted from 7 Sep, paper says 43; P2 window 16–40 unaffected). Flagged to the research side in REQUESTS R19 and kept as a strict xfail. The AMFI daily window now skips weekend special sessions so Friday's late overseas NAV is still fetched.
- **AMFI gap on Muhurat days.** The band for 5 Nov 2024 implies a base of about 164.01 (MON100) / 65.175 (MONQ50), which matches no AMFI NAV: AMFI history has no row dated 1 Nov 2024 although the US traded that day. The exchange used a NAV that AMFI does not carry. `nav_t2` falls back to the 31 Oct NAV and `band_mismatch` flags the day; the official band is the truth for those sessions.
- **Excel served as CSV.** NSE served `sec_bhavdata_full_08082022.csv` as an .xlsx workbook (1 of ~600 files so far). The parser now reads .xlsx with the standard library (zip + XML) into the same code path; adding openpyxl as a dependency was not approved in this session.

### D25. etf_day and research export (`src/etfp/build.py`, `src/etfp/export.py`)
- One row per configured ETF per NSE price day (137 columns; definitions in `export.ETF_DAY_COLUMNS`, exported as `schema.json` and `DATA_DICTIONARY.md`). `schema_version` 1.0.0; any change of meaning bumps it and adds to `SCHEMA_CHANGELOG`.
- Prices, NAVs and official band limits adjusted to today's unit scale with kind-specific factors (D22); raw prices kept in `*_raw`.
- Fair value per row: Bloomberg if complete, else Yahoo (`free_futures`), else `nav_only`. NAV-time FX approximated by the rate at the underlying close (AMC FX time still VERIFY).
- `fv_beta` (R12) = NAV × (F_t/F_a)^β × S_t/S_a, β from 250 NAV dates before 2026-09-07; NaN until futures history exists. HK and domestic funds copy `fv`.
- `cap_pi` uses the official upper limit only; `cap_pi_computed` uses the computed band (covers pre-Feb-2024).
- `regime_inferred`: official band (±1 tick) where available; before 2024-02-05 a price test (`nav_t2_consistent` / `not_nav_t2`).
- Unit changes land on the first price row on or after the security-master file showing them. `units` = security master, else monthly AUM/NAV within 45 days (`units_source`). `turnover` and `float_net` use `units`.
- `rationed` = `pi` > 0.02 and no creation in the previous 20 rows (BLUEPRINT). BLUEPRINT aliases (`nav_known`, `prem_known`, `pi`, `band_low/high`, `creation_event`, `units`) are real duplicate columns.
- `etfp build` runs `build_all` then the research export (CLAUDE.md). First real build 14 Nov 2026: 15,410 ETF-days in 2.7 min; NAV empty until the AMFI backfill finishes.

### D26. Dashboard and alerts (M4)
- Dashboard (`uv run --extra dashboard streamlit run dashboard/app.py`) reads Parquet only, never DuckDB, so it cannot block a build. SPEC §9 disclaimer at top and bottom; a test scans labels for advice words.
- Alerts (`etfp alerts [--dry-run]`): premium crossing 25/50/100% (on `prem_fv`), band touch, premium jump > 10 points, intraday iNAV premium crossing (Nippon excluded, D15), stale data / recorder / source failures. Thresholds in `config/alerts.yaml`. Dedupe key `rule|symbol|data day|variant` in `data/state/alerts_sent.json`, written only after Telegram accepts; lock file prevents overlapping runs. Token from environment or `.env`, never logged. No real message sent yet (needs `.env` from Amish).
- OPEN: the 21:00 IST "build should exist" time is a guess; no "NAV missing" alert until HK/domestic NAV timing is confirmed.


### D27. BSE daily bhavcopy (R9) and NSE securities lending (R8)
- BSE: `https://www.bseindia.com/download/BhavCopy/Equity/BhavCopy_BSE_CM_0_0_0_YYYYMMDD_F_0000.CSV` (UDiFF, from 2024-01-02 in our cutover) and legacy `EQ_ISINCODE_DDMMYY.zip` before that. A missing day returns HTTP 200 with BSE's home page (soft 404); detected and recorded as not available. Rows matched by ISIN (BSE names differ, e.g. NIFTYIETF was "ICICINIFTY" in 2021). Both formats agree on MON100, 5 Jul 2024. BSE rate limit 2 s. First UDiFF day and last legacy day bounded, not pinned.
- `turnover` = (NSE + BSE units) / `units` from schema 1.1.0 (MEANING CHANGE); `turnover_is_nse_only` true only where BSE has no row.
- SLB: `nsearchives.nseindia.com/archives/slbs/seclist/SLB_ELG_SEC_DDMMYYYY.csv` and `.../slbs/bhavcopy/SLBM_BC_DDMMYYYY.DAT` (header-less), back to at least 2021-01-04. Fee column order inferred from the data (VERIFY against NSE's layout). Finding: on 2021-01-04 none of the five international ETFs were SLB-eligible (only the three Nippon domestic ETFs); on 2024-01-02 and 2026-11-12 all five were. On 12 Nov 2026 only MAFANG had a lending trade (8,000 units). R8's "MON100 admitted Sep 2013" does not hold for the 2021 list.

### D28. Limit-day diagnostics (R13, `features/limit_days.py`, table `limit_day`)
- One row per ETF-day a limit was touched (daily flags or intraday). `obs_source` = `bloomberg_1m`, `snapshots_15m` or `none`; minutes at limit are an estimate with a lower bound. Widenings are a minimum consistent with prices seen. `locked_*` = opposite side of the book empty at the limit; NA when quantities unknown (never 0). Session window 09:15–15:30, so Muhurat evening sessions show no intraday data. Bloomberg ETF minute bars use instrument `<SYMBOL>.1M` (docs/bloomberg_template.md). Exported in schema 1.2.0 with BSE and SLB tables; manifest lists coverage.

### D29. Scheduled daily run (`etfp run daily`, docs/OPERATIONS.md)
- Evening 19:45 IST Mon–Fri: collect all daily sources → build → export → alerts. Morning 10:35 IST Mon–Fri for the previous session: gap-fill, force re-download of the AMFI window (overseas NAVs, D10), build, export, alerts. Mon–Fri (not Tue–Sat) so Monday fetches Friday's US NAV. A failed source does not stop the build; alerts always run; missing Telegram credentials = warning. Lock `data/state/run_daily.lock` (stale after 2 h); run records in `data/state/runs/`.
- First supervised real run 14 Nov 2026 (morning, day 13 Nov): collect, build (15,434 ETF-days, 3 min) and export OK; Yahoo returned 429 again. Plists printed by `etfp ops launchd-plist --job daily-evening|daily-morning`; **not installed yet** (Amish to decide).


### D30. Owner decisions, 14 Nov 2026
- **x_april (Amish).** `x_april` = ln(close / (1.2 × `nav_known_at_close`)) — BLUEPRINT definition, and the April 2027 band is anchored on NAV. The fair-value version is `x_april_fv` (was `x_april`); `x_april_nav_known` removed. MEANING CHANGE, schema 1.3.0.
- **Free futures source (Amish: look for another).** Search on 14 Nov found no free source of multi-year intraday NQ/ES futures: FinzData free = 30 days of daily bars; MarketParquet free = one sample week; Kibot, FirstRateData, Portara = paid (FirstRateData ≈ $100/yr). For USD/INR, Twelve Data lists 1-minute FX series with a free credit tier (whether free covers minute history is unconfirmed); TraderMade's free tier has no minute history. Conclusion: history must come from the Bloomberg export; a free key (e.g. Twelve Data) could cover forward FX only. Signing up for any API is Amish's call. A retry of Yahoo with browser-like headers was not attempted (it would mean working around their block). Yahoo stays in the daily job and fails loudly.
- **openpyxl (Amish: no).** Keep the standard-library .xlsx reader (D24).
- **Daily schedules installed** (Amish asked to proceed): `com.etfp.daily-evening` (19:45 IST) and `com.etfp.daily-morning` (10:35 IST), Mon–Fri. `.env` created from `.env.example` (empty, mode 600) for Telegram.


### D31. NSE quote capture, feeder NAVs, attention, China ETFs, stale-NAV cap (schema 1.3.0)
- **NSE quote capture (D12 follow-up).** `etfp.collect.nse_quote` opens each ETF's NSE quote page in Playwright and saves the page's own JSON answers untouched (`nse_quote/<date>/<SYM>_HHMMSS.json` price block, `<SYM>_trade_info_HHMMSS.json` order book); the clean layer joins them into one `nse_quote` row (`source_file` lists both). One supervised headless attempt on 14 Nov got `ERR_HTTP2_PROTOCOL_ERROR` (NSE bot protection); no evasion was tried. The recorder runs it as an isolated extra step after the plain sources; any failure is one warning and never affects AMC / NSE ETF-API capture. **Shipped `nse_quote.enabled: false`** until Amish runs `uv run playwright install chromium` and a supervised `uv run etfp collect nse-quote -s MONQ50 --headed` in market hours. Response shape (iNAV, depth field names) still OPEN.
- **Feeder NAVs (R14).** `config/feeders.yaml`: Regular/Direct plans of four feeders — MON100 145551/145552, MAFANG 148929/148928, MASPTOP50 149171/149170, MAHKTECH 149380/149381 (codes, names and ISINs checked in the AMFI file of 13 Nov 2026; feeder→ETF link from scheme names). No Motilal Nasdaq Q50 feeder exists in AMFI. Table `feeder_nav_daily`.
- **attention (R15).** Mean of the ETF's terms in `config/trends_terms.yaml`, NaN unless every term has a value that day (avoids jumps from mixing differently scaled terms). Same-day Trends value (includes after-close searches). MASPTOP50, MAHKTECH have no terms.
- **China QDII ETFs (R17).** `china_etf_daily` from Bloomberg instruments `<code> CH` (Asia/Shanghai). Licensed: research export only, never the open dataset. OPEN: `EQY_SH_OUT` unit; NAV date vs US close for the premium.
- **Stale known NAV.** The first real 1.3.0 build showed MONQ50's `nav_known_at_close` on 12 Nov 2026 = 68.8755, a NAV from Nov 2024 (AMFI backfill not yet at 2026), giving a fake 236% premium. Rule: a known NAV more than 7 calendar days old is blanked (date and lag kept, `dq_nav_stale` set); premiums, `fv`, `x_april` become empty, `fv_method = no_nav`. MEANING CHANGE in 1.3.0.


### D32. First full-history build (14 Nov 2026): source defects found and handled
- **M3 spot-check passes.** MONQ50 `prem_raw`: 4 Sep 2026 19.53% (Value Research 19.5%), 7 Oct 2026 110.07% (press ~110%), 9 Nov 2026 100.8% raw / 107.1% known (paper R19). From 7 Sep 2026 all 564 computed bands equal NSE's official band to the tick.
- **More missing NSE sessions.** Special Saturday sessions 2024-01-20 (full session; 22 Jan declared a holiday), 2024-03-02 and 2024-05-18 (disaster-recovery test sessions with narrow bands) have NSE bhavcopies (HTTP 200) and were missing from the calendar; added to `extra_sessions`. The narrow special-session bands are not modelled in rules.yaml, so those days show `band_mismatch`.
- **NSE archive defect.** `sec_bhavdata_full_04112021.csv` (2021 Muhurat) is a byte copy of the 3 Nov 2021 file; the session happened (8 Nov 2021 `PREV_CLOSE` MON100 120.09 ≠ 117.54). Listed in new `config/raw_exceptions.yaml` and skipped with a warning; raw untouched.
- **AMFI defect.** Three history windows (21–27 Jun, 28 Jun–4 Jul, 12–18 Jul 2026) came back as AMFI's HTML page with HTTP 200 and were stored. The collector now rejects HTML as `SourceUnavailableError` (not stored, retried); the three windows were re-downloaded as `.v2` (cleaner uses the highest version).
- **BSE defects.** Legacy files have rows with no ISIN (14 Jan 2022) and rows glued together (7 Feb, 15 Jul, 22 Jul 2022). No-ISIN rows are skipped; malformed legacy lines are set aside with a warning, and the build stops if a set-aside line mentions one of our funds' ISINs.
- **Pre-Sep-2026 band mismatches: 414 of 7,668 rows.** 237 differ by ≤ ₹0.05 (likely NAV revisions or provisional NAVs used by the exchange); the larger ones cluster on special sessions (2 Mar, 18 May 2024), the sessions after them (no Saturday NAV), 5 Nov 2024 (Muhurat NAV gap, D24) and a few others. Kept as `band_mismatch`; the official band is authoritative. OPEN for the research side.

### D33. Valve status per ETF-month (M5, table `valve_monthly`, `src/etfp/features/valve.py`, schema 1.4.0)
- **What "valve" means.** Open = the fund house accepts creations, so arbitrage can cap the premium; closed = it does not (overseas cap hit). We only see the footprint: units outstanding rise only through a creation and fall only through a redemption.
- **Rule, one row per symbol x calendar month from 2021-01.** Source priority: daily security master (`units_daily`, IssdCptl) if the month has a file; else hand-entered `units_monthly` (AUM / NAV, R11); else none. Daily: `units_start` = last file *before* the month; every file compared with the one before. `open` = at least one file shows a rise (`daily_rose`; a month with rises and falls is open); `redeeming_only` = falls, no rise (`daily_fell_only`); `closed` = count never moved **and** NSE refreshed the counts in that window (`daily_flat`); `unknown` = flat but no refresh (`daily_no_refresh`), or a single first file with nothing to compare (`daily_no_baseline`). Monthly: vs the previous month-end, > +1% open, < -1% redeeming_only, else closed (SPEC 6.5 threshold, `MONTHLY_THRESHOLD`); no previous month -> unknown. No data -> `unknown` (`no_data`); never guessed from neighbours. `closed` means *no creation happened*; for a fund at a large premium that is the evidence the valve was shut, but it is not proof (a fund can be open with no orders).
- **Creation units.** `creation_units_net` = change / basket size; empty for MASPTOP50, MAFANG and the domestic funds (size null). `all_changes_whole_cu` flags a month whose changes are not whole baskets: none in the current data. Unconfirmed hint for R6: every MAFANG change is a multiple of 200,000 and every MASPTOP50 change a multiple of 550,000 (candidate basket sizes; read the Mirae SID before using).
- **Splits.** Monthly AUM/NAV units are split-adjusted (`corporate_actions.yaml`, kind `units`) before comparison, so the MON100 10:1 split (Jun 2021) cannot look like a creation. Daily files start after it.
- **Finding: IssdCptl is refreshed in batches, about weekly, not daily** (answers part of the VERIFY item "IssdCptl update lag"). Only 115 distinct dates in 5 Feb 2024 – 13 Nov 2026 show any change; NIFTYBEES and GOLDBEES change on every one of them, usually the first session of a week, with occasional 2-3 week gaps (Dec 2024 – Jan 2025, late Sep – early Oct 2025). Consequences: (1) `n_creation_days` / `n_redemption_days` count publication batches with a net rise/fall, netting about a week of activity; (2) a creation late in a month can be dated to the next month; (3) no batch ran between 5 Feb and 14 Mar 2024, so Feb 2024 is `unknown` for every fund and the March 2024 changes may include February creations. The refresh calendar is "dates any tracked ETF's count changed" (`refresh_dates`); `n_refresh_dates` is kept per row. The R1 event dates (`units_change` events, etf_day `units_change`) have the same batch dating; the research side should treat them as "published by" dates.
- **Results (build of 14 Nov 2026, units through 13 Nov 2026; `docs/VALVE_STATUS.md`).** MONQ50: flat at 3,275,018 Feb–Jun 2024; creations Jul–Aug 2024, redemptions Sep–Nov 2024, creations Dec 2024, Apr–May 2025, Sep–Nov 2025; last rise in the batch first seen 17 Nov 2025 (+600,000 to 14,675,018), flat since (closed Dec 2025 – Nov 2026). MON100: two-way flow through 2024–2025 (11 open months), last rise Nov 2025, one redemption Mar 2026, flat since. MASPTOP50: no rise at all since Feb 2024 (5 redemption months, last Oct 2024). MAFANG: rises Mar–Apr 2024 only, redemptions Jul 2024 – Jan 2025, flat since. MAHKTECH: rises Mar 2024 and Jan 2025, flat since. HNGSNGBEES: rises Mar and Oct 2024 only, flat since despite a low premium (D7). Domestic and bullion controls (NIFTYBEES, JUNIORBEES, NIFTYIETF, BANKBEES, GOLDBEES, SILVERBEES): open in nearly every month, as a working valve should look; this supports reading flat months of the international funds as closed. Jan 2021 – Jan 2024 are `unknown` for all funds until `data/manual/units_monthly.csv` is filled (R11; file is still header-only).
- **Hand spot-checks against `units_daily`.** MONQ50 Aug 2024: start 5,675,018 (29 Jul), +3,000,000 on 6 Aug and +600,000 on 13 Aug -> 9,275,018; 2 rise batches, +6 baskets, open: table agrees. MON100 Apr 2024: start 508,884,740 (26 Mar), -1,000,000 (5 Apr), +500,000 (15 Apr), +1,000,000 (22 Apr), +2,000,000 (29 Apr) -> 511,384,740; 3 rises, 1 fall, +10 baskets, open: agrees. HNGSNGBEES Mar 2024: 14,161,680 -> 19,111,680 on 14 Mar = +198 baskets of 25,000: agrees. MONQ50 17 Nov 2025 – 13 Nov 2026: no change: closed months agree.
- **Schema 1.4.0.** New file `valve_monthly.parquet` in the research export (COPIED_TABLES), documented in schema.json / DATA_DICTIONARY.md from the registered schema; manifest gets `valve_status_counts_by_symbol` and the row count. No existing column changes meaning. `uv run etfp export valve-status` regenerates `docs/VALVE_STATUS.md` from the table only (no clock, no git), so the same build gives the same file.
- **Open.** Basket sizes for MASPTOP50 and MAFANG (R6); IssdCptl batch schedule vs AMC creation dates (is the batch date the listing date of new units?); the monthly AUM/NAV table for 2021 – Jan 2024 (R11); whether +-1% is right for the monthly rule once real AUM data is entered.

### D34. Open dataset export (`etfp export dataset`, `src/etfp/export_dataset.py`, `config/dataset.yaml`)
- Writes `exports/dataset/<YYYY-MM-DD>/` (local only, gitignored; nothing is uploaded): CSV + Parquet per public table, `DATA_DICTIONARY.md` (the research generator, limited to published columns), `README.md` (coverage, how built, cite placeholder, SPEC 9 disclaimer, redistribution questions), `LICENSE-DATA` ("TO BE DECIDED BY OWNER"), `manifest.json` (git commit + dirty flag, package version, sha256 of the config, build time IST, date ranges, row counts, schema_version, sha256 + size of every other file). Not run by `etfp build` or the daily job: a release is a deliberate step.
- **Licence filter.** `config/dataset.yaml` lists every exported table and column. Guard 1: a column of an exported table that is not listed (export, `drop` or deny) stops the export, so new columns never leak by default; a listed column that is missing also stops it. Guard 2: denied tables, columns (exact names and regex patterns `(^|_)fv(_|$)`, `futures`, `bloomberg`, `bbg`, `yahoo`, `china`), rows, and lineage paths naming `bloomberg`/`yahoo` raw folders fail the export; checked in memory and again on the Parquet and CSV files read back. The folder is built under a temporary name, so a refusal leaves nothing behind. Tables not listed (e.g. `prices_daily`, `valve_monthly`) are not exported.
- **Excluded (Bloomberg/Yahoo, licence forbids redistribution).** Tables `china_etf_daily`, `underlying_daily`, `fx_daily`. From `etf_day`: `fv` and its input block, `fv_beta*`, and everything computed from `fv`: `prem_fv`, `log_prem_fv`, `pi` (= `log_prem_fv`), `x_april_fv`, `cap_pi`, `cap_pi_computed` (both divide by `fv`), `rationed` (defined on `pi`), `ret_fv_1`, `dq_prem_fv_*`, `dq_fv_far_from_nav`. `etf_master.futures_proxy` (pattern). `limit_day` rows with `obs_source = bloomberg_1m`. The fair value is excluded even on `nav_only` rows so the column set does not change when Bloomberg data arrives.
- **Included, `licence_status: unverified`** (the owner must settle before publishing; listed in the README): NSE (prices, bands, units, SLB, iNAV API), BSE, AMFI NAVs, AMC iNAV pages, AMC disclosures entered by hand, Google Trends. Project config, hand-written events and formulas are `own_work`.
- First real run 14 Nov 2026 (dirty tree on f3ffa9b): 12 tables, `etf_day` 15,504 rows x 112 columns (138 in research), 2021-01-01 to 2026-11-13; empty tables (`inav_snapshots`, `units_monthly`, `fof_monthly`, `trends_daily`) skipped and listed. All sha256 values re-checked; no denied column or Bloomberg/Yahoo path in any file.
- OPEN: redistribution terms of each unverified source; licence choice; whether to publish a NAV-only premium set only or wait for a licensable futures source.

### D35. Bullion module v1: domestic metal reference price (IBJA), `bullion_ref_daily`, `features/bullion.py`
- **Why.** GOLDBEES and SILVERBEES are the control group: they buy physical metal in India, so creations are never capped and arbitrage should hold the price at NAV. To check their NAV and to separate "premium because the valve is shut" from "premium because the metal itself is scarce in India" (Oct 2025 silver) we need the domestic metal price.
- **Already in `etf_day` (no change needed):** close, same-date NAV (lag 0 on 1,440 of 1,452 GOLDBEES days), `prem_raw`/`prem_nav_known`, bands (rules.yaml gold/silver entries), `units` from IssdCptl since 2024-02-05, `fv_method = nav_only`. **Missing:** the metal price, grams of metal per unit, premium over metal, an NAV cross-check, and a domestic-vs-London premium.
- **Sources checked live 14 Nov 2026 (one request each, no evasion):**
  - **IBJA, `https://ibjarates.com/` — USABLE.** Plain HTTP 200, no cookies. Polled average of dealers/refiners/importers/jewellers, AM poll 11:40-12:00 IST, PM poll 16:40-17:00 IST; includes import duty and customs, excludes 3% GST (IBJA methodology PDF `ibjarates.com/pdf/spot-polling-mechanism/spot-polling-mechanism.pdf`). Gold quoted per 10 g (999/995/916/750/585), silver 999 per 1 kg. RBI uses it for SGB prices and bank gold loans. **History on the free site: ~4 months only** — the home page's hidden chart fields `HdnGold`/`HdnSilver` hold 84 publication days of PM rates (14 Jul – 13 Nov 2026), the AM/PM tables the last 4 days, and a daily 30-day PDF (`UploadedFiles/30DaysPdf/...`, not used: needs a PDF library). No robots.txt (404). Terms page is a generic website T&C with no scraping clause, but a site notice says parties using IBJA rates "for valuation and pricing activities and in transactions & financial products" should subscribe to the paid IBJA API. Research use of a once-a-day page view seems fine; **publishing IBJA numbers in the open dataset needs the owner's decision** (raw folder is gitignored; a 19 KB trimmed copy of the public page is a test fixture).
  - **LBMA `prices.lbma.org.uk/json/silver.json` — 403** (robots.txt 401). Not retried. LBMA prices are licensed anyway.
  - **MCX spot market price page — 403.** Not retried.
  - Not tried: NSE commodity spot files (NSE needs a residential IP and session cookies), AMC monthly portfolio (gold kg x value gives a monthly valuation price; possible cross-check later).
- **Built.** `etfp.collect.ibja_rates` (`fetch()` saves the untouched home page under `data/raw/ibja_rates/<IST date>/ibjarates_home.html`; `parse()` reads the AM/PM tables and the chart JSON, table and chart must agree or it raises; '-' cells skipped, never 0; platinum skipped because the page gives no unit). Clean table `bullion_ref_daily` (key date, metal, purity, session; `price_inr_per_gram`, `quote_inr`, `quote_unit`, `source`, `source_file` = newest page carrying the rate; revisions between downloads logged). `uv run etfp collect ibja` (one request; **not yet in `etfp run daily`** — jobs.py is being changed by another agent). Pure functions in `etfp.features.bullion`: `implied_metal_per_unit`, `metal_per_unit_stability`, `bullion_premium_frame` (`prem_nav`, `prem_metal` = close / (grams per unit known the day before x today's price) - 1, `nav_check` = NAV / that metal value - 1), `bullion_monthly_summary` (premium stats + month-end units growth).
- **Results (14 Jul – 13 Nov 2026, same-date NAV / IBJA PM 999).** GOLDBEES 0.008196 g gold per unit, CV 0.16%, range 0.008167–0.008231, drift -0.44%/yr. SILVERBEES 0.9488 g silver per unit, CV 0.26%, range 0.9418–0.9564, drift +0.31%/yr (4 months is too short to read the expense ratio from the drift). Lagging the price one day raises CV to 1.1% / 1.7%, so the NAV dated t uses the price of t. `nav_check` mean 0.00%, sd 0.19% (gold) / 0.32% (silver), max |0.55%| / |0.80%|: the NAV equals metal x IBJA price within under 1%. `prem_metal` over 83 days: gold mean +0.10% (range -0.58% to +0.87%), silver -0.15% (-1.06% to +1.07%).
- **Oct 2025 silver (from `etf_day`, before IBJA coverage).** SILVERBEES close/NAV premium -2.9% to +3.0% (peak +3.0% on 9 Oct, +2.5% on 14 Oct), |premium| > 1% on 10 of 20 days, while units grew +18.0% in the month (1.04bn -> 1.22bn; +15% in Sep). GOLDBEES same month: within ±0.9%, units +11.6%. Reading: the valve was open and creations ran hard; the close premium over NAV stayed within ~3%. The scarcity premium of Indian silver over London is not visible in close/NAV if the NAV itself was struck at the scarce domestic price — needs the valuation basis (below) and a London price to measure.
- **VERIFY.** (1) Which price SEBI requires bullion ETFs to value at, and from when (believed: LBMA-based landed price earlier, domestic exchange-polled spot later); IBJA matches the NAV within 1% now, but which polled price is used and its timing (AM vs PM; IBJA PM is after the 15:30 close) is unconfirmed — only 3 AM days to compare so far. (2) A free, licensable London price + USD/INR for the domestic-vs-London silver premium (LBMA blocked; FBIL for FX). (3) Free history before Jul 2026 for IBJA (paid API or RBI SGB notices only). (4) IBJA redistribution terms for the open dataset.

---

### D36. Creation-unit sizes and NAV timing/FX from official documents (R6; 14 Nov 2026)
Read from the AMC documents themselves (text extracted from the PDFs; page = PDF page).

**Creation-unit sizes (R6) — CONFIRMED, config/etfs.yaml updated**
| ETF | Units | Document | Page | Exact words |
|---|---|---|---|---|
| MASPTOP50 | 550,000 | SID dated 29 Nov 2025, https://www.sebi.gov.in/sebi_data/mutualfundfile/dec-2025/1764670328212.pdf | 12 | "The Creation Unit size for the scheme shall be 5,50,000 units." |
| MAFANG | 200,000 | SID dated 31 May 2025, https://www.miraeassetmf.co.in/docs/default-source/sid/68-sid-mirae-asset-nyse-fang-etf-may2025.pdf | 12 | "The Creation Unit size for the scheme shall be 2,00,000 units." |
| MAHKTECH | 800,000 | SID dated 31 May 2025, https://www.miraeassetmf.co.in/docs/default-source/sid/24-sid-mirae-asset-hang-seng-tech-etf-may2025.pdf | 9 | "The Creation Unit size for the scheme shall be 8,00,000 units" |
| MON100 | 250,000 | SID dated 29 May 2026, https://www.motilaloswalmf.com/content/dam/motilal-mf/downloads/mf/sid/2026/may/SID_Motilal%20Oswal%20Nasdaq%20100%20ETF.pdf | 56, 67 | "Each Creation Unit consists of 2,50,000 units" |
| MONQ50 | 600,000 | KIM dated 29 May 2026, https://www.motilaloswalmf.com/content/dam/motilal-mf/downloads/mf/key-information-memorandum/2026/may/KIM%20-%20Motilal%20Oswal%20Nasdaq%20Q%2050%20ETF.pdf | 15 | "subscribe is 6,00,000 units and in multiples thereafter" |
| HNGSNGBEES | 25,000 | SID dated 30 Jun 2024, https://www.sebi.gov.in/sebi_data/mutualfundfile/jul-2024/1720774024905.pdf (also Nippon product note, data as of 30 Sep 2026) | 5 | "Minimum number of Units (Creation Units) - 25,000 Units and in multiples thereof" |

The data hint was right: multiples of 200,000 (MAFANG) and 550,000 (MASPTOP50). Every Mirae SID adds "The Creation Unit size may be changed by the AMC at their discretion", and only the dated versions above were read. No earlier SID versions were compared, so a change before those dates (e.g. after a unit split) is not ruled out; the unit-change data (D7) show none. All six values now have `creation_unit_size` removed from `verify`.

**NAV timing and FX — what the documents say**
- Mirae (MASPTOP50 SID p. 23; MAFANG SID p. 23): "the NAV of the Scheme for a business day (Day T) will be based on the prices of the underlying securities for that day (Day T)" and the scheme "will declare the NAV for a Business day on the next Business Day by 10.00 a.m". This matches D10's finding (NAV dated t = US close of date t, published next morning). It also explains why AMFI carries no Mirae row on US holidays (D22).
- Mirae FX (same pages; same sentence in the MAHKTECH SID p. 19): "all the assets and liabilities in foreign currency will be valued in Indian Rupees on the basis of Foreign Exchange rate quoted on Bloomberg/Reuters around India markets close time (which is currently around 3:30 p.m. IST) or at the RBI Reference rate as at the close of the Banking hours on that day in India."  So the rate is a 15:30 IST quote (or the RBI reference rate), not the US-close rate. Whether it is Bloomberg/Reuters or RBI on a given day is not stated.
- Mirae HK (MAHKTECH SID p. 19): the NAV section has no Day-T sentence, only "NAVs will be rounded off to four decimal places and will be computed and declared on every Business Day, as of the close of such Business Day" and publication "by 11.00 p.m.". The risk section (p. 25) says only that "due to the time zone difference ... subscription or redemption may only be implemented on the next day during Hong Kong market hours". Hong Kong closes (16:00 HKT, 13:30 IST, plus closing auction) before India's close, so a same-calendar-day HK close and a 15:30 IST FX rate is the natural reading, but the SID does not say it in words. PARTLY.
- Motilal MON100 (SID dated 29 May 2026): p. 28 "for the purposes of valuation and calculating NAV of the Scheme for a particular day, the last available prices of securities on principal stock exchange shall be considered (which would be the previous day's closing prices)"; p. 29 "For calculation and disclosure of NAV as on February 28, 2011, prices of securities at the close of trading on NASDAQ Stock Market on February 27, 2011 have been considered." NAV is disclosed "by 11.00 p.m." (p. 28). FX (p. 29): "valued in Indian Rupees exchange rate made available by Reuters Rate (11:30:00 GMT) failing which exchange rates made available by Bloomberg / RBI or any other standard reference rate at the close of banking hours in India." 11:30 GMT = 17:00 IST.
- CONFLICT, unresolved: the Motilal wording ("previous day's closing prices", 2011 example) reads as NAV dated t = US close of t-1, but D10's data for MON100 in Sep 2026 show NAV dated t moves with the US close of date t. The SID text is legacy wording written for a 2011 launch; the data win, but the SID does not confirm them. The NAV file may be dated the day the NAV is struck with the previous US session. Needs the AMFI NAV date convention checked with Motilal or against more dates (also pre-Jul 2023). The Motilal FX time (17:00 IST) is later than Mirae's (15:30 IST), so the two families use different FX times; the 17:00 IST rate is closer to the data's "same-date FX" match in D10 only if FRED's daily rate is noon-NY or RBI based, which was not tested.
- Nippon HNGSNGBEES (SID dated 30 Jun 2024, p. 18): "Due to difference in time zones of different markets, in case the closing prices of Securities are not available within a given time frame to enable the AMC to upload the NAV for a valuation day, the AMC shall use the last available traded price for the purpose of valuation." FX: "valued in Indian Rupees at the exchange rate made available by Reserve Bank of India failing which exchange rates made available by Reuters or any other standard reference rate at the close of banking hours in India." So the RBI reference rate, published around noon IST, is the primary source (RBI/FBIL timing not stated in the SID). NAV is "calculated and announced at the close of each Working Day ... by 11.00 p.m.". The HK close is the same calendar day.
- MONQ50: the SID text could not be fetched (the Motilal SID links returned 404 or were not readable); its KIM (29 May 2026) was read only for creation units. Valuation wording is assumed to match MON100's (same fund house, same SID template); not read.

**Motilal iNAV page (D15) — CONFIRMED**
https://www.motilaloswalmf.com/inav (read 14 Nov 2026): for the international ETFs (MON100, MONQ50) iNAV is calculated "considering only currency movement from the previous day's closing NAV", with a 15-second disclosure delay. So the Motilal iNAV is previous NAV times the USD/INR move; it does not follow the US index or futures during Indian hours. A Motilal "NAV calculation" PDF found by search (motilaloswalmf.com/CMS/assets/uploads/Documents/b08fe-motilal-oswal-nasdaq-100-etf-nav-calculation.pdf) returned HTTP 404 when fetched, so its search snippet (previous-day NAV adjusted for forex in real time; Thomson Reuters 5 pm rate) is not verified from the document. Mirae's SID says only that iNAV is "based on the latest available data regarding the portfolio of the ETF" (p. 9) and gives no formula.

**Consequences for fair value**: do not use one FX time for all funds. Mirae US funds: 15:30 IST (or RBI) rate on the NAV's date and the US close of that date. Motilal: 17:00 IST Reuters rate. Nippon: RBI rate. The USD/INR from FRED `DEXINUS` (noon NY) used in D10 is only a proxy.

### D37. Notice reader v1, no LLM: SEBI + NSE notice listings -> `notices` (keyword candidates only)
Live checks 14 Nov 2026, plain HTTP client (project UA), a handful of requests, >= 2 s apart for SEBI and >= 1.5 s for NSE (`sebi.gov.in: 2.0` added to the default intervals in `etfp/http.py`).

| Source | URL | Format | History | Result |
|---|---|---|---|---|
| SEBI RSS | https://www.sebi.gov.in/sebirss.xml | RSS 2.0, all SEBI sections (circulars, press releases, orders) | last 30 items, about 3 days | WORKS, no cookies. Item dates have no time of day ("13 Nov, 2026 +0530"). Used daily (`sebi_rss`). |
| SEBI Legal listing | `POST https://www.sebi.gov.in/sebiweb/ajax/home/getnewslistinfo.jsp` (the call the listing page makes), form fields `sid=1 ssid=-1` (Legal, all sub-sections), `fromDate`/`toDate` dd-mm-yyyy; page k+1 = `next=n&nextValue=k` | HTML fragment, 25 rows/page, "N to M of T records" + `totalpage` | full (2,821 circulars listed); Jan-Jun 2026 = 86 rows, same as the sum of the six monthly windows | WORKS, no cookies. One request timed out once and worked on a single retry 5 s later. Shows SEBI's "Active" view: items moved to "Archive" may be missing (VERIFY). Used daily (7-day window) and for backfill (`sebi_listing`). |
| NSE circulars API | `https://www.nseindia.com/api/circulars?fromDate=dd-mm-yyyy&toDate=dd-mm-yyyy` after loading https://www.nseindia.com/resources/exchange-communication-circulars for cookies (same pattern as `nse_etf_api`, D-logged earlier) | JSON `data` list: date, number, subject, department, PDF link; NSE + NCL | a whole month in one answer (~700 rows, ~300 KB) | WORKS. The same circular appears once per department (125 of 700 rows in Sep 2026); merged in cleaning. Used daily (7-day window) and for backfill (`nse_circulars`). |
| NSE circulars RSS | https://nsearchives.nseindia.com/content/RSS/Circulars.xml | RSS | only the last day (25 items) | Works, NOT used: the API has more fields and history. |
| AMFI notices | https://www.amfiindia.com/ (new Next.js site) | rendered in the browser | - | NOT FOUND: the only RSS feeds are NAV feeds (`portal.amfiindia.com/RssNAV.aspx`); a guessed `/amfi-circulars` route is 404. No headless browser used. Not implemented. |
| AMC sites | various | mostly PDFs | - | Not attempted. NSE's "Mutual Fund" department relays AMC suspension/resumption of subscriptions for funds on NSE's MF platform, which covers part of this. |

**What was built.** `src/etfp/collect/notices.py` (fetch saves untouched bytes; parsers separate and fixture-tested), `src/etfp/clean/notices.py` (table `notices`, wired as the last entry in `etfp.clean.CLEANERS`), `config/notices.yaml` (keyword rules, `version: 2`), CLI `uv run etfp collect notices` (last 7 days + RSS, re-downloads even if saved today; `--from/--to` = one window per calendar month, resumable), source `notices` added to `jobs.DAILY_SOURCES` so `etfp run daily` collects it (RSS only when the day is today). Raw: `data/raw/sebi_rss/<day>/sebirss.xml`, `data/raw/sebi_listing/<window end>/sebi_legal_<from>_<to>_p<n>.html` (all pages are downloaded before any is saved, page 1 last, so a half-failed window is retried whole), `data/raw/nse_circulars/<window end>/nse_circulars_<from>_<to>.json`. The cleaner reads every version of every raw file (a newer "latest items" download must not hide older items) and warns when a SEBI window's pages hold fewer rows than the page states.

**Table `notices`** (one row per `notice_id` = `sebi:<SEBI page id>` or `nse:<circular no.>`): source, issuer, published_date, `published_ts` (= date at 00:00 IST, with `published_time_known = False`: no source gives a time), `first_seen_ts` (first download time from the raw manifest; an upper bound on publication only for notices caught by the daily run), title, url, section (SEBI section / NSE departments), ref_no, category, matched_keywords, relevant, symbols, rules_version, feeds, source_file (first raw file by download time), n_source_files. **Keyword matches are candidates, never events**: nothing writes to `events`; a notice becomes an event only when someone adds it to `data/manual/events.csv` with its primary document (D23). For a later LLM step, add a separate table (e.g. `notice_labels` keyed by notice_id: model, run time, category, direction, summary) rather than changing this one; `rules_version` makes the two comparable. Not in the research export yet (orchestrator, with the schema bump).

**Rules.** Title-only, case-insensitive, whole words with optional plural; `any` or `all_of` (AND of OR-groups); the first matching rule gives the category (cap, rule, caution, fund_status, amc_notice, as in `events`); all hits are listed. v1 -> v2 the same day: `amc_notice` no longer accepts "ETF"/"fund of funds" as context (it flagged every domestic Mirae/Motilal ETF listing); it now needs an overseas word (Nasdaq, NYSE, FANG, S&P 500, Hang Seng, overseas, international, global, US, feeder).

**Backfill run (14 Nov 2026).** 1 Jan - 14 Nov 2026: 22 monthly windows + today's window, 0 failures; July and October needed a second SEBI page. Result: 5,527 notices (NSE 5,323, SEBI 204), 100 relevant (rule 10, caution 1, fund_status 89, cap 0, amc_notice 0). The ETF band change is fully visible: SEBI circular 15 Jun 2026 "Norms for Base Price, Price Bands, Call Auction in pre-open session and Close-out procedure for ETFs" (sebi:102121), NCL/CMPT/74712 (16 Jun) and NSE/CMTR/74767 (18 Jun); NSE mock trading 27 Aug (NSE/CMTR/76017); SEBI extension of the timeline 28 Aug (sebi:104094, also NSE/CMTR/76041, NCL/CMPT/76060); NSE/CMTR/76101 "Trading in ETFs - update in trading parameters" 1 Sep; go-live mock 4 Sep (NSE/CMTR/76187); NSE/SURV/76993 "Cautionary Messages on Trading Terminal - ETF Update" 16 Oct (caution). Overseas fund-of-fund suspensions/resumptions appear as fund_status (e.g. PGIM Global Equity Opportunities FoF suspended 9 Mar 2026, PGIM Global Select Real Estate FoF resumed 7 Oct 2026, Baroda BNP Paribas Aqua FoF resumed 1 Sep 2026).

**Open.** (1) No `cap` notice in 2026: SEBI's overseas-limit changes for mutual funds have usually gone out as letters to AMFI, not as public circulars, so these sources may never show them; the research dossier's cap history still needs the primary letters / AMFI communications (R5). (2) SEBI listing "Active" vs "Archive": check that superseded circulars stay listed before relying on the backfill for 2020-2025. (3) AMFI and AMC notices: no plain-HTTP listing found. (4) Times of day: none of the sources gives one; `first_seen_ts` is only as good as the daily schedule. (5) History before 2026 not pulled yet: `uv run etfp collect notices --from 2020-01-01 --to 2025-12-31` (72 months x about 3 requests, about 8 minutes at the rate limits). (6) BSE notices not attempted.

### D38. Pre-Sep-2026 band mismatches explained: rounding, special sessions, missing-NAV fallback, budget sessions (14 Nov 2026)
Method: every official band (security master `PricRg`, Feb 2024 - 4 Sep 2026, 7,668 ETF-days) compared with candidate bases (NAVs dated T-10..T, previous closes, each rounding combination), read-only on `data/raw`. Rules are kept only where they hold across all ETFs on the days they apply; single-row tuning was not done.

| Cause (414 mismatches before) | Rows | What the data show | Fix |
|---|---|---|---|
| Rounding | 233 | The base NAV is first rounded to the nearest paisa; high limit rounded down; low limit rounded **down** for sessions up to 8 Dec 2025 and **up** from 9 Dec 2025 (all 12 ETFs switch the same day). MAFANG 7 Feb 2024: NAV 78.8640 -> 78.86; x0.8 = 63.088 -> 63.08; x1.2 = 94.632 -> 94.63 = NSE (old code: 63.10). BANKBEES 9 Dec 2025: 617.0581 -> 617.06; x0.8 = 493.648 -> **493.65**; 740.472 -> 740.47 = NSE. BANKBEES 8 Dec 2025: 612.0191 -> 612.02; 489.616 -> **489.61**; 734.42 = NSE. | `rounding.base: nearest` + dated `rounding.history` in rules.yaml |
| No NAV dated T-2 (US/HK holiday, Sat special session, Muhurat 1 Nov 2024) | 98 | Through 9 Oct 2025 NSE centred the band on the **previous close** (102 of 103 such days); from 24 Oct 2025 on the **latest earlier NAV** (52 of 52). HNGSNGBEES 14 Feb 2024 (HK holiday 12 Feb): prev close 241.12 x0.8 = 192.896 -> 192.89, x1.2 = 289.344 -> 289.34 = NSE. MON100 5 Nov 2024: 164.02 -> 131.21-196.82 = NSE (this is the "Muhurat NAV gap" of D24: not a hidden NAV, the previous close). MAFANG 27 May 2026 (Memorial Day): NAV 22 May 162.2176 -> 162.22 -> 129.78-194.66 = NSE. | `missing_nav_fallback` in the nav_t2 regime; `band_base_kind` = `prev_close` on those rows |
| Special sessions 2 Mar and 18 May 2024 | 24 | Fixed +/-5% around NAV(T-2), all 12 ETFs, both days. BANKBEES 2 Mar: 470.0147 -> 470.01; x0.95 = 446.5095 -> 446.50; x1.05 = 493.5105 -> 493.51 = NSE. NIFTYBEES 2 Mar: 243.22 -> 231.05-255.38 (the "ratio 1.105" of D22). | `special_sessions` in rules.yaml |
| NAV struck on a non-session day (31 March) | 23 | NSE counts a fund's NAV dated on a non-session day as T-2: for 2 Apr 2024/2025/2026 it used the NAV of 31 Mar (fiscal year-end NAV, struck even on Sun/holiday). GOLDBEES 2 Apr 2024: NAV(31 Mar) 56.9324 -> 56.93; 45.544 -> 45.54; 68.316 -> 68.31 = NSE. For 1 Apr 2024 the T-2 NAV stays 27 Mar. | `nav_align`: T-2 = latest day before T-1 that is a session or a NAV date of the fund |
| Union Budget sessions missing from the calendar | 16 (+2) | 1 Feb 2025 (Sat) and 1 Feb 2026 (Sun) were NSE sessions: 3 Feb 2025 PREV_CLOSE NIFTYBEES 262.13 ≠ 31 Jan close 263.00 (2026: 282.41 vs 286.73); AMFI NAVs dated 1 Feb for domestic funds; the security master dated 31 Jan 2025 / 30 Jan 2026 equals +/-20% of NAV(30 Jan / 29 Jan) exactly for all 12 ETFs. The band of 4 Feb 2025 / 3 Feb 2026 uses the 1 Feb NAV. | Added to `config/calendars/nse_holidays.yaml` `extra_sessions` |
| Still unexplained | 18 (+2 new) | **22 Jul 2024, all 12 ETFs**: NSE used the previous close although NAVs dated T-2 (18 Jul) exist (bands made on Fri 19 Jul, the day of the global IT outage; cause unconfirmed). **MAFANG, MASPTOP50 21 May and 16 Oct 2024**: previous close used though a Mirae NAV dated T-2 exists (late NAV?). **MAFANG, MASPTOP50 4 Oct 2024**: implied base ~93.46 / ~44.13 matches no NAV or close. **New:** MAHKTECH 3 Jan 2025 (NSE used the stale NAV in the prev-close era; was 1 tick off by chance before) and MAFANG 7 Mar 2024 (NAV 80.195 is an exact tie; NSE's band implies 80.19, 2 ticks on the high side). | Left as `band_mismatch`; the official band is authoritative |

- **Result after rebuild.** Pre-Sep-2026: 20 mismatches (was 414), 7,611 exact to the paisa (was 1,388), 13 within one tick (was 5,866). The 13 one-tick rows include exact-tie NAVs (x.xx5): 14 of 72 such ties in NSE data round down, not up, probably float arithmetic at NSE or more NAV decimals than AMFI shows. From 7 Sep 2026 still 564 of 564 exact.
- **Compared rows 7,668 -> 7,644.** The security-master files dated 31 Jan 2025 and 30 Jan 2026 now apply to the budget sessions, so 3 Feb 2025 and 2 Feb 2026 (12 ETFs each) have no official band until the files dated 1 Feb are downloaded (raw for the two budget sessions was never fetched; the next backfill run will ask for them).
- **Schema impact (meaning change, schema bump needed; version not changed here).** `band_base_kind` is now the base actually used (can be `prev_close` in the nav_t2 regime, 246 rows); `band_base_price`, `band_low_initial`, `band_high_initial`, `band_low_max`, `band_high_max` follow the new rounding, special sessions and fallback; `nav_t2_date` can be a non-session NAV day (31 Mar; 36 rows) and shifts after the budget sessions. `band_low`/`band_high` (official) are unchanged.
- **VERIFY.** Exact switch date of the missing-NAV fallback (between 10 and 24 Oct 2025) and of the low-limit rounding (9 Dec 2025 is the first session seen) and the reason for both: an NSE circular would settle them. Budget sessions: confirm from the NSE circulars.
- **Follow-up (orchestrator, 14 Nov).** Security-master files dated 2025-02-01 and 2026-02-01 downloaded (HTTP 200), so the budget-session band files now exist in raw.

### D39. Integration of the 14 Nov build wave: look-ahead fix, SLB source defect, schema 1.5.0
- **Look-ahead in `nav_known_at_close` — FIXED.** Found while checking the bullion module: 43 ETF-days had `nav_publication_lag_days` < 0, i.e. a NAV dated *after* t counted as known at t's close. Cause: a NAV dated on a later non-session day (Sun 31 Mar 2024 year-end NAV; also NAVs dated across HK/US holidays, e.g. MAHKTECH 22 Dec 2023 -> 26 Dec) embeds the same underlying close as t, so the "underlying close <= India close" rule accepted it. It is struck and published after its own date, so it was not knowable. Values differ (NIFTYBEES 28 Mar 2024: 247.0198 used vs 247.0203 dated 28 Mar; MAHKTECH 12.3115 vs 12.3207). Rule added in `features/nav_align.py`: candidate NAVs must also be dated <= t. Test `test_nav_dated_after_t_is_never_known_at_t`. After rebuild: 0 rows with negative lag. Research results using `prem_nav_known`, `fv` or `x_april` near 31 March should be re-run.
- **NSE SLB archive defect.** `nse_slb_eligible/2021-09-16/SLB_ELG_SEC_16092021.csv` is cut off mid-line at 290,816 bytes (71 x 4 KiB, no final newline); a forced re-download returned the same bytes, so the defect is NSE's. Listed in `config/raw_exceptions.yaml`; the SLB cleaners now honour that list (they did not before, so the build stopped). The SLB collector also refuses to store any body without a final newline (`SourceUnavailableError`, retried): all 446 other stored SLB files end with one.
- **Nippon iNAV feed still frozen.** A scratch snapshot on Sat 14 Nov 19:43 IST returned NIFTYBEES 250.7151, the same value seen on 10 Nov (D15) against NAV 288.76. Recorder first live run is Mon 16 Nov; judge Nippon after that day.
- **Schema 1.5.0** (changelog in `export.py`): D38 band meaning changes + this look-ahead fix + new research files `notices.parquet` (D37) and `bullion_ref_daily.parquet` (D35). The open dataset (`config/dataset.yaml`) lists neither new table, so neither is published.
- **Not wired, deliberately.** IBJA is not in the daily job (`jobs.py`): IBJA's site asks users who value financial products to subscribe to its paid API; Amish decides. Bullion columns are not in `etf_day` yet (D35 proposal stands).
- **Daily valve status is not split-adjusted.** No split happened since daily data starts (2024-02-05); a future split would read as a creation in `valve_monthly`. Handle when `config/corporate_actions.yaml` gets a new entry.
- **Lint.** `paper/` (frozen research-draft analysis scripts) excluded from ruff (`pyproject.toml`); `ruff check .` is clean.

### D40. Notices backfill 2020-2025, SEBI Archive view, overseas-cap event candidates (R5; 14 Nov 2026)
**SEBI Archive (D37 open item 2): RESOLVED, it matters.** The circulars page has an "Archive" tab (`HomeAction.do?doListingCirArchive=yes&sid=1&ssid=7&smid=0`) that posts the same form to `/sebiweb/ajax/home/getArchiveCircularlistinfo.jsp` (found in SEBI's own `js/entry.js`, `searchArchiveCircularList`). It lists circulars SEBI has moved out of the Active view: 200 records, Jan 2020 - Apr 2025 at the check. For Jan 2020 - Mar 2021, 41 archive circulars were compared with the 299 Active-listing ids: zero overlap, so the Active backfill alone misses them. Same HTML layout, so the existing parser reads it (real-page fixture `tests/fixtures/notices/sebi_archive_20200101_20210331_p1.html`). Built: source `sebi_archive` (`fetch_sebi_archive`, raw `data/raw/sebi_archive/<to>/sebi_archive_<from>_<to>_p<n>.html`), parsed with `parse_sebi_listing`, wired into the cleaner, and `collect notices --from/--to` now also pulls one wide Archive window (8 pages). Not in the daily run (the archive changes only when SEBI moves items; re-run the backfill command to refresh it).

**NSE legacy entries.** 12 of about 190,000 NSE circular entries (2020-2025) have an empty `circDisplayNo` but a `circNumber` and subject; the parser crashed on them and stopped `etfp build`. Now: the plain `circNumber` is used as the reference (`nse:46162`); with no number at all but a subject, the row is kept as `nse:<YYYYMMDD>:<first 10 hex of sha1(subject|department)>` (stable across files, `ref_no` empty); with no subject the entry is set aside and a warning names the file and the count. None of the real files needed the hash or set-aside paths. Tests added.

**Backfill result.** `collect notices --from 2020-01-01 --to 2025-12-31`: 144 windows (72 months x SEBI + NSE) fetched, 0 failed, plus the Archive window. Notices table rebuilt standalone in memory (not written; `etfp build` not run by this agent): 36,009 notices (NSE 34,125; SEBI 1,884, of which 111 from the Archive feed). Relevant before 2026: cap 8, fund_status 133, rule 10, amc_notice 6, caution 2. Keywords unchanged (`config/notices.yaml` stays `version: 2`): a scan of unmatched 2020-2025 titles with overseas/foreign/international/AMFI words found no missed cap notice (only SEBI 2023-03-29 NAV-disclosure timing for overseas funds, which is not a limit change). Cap-category hits worth reading: SEBI circulars of 2020-11-05 and 2021-06-03 (both primary, read), 2024-04-30 (fund-manager rules, not a limit), 2024-11-04 (investing in overseas funds with Indian exposure, not a limit); the rest are AIF notices and noise. `fund_status` hits relevant to us are Nippon (NSE/NMF/64537 resume 14 Oct 2024, 64597 stop 17 Oct 2024) and Motilal (NSE/NMF/65572 SIP stop 12 Dec 2024, 65967 subscriptions 3 Jan 2025). Notice-reader limitation confirmed: SEBI's letters to AMFI (Jan 2022, Jun 2022, Mar 2024) are not public circulars and appear nowhere in these listings.

**Overseas-cap history candidates:** `data/manual/events_candidates_cap.csv` (18 rows, events-CSV format, loads cleanly through `manual_events.build`; the owner/research side decides what moves into `events.csv`). `direction` is left blank (the column means premium effect, which is set before looking at prices); open/close is written in the description. `verified = true` (9 rows) only where the primary text was read: SEBI circulars 2020-11-05 (USD 600m per fund, 200m per fund for ETFs, industry 7bn / 1bn) and 2021-06-03 (USD 1bn per fund, ETF 300m per fund, industry 7bn / 1bn, effective immediately), and Motilal Oswal MF notices of 2022-01-13, 2022-02-01 (cites SEBI email of 28 Jan and AMFI clarification of 30 Jan 2022; ETF creation by the AMC stopped from 2 Feb 2022), 2022-03-22, 2022-09-15 (creation reopened 19 Sep 2022, APs/market makers only), 2022-11-22, 2023-03-03 and 2024-12-05. Not verified (press or listing title only): the SEBI email/AMFI note of Jan 2022, SEBI letter of 17 Jun 2022 with AMFI circular of 20 Jun 2022 (reference numbers quoted in Motilal's own notices), SEBI letter to AMFI of 20 Mar 2024 stopping inflows into overseas-ETF schemes from 1 Apr 2024, Mirae's Feb 2022 suspension, Nippon's Jun 2022 and Oct 2024 notices (Hang Seng BeES), and the NSE/NMF circular titles. Source notes: AMC PDFs open in a browser or WebFetch, but plain scripted requests to motilaloswalmf.com return an HTML block page, so the PDFs were read through the fetch tool, not worked around.

**Gaps.** (1) No primary text for any SEBI/AMFI letter; ask the owner or look for them in AMC addenda and AMFI's member circulars (AMFI site not machine-readable, D37). (2) Mirae (MAFANG, MASPTOP50, MAHKTECH) and Nippon (HNGSNGBEES) primary notices not found; search engines surfaced Mirae notices only in summaries. (3) Nothing found for 2025 and 2026 apart from fund-house pauses in press (Nippon 21 Apr 2026, Axis 6 May 2026, PGIM Mar 2026) and a secondhand mention of a March 2026 SEBI master circular restating the same limits; no source showed the industry limit changing since 2021. (4) Whether the per-fund limit applies to the AMC or the scheme is worded both ways in the press; the 2021 circular says per Mutual Fund. (5) Full `etfp build` with the new notice data was not run here.

### D41. Owner decisions, 15 Nov 2026: free project, licences, derived-only dataset
- **Free research project (Amish).** No paid data APIs, subscriptions, LLM API costs or paid hosting. Bloomberg (December 2026, via access Amish already has) stays research-only.
- **IBJA (yes).** `ibja` added to `jobs.py` DAILY_SOURCES: fetched only for today (the page is a latest snapshot with ~84 days of PM history), so the 19:45 IST evening run picks it up after IBJA's PM poll; the morning run skips it with a note. Research use only: IBJA asks valuation users to buy its API, so `bullion_ref_daily` is not in `config/dataset.yaml` and never published.
- **LLM step in the notice reader (skip).** Keyword tags plus manual review only (D37).
- **Licences (ok).** Code MIT (`LICENSE`, `pyproject.toml`); open-dataset data CC BY 4.0 (`LICENSE-DATA` written by `etfp export dataset`, manifest `licence` field). Publication via Zenodo (free DOI) remains Amish's step.
- **Derived-only dataset (ok).** The release republishes no value taken verbatim from a source. In `config/dataset.yaml`: `etf_day` drops prices, volumes, NAVs, official bands, `band_base_price`, `band_*_diff` (official = computed + diff), units, BSE figures, feeder units, `float_net`, `attention`; `unit_events` drops `units_prev`/`units_new`; `limit_day` drops limit levels and order-book quantities; tables that are pure source copies (`nav_daily`, `feeder_nav_daily`, `units_daily`, `band_official`, `bse_prices`, `slb_*`, `inav_snapshots`, `units_monthly`, `fof_monthly`, `trends_daily`) are no longer exported. `valve_monthly` is added (without `units_start`/`units_end`). Published: premiums, `x_april`, returns, ratios (turnover), computed bands and limit flags, unit changes and creation units, valve status, events, limit-day diagnostics. Users rebuild source values with `etfp backfill` + `etfp build` under each source's terms. Test `test_real_config_publishes_no_source_values`. First release build (15 Nov): 6 tables, `etf_day` 15,528 rows x 74 columns.
- **Hosting (later).**

### D42. 18 more domestic control ETFs (R20) and NSE symbol changes (18 Nov 2026)
**Selection rule.** From NSE's ETF list (`nse_etf_seclist/2026-11-16/eq_etfseclist.csv`), `ETF Underlying = EQUITY` only (no gold/silver/debt/global), ranked by traded value in the NSE full bhavcopy (`TURNOVER_LACS`, series EQ, 106 sessions 1 Jun - 31 Oct 2026, old and new ticker summed). Kept broad and sector index funds listed by Feb 2023 (data from 2021 where the fund existed), spread over fund houses and indices; skipped: thematic/factor funds listed after Jan 2024 (MODEFENCE, METALIETF, SMALLCAP, MOREALTY, MOCAPITAL), and funds with a unit split that also changed the ISIN inside the security-master period (PVTBANIETF 1 Mar 2024, BANKNIFTY1 27 Feb 2026, PSUBANK 10 Jul 2026, HDFCNIFBAN 2 Feb 2024, FMCGIETF 10 May 2024) because units/ISIN lineage would need extra handling. Domestic funds have no overseas cap, so creation is open (`group: control`, `tracked: false`).

| Symbol | Fund house | Index | Avg traded value Jun-Oct 2026, Rs cr/day | AMFI code | Former NSE symbol (last day) |
|---|---|---|---|---|---|
| ITBEES | Nippon India | Nifty IT | 58.10 | 148408 | NETFIT (2022-05-09) |
| HDFCSML250 | HDFC | Nifty Smallcap 250 | 30.32 | 151375 | - (listed 2023-02-21) |
| SBINIFTY50 | SBI | Nifty 50 | 22.53 | 135106 | SETFNIF50 (2026-10-29) |
| PSUBNKBEES | Nippon India | Nifty PSU Bank | 21.87 | 140089 | - |
| MID150BEES | Nippon India | Nifty Midcap 150 | 18.82 | 146271 | NETFMID150 (2022-05-09) |
| PHARMABEES | Nippon India | Nifty Pharma | 13.87 | 149008 | NETFPHARMA (2022-05-09; listed 2021-07-07) |
| CPSEETF | Nippon India | Nifty CPSE | 9.07 | 140107 | - |
| HDFCNIFTY | HDFC | Nifty 50 | 7.71 | 135853 | HDFCNIFETF (2023-06-15) |
| NIFTYETF | Mirae Asset | Nifty 50 | 7.40 | 145633 | MAN50ETF (2023-08-31) |
| NEXT50IETF | ICICI Prudential | Nifty Next 50 | 7.29 | 144587 | ICICINXT50 (2023-12-19) |
| MIDCAPETF | Mirae Asset | Nifty Midcap 150 | 6.24 | 149918 | MAM150ETF (2023-08-31; listed 2022-03-15) |
| HDFCBSE500 | HDFC | BSE 500 | 6.15 | 151376 | - (listed 2023-02-21) |
| NIFTY1 | Kotak Mahindra | Nifty 50 | 4.88 | 112351 | KOTAKNIFTY (2024-03-19) |
| ICICIB22 | ICICI Prudential | BSE Bharat 22 | 4.80 | 141957 | - |
| BANKBETA | UTI | Nifty Bank | 3.21 | 148173 | UTIBANKETF (2025-12-23) |
| NIFTYBETA | UTI | Nifty 50 | 2.73 | 135320 | UTINIFTETF (2025-12-23) |
| MOM100 | Motilal Oswal | Nifty Midcap 100 | 2.71 | 114456 | M100 (2021-06-15) |
| SENSEXIETF | ICICI Prudential | BSE Sensex | 1.66 | 101705 | ICICISENSX (2023-12-19) |

Controls are now 22 (with NIFTYBEES 180.3, BANKBEES 46.8, JUNIORBEES 29.8, NIFTYIETF 19.9 Rs cr/day), 9 fund houses, 12 indices. HDFCSML250 and HDFCBSE500 traded only a few lakh rupees a day in 2023 (HDFCBSE500 median premium 4.3% in 2023 Q2, below 0.5% from 2023 Q4): liquidity was measured on 2026 data and is not uniform over the sample.

**Verified (from raw files we hold, no new downloads).** ISIN: NSE ETF list. AMFI scheme code: the only `NAVAll.txt` (2026-11-16) row with that ISIN; the code is present in AMFI NAV history from 2021-01-01 (or launch) to 2026-11-16 with the same ISIN and name throughout (real extract kept as test fixture `tests/fixtures/amfi/NAVAll_controls_20261116.txt`). Prices: full bhavcopy 2021-2026 and UDiFF 2024-2026. Security master (`IssdCptl`, `PricRg`): present for all 18 for every file from 2024-02-05. Gaps in NAV only on the special sessions already known (Muhurat days), same as the existing ETFs.

**Unit splits (config/corporate_actions.yaml, verified from raw data only):** HDFCNIFTY 10:1 price ex 2021-02-17, NAV ex 2021-02-22; BANKBETA 10:1 price ex 2021-02-17, NAV ex 2021-02-22; NIFTYBETA 10:1 price ex 2023-09-25, NAV ex 2023-09-26 (numbers in the yaml). BANKBETA traded 3-7% above NAV for the first days after its split (17-19 Feb 2021): real prices, not an adjustment error (the NAV on the new scale agrees).

**NSE symbol changes — FOUND, affects existing ETFs too.** NSE renames ETF tickers (most fund houses did between 2022 and 2026). The cleaners kept only symbols on the current NSE list, so a renamed fund's history before the rename was silently missing from `prices_daily`, `units_daily`, `band_official` and `etf_day`: **NIFTYIETF had no rows before 2023-12-20 (was ICICINIFTY), MON100 none before 2021-06-16 (was N100), SILVERBEES none before 2022-05-10 (was NETFSILVER).** New config field `former_symbols` (`symbol`, `until`, `source`) and `clean.common.map_former_symbols`, applied in `prices_daily` and the security-master reader: rows of the old ticker dated on or before `until` get the current ticker; the raw files and `source_file` are unchanged. The build stops if a relabelled row's ISIN (UDiFF, security master) differs from the configured ISIN, or if old and new ticker give two rows for one day. Sources: NSE `symbolchange.csv` (https://nsearchives.nseindia.com/content/equities/symbolchange.csv, read once on 2026-11-18, not stored in data/raw) lists 13 of the 16 renames used. Not listed there, inferred from price continuity (old ticker's last close = new ticker's first PREV_CLOSE, old ticker gone the same day): ICICINIFTY -> NIFTYIETF and ICICISENSX -> SENSEXIETF (20 Dec 2023, the same day as ICICI's listed renames), SETFNIF50 -> SBINIFTY50 (30 Oct 2026, also same ISIN INF200KA1FS1 under both tickers in UDiFF; probably too recent for NSE's list).

**Result (scratch rebuild of the clean tables + `etf_day` from the real raw store, not written to data/clean).** `etf_day` 41,148 rows. Additions only: no existing row of `prices_daily`, `units_daily` or `band_official` changed value. NIFTYIETF +734 rows (now from 2021-01-01), MON100 +111 (2021-01-01 to 2021-06-15; split factors apply), SILVERBEES +61 (from 2022-02-07). Every new control has NAV on all but the Muhurat days. Band check vs official `PricRg`: new 18 controls, before 7 Sep 2026: 23 mismatches in 11,538 ETF-days (0.20%): 18 on 22 Jul 2024 (the all-ETF day of D38) and 5 one-tick misses on exact-tie NAVs (x.xx5; NEXT50IETF 3 Jun and 11 Jul 2024, HDFCNIFTY 2 Jun 2025, SBINIFTY50 11 Jul 2025, PSUBNKBEES 2 Sep 2026), the same tie issue as D38. From 7 Sep 2026: 0 of 864. Existing 12: unchanged (20 of 7,692 before; 0 of 576 after). Median `prem_nav_known` from 7 Sep 2026 is within +/-0.2% for every control.

**VERIFY / open.** (1) Creation-unit sizes not read from the SIDs (18 x `creation_unit_size: null`, in `verify`). Hint from the security master, the largest common divisor of all unit changes 2024-2026: ITBEES 100,000; PSUBNKBEES 25,000; MID150BEES 35,000; PHARMABEES 75,000; NIFTYETF 50,000; MIDCAPETF 400,000; BANKBETA 100,000; MOM100 125,000; NIFTY1 12,500; HDFCNIFTY 4,000; the rest 1-20, i.e. not informative (units also change by small amounts). (2) The three unlisted renames rest on price continuity (and ISIN for SBI). (3) `symbolchange.csv` is not in the raw store; a collector for it would let a test catch future renames. (4) SLB tables and BSE prices before an ISIN change are not relabelled (SLB uses config symbols; BSE matches ISIN). (5) Not changed: `schema_version` (no column changed meaning; rows were added for NIFTYIETF, MON100 and SILVERBEES, so research results using those three before the dates above should be re-run; orchestrator may prefer a minor bump). The frozen research scripts `paper/draft3/analysis/common.py` hard-code the four old controls (`DOM = [...]`); the research side should select `group_label == "control"`.

### D43. Free fair-value inputs: Yahoo 60m history mode (still HTTP 429), FBIL reference rates (works), roll guard (17-18 Nov 2026)
**Sources tried (17 Nov 2026, 19:44-20:02 UTC; generic desktop-Chrome User-Agent, no personal headers):**
| Request | Result |
|---|---|
| Yahoo `query1.../v8/finance/chart/NQ=F?interval=60m&range=730d` (curl) | HTTP 429 "Too Many Requests" (19 bytes) |
| Yahoo same on `query2` host, 24 s later | HTTP 429 |
| Yahoo NQ=F 60m via `etfp collect yahoo-history` (httpx, period1/period2), 17 min later | HTTP 429; command stopped, nothing stored |
| Stooq `stooq.com/q/d/l/?s=nq.f&i=d` | HTTP 200 but an HTML JavaScript proof-of-work page, no CSV. Not worked around. |
| Dukascopy datafeed (`datafeed.dukascopy.com`) hourly candles, index CFDs `USATECHIDXUSD`, `USA500IDXUSD` | HTTP 503 or hung (timeout); EURUSD candles answered 200, so the server works but these instruments are not served there. No INR pair seen. Dropped. |
| FBIL `www.fbil.org.in/wasdm/refrates/fetchfiltered?fromDate&toDate&authenticated=false` | HTTP 200 JSON; a full calendar year in one request (~4 s, ~130 KB) |

Yahoo got three requests in total, each >= 10 s apart; the 429 comes back at once, so it is a block on this client/network, not a rate on our pace. A real browser session (as for NSE quotes, D12) or another network might pass; not tried (owner's call).

**What works: FBIL reference rates.** New collector `collect/fbil_refrates.py` (`etfp collect fbil --from/--to`, one request per calendar year, 3 s apart), clean table `fx_ref_daily` (date, pair, rate = rupees per ONE unit, quote_units, rate_quoted, published_ist, source, source_file), and `etf_day.usdinr_ref` = FBIL USD/INR dated the same day (NaN if none, never carried forward). Backfilled 2021-01-01..2026-11-18 into `data/raw/fbil_refrates/2026-11-18/` (6 files, 836 KB): USD, EUR, GBP, JPY 1,415 days each from 2021-01-01; AED and IDR from 2026-01-05; RUB from 2026-09-15. Published 13:30 IST (older) or 13:00 IST (recent), so known before the 15:30 close. Hand check: 9 Nov 2026 "INR / 1 USD" 93.8579 in the raw JSON = `fx_ref_daily` = `usdinr_ref`.
- **VERIFY:** the public feed stopped at 2026-11-09 when asked on 2026-11-18 (about a week's lag for non-subscribers?). The daily job should re-ask a rolling 31-day window; `etfp collect fbil` defaults to that. It is NOT yet in `jobs.DAILY_SOURCES` (adding it changes the daily-run file counts other tests pin; left to the jobs.py owner).
- **No HKD/INR** at FBIL (none 2021-2026). HK ETFs keep `nav_only` until a free HKD source is found (USD/HKD cross from another free source is an option; not built).
- Redistribution terms not checked: `usdinr_ref` is under `drop` in `config/dataset.yaml` (D41).

**Yahoo 60m history mode (built, untested live).** `yahoo_intraday.fetch_history` / `etfp collect yahoo-history`: one request per symbol (NQ=F, ES=F, USDINR=X, HKDINR=X; no Hang Seng future because the HK ETFs have `futures_proxy: null`), 728 days of 60m bars (Yahoo's 60m limit is ~730 days, VERIFY), `period2` = now rounded down to the hour so no in-progress bar is stored, >= 10 s between requests, stop at the first 429/403. Saved untouched as `yahoo_intraday/<IST date>/<SYM>_60m_history.json` beside the daily 5m files; the daily 5m collection is unchanged. 728 days from 17 Nov 2026 reaches 19 Nov 2024: 448 US sessions to 4 Sep 2026, so the R12 beta window of 250 NAV dates is reachable (14 sessions are left out by the roll guard below, each costing at most 2 pairs).

**Bar chosen at 15:30 IST:** the close of the LAST BAR THAT HAD ENDED at or before 15:30 IST (10:00 UTC). For 60m futures/FX bars (Yahoo stamps the bar START; these bars start on the hour) that is the bar 09:00-10:00 UTC, staleness 0. For the US close of the NAV's session: the bar ending at 16:00 New York (15:00-16:00), 13:00 on early-close days. Staleness limits unchanged from the build: futures 30 min, FX 60 min; staler = missing, never carried. The audit table `underlying_bar_values` (date, moment IN_1530/US_CLOSE, symbol, close, at, bar_start, bar_end, interval_minutes, stale_minutes, source_file) records the bar used at the 30-minute rule.

**Fixes and rules in the free tier (build.py, small):**
- `load_underlying` de-duplicated bars on (symbol, start) only, so a 5m and a 60m bar starting at the same minute were treated as one and the 60m bar could be lost. Now (symbol, start, bar length). `yahoo_intraday.value_at` now picks the latest bar END (ties: the shorter bar).
- **Roll guard.** NQ=F/ES=F are continuous front-month series; at the quarterly switch the next contract trades ~1% higher (carry), which would inflate F_t/F_a. Only a switch during the US night can sit between F_a (US close) and F_t (15:30 IST). Candidate moments: 18:00 New York opening the CME roll-date session (third Friday - 8 days) and opening the expiry session (third Friday, or the session before it if that Friday is a US holiday: 18 Jun 2026). Free-tier rows whose (US close of a, 15:30 IST t] holds one get no futures ratio -> `nav_only` (about 2 rows per ETF per quarter). For the beta, sessions whose close-to-close interval holds a candidate (also 09:30 New York on expiry day) have no futures level. Which moment Yahoo really switches at is **VERIFY** once bars arrive (compare NQ=F with NQZ26.CME-type contract symbols); then drop the other candidate. Bloomberg rows are not guarded (generic tickers roll differently; check when the data arrive).
- `fv_method` meanings unchanged: `free_futures` where all needed free inputs exist and no roll is straddled, else `nav_only`. `fv_beta_n` already reports the sample; with no bars it stays 0/`no_beta`.

**Open (owner/analysis):**
- `fv_in_fx_a` (S_a) is the FX at the US close of a (existing build rule). D36 says the fund houses value FX at ~15:30 IST (Mirae), 17:00 IST (Motilal) or the RBI/FBIL rate (Nippon) on the NAV date. FBIL of date a is now available and is the documented rate for Nippon; switching S_a to it (or to the 15:30 IST bar of date a) would change what `fv_in_fx_a` means -> schema bump. Not done; owner to decide.
- Hand-computed tests: `tests/test_fair_value_free_futures.py` (normal day 203.01; stale future -> nav_only; FX 60-min stale 202.6446809; no bars -> NAV; US holiday 148.57425; roll night -> nav_only and next day 101.0; 5m/60m same start), `tests/test_collect_fbil_refrates.py`, `tests/test_clean_underlying_values.py`.
- Schema: `etf_day.usdinr_ref` is a NEW column (no existing meaning changed): suggest schema 1.6.0 and a SCHEMA_CHANGELOG line (not bumped here). New clean tables `fx_ref_daily`, `underlying_bar_values` (not in the open dataset; Yahoo tables are denied by pattern only if named so - `underlying_bar_values` is simply not listed under `tables:`).

### D44. Ops: offline fail-fast, missed-session catch-up, launchd priority, build speed (18 Nov 2026)
- **Why:** the 17 Nov evening run had no network; it spent 50 min retrying 17 sources (all httpx ConnectError), then rebuilt and alerted on stale data. The launchd build also took 29 min against ~5 min in Terminal.
- **Offline check** (`src/etfp/netcheck.py`): before collect, DNS + one HEAD (5 s) to AMFI, then BSE, then Yahoo; the first HTTP answer of any status means online. NSE is never probed. Offline: collect fails at once with "offline (no network)", build/export/alerts are SKIPPED with the reason, exit code 1, `etfp status` shows it.
- **Network breaker** (`collect/jobs.py` `NetworkBreaker`): 3 connection failures in a row across at least 2 hosts stop the remaining downloads ("not tried: network down"). Any server answer resets it. The per-host 5-failure rule is unchanged.
- **Catch-up** (`jobs.plan_catch_up`): each daily run also plans the dated files of NSE sessions in the 7 calendar days before its day that are neither stored nor recorded as missing; 404s go to `backfill_state.json`. Before this, a session whose evening and morning runs both failed was never fetched.
- **launchd priority:** daily plists now use `ProcessType = Standard`; "Background" ran Python ~5x slower (`taskpolicy -b`). The iNAV recorder plist stays Background. Plists must be reinstalled to take effect.
- **Build speed:** security-master parsing (58% of time) strips only the 11 fields used; AMFI dates parsed once per file; `store.coerce` builds columns in one step (no pandas fragmentation warning). Terminal build 305 s -> 241 s. All 24 clean tables compared with `assert_frame_equal(check_exact=True)` before/after on the same config: identical.
- **Schema 1.6.0** (export changelog): covers D42 (more rows from symbol changes, 18 controls) and D43 (`usdinr_ref`, `fx_ref_daily.parquet` in the research export).

## Open VERIFY items (running list)

| Item | Where it matters | Status |
|---|---|---|
| iNAV response shape; is depth exposed | M2 | OPEN |
| AMFI history filter parameters and window limit | M1 backfill | PARTLY (D22): 7-day windows work; max and filters OPEN |
| Earliest UDiFF and security-master dates | M1 backfill | RESOLVED (D22): UDiFF 2024-01-01, security master 2024-02-05 |
| Band rounding rule on more days | M3 bands | RESOLVED (D38): base to nearest paisa; high down; low down until 8 Dec 2025, up from 9 Dec 2025 (switch reason VERIFY) |
| `IssdCptl` update lag vs AMC figures | M5 valve status | PARTLY (D33): refreshed in about-weekly batches; lag vs AMC creation dates OPEN |
| NAV timing for HK ETFs and before Jul 2023 | M3 fair value | PARTLY (D36): Mirae US SIDs say NAV(T) uses day-T prices; HK SID silent but implies same-day close; Motilal SID wording (previous day's close) conflicts with D10 data; pre-Jul 2023 not checked |
| FX rate and time used in each AMC's NAV | M3 fair value | RESOLVED from SIDs (D36): Mirae Bloomberg/Reuters ~15:30 IST or RBI; Motilal Reuters 11:30 GMT (17:00 IST); Nippon RBI. MONQ50 SID not read |
| Free futures/FX source reliability | M2/M3 | OPEN — Yahoo 429 again 17 Nov (3 requests, incl. 60m history mode); Stooq JS challenge; Dukascopy has no index CFDs on its datafeed (D43) |
| NSE circular 122/2026 full text | `config/rules.yaml` | OPEN (secondary summaries only) |
| 2027 NSE holiday list | calendars | OPEN (not yet published) |
| Bloomberg tickers per ETF | `config/etfs.yaml` | OPEN |
| Futures proxies for S&P 500 Top 50 and FANG+ | M3 fair value | OPEN (ES used as proxy per SPEC §6.2) |
| NSE quote JSON shape (depth, total buy/sell) | iNAV snapshots | OPEN — reader written against assumed shape (D17) |
| AMFI NAV rows on US holidays per AMC | NAV alignment | OPEN — Mirae has none, Motilal carries forward (D22) |
| NIFTYBEES band ratio 1.105 on 2024-03-01 | R7/R10 | RESOLVED (D38): file applies to the 2 Mar 2024 special session, +/-5% |
| Muhurat session times (calendar uses 09:15–15:30) | calendars, nav_known on those days | OPEN (D24) |
| AMFI NAV missing on Muhurat days the exchange used (e.g. 1 Nov 2024) | nav_t2 band base | RESOLVED (D38): no hidden NAV; NSE used the previous close (missing-NAV fallback) |
| SLB bhavcopy fee column order | R8 | OPEN — inferred from data (D27) |
| Yahoo intraday access | free fair value | OPEN — 429 on 13 and 14 Nov (D22, D29) |
| NSE quote JSON shape + capture blocked for headless Chromium | iNAV depth (CLAUDE.md) | OPEN (D31) |
| China `EQY_SH_OUT` unit; NAV date vs US close | R17 | OPEN (D31) |
| Pre-Sep-2026 band mismatches (414 rows; 237 within ₹0.05) | R2/R10 | PARTLY (D38): 20 left (22 Jul 2024 all ETFs; 6 Mirae rows; 2 single rows) |
| Redistribution terms: NSE, BSE, AMFI, AMC iNAV/disclosures, Google Trends | open dataset (D34) | OPEN — owner to check before publishing |
| Special-session band widths (2 Mar, 18 May 2024) | rules.yaml | RESOLVED (D38): +/-5% around NAV(T-2), all asset classes |
| Bullion ETF valuation price (SEBI basis, AM vs PM, start date) | bullion `nav_check`, `prem_metal` | OPEN (D35) |
| Free London silver/gold price + FX for domestic premium | bullion module | OPEN — LBMA and MCX 403 (D35) |
| IBJA free history before Jul 2026; redistribution terms | bullion module, open dataset | OPEN (D35) |
| SEBI listing "Active" view may drop archived circulars | notices backfill before 2026 | RESOLVED (D40): Archive tab holds 200 other circulars; now collected as `sebi_archive` |
| SEBI overseas-limit letters to AMFI not in any public listing found | notices `cap`, events (R5) | OPEN (D37) |
| AMFI / AMC notices: no plain-HTTP listing | notices | OPEN (D37) |
| Missing-NAV band fallback switch date (prev close -> latest NAV, 10-24 Oct 2025) | nav_t2 bands | OPEN (D38) |
| Union Budget sessions 1 Feb 2025 / 1 Feb 2026 (inferred from data) and their raw files | calendars, backfill | OPEN (D38): circular not read; raw not yet downloaded |
| Creation-unit sizes of the 18 R20 controls | units / valve status for controls | OPEN (D42): SIDs not read; divisor hints in D42 |
| Renames ICICINIFTY->NIFTYIETF, ICICISENSX->SENSEXIETF, SETFNIF50->SBINIFTY50 (not in NSE symbolchange.csv) | `former_symbols` | PARTLY (D42): price continuity (+ ISIN for SBI) |
| FBIL public feed lag (~1 week?) and HKD/INR absence | `usdinr_ref`, HK fair value | OPEN (D43): data to 9 Nov when asked 18 Nov; no HKD 2021-2026 |
| Yahoo 60m history depth (~730 d) and bar alignment (on the hour) | free fair value, beta | OPEN (D43): untested, every request 429 |
| Moment Yahoo's NQ=F/ES=F switch contract (roll date vs expiry) | roll guard | OPEN (D43): both candidates guarded |
| S_a timing: FX at US close vs fund's own FX time (FBIL/15:30/17:00 IST) | `fv_in_fx_a` | OPEN (D43, D36): owner decision, schema bump if changed |
