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

---

## Open VERIFY items (running list)

| Item | Where it matters | Status |
|---|---|---|
| iNAV response shape; is depth exposed | M2 | OPEN |
| AMFI history filter parameters and window limit | M1 backfill | OPEN |
| Earliest UDiFF and security-master dates | M1 backfill | OPEN |
| Band rounding rule on more days | M3 bands | PRELIMINARY |
| `IssdCptl` update lag vs AMC figures | M5 valve status | OPEN |
| NAV timing for HK ETFs and before Jul 2023 | M3 fair value | OPEN |
| FX rate and time used in each AMC's NAV | M3 fair value | OPEN |
| Free futures/FX source reliability | M2/M3 | OPEN |
| NSE circular 122/2026 full text | `config/rules.yaml` | OPEN (secondary summaries only) |
| 2027 NSE holiday list | calendars | OPEN (not yet published) |
| Bloomberg tickers per ETF | `config/etfs.yaml` | OPEN |
| Futures proxies for S&P 500 Top 50 and FANG+ | M3 fair value | OPEN (ES used as proxy per SPEC §6.2) |
