# Yahoo intraday fixtures are SYNTHETIC

Yahoo answered HTTP 429 to the first live request on 2026-11-13 and was not retried, so
these files are **hand-built in the documented shape of the Yahoo v8 chart API**
(`chart.result[0].meta / timestamp / indicators.quote[0]`), not downloaded. Values are a
seeded random walk with a few bars pinned to hand-chosen prices:

| File | Bar start (UTC) | Meaning | Close |
|---|---|---|---|
| NQ_F_5m.json | 2026-11-12 20:55 | last bar before the US close (21:00 UTC = 16:00 New York) | 21100.00 |
| NQ_F_5m.json | 2026-11-12 21:00 | first bar after the US close | 21101.75 |
| NQ_F_5m.json | 2026-11-13 09:55 | last bar before 15:30 IST (= 10:00 UTC) | 21000.50 |
| NQ_F_5m.json | 2026-11-13 10:00 | first bar after 15:30 IST | 21010.25 |
| USDINR_X_5m.json | same four times | | 83.4125, 83.4130, 83.4550, 83.4600 |

Also: a CME break with no bars 22:00-23:00 UTC (NQ), null bars at 2026-11-13 03:15 UTC
(NQ) and 05:00 UTC (USDINR), and `error_not_found.json` (Yahoo's JSON error shape).

Replace them with a real download (and update `tests/test_collect_yahoo_intraday.py`
expectations) as soon as a live request succeeds.
