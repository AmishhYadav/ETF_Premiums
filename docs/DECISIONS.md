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

---

## Open VERIFY items (running list)

| Item | Where it matters | Status |
|---|---|---|
| iNAV response shape; is depth exposed | M2 | OPEN |
| AMFI history filter parameters and window limit | M1 backfill | PARTLY (D22): 7-day windows work; max and filters OPEN |
| Earliest UDiFF and security-master dates | M1 backfill | RESOLVED (D22): UDiFF 2024-01-01, security master 2024-02-05 |
| Band rounding rule on more days | M3 bands | PRELIMINARY |
| `IssdCptl` update lag vs AMC figures | M5 valve status | OPEN |
| NAV timing for HK ETFs and before Jul 2023 | M3 fair value | OPEN |
| FX rate and time used in each AMC's NAV | M3 fair value | OPEN |
| Free futures/FX source reliability | M2/M3 | OPEN — Yahoo returned 429 on first try (D22) |
| NSE circular 122/2026 full text | `config/rules.yaml` | OPEN (secondary summaries only) |
| 2027 NSE holiday list | calendars | OPEN (not yet published) |
| Bloomberg tickers per ETF | `config/etfs.yaml` | OPEN |
| Futures proxies for S&P 500 Top 50 and FANG+ | M3 fair value | OPEN (ES used as proxy per SPEC §6.2) |
| NSE quote JSON shape (depth, total buy/sell) | iNAV snapshots | OPEN — reader written against assumed shape (D17) |
| AMFI NAV rows on US holidays per AMC | NAV alignment | OPEN — Mirae has none, Motilal carries forward (D22) |
| NIFTYBEES band ratio 1.105 on 2024-03-01 | R7/R10 | OPEN (D22) |
| Muhurat session times (calendar uses 09:15–15:30) | calendars, nav_known on those days | OPEN (D24) |
| AMFI NAV missing on Muhurat days the exchange used (e.g. 1 Nov 2024) | nav_t2 band base | OPEN (D24) |
