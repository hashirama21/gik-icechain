# Archive backfill: run log and follow-up

Full-archive parallel backfill dispatched on 2026-07-16, run
[29521673050](https://github.com/hashirama21/gik-icechain/actions/runs/29521673050)
(`backfill.yaml` on main). One-shot ingestion of 1 274 days
(2023-01-18 to 2026-07-14) straight from the `.index` files on
`s3://ecmwf-forecasts`, both grid eras.

## Pipeline shape

| Stage | Fan-out | Expected |
|---|---|---|
| plan | 1 job | seconds |
| C2 shards | 51 chunks x 25 days, 20 concurrent | ~7 h (3 waves, ~2h20 each) |
| merge | 1 job, per-era routing + date dedup | ~1-2 h |
| C3 risk | 11 chunks x ~120 days + 14-day spin-up, 8 concurrent | ~2-3 h |

Dispatched with `keep_shards=true`: shards under
`s3://gik-icechain/backfill-shards/` survive the merge so a timed-out or
failed merge resumes at zero recompute cost (dedup skips merged dates).

## Store layout after the backfill

| Store | Era | Grid |
|---|---|---|
| `s3://gik-icechain/exceedance-zarr` | 2024-02-29 -> present (0.25 deg) | 159x137 |
| `s3://gik-icechain/exceedance-zarr-0p4` | 2023-01-18 -> 2024-02-28 (0.4 deg) | 100x86 |

Two stores by design: `write_exceedance_store` refuses mixed grids.
C3 consumers pick the store by date era (see `scripts/plan_backfill.py`,
boundary 2024-02-29).

## Validation already done (4-day era-boundary run 29514337224)

All green: parallel shards both eras, merge routed to both stores,
daily's dates preserved by dedup, C3 spin-up chunks on both eras,
4 risk JSONs on `s3://gik-icechain/admin1_risk/`.

## Outcome (2026-07-17)

Run 29521673050 finished green: **66 jobs success, 1 skipped**
(notify-failure). Verified in S3:

| Store | Dates | Range | Missing in era |
|---|---|---|---|
| exceedance-zarr-0p4 | 401 | 2023-01-18 -> 2024-02-28 | 6 (2023-04-27..05-02: absent from the ECMWF bucket itself, 404) |
| exceedance-zarr | 863 | 2024-02-29 -> 2026-07-15 | 6 (5 transient fetch failures + 2026-07-16 handled by the daily) |

1 264 risk JSONs on `s3://gik-icechain/admin1_risk/`
(2023-01-18 -> 2026-07-15). Shards deleted.

## Post-run steps executed

1. Shards deleted (`backfill-shards/` empty).
2. Catch-up dispatched for the 5 recoverable dates (2025-10-16,
   2025-10-18, 2026-04-05, 2026-04-30, 2026-07-08), sequentially:
   GitHub keeps at most ONE pending run per concurrency group, so
   parallel dispatches cancel each other - always wait between
   dispatches to the backfill group.
3. Dashboard contract rebuild: `dashboard-data/` cleared, then a
   deploy-web dispatch - its "Rebuild the contract from admin1_risk"
   step fires when the pulled contract is empty and loops every risk
   JSON (1 264 dates). `dependency()` falls back gracefully for
   0p4-era dates absent from the 0.25-deg store.

The 2023-04-27..2023-05-02 gap is unrecoverable from the open bucket
(files were never published); it is also absent from the GIK/E4DRR
catalogs. Permanent hole, documented here.

The daily real-time pipeline (`daily_update.yaml`, 08:00 UTC,
`ecmwf_direct`) is independent and keeps running; merge dedup makes
any overlap harmless.
