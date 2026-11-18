# Hand-filled data

Some facts cannot be downloaded: dated rule changes, feeder-fund holdings, monthly AUM, Google Trends. They are typed (or downloaded by hand) into files, and `uv run etfp build` turns them into clean tables. This page says what each file looks like.

General rules, true for every file below:

- A missing file is not an error. The table is simply empty and the build logs an info line.
- A bad value stops the build with a message naming the file and the line (header = line 1). Nothing is skipped or corrected silently.
- Dates are `YYYY-MM-DD`. Times are IST unless a UTC offset is written (`2026-09-25T16:10` is IST; `2026-09-25T10:40+00:00` is the same moment).
- Money is in rupees, never lakhs or crores. Units are plain counts. A number with thousands commas must be in double quotes (`"1,200,000"`).
- ETF symbols are NSE trading symbols (`MONQ50`), checked against NSE's ETF list and `config/etfs.yaml`.
- Column order does not matter; an unknown column name is an error (a typo would otherwise drop a column silently).
- Each clean row keeps `source_file = data/manual/<file>.csv:<line>`, so every number traces to a typed row.

## `data/manual/events.csv` -> table `events` (R5, R16)

Dated rule changes, caps, cautions and notices. The research side supplies the full list.

| column | required | meaning |
|---|---|---|
| `event_id` | no | stable id. Blank: the loader makes `<date>_<category>_<8-char hash>` from the content (editing the description changes it, so give ids to rows you will edit) |
| `date` | yes | the day the event became public (IST) |
| `ts_ist` | no | publication time, IST. Its IST date must equal `date` |
| `type` | yes | `rule`, `limit`, `suspension`, `warning`, `other` |
| `category` | yes | `rule`, `cap`, `caution`, `amc_notice`, `fund_status`, `media` |
| `direction` | no | `premium_reducing`, `premium_preserving`, `neutral`. Set before looking at prices. Blank = not classified yet (kept as empty; the build log counts them) |
| `symbols` | no | affected ETFs separated by `;`. Blank = not recorded, or market-wide |
| `first_tradable_session` | no | first NSE session that could react. Blank = computed (below). If typed it must be an NSE trading day and not before `date` |
| `description` | yes | what happened |
| `url`, `source_primary` | no | link, or raw-file path (`data/raw/rules/...`) of the original document |
| `verified` | no | `true` only once the primary document is saved and checked. Default false |

`verified = true` needs `source_primary`: either an `http(s)` link, or a path to a file that exists under the project root.

**First tradable session.** A circular published after the market closed cannot move prices until the next session. The loader uses NSE's calendar (`etfp.calendars`):

