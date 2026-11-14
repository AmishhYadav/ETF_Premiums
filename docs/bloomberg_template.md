# Bloomberg CSV template

How to hand Bloomberg data to the pipeline. The importer is `src/etfp/collect/bloomberg.py`;
it only reads files and never changes them.

## Where files go

```
data/raw/bloomberg/<export date YYYY-MM-DD>/<any name>.csv
```

`data/raw/` is git-ignored. Bloomberg's licence forbids redistribution, so **never commit a real
export** and never copy numbers from one into docs, tests or issues. Re-exporting an overlapping
range is fine: save it in a new date folder, never overwrite an old file. If the new file gives a
different value for a row that already exists, the import stops and names both files (you decide
which is right).

## Format: one row per observation ("long" format)

Header row, exactly these columns (lower case, any order). `note` is optional.

| Column | Meaning | Rule |
|---|---|---|
| `date` | Calendar date of the observation **in the `timezone` of that row** | `YYYY-MM-DD` |
| `time` | Clock time of the observation in that time zone | `HH:MM` or `HH:MM:SS`, 24-hour |
| `timezone` | Time zone of `date` and `time` | one of `Asia/Kolkata`, `America/New_York`, `Asia/Hong_Kong`, `UTC` |
| `instrument` | Project code (table below) | letters, digits, space, `. _ / -` |
| `field` | Bloomberg field | UPPER_SNAKE_CASE, for example `PX_LAST` |
| `value` | The number | plain number: no thousands commas, no `#N/A N/A`, no blanks |
| `note` | Free text | optional |

Fields that must be greater than zero: `PX_LAST, PX_OPEN, PX_HIGH, PX_LOW, PX_SETTLE, PX_BID,
PX_ASK, LAST_PRICE, FUND_NET_ASSET_VAL, EQY_SH_OUT`.

Example (all numbers made up):

```
date,time,timezone,instrument,field,value,note
2026-09-04,16:00,America/New_York,NQ1,PX_LAST,20000.00,US close
2026-09-07,15:30,Asia/Kolkata,NQ1,PX_LAST,20100.00,
2026-09-07,15:30,Asia/Kolkata,USDINR,PX_LAST,84.21,
```

## Instruments the fair value needs

Fair value (`FV = NAV_a x F_t/F_a x S_t/S_a`, DECISIONS D10) needs four numbers per Indian
trading day *t*. In plain words: the index future now (15:30 IST) and at the US close that the NAV
was struck on, and the rupee rate now and at the NAV's time.

| Instrument | What | Used for | Rows needed |
|---|---|---|---|
| `NQ1` | E-mini Nasdaq-100 front-month future | MONQ50, MON100 | `PX_LAST` at **15:30 Asia/Kolkata** on every Indian trading day; `PX_LAST` at the **US cash close (16:00 America/New_York)** on every US trading day |
| `ES1` | E-mini S&P 500 front-month future (proxy for S&P Top 50 and FANG+, SPEC 6.2) | MASPTOP50, MAFANG | same as `NQ1` |
| `USDINR` | Rupees per US dollar | US ETFs | `PX_LAST` at 15:30 Asia/Kolkata on every Indian trading day; one rate per US trading day at the time the fund house uses for its NAV (record that time in `time`; unknown yet, DECISIONS D10) |
| `HKDINR` | Rupees per Hong Kong dollar | MAHKTECH, HNGSNGBEES | `PX_LAST` at 15:30 Asia/Kolkata on every Indian trading day; one rate per Hong Kong trading day at NAV time |
| `NDX`, `SPX`, `HSI`, `HSTECH` | Underlying index closes | checks, beta (R12) | `PX_LAST` at each market's close |
| anything else | For example China ETF codes (R17) | other tables | any `PX_LAST`, `FUND_NET_ASSET_VAL`, `EQY_SH_OUT` |

Which tickers Bloomberg actually uses (`NQ1 Index`, `ES1 Index`, `USDINR Curncy`, ...) goes in the
`note` column or `config/etfs.yaml`; the `instrument` code here is the project's own name.

VERIFY before relying on the numbers:

* **Daily `PX_LAST` of a future is its settlement price**, not the price at the US cash close. For
  `F[close of a]` export the **16:00 New York bar** (intraday export) so the timestamp is true. If
  only the daily value is available, put the settlement time in `time` and note it; the
  fair-value inputs then carry a known timing error.
* The time and rate the fund houses use for NAV (SPEC 13 question 4).
* Early-close days (the US half days) have a 13:00 close, not 16:00; use the real close time.

## ETF minute bars (request R13)

The limit-day diagnostics (`etfp.features.limit_days`) use 1-minute bars of the ETFs themselves
when they exist, because the 15-minute recorder snapshots only see the price every 15 minutes
(and for Sep-Oct 2026, before the recorder ran, there is nothing else). Same long format, no new
columns. The convention:

