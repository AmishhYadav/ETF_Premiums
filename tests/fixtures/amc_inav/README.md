# Fund-house iNAV fixtures

Real responses from the three fund houses' public iNAV pages, saved unmodified
(byte for byte) and used by `tests/test_collect_amc_inav.py`.

Captured 2026-11-10 at about 15:50 IST. That day was an NSE market holiday, so the
values are frozen end-of-day figures and the fund-house timestamps are the
servers' clocks at response time, not the time an iNAV was computed.

| File | Request | Source URL |
|---|---|---|
| `motilal_GetINAVandPrice.json` | `POST`, JSON body `{"apiName": "GetINAVandPrice"}` | https://www.motilaloswalmf.com/mutualfund/api/v1/someFunc |
| `mirae_ticker.json` | `GET` | https://miraeassetetf.co.in/api/ticker |
| `nippon_DetailsFill.json` | `POST`, empty body (`Content-Length: 0`) | https://investeasy.nipponindiaim.com/Online/Realtime/DetailsFill |

Notes:
- Nippon's values on that day looked stale (Nifty 50 BeES 250.72 against an official NAV of 285.81).
  They are fixtures for parsing tests only, not a statement about real iNAVs.
- No cookies, tokens or personal data are in these files.
