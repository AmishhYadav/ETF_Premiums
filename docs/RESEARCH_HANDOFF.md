# Research handoff: what changed since schema 1.5.0 (one page)

For the research chat that reads only `exports/research/LATEST.txt` -> the dated folder. Current schema: **1.8.0**. Read `manifest.json` first (commit, build time IST, row counts, dq flag counts, open VERIFY items with `open_verify_state_counts`), then `DATA_DICTIONARY.md` (every column: type, definition, units, formula, source). Facts only; no results are implied.

## Changes that touch draft 3

| Since | Change | Where to look |
|---|---|---|
| 1.6.0 | **Controls are 22, not 4.** Select with `group_label == "control"` (other labels: `blocked`, `possibly_open`, `unclear`, `bullion`). `common.py` hard-codes the old four (`DOM`). HDFCBSE500 and HDFCSML250 start in 2023-24 (thin before). | D42, R20 |
| 1.6.0 | **Renamed-ticker history added.** `symbol` is always the current ticker. NIFTYIETF gains rows before 2023-12-20 (was ICICINIFTY), MON100 before 2021-06-16 (N100), SILVERBEES before 2022-05-10 (NETFSILVER). Earlier exports start these funds later. `symbol_changes.parquet` (1.8.0) lists every NSE rename and whether the config maps it. | D42, D45 |
| 1.6.0 | `usdinr_ref` (FBIL USD/INR, same day, published about 13:00 IST, never carried forward) and `fx_ref_daily.parquet`. | D43 |
| 1.7.0 | `fof_monthly` and `units_monthly` are **filled** from AMC monthly portfolios (D46): feeder units for MON100, MAFANG, MASPTOP50, MAHKTECH; AUM/NAV units for the six international ETFs from Jan 2021. So `feeder_units`, `float_net` and pre-2024 `units` are now non-empty. `units_monthly.nav_date` = AMFI NAV date behind the estimate. | D46, D51 |
| 1.7.0 | `valve_monthly` uses a **basket rule** (+-0.9 creation basket) where the basket size is known, not +-1%; 46 of 175 monthly statuses of the six funds changed; `*_timing` statuses mark months whose month-end NAV is older than the month-end (use two-month nets or drop them). | D48 |
| 1.7.0 | New flags: `etf_day.dq_units_stale` (units from the frozen NSE security-master stretch 5 Feb - 13 Mar 2024; HNGSNGBEES shows 14.16M, the AMC figure was 19.11M), `etf_day.dq_nav_misaligned` (0 rows now), `inav_snapshots.inav_stale`. | D51, D53, D49 |
| unchanged | **`fv_method` is still `nav_only`** (no free futures bars). So `fv` = `nav_known`, `pi` = `ln(close / nav_known)` (equal to draft 3's `lp`), `fv_beta` equals `fv`, `cap_pi` = `ln(band_high / nav_known)`. Do not read these as futures-adjusted. | D43 |

## Traps to avoid

- `rationed` is **null (not False) before 2024**: no daily units, so no creation history to test. `common.py` does `fillna(False)`, which turns "unknown" into "not rationed" for 2021-2023.
- `etf_day.units` before 5 Feb 2024 is a monthly AUM/NAV estimate (`units_source == "monthly_aum_nav"`); `creation_units_change` / `creation_event` exist only from 2024 (daily, dated by NSE's publication batch, not the AMC's day). Pre-2024 creations: use `valve_monthly`.
- `regime` is the regime in force by date (config); `regime_inferred` is the base that reproduces the official band (the BLUEPRINT meaning).
- `band_low` / `band_high` are aliases of the official band (`*_official`); `hit_up`, `hit_up_max` use the computed initial / maximum limits.
- `attention` is empty (no Google Trends files pulled); `china_etf_daily` is empty (no Bloomberg export yet); `inav_snapshots` holds only the days since 16 Nov 2026.

## Which draft-3 numbers likely need re-running

Depends on the export folder you used (`2026-11-14`; check its `manifest.json` `schema_version`).
1. **Anything over the control group** (P1 and Conley-Taber intervals, descriptives of domestic ETFs): controls 4 -> 22; the date window for MON100 / NIFTYIETF / SILVERBEES starts earlier.
2. **Anything using MON100, NIFTYIETF or SILVERBEES before 2021-06-16 / 2023-12-20 / 2022-05-10** (history, placebo windows, full-sample moments).
3. **If your folder was schema 1.4.0 or older:** `nav_known`, `prem_known`, `x_april`, `fv` near 31 March (43 rows changed, 1.5.0), and the computed band columns `band_high_initial`, `band_high_max`, `hit_up*`, `band_mismatch`, `band_base_kind` (1.5.0). Official `band_high`, `band_low` did not change.
4. **Anything using units, creation events, `rationed` or `float_net`** (a4 creations table, redemption counts, feeder float): daily units from 2024 are unchanged, but pre-2024 units and all feeder units are new; `dq_units_stale` rows (Feb - Mar 2024) should be excluded or flagged.
5. **Anything using `valve_monthly`** statuses (2021-2024 episodes): 46 statuses changed.

Re-run `common.py` with `ETFP_EXPORT` set to the new folder; change nothing else first, then diff the `out/*.json` files.
