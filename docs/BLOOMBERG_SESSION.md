# Bloomberg terminal session: checklist (December 2026)

One session, research use only. Bloomberg data never goes into git, the open dataset, the
dashboard, a cloud folder or an email. Everything below was rehearsed on made-up files (D52); the
formats are in `docs/bloomberg_template.md`. **VERIFY** = I could not confirm this without the
terminal; check it first and write what you find in `docs/DECISIONS.md`.

## Before you sit down
- Bring a USB stick. Bring this page and `docs/bloomberg_template.md`.
- On the Mac, run `uv run etfp build` once so the rows to compare against exist.
- Do the minute bars FIRST (intraday history is the part that expires). Then daily series. Then China.

## 1. Terminal settings (do these first)
- Time zone: `TZDF <GO>` or the Excel add-in setting, set to **India (Kolkata)** so intraday stamps
  are IST. **VERIFY**: the first ETF bar of a day must be `09:15`. If not, the zone is wrong; every
  bar is mis-stamped and `etfp bloomberg check` will say so.
- Intraday history depth is limited (usually about 140 trading days, **VERIFY** with `BDIB` on a
  date far back). 1 Sep 2026 is the part that matters; ask for the earliest date it allows.

## 2. Tickers (project code on the left, what to type on the right)
| Project code | Terminal ticker | Notes |
|---|---|---|
| `MONQ50.1M`, `MON100.1M`, `MASPTOP50.1M`, `MAFANG.1M`, `MAHKTECH.1M`, `HNGSNGBEES.1M` | `<SYMBOL> IS Equity` | **VERIFY** `IS` = NSE (`IN` = composite, `IB` = BSE). Use the NSE line. Old tickers: MON100 was `N100` before 16 Jun 2021 (not needed, bars start Sep 2026) |
| `NQ1` | `NQ1 Index` | E-mini Nasdaq-100, first generic. **VERIFY** roll rule is "on expiry" |
| `ES1` | `ES1 Index` | E-mini S&P 500 (a proxy for S&P Top 50 and FANG+) |
| `USDINR` | `USDINR Curncy` | |
| `HKDINR` | `HKDINR Curncy` | **VERIFY** it exists; else `USDINR` divided by `USDHKD Curncy` in Excel, note it |
| `NDX`, `SPX`, `HSI`, `HSTECH` | `NDX Index`, `SPX Index`, `HSI Index`, `HSTECH Index` | optional checks only; **VERIFY** `HSTECH` |
| `513100 CH`, `159941 CH`, `513300 CH`, `159509 CH`, `513500 CH`, `159612 CH` | `513100 CH Equity` etc. | China QDII ETFs (R17). **VERIFY** each code exists and holds Nasdaq-100 or S&P 500; drop any that do not; `CH` suffix may need to be `C1` (Shanghai) / `C2` (Shenzhen) |

Never rename the project code: the importer and build look for exactly these strings.

## 3. What to pull
**A. ETF minute bars (R13)** one CSV per ETF, six files.
`=BDIB("MONQ50 IS Equity","TRADE",start,end,"BarSize=1")`, **1 Sep 2026 to the session date**.
Keep High, Low, Close and Volume (Open is not used). Long format: `field` = `PX_HIGH`, `PX_LOW`,
`PX_LAST` (the Close), `VOLUME`; `time` = the bar's own start time (09:15 .. 15:29); `timezone`
`Asia/Kolkata`; `instrument` = `MONQ50.1M`. Excel allows 1,048,576 rows per sheet: one ETF per file.

**B. Fair-value inputs, intraday (about 140 trading days back)**, `NQ1`, `ES1`, `USDINR`, `HKDINR`.
Pull 1-minute bars (`BDIB`, for FX use the event your terminal offers: `TRADE` does not exist for
spot FX, try `MID` or `BID`, **VERIFY**, say which in `note`). Keep only these minutes and write
the **close of the bar before** with the clock time shown (a bar starts 15:29 and ends 15:30):
- every Indian trading day: **15:30 Asia/Kolkata** for all four instruments;
- every US trading day: **16:00 America/New_York** (13:00 on half days) for `NQ1`, `ES1`, `USDINR`;
- every Hong Kong trading day: **16:00 Asia/Hong_Kong** (12:00 on half days) for `HKDINR`.
Field name in every row is `PX_LAST`. The rupee rate MUST be present on US and HK days inside
09:30-17:00 of that market (the 15:30 IST row alone is not enough, the check will fail).