| Column | Rule for ETF minute bars |
|---|---|
| `instrument` | the NSE symbol plus `.1M`, for example `MONQ50.1M`, `MON100.1M`. The plain symbol (`MONQ50`) stays free for daily series, so a daily row is never mistaken for a bar |
| `field` | `PX_OPEN`, `PX_HIGH`, `PX_LOW`, `PX_LAST` (the bar's closing price) and `VOLUME` (units traded in the bar). `PX_LAST` is required for a bar to be used; `PX_HIGH` / `PX_LOW` are optional but make "touched the limit" exact even when the bar closed away from it |
| `timezone` | `Asia/Kolkata` |
| `time` | the START of the bar (Bloomberg's own bar label): the bar stamped `15:29` covers 15:29 to 15:30. Regular session bars run `09:15` to `15:29` |
| `date` | the Indian trading day |

Bloomberg leaves out minutes with no trade; the pipeline reads a missing minute as "no trade, the
last price stood". Prices are on the scale quoted on the day (not adjusted for later splits).
Example (made-up numbers):

```
date,time,timezone,instrument,field,value,note
2026-11-10,10:15,Asia/Kolkata,MONQ50.1M,PX_LAST,252.63,
2026-11-10,10:15,Asia/Kolkata,MONQ50.1M,PX_HIGH,252.63,
2026-11-10,10:15,Asia/Kolkata,MONQ50.1M,VOLUME,1200,
```

Pull them with `=BDIB("MONQ50 IN Equity", "TRADE", start, end, "BarSize=1")` (or the intraday
tools) for the sessions of interest only (days a limit was touched, at least Sep-Oct 2026); the
volume of rows is large. Read them back with `bloomberg.etf_minute_bars(frame, "MONQ50")`.

## China-listed ETFs (request R17)

China has the same cap problem as India: ETFs that hold US shares are limited by the overseas
"QDII" quota. Their price, NAV and size, from Jan 2024, are an outside comparison for the Indian
premiums. They go into the clean table `china_etf_daily` (`src/etfp/clean/china_etf.py`). Same long
format, no new columns. The convention:

| Column | Rule for China ETF rows |
|---|---|
| `instrument` | the 6-digit exchange code, one space, `CH`: `513100 CH` (Bloomberg's own ticker is `513100 CH Equity`). Candidate codes from R17 to confirm on the terminal: 513100, 159941, 513300, 159509 (Nasdaq), 513500, 159612 (S&P 500) |
| `field` | `PX_LAST` (close, yuan), `FUND_NET_ASSET_VAL` (NAV per unit, yuan), `EQY_SH_OUT` (units outstanding). A day may have any of the three |
| `timezone` | `Asia/Shanghai` (allowed since this request) |
| `time` | `15:00` (the Chinese close). If you export only a daily value, still use `15:00` |
| `date` | the Chinese trading day |
| `value` | plain number, greater than zero. **`EQY_SH_OUT`: paste exactly what Bloomberg returns.** The importer does not rescale it. VERIFY the unit on the terminal (Bloomberg normally quotes millions of shares) and say it in `note` |

`premium` in the table is `close / nav - 1` on the same date. VERIFY: for ETFs that hold US shares,
the NAV dated T probably reflects the US close of T-1, so this premium is not lined up for that
one-day lag the way the Indian `prem_nav_known` is. If several rows exist for the same ETF, field
and day, the last one of the day is used. Other instruments in the same file are ignored by this
table. Example (made-up numbers):

```
date,time,timezone,instrument,field,value,note
2026-11-09,15:00,Asia/Shanghai,513100 CH,PX_LAST,1.200,
2026-11-09,15:00,Asia/Shanghai,513100 CH,FUND_NET_ASSET_VAL,1.000,
2026-11-09,15:00,Asia/Shanghai,513100 CH,EQY_SH_OUT,5000,millions of units (VERIFY)
```

Chinese ETFs have a 10% limit on the previous close and issuers can halt trading; a halted day has
no `PX_LAST` row, which stays empty in the table (never zero). **Licence:** the table is Bloomberg
data. It is in the research export (git-ignored) but must never go into the public open dataset.
The synthetic test files are in `tests/fixtures/bloomberg_china/`.

## How to build the file in Excel

1. In a sheet use `=BDH(...)` (daily) or the intraday tools (`=BDIB`/`=BDH` with `BarSize`) to pull
   each series. One block per instrument and field.
2. Reshape to long form: Data > Get Data > Power Query, select the date columns, Unpivot, then
   add the constant columns `time`, `timezone`, `instrument`, `field`.
3. Format `date` as text `YYYY-MM-DD` (Excel's default `04/09/2026` is rejected).
4. Save as CSV UTF-8 (a BOM at the start is accepted).

## What the importer checks

It stops with the file name and line number for: wrong or missing header, a date or time in
another format, an unknown time zone, a value that is not a plain number, a price that is zero or
negative, or the same (time, instrument, field) twice. Afterwards `check_coverage` /
`assert_coverage` compare the data with the NSE, NYSE and HKEX trading calendars and name every
trading day for which a required row is missing.
