# Operations runbook

How the pipeline runs by itself on the project Mac, how to check it, and what to do when
something goes wrong. Everything here is a command you type in Terminal from the project
folder (`cd ~/ETF_Premiums`). Nothing in this file is installed automatically: the scheduler
files (plists) are *printed* by the program and you decide when to install them.

## 1. What runs when

All times are India time (IST). The Mac's own clock and time zone must be set to India time,
because the scheduler (`launchd`) uses the Mac's local time.

| Job (launchd label) | When | What it does |
|---|---|---|
| `com.etfp.inav` | 09:10, Mon-Fri | Records iNAV snapshots every 15 minutes until about 15:45 (`etfp collect inav --loop`). Already installed. |
| `com.etfp.daily-evening` | 19:45, Mon-Fri | `etfp run daily --phase evening`: download every daily source for today, rebuild all tables, write the research export, send alerts. |
| `com.etfp.daily-morning` | 10:35, Mon-Fri | `etfp run daily --phase morning`: finish the previous session's data (see below), rebuild, export, alert. |

### Why two daily runs

A US-linked ETF's NAV for day *t* uses the US close of day *t*, which happens after the
Indian market has closed. The fund house publishes it the next morning (up to 10:00; see
DECISIONS D10). So on the evening of *t* the AMFI file does not yet hold the overseas NAVs for
*t*. The morning run downloads the AMFI window again and the table is rebuilt with the new
NAVs.

* **19:45** is after NSE posts its daily files (about 19:00, D22) with a margin.
* **10:35** is 35 minutes after the 10:00 publication deadline.
* The morning run is Monday to Friday (not Tuesday to Saturday) because **Monday's morning run
  is the one that fetches Friday's US NAV.** A Tuesday to Saturday schedule would deliver
  Friday's NAV only on Saturday.

### The steps of `etfp run daily`

1. **collect**: evening downloads every source `etfp collect daily` knows (NSE bhavcopy, UDiFF,
   security master, ETF list, AMFI NAVAll and history, Yahoo). Morning does two things for the
   *previous session*: fills any gap left by the evening run (files already stored are
   skipped, so this costs almost nothing), and downloads the AMFI history window again with
   force. The new copy is stored beside the old one as `.v2`; the build uses the newest.
   One source failing does not stop the others.
2. **build**: raw files to clean tables to `etf_day` (about 3 minutes).
3. **export**: writes `exports/research/<date>/` and `LATEST.txt` (skipped if the build failed).
4. **alerts**: sends new Telegram alerts. With no Telegram credentials this step prints a
   warning (and lists what it *would* have sent) and the run still counts as OK.

A failed step is printed loudly to the error log and the run **continues** with the next
step, but the run's exit code is 1. Alerts still run after a failure so the "stale data" alert
can fire.

On a day NSE was closed the command prints "NSE was closed on ..." and exits 0. This includes
Saturdays, Sundays and holidays. Diwali Muhurat sessions are real sessions (D24) and do run.

### Muhurat Sunday sessions

The schedule is Monday to Friday, so a Muhurat session on a weekend is not started by the
plist. You do not have to do anything for the NSE data: the next morning run (Monday) finishes
the previous session, which is the Muhurat session, and downloads its NSE files and the
late NAVs. The two "latest file only" snapshots (NSE ETF list and AMFI NAVAll) can only be
taken on the day itself. If you want them, run this by hand on the Muhurat evening after the
exchange files are posted (after about 19:00):

```
uv run etfp run daily --phase evening
```

## 2. Install and uninstall

The recorder (`com.etfp.inav`) is already installed. Do not repeat that step for it.

### Install the two daily jobs