**C. Daily history for the beta and older days** `BDH(ticker,"PX_LAST","2021-01-01",session date)`
for `NQ1`, `ES1`, `USDINR`, `HKDINR`. A daily future value is its settlement price: label it
`16:00 America/New_York` (US days) and add `settle` in `note`. Daily `USDINR` label `17:00
America/New_York`; daily `HKDINR` label `16:00 Asia/Hong_Kong` and add `daily proxy` in `note` (the
real rate time differs; this is a known timing error). The beta needs these for the 250 NAV dates
before 7 Sep 2026 (from about Sep 2025); give the whole range from 1 Jan 2021 if it is cheap. Old
days will have no 15:30 IST row, so they stay `nav_only`, which is expected. **Leave out of C every
day that B already covers**: the same day twice (a 16:00 settle and a 16:00 bar) is a conflict, and
a 17:00 daily rupee row would override the 16:00 bar.

**D. China ETFs** `BDH(code,"PX_LAST|FUND_NET_ASSET_VAL|EQY_SH_OUT","2024-01-01",session date)`.
`instrument` `513100 CH`, `timezone` `Asia/Shanghai`, `time` `15:00`. **VERIFY** the unit of
`EQY_SH_OUT` (millions?) and write it in `note`. If Bloomberg uses another field for ETF units
(search `FLDS <GO>` "shares outstanding"), rename it to `EQY_SH_OUT` and say so in `note`.

## 4. Export to the template
Excel: one block per series, then Power Query: Unpivot, add constant columns `time`,
`timezone`, `instrument`, `field`, `note`. Header exactly `date,time,timezone,instrument,field,value,note`.
`date` as text `YYYY-MM-DD`. **Delete rows where the value is `#N/A N/A` or blank.** Round nothing.
Save as CSV UTF-8. (BQL cannot return intraday bars; use BDIB/BDH.)

Files (new folder for the session day, never overwrite an old one):
```
data/raw/bloomberg/<YYYY-MM-DD of the session>/etf_minute_MONQ50.csv   (one per ETF)
data/raw/bloomberg/<YYYY-MM-DD>/fair_value_intraday.csv
data/raw/bloomberg/<YYYY-MM-DD>/fair_value_daily.csv
data/raw/bloomberg/<YYYY-MM-DD>/china_etf.csv
```
Copy from the USB stick on the Mac. `git status` must show nothing new (the folder is ignored).

## 5. After the session (on the Mac, in this order)
```
uv run etfp bloomberg check --from 2026-07-01     # reads only; lists missing days, wrong bar times
uv run etfp build                                  # about 5 minutes; also writes the research export
uv run etfp bloomberg report                       # did Bloomberg reach the tables?
uv run etfp export dataset && uv run etfp export dashboard
grep -ril bloomberg exports/dataset exports/dashboard
```
Expected:
- `check` prints `ok`. A problem line names the instrument and the days. Use `--from` the first
  day of your 15:30 IST data; before it, missing rows are normal (those days stay `nav_only`).
  Any file error names the file and line: fix the CSV, re-save in a new date folder.
- `report`: for MONQ50, MON100, MASPTOP50, MAFANG `fv_method` has many `bloomberg` days and the beta
  line says `250 NAV dates`; MAHKTECH and HNGSNGBEES say "no futures term" (no beta by design); in
  `limit_day`, `obs_source` shows `bloomberg_1m` for Sep-Oct limit days; `china_etf_daily` has
  rows. It prints `ok`.
- Spot-check by hand: `fv / nav_known` on a recent day is within a few percent of 1 (e.g. 0.97-1.03);
  a jump of 1% right after a quarterly roll (third Friday of Mar, Jun, Sep, Dec) means the generic
  future rolled between the two rows. Report it, do not edit raw files (D43 note: Bloomberg rolls are
  not guarded).
- Export check: `exports/research/LATEST.txt` points to a folder with `china_etf_daily.parquet` and
  a manifest listing your files. The `grep` above must find only `README.md` / `manifest.json` text
  (statements that Bloomberg is excluded), no parquet or CSV data file. The dataset's `etf_day`
  has no `fv*`, `pi`, `prem_fv` columns; `limit_day` has no `bloomberg_1m` rows.
- Open `docs/DECISIONS.md` and record every VERIFY answer above (tickers, `IS` vs `IN`, intraday depth,
  FX event type, `EQY_SH_OUT` unit, roll rule). Then set the `bloomberg:` fields in `config/etfs.yaml`.
