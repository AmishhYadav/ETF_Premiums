# Paper blueprint

12 Nov 2026. Working version of "ETF Premiums: Gap Audit and Paper Blueprint" (https://claude.ai/artifact/BYbVcvuiXv7e7fsvAobrZJ), which has the sources. This file is what the analysis and the pipeline read. Nothing here uses data the project does not yet have.

## Status

- Second draft written 12 Nov 2026: "Anchored to What? Price Limits, Rationed Creation and the Premiums on India's International ETFs". PDF at `paper/ETF_Premiums_Paper_draft2.pdf` (35 pages, single column); LaTeX source, figures, spot checks and Monte Carlo at `paper/draft2/` (see `README.md` there).
- It is a pre-results paper. Design, measures and tests are fixed; the only numbers from primary files are spot checks on the fixtures for 1 Jul 2024, 2 Jan 2025, 4 Sep 2026 and 9 Nov 2026. Press figures fill the dates between and are labelled.
- The eleven changes listed below are all in. Beyond them the draft adds a framework section (three propositions), a Monte Carlo test of the P2 estimator, a section of file-based evidence, and appendices (protocol timeline, Monte Carlo design, variables, chronology). 123 references.
- Not done: no Word or IEEE build of the second draft (Amish decides whether the college needs one); no author email on the title page (Amish adds it); nothing posted or registered anywhere.

## The claim

When arbitrage is blocked, the reference price of a daily price limit decides whether mispricing is bounded. Re-anchoring India's ETF band from lagged NAV to the previous close (7 Sep 2026) both revealed demand the old band hid and induced new demand. The paper measures the split.

Claims the paper can defend: first study of a change in a price limit's reference price in the same securities; first estimate of the premium a NAV-anchored limit censored and of the revealed/induced split; first use of exchange unit counts to date quota-rationed creations; a registered April 2027 test.

Claims to avoid: "first on creation halts" (Johnson 2016 exists), "first on capital-control premiums" (closed-end country fund literature), "buyers were irrational", anything general about ETFs.

## Framework (draft 2, Section 3)

`V` value, `P` price, `pi` = ln(P/V), band half-width `b`, lag `l`.

- Fence (value anchor, base = V(t-l)): `pi(t) <= ln(1+b) - ln(V(t)/V(t-l))` whatever demand is. The observed premium is min(latent, ceiling), and the ceiling moves daily for reasons unrelated to demand.
- Speed limit (price anchor, base = P(t-1)): only the k-day change in `pi` is bounded; the level is not. A latent premium is reached in at most ceil((pi* - pi0)/ln(1+b)) sessions.
- Cliff (price anchor back to value anchor on day T, eve premium `Pi` over the anchoring NAV): one-day fall of at least 1 - (1+b)/(1+Pi). At 86%: 36% (41% at the first trade, because the band starts the day at 10%). Getting inside the band beforehand takes ceil(ln((1+Pi)/(1+b)) / ln(1/(1-b))) lower-limit sessions.
- Revealed vs induced: post-change premium = forecast of the latent premium from the pre-change law of motion (revealed) + remainder (induced, plus news).

## Two papers

- Working paper (draft 2 with results added): institutions, framework, corrected premium measure, P1 and P2, registered April test. This is the SSRN version. Target for the results version: mid-December 2026; SSRN and registration before 31 Dec 2026.
- Length check, 12 Nov: draft 2 runs to about 11,000 words of text. Finance Research Letters takes about 2,500 words and Economics Letters about 2,000. A letter is therefore a cut of the working paper made after results exist (one claim: the anchor; P1, P2 and the registered test; two tables, two figures), not this draft. Amish decides whether to cut a letter or send the full paper to a field journal.
- Paper B (full): A plus P3, S1, S2, China comparison, April outcome. After 1 Apr 2027. Targets: Pacific-Basin Finance Journal, International Review of Financial Analysis, Emerging Markets Review.
- Layout: single-column working paper in LaTeX. The IEEE two-column Word build of the first draft is kept in `paper/source/` and is superseded; rebuild in Word or IEEE only if the college requires it (Amish decides).

## Hypotheses

| ID | Statement | Test | Refuted if |
|---|---|---|---|
| P1 Rule effect | From 7 Sep 2026 `pi` of rationed funds rises against own history and against domestic equity ETFs; the break is on the rule date | 7 Sep return of each rationed fund ranked within its own daily returns since 2023 (US holiday: NAV fixed). Break-date search per fund. Event-study statistic recomputed for every date since Jan 2023. Conley-Taber intervals with domestic ETFs as controls | Break more than 2 sessions from 7 Sep in half or more of US-linked rationed funds; or domestic ETFs break by a similar amount; or true date outside top 1% of placebo dates |
| P2 Hidden premium | Before the change `pi` was censored at `cap_pi`; after it, realised `pi` exceeds a forecast of the latent premium | Censored autoregression of `pi` to 4 Sep with censoring point `cap_pi` and the latent (not observed) premium as the lag; likelihood by a forward filter on a grid over capped runs. Regressors: 5-day underlying return, Nifty return, rupee change, attention. Forecast from 7 Sep. Induced share = 1 - forecast / realised, averaged over sessions 16-40 after the change (session 40 = 4 Nov 2026). Critical values from a parametric bootstrap of the whole procedure under the estimated null, with each fund's actual ceilings | Two-sided. Realised inside the 90% forecast band: rule revealed demand. Above: rule induced demand |
| P3 Supply and float | `pi` falls after a creation; across funds it is lower where `float_net` is larger | Event study of `pi` over sessions -5 to +10 around each `creation_event`, scaled by % change in units. Rank correlation of `pi` and `float_net` on the first session of each month after the change | Mean change after creations zero or positive; or rank correlation among US-linked funds positive |
| S1 Limit-hit dynamics | After an upper-limit day: positive next-day return, more trades, smaller trades; premium changes follow own lagged return after controlling for turnover | Regression of change in `pi` on `turnover`, `deliv_share`, `ret_own_1`, `ret_own_5`, `attention`, with day-after-limit dummies; Driscoll-Kraay errors | No continuation, or lagged-return coefficient at or below zero |
| S2 Feeders | Feeder funds shed ETF units when the premium is high | Monthly change in `feeder_units` against lagged `pi`. Descriptive | Feeder units rise in high-premium months |
| R1 April | If a NAV anchor is applied on 1 Apr 2027 the premium collapses before the date in a run, not on the date in a jump | Registration text below | Per branch below |

Inference: lead with the holiday day, placebo-date rank and fund-by-fund break dates. No clustered panel standard errors (4-6 treated funds, one date). The float effect is a figure and a rank correlation, not a regression on six points.

P2 variants reported beside the main model: lag = observed premium (lower bound on the hidden premium); fixed ceiling ln 1.2 on the premium over the band's base (check); with and without attention.

## What the Monte Carlo says about P2 (`paper/draft2/analysis/`, 240 replications x 3 designs)

| | 35% capped | 60% capped | 80% capped |
|---|---|---|---|
| Hidden premium, mean error (points): ignore censoring / fixed ceiling / moving ceiling | -9.8 / 2.7 / 1.3 | -12.2 / 3.0 / 1.3 | -17.8 / 1.6 / -0.7 |
| RMSE (points): fixed / moving | 9.2 / 7.6 | 11.8 / 11.1 | 13.7 / 13.9 |
| False "induced" when none, nominal 5%: ignore / moving | 26.2 / 5.8 | 66.7 / 10.4 | 88.8 / 15.4 |
| Power against 30 log points, moving | 89.6 | 91.7 | 95.0 |

Reading: ignoring censoring mistakes hidden demand for induced demand; both censored estimators are nearly unbiased; the moving ceiling's gain over the fixed one is modest; the test over-rejects when most days are capped, hence the bootstrap. Report each fund's capped share and simulated size beside its test.

## Measures (columns the analysis expects in exports/research/etf_day.parquet)

| Variable | Definition | Source |
|---|---|---|
| `close`, `last`, `high`, `low`, `avg_price` | NSE daily prices; close = last half-hour average | D1, D3 |
| `nav_same_date` | NAV dated t | D9 |
| `nav_known`, `nav_known_date` | Last NAV whose underlying market had closed before 15:30 IST on t | R3 |
| `fv` | `nav_known` moved to 15:30 IST by index future and rupee rate (beta 1) | D10 |
| `fv_beta` | Same with the future's return scaled by the fund's estimated beta | R12 |
| `pi` | ln(`close` / `fv`). Main dependent variable | derived |
| `prem_raw` | `close` / `nav_same_date` - 1. Press comparison only | derived |
| `prem_known` | `close` / `nav_known` - 1 | R3 |
| `band_low`, `band_high` | Official band for session t (security-master file of the previous session) | R2 |
| `cap_pi` | ln(`band_high` / `fv`) | R10 |
| `hit_up`, `hit_up_max`, `last_at_up` (and lower-limit twins) | High at initial upper limit; at maximum limit; last trade at limit | D6, R10 |
| `regime` | Base that reproduces the band midpoint: nav_t2, prev_close, nav_t1 | R10 |
| `units` | Units outstanding: daily `IssdCptl`; monthly AUM / NAV before the first file | R1, R11 |
| `creation_event` | Signed change in units / creation-unit size | R1, R6 |
| `rationed` | 1 when `pi` > 0.02 and no creation in the previous 20 sessions (threshold varied 0.01-0.05) | derived |
| `feeder_units` | ETF units held by feeder funds, monthly, carried forward | R14 |
| `float_net` | (`units` - `feeder_units`) x `nav_known` | derived |
| `turnover` | (NSE + BSE units traded) / `units` | R9 |
| `deliv_share`, `trades`, `avg_trade` | Delivered share; number of trades; value per trade | D1 |
| `ret_own_1`, `ret_own_5` | Fund's log price return over previous 1 and 5 sessions | derived |
| `attention` | Google Trends daily index, fixed terms | R15 |
| `x_april` | ln(`close` / (1.2 x `nav_known`)) | derived |
| `excess_rupees` | Units traded x max(0, `avg_price` - 1.2 x NAV(t-2)) | derived |

Old-regime ceiling on the quoted premium: 1.2 x NAV(t-2) / NAV(t) - 1 (16.5% after a 3% two-day NAV rise, 23.7% after a 3% fall).

## Exhibits

In draft 2 (pre-results):

| Exhibit | Shows | Basis |
|---|---|---|
| Table 1 | Three price-band regimes | SEBI circular paras 2, 4.1, 4.2, 4.4, 5.1; NSE circular |
| Table 2 | Hypotheses, tests, refutation conditions | this file |
| Table 3 | Six funds: house, index, listing, creation unit, units, value at NAV | files, 9 Nov |
| Table 4 | Monte Carlo: bias, size, power, induced share | simulation |
| Table 5 | Last session of the NAV-anchored band, 4 Sep 2026: five of six at the limit | files |
| Table 6 | MONQ50 close, NAV, quoted premium on 14 dates | files for the two end dates; press between |
| Table 7 | Units outstanding on five dates, change in creation units | files |
| Table 8 | Six funds on 9 Nov 2026: two premiums, value above NAV, turnover, delivered share, trade size | files |
| Tables 9-11 | Protocol timeline; variable definitions; chronology | appendices |
| Figure 1 | What is known when (Indian and US sessions, NAV publication) | schematic |
| Figure 2 | The cliff: forced one-day fall against eve premium, six funds placed | Proposition 3; files |
| Figure 3 | Fence and speed limit, simulated path with and without induced demand | simulation |
| Figure 4 | Monte Carlo: error in the hidden premium by estimator | simulation |
| Figure 5 | MONQ50 price, NAV and old-rule ceiling, 4 Sep to 9 Nov | Table 6 |
| Figure 6 | Premium against float, six funds, 9 Nov | Table 8 |

Planned for the results version (replace Section 8 and the press figures when the export exists):

| Exhibit | Shows | Feeds on |
|---|---|---|
| Table R1 | Six funds with float gross and net, feeder share, lending status | `units`, `feeder_units`, lending list |
| Table R2 | Cap history and every dated creation with the premium that day | `creation_event`, `pi`, events |
| Table R3 | Summary by regime, rationed vs domestic ETFs: premium, share of days at upper limit, turnover, delivery share | `pi`, `hit_up`, `turnover`, `deliv_share` |
| Table R4 | P1: 7 Sep return and rank, break dates, placebo rank, Conley-Taber interval | `pi`, returns |
| Table R5 | P2: censored-model estimates, hidden premium on capped days, forecast vs realised, induced share, bootstrap size | `pi`, `cap_pi`, regressors |
| Table R6 | P3: premium change around creations, elasticity, quota that would have closed the premium | `creation_event`, `units`, `pi` |
| Table R7 | S1: day after a limit hit; premium-change regression | `hit_up`, `trades`, `avg_trade`, `ret_own_*`, `attention` |
| Table R8 | Money paid above the old-rule ceiling, by fund and week | `excess_rupees` |
| Table R9 (B) | April outcome vs registered predictions | `x_april`, events |
| Figure R1 | `pi` of six funds from Jan 2024, log scale, regime dates | `pi` |
| Figure R2 | MONQ50 price vs official band vs old-rule ceiling, Aug-Nov 2026, daily. Replaces Figure 5 | prices, `band_high`, NAV(t-2) |
| Figure R3 | Units (steps) and premium, MONQ50 and MON100, 2024-26 | `units`, `pi` |
| Figure R4 | Hidden vs observed premium before; forecast vs realised after. Replaces Figure 3 | Table R5 model |
| Figure R5 | Premium vs net float, six funds, monthly. Replaces Figure 6 | `pi`, `float_net` |
| Figure R6 | 21 Sep 2026 minute by minute: price, band, volume | Bloomberg minute bars |
| Figure R7 (B) | Excess over April cap, Jan-Apr 2027 | `x_april` |

## Abstract

Draft 2 uses a pre-results abstract (see the PDF). Skeleton for the results version, blanks A-G from the export:

India caps what its mutual funds may invest abroad, and the cap has rationed unit creation in its six listed international ETFs since 2022. Until 4 September 2026 a daily price band anchored to lagged NAV capped their premiums; on [A]% of fund-days the price sat at that ceiling. On 7 September the band was re-anchored to the previous close. The fair-value premium of the rationed funds rose by [B] log points within [C] sessions, with no change in domestic ETFs under the same rule. A censored model fitted before the change puts the hidden premium at [D]%; the realised premium was [E] times that. Dated creations in 2024-26 imply that a [F]% increase in units lowers the premium by [G] points. Tests for the planned return to a NAV anchor on 1 April 2027 were registered on [date].

## Registration text for April 2027 (file on OSF or AsPredicted before 31 Dec 2026)

Same text as Section 9 of draft 2.

Definitions. Excess x = ln(close / (1.2 x last NAV published before that close)). Exposed fund = one of the six with x > 0 at the close of the last session of December 2026. Switch date T = first session on which NSE's official band for a fund is centred on a NAV of any lag (from the security master). Event = dated publication by SEBI, RBI, NSE, BSE, AMFI or the three fund houses about the band's base, the overseas limits or creation in these funds; logged with its time and classed before prices are examined as premium-reducing (anchor confirmed or advanced, limits raised, creation resumed) or premium-preserving (delay, exemption).

Branches. A: a NAV anchor is applied to these funds on or before 30 Apr 2027. B: a delay or exemption is announced before T. C: limits are raised, or an exposed fund's units rise by at least one creation unit, before T.

Predictions.
1. Run, not cliff (A): for every exposed fund x <= 0 at the close of T-1. Refuted for a fund if x > 0 then (the fall arrives as a jump on T).
2. Path (A): between 1 Jan and T-1 each exposed fund has at least one session whose low equals the official lower limit, and median x over the last 10 sessions before T is below median x over sessions 11-40 before T.
3. News (all): across events and exposed funds, x falls from the close before an event's first tradable session to the close two sessions later for premium-reducing events and rises for premium-preserving ones. Exact sign test, pooled.
4. Trading (A, B): units traded / units outstanding in the 20 sessions before T (or before the announcement in B) exceed the ratio in sessions 21-60 before.
5. Supply (C): after units rise, x falls, by more where the percentage rise in units is larger.

Known when written: press premiums 4 Sep-4 Nov 2026; exchange and AMFI files for 4 Sep and 9 Nov 2026; units outstanding on five dates; NAV timing check for one fund; no price after 9 Nov 2026; no pipeline output. Update this paragraph on the filing date. All five predictions are reported whatever the result.

Why "cliff": on T the price must jump into the NAV-anchored band. At an 86% premium the minimum one-day loss is 1 - 1.2/1.863 = 36%. Before T a price can fall at most 20% a day.

## The eleven changes: done in draft 2

| Change | Where in draft 2 |
|---|---|
| Abstract: "capped their premiums"; moving ceiling; revealed/induced question | Abstract |
| Contribution paragraph replaced: four claims, and what is not claimed | Section 1 |
| The US$7 billion pool binds; "remains to be confirmed" deleted | Section 2.2 |
| Why T-2; China halts at high premiums, India has no such tool (Korea added) | Section 2.3 |
| Feeders rewritten as a supply channel | Section 2.5 |
| Literature: Johnson (2016), country-fund barriers, ChiNext width studies, censored-limit econometrics, Mei-Scheinkman-Xiong (2009), Reddy and Dhabolkar's sample | Section 4 |
| H1-H7 -> P1-P3, S1, S2, R1 | Section 5, Table 2 |
| Fixed 1.2 bound -> `cap_pi` | Section 6.4, eq. (9) |
| Forecast and induced share; panel inference demoted; float as figure plus creation-event elasticity | Sections 7.2, 7.4, 7.7 |
| Cliff, three branches, five predictions | Section 3 (Proposition 3), Section 9 |
| Short-selling paragraph; cap caveat removed | Section 10 |

## Pipeline requests (docs/REQUESTS.md)

R9 BSE daily data and unit turnover. R10 `cap_pi`, limit flags, inferred regime. R11 monthly units before the first security-master file. R12 `fv_beta`. R13 limit-day diagnostics. R14 feeder holdings and `float_net`. R15 Google Trends windows. R16 event timestamps and direction. R17 China comparison. R18 research export. R19 (12 Nov) regression targets from the paper's spot checks, and 14 Sep 2026 as a holiday. R9-R12 are needed for the results version. R7 note updated: the final circular kept no breadth test for widening.

## Still open, with owner and fallback

| Question | Owner | Fallback wording |
|---|---|---|
| Word or IEEE version of draft 2 for the college | Amish | LaTeX PDF stands |
| Author email on the title page; any co-author line | Amish | Title page as is |
| Letter (about 2,500 words) or full paper as the journal submission | Amish, after results | Post the working paper on SSRN only |
| Registration of Section 9 (OSF or AsPredicted) before 31 Dec 2026 | Amish files; text is ready | Section 9 says "will be filed" |
| Bloomberg minute-bar retention; export Sep-Nov now | Amish | Figure R6 and R13 dropped for September |
| Faculty co-author | Amish, by 30 Nov | Working paper stands; "who buys" dropped |
| Manual SSRN / Scholar / CNKI check | Amish, 30 min | "to our knowledge" |
| Could the six be borrowed and shorted | Pipeline R8 | "overnight short positions needed a borrow we could not observe" |
| When the T-2 band began | Pipeline R10 | Inferred from prices |
| iNAV method at Mirae and Nippon | Pipeline, first live week | One-day check reported as such |
| Creation-unit sizes of MASPTOP50 and MAFANG | Pipeline R6 | "not yet established" in Tables 3 and 7 |
| How a fund house allocates freed room | Not public | "allocation is not disclosed" |
| Base for overseas ETFs in April 2027 | Time | Three registered branches |

Closed 12 Nov: full text of the SEBI circular of 15 Jun 2026 (paragraph numbers now cited) and the NSE circular number.