```
cd ~/ETF_Premiums
uv run etfp ops launchd-plist --job daily-evening > ~/Library/LaunchAgents/com.etfp.daily-evening.plist
uv run etfp ops launchd-plist --job daily-morning > ~/Library/LaunchAgents/com.etfp.daily-morning.plist
plutil -lint ~/Library/LaunchAgents/com.etfp.daily-evening.plist ~/Library/LaunchAgents/com.etfp.daily-morning.plist
launchctl load ~/Library/LaunchAgents/com.etfp.daily-evening.plist
launchctl load ~/Library/LaunchAgents/com.etfp.daily-morning.plist
```

`launchd-plist` prints only the plist to the screen (the Muhurat note goes to the error
output), so the `>` redirect gives a clean file. `plutil -lint` must say `OK` for both.

### Check that they are loaded

```
launchctl list | grep etfp
```

You should see `com.etfp.inav`, `com.etfp.daily-evening` and `com.etfp.daily-morning`. A job
that is loaded shows a `-` as its PID when it is not running right now.

### Run a job right now (a test)

```
launchctl start com.etfp.daily-morning
```

or just run the command directly: `uv run etfp run daily --phase morning`.

### Uninstall a job

```
launchctl unload ~/Library/LaunchAgents/com.etfp.daily-evening.plist
rm ~/Library/LaunchAgents/com.etfp.daily-evening.plist
```

Same for `daily-morning` and for `inav`. After editing the schedule, `unload`, regenerate
the file with `launchd-plist`, and `load` again.

On newer macOS versions `launchctl load` and `unload` still work; the modern spelling is
`launchctl bootstrap gui/$(id -u) <file>` and `launchctl bootout gui/$(id -u) <file>`.

### When the Mac is asleep or off

* Asleep at the scheduled time: `launchd` normally runs the job when the Mac wakes up. The
  job itself holds the Mac awake (`caffeinate`) while it runs.
* Powered off or lid closed without power: the run is missed. The gap is not harmful (raw
  files are fetched by date), so just re-run the missed day (section 4).
* Changing a launchd job's time does not change the code: the run always works out which
  trading day it is processing (today in the evening, the previous session in the morning).

## 3. Where to look

| What | Where |
|---|---|
| Is everything healthy? | `uv run etfp status` (recorder heartbeat, failures today, raw file counts, **last evening run and last morning run, step by step**) |
| Run records | `data/state/runs/<date>_<evening or morning>.json` (steps, start times, durations, outcome, failed downloads) |
| Program log (rotating) | `logs/collect.log` |
| Scheduler output | `logs/launchd.daily-evening.out.log` and `.err.log`, same for `daily-morning` and `inav`. Failures appear in the `.err.log` |
| Overlap lock | `data/state/run_daily.lock` (only exists while a run is going) |
| Raw downloads (never edited) | `data/raw/<source>/<date>/` |
| Research export | `exports/research/LATEST.txt` points to the newest folder |

`etfp status` says `RUNNING` for a run in progress and `INTERRUPTED` for a record still marked
running after 2 hours (the program was killed, or the Mac shut down).

To read the last errors: `tail -50 logs/launchd.daily-evening.err.log`.

### Exit codes of `etfp run daily`

| Code | Meaning |
|---|---|
| 0 | Everything worked, or NSE was closed, or the only problem was missing Telegram credentials |
| 1 | At least one step failed (the record and the error log say which) |
| 2 | Bad option (for example a date in the future) |
| 3 | Another `run daily` is still running; nothing was run |

### The overlap lock

Two runs must not overlap (for example a slow evening run and a manual re-run). A second run
exits with code 3 and changes nothing. The lock is taken over automatically, with a
warning in the log, when it is more than 2 hours old or when the process that created it no
longer exists (a crash). If you are sure nothing is running and the lock is still there,
delete `data/state/run_daily.lock`.

## 4. Re-run a missed or failed day

Everything is safe to repeat: files already in `data/raw` are skipped, and a changed file is
stored beside the old one, never over it.