- time known: before 15:30 IST on a trading day -> that day; at or after 15:30, or on a weekend or holiday -> the next trading day;
- time unknown: assumed to be public during the day, so the same day if it is a trading day, else the next one. If the real time was after the close, fill `ts_ist` or `first_tradable_session`;
- events before 2021-01-01 (the calendar's start) get no computed value.

`data/manual/events.csv` is seeded with six rows whose facts are written in `docs/DECISIONS.md` or `docs/REQUESTS.md`: the SEBI circular of 15 Jun 2026, NSE circular 122/2026 of 1 Sep 2026, the start of the `prev_close` regime on 2026-09-07, BSE's caution of 2026-09-25 (after the close, so first tradable 2026-09-28), and the Motilal Oswal Nasdaq 100 FoF SIP stop (2024-12-10) and addendum (2024-12-24). All are `verified = false`, with `direction` blank and no links, because the repo does not hold the primary documents.

## `data/manual/fof_monthly.csv` -> table `fof_monthly` (R4, R14)

One row per feeder fund (fund of funds) per month end. Feeders in R4: Motilal Oswal Nasdaq 100 FoF (holds MON100); Mirae Asset NYSE FANG+ ETF FoF (MAFANG), S&P 500 Top 50 ETF FoF (MASPTOP50), Hang Seng TECH ETF FoF (MAHKTECH). The list was confirmed from AMFI's own NAV file on 14 Nov 2026; every plan (Regular and Direct) has its own scheme code. They are in `config/feeders.yaml`, and their daily NAVs go into the clean table `feeder_nav_daily` (`scheme_code`, `fund_name`, `etf_symbol`, `nav_date`, `nav`, `source_file`) from the same AMFI files as the ETF NAVs (AMFI history beats the daily file; "N.A." is skipped, never zero; a day with no AMFI row has no row).

| column | required | meaning |
|---|---|---|
| `amfi_scheme_code` | yes | the feeder's AMFI scheme code (whole number) |
| `month_end` | yes | last day of the month |
| `fund_name` | yes | feeder name |
| `etf_symbol` | yes | the one ETF it holds |
| `aum` | no | feeder AUM, rupees |
| `etf_units_held` | no | ETF units held, from the monthly portfolio (not negative) |
| `fof_net_flow` | no | net inflow in rupees (AMFI monthly); negative for net outflow |
| `subscription_status` | no | `open`, `lump_sum_shut`, `sip_shut`, `shut` |
| `notice_date` | no | date of the notice that set the status |

Checks: one row per (scheme code, month end); one scheme code never holds two different ETFs.

**Automatic rows (D46).** You no longer have to type the monthly holdings. `uv run etfp collect amc-portfolio` saves the fund houses' own monthly portfolio Excel files (Motilal Oswal, Mirae Asset, Nippon India), and at build time every month without a typed row is filled from them: `etf_units_held` = the quantity of the ETF in the feeder's portfolio, `aum` = the feeder's total net assets in rupees, `source_file` = `<raw file>#<sheet>`; `fof_net_flow`, `subscription_status` and `notice_date` stay blank. The Regular and Direct plans share one portfolio, so the automatic row is written once, on the Regular plan's scheme code (a typed Direct-plan row with units would be counted twice by `feeder_units_daily`). A typed row always wins; if it differs from the AMC file by more than 1% the build logs a warning naming both. Numbers are as published (not split-adjusted).

`etfp.clean.manual_fof.feeder_units_daily(fof_monthly, dates, lag_days=0)` gives, per ETF and date, the sum over feeders of the latest disclosed `etf_units_held` (carried forward; a blank month is skipped so the previous figure continues). Columns: `symbol`, `date`, `feeder_units` (NaN, not 0, until some feeder has reported), `feeders_counted`, `oldest_month_end` (how stale the sum is). Monthly portfolios are published some days after month end; with `lag_days=0` a month-end figure counts from the month-end date itself, which uses information the market did not yet have. Use `lag_days=10` for a series without look-ahead.

## `data/manual/units_monthly.csv` -> table `units_monthly` (R11)

Month-end units outstanding before NSE's daily file exists. `units = aum / nav` (an estimate, so not a whole number).

| column | required | meaning |
|---|---|---|
| `symbol` | yes | NSE symbol |
| `month_end` | yes | last day of the month |
| `aum` | no | assets under management, rupees (not crores) |
| `nav` | no | NAV per unit used, rupees |
| `units` | no | typed units; blank = `aum / nav` |
| `source` | yes | where the figures came from, e.g. `amfi 2022-01`, `factsheet` |
| `nav_date` | no | date (YYYY-MM-DD) the NAV in `nav` was struck on; leave blank if unknown or if you typed `units` directly. Old files without this column still load (D51) |

Give `units`, or both `aum` and `nav`. If all three are given they must agree within 1% (a common mistake is AUM in crores).

**Automatic rows (D46).** Months without a typed row are filled from the ETF's own monthly portfolio file: `aum` = total net assets (GRAND TOTAL line, rupees), `nav` = the last AMFI NAV on or before the month end (`nav_daily`; at most 5 days earlier, else no row), `units` = `aum / nav`, `source` = `amc_portfolio_<amc> AUM / AMFI NAV of <date>`, `source_file` = `<raw file>#<sheet>`, `nav_date` = the date of that AMFI NAV (explicit column; `valve_monthly` uses it for `nav_lag_days`, D51). A typed row wins; a difference above 1% is logged. Check against NSE's own units: `uv run etfp amc-portfolio crosscheck`.

## `data/raw/trends/<pull date>/*.csv` -> table `trends_daily` (R15)

Google Trends daily search interest for India. Trends gives daily numbers only for windows of about 90 days and rescales each window to 0-100, so windows are pulled so that they overlap (90 days, shifted by 60) and chained.

How to pull (v1, by hand): trends.google.com, region India, a custom date range of at most 90 days, one search term, "Download CSV". Save the file **unchanged** as `data/raw/trends/<today>/<anything>.csv`. Raw files are never edited. Terms from R15: `MONQ50`, `Nasdaq Q50 ETF`, `MON100`, `Motilal Oswal Nasdaq 100 ETF`, `MAFANG`, `Mirae FANG ETF`, `international ETF premium`.

A file looks like this (a header block, a blank line, then the data):

```
Category: All categories

Day,MONQ50: (India)
2026-08-01,12
2026-08-02,<1
```

`<1` is stored as 0.5. A file may hold several terms side by side; each is chained on its own.

How the chain is built, per term:

1. Windows are taken in order of start date. The earliest sets the starting scale.
2. Each next window is multiplied by `factor = (sum of the chain on the shared days) / (sum of the window on the shared days)`, then adds only the days the chain does not have yet. A day already in the chain keeps its earlier value.
3. Finally the whole series is rescaled so its maximum is 100.

Columns: `term`, `geo`, `date`, `value` (chained, maximum 100), `window_value` (the number printed in the file that supplied the day), `n_windows` (windows containing the day), `source_file` (that raw file). Key: (`term`, `geo`, `date`).

Limits: values compare days of one term with each other, not different terms. Pull order and pull date do not change the result, but a window pulled later can differ slightly from one pulled now (Trends samples its data), so keep every raw file. The build stops with an error naming the file if a window shares no day with the series before it (pull a window that overlaps), if the shared days add up to zero, or if the file is weekly rather than daily (the window was longer than about 270 days).

### From search interest to `etf_day.attention`

`config/trends_terms.yaml` says which search term belongs to which ETF (MONQ50: "MONQ50" and "Nasdaq Q50 ETF"; MON100: "MON100" and "Motilal Oswal Nasdaq 100 ETF"; MAFANG: "MAFANG" and "Mirae FANG ETF"; MASPTOP50 and MAHKTECH: none yet; "international ETF premium" is about the whole group and stays unassigned). `attention` for an ETF and a day is the plain average of its terms on that calendar day, **only if every one of its terms has a number that day**; otherwise it is empty. Reason: each term is scaled to its own maximum, so averaging "whichever terms exist" would make the series jump on the day a term starts or stops. The value is the Trends number for the same day, which includes searches after the 15:30 close: lag it in analysis if you need what was known at the close. To add a term, add a line to the yaml and pull its windows.