```
# the whole evening job for one past trading day
uv run etfp run daily --date 2026-11-12 --phase evening

# the morning job for that day (re-fetches the AMFI window, rebuilds, exports, alerts)
uv run etfp run daily --date 2026-11-12 --phase morning

# only download (no build)
uv run etfp collect daily --date 2026-11-12
uv run etfp collect daily --date 2026-11-12 --sources bhavcopy,udiff

# only rebuild and export from what is already in data/raw
uv run etfp build

# run without sending any Telegram alert
uv run etfp run daily --date 2026-11-12 --no-alerts
```

Limits of a past date:

* The NSE ETF list and AMFI NAVAll only exist as "today's latest file", so they are skipped
  for any day but today (the output says so). The AMFI history window covers past dates.
* Yahoo keeps only about 59 days of 5-minute bars (D22). Older days cannot be recovered.
* If you re-run a date whose alerts were already sent, nothing is sent twice (alerts remember
  what they sent in `data/state/alerts_sent.json`).

## 5. Backfill history

```
uv run etfp backfill --from 2021-01-01 --dry-run   # only count what would be downloaded
uv run etfp backfill --from 2021-01-01             # download; safe to stop and start again
```

* It resumes: files already stored are skipped and files NSE answered 404 for are remembered
  in `data/state/backfill_state.json`. Add `--retry-unavailable` to ask for those again.
* It is polite on purpose: NSE requests at least 1.5 seconds apart, AMFI in 7-day windows. A
  full backfill takes hours. Do not run it at the same time as `run daily` (both talk to NSE).
* `--sources` limits it (`bhavcopy`, `udiff`, `secmaster`, `amfi_history`, `yahoo`).
* After a backfill, run `uv run etfp build`.

## 6. Set up Telegram alerts

Until this is done, `run daily` works but prints a `WARNING ... Telegram credentials missing`
in the alerts step and sends nothing.

1. In Telegram, message **@BotFather**, send `/newbot`, and follow the prompts. It gives you a
   **token**.
2. Send any message to your new bot.
3. Open `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser (replace `<TOKEN>`) and
   find `"chat":{"id": ...}`. That number is your **chat id** (a group id starts with `-`).
4. Create the settings file and fill it in:

   ```
   cp .env.example .env
   open -e .env      # set TELEGRAM_BOT_TOKEN= and TELEGRAM_CHAT_ID=
   ```

   `.env` is ignored by git. Never paste the token anywhere else (chat, issue, screenshot).
5. Test: `uv run etfp alerts --dry-run` prints what would be sent (works without `.env`), then
   `uv run etfp alerts` sends for real.

The scheduled jobs read `.env` from the project folder, so nothing else needs to change. To
turn alerts off for a run: `--no-alerts`.

## 6b. Build the open dataset (by hand, local only)

The open dataset (project output 3) is never built by the scheduler and never uploaded by
the program. After a good build:

```
uv run etfp export dataset
```

It writes `exports/dataset/<today>/` (CSV + Parquet per table, `README.md`,
`DATA_DICTIONARY.md`, `LICENSE-DATA`, `manifest.json`) and `exports/dataset/LATEST.txt`.
What may go in is decided by `config/dataset.yaml` (DECISIONS D34):

* **`DATASET EXPORT REFUSED: ... not classified`**: the pipeline gained a column. Decide
  whether it may be published and add it to that table's `columns`, `drop` or the `deny`
  list, then run again.
* **`DATASET EXPORT REFUSED: ... denied`**: something licensed (Bloomberg, Yahoo) reached a
  public table. Do not work around it; find which column or row carries it.
* `manifest.json` → `code.git_dirty: true` means uncommitted code; commit first for a
  release you intend to publish.
* Licences are set (D41): data CC BY 4.0 (`LICENSE-DATA`), code MIT (`LICENSE`). The release
  carries only derived columns; a new column that copies a source value goes under `drop`.
  Publishing (Zenodo upload, DOI) is Amish's step.

## 7. When something fails

First run `uv run etfp status` and read the step that failed, then the matching section below.

### NSE returns 403 or 429 (blocked or rate limited)

Meaning: NSE refused us. The program stops all further requests to NSE for that run (retrying a
block is what gets an address banned), so several NSE files will show `FAILED ... blocked`.

* Do **not** re-run straight away or in a loop. Wait at least an hour (often until the next day).
* Check in a normal browser that nseindia.com opens. If it does not either, it is NSE.
* Then repeat the day: `uv run etfp run daily --date YYYY-MM-DD`. Stored files are skipped.
* If it keeps happening, increase `nse_min_interval_s` in `config/collect.yaml` (currently 1.5)
  and tell Claude: record the finding in `docs/DECISIONS.md`.
* A `404` on a day's file is different: usually NSE has not posted it yet (after about 19:00).
  The morning run fills the hole automatically. A 404 for a day NSE was closed means the
  calendar is wrong (add it to `config/calendars/nse_holidays.yaml`).

### AMFI times out

Meaning: AMFI builds the history file on request and sometimes does not answer in time
(30-day windows already failed, D22; the program uses 7-day windows and waits 120 seconds).

* Just repeat: `uv run etfp collect daily --date YYYY-MM-DD --sources amfi_history`. A failed
  download stores nothing, so the repeat asks again.
* Until it works, the NAV columns for that day are empty or late; the tables rebuild once the
  file arrives (`uv run etfp build`).
* If it fails for several days, check `https://www.amfiindia.com/` in a browser. The next
  morning run also retries it.

### Yahoo returns 429

Meaning: Yahoo throttled us. Yahoo is only the interim free source for futures and currency
(D13, D22); a day without it falls back to `nav_only` fair value.

* Wait a few hours and run `uv run etfp collect daily --date YYYY-MM-DD --sources yahoo`.
* You have about 59 days (5-minute bars). After that the data cannot be recovered, so do not
  leave it for weeks.
* If 429 happens every day, the free source is not reliable. That is an open decision for
  Amish (D22), not something to work around by requesting faster.

### Bhavcopy was served as an Excel file

Meaning: once in a while NSE sends an `.xlsx` workbook named like `.csv` (D24). The raw file is
saved untouched. The parser reads Excel with the standard library, so **nothing needs to be
done**.

* If the build still fails and the message names that file, do not edit or rename it. Tell Claude
  which file, and keep it for the fix.

### The build fails with CleanError

Meaning: a raw file was not in the expected shape (or a manual CSV under `data/manual/`
is malformed). The message names the table and the exact file and line, for example
`nav: data/raw/amfi_nav_history/2026-11-13/... line 4`.

1. Open the named file and look at that line. **Never edit anything in `data/raw/`.**
2. If a download was cut short or the source sent junk, download it again:
   `uv run etfp collect daily --date YYYY-MM-DD --sources <source> --force`. The new copy is
   stored as `.v2` and the build uses the newest version.
3. If it is a manual file in `data/manual/`, fix it (formats are in `docs/manual_data.md`).
4. Run `uv run etfp build` to check.
5. If the file looks valid, the parser needs a fix: send Claude the message and the file name.

While the build is failing, the research export is not updated (the export step says
`SKIPPED`) and the stale-data alert will fire after the expected build time.

### "another `etfp run daily` is running" (exit code 3)

A run is in progress. Look at `uv run etfp status` (it shows who holds the lock and since
when) and wait. A lock from a crashed run is replaced by the next run on its own after it is
shown to be dead or 2 hours old; see "The overlap lock" above.

### Nothing ran at all

* `launchctl list | grep etfp` shows nothing: the job is not loaded (section 2).
* `logs/launchd.<job>.err.log` is empty and `etfp status` shows an old date: the Mac was off
  or asleep; re-run the day (section 4).
* The error log says `uv: command not found`: the plist's PATH did not include `uv`. Regenerate
  the plist (`etfp ops launchd-plist --job ...`) from a shell where `which uv` works.
