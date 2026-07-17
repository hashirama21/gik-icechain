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

## After the run completes

1. Check all 53+ jobs green; on partial failure use "Re-run failed jobs"
   (chunks and merge are idempotent).
2. Verify store date counts (~407 dates 0p4, ~867 + daily dates 0p25).
3. Delete the shards:
   `aws s3 rm --recursive s3://gik-icechain/backfill-shards/`
4. Rebuild the dashboard contract for the backfilled dates (the backfill
   does not touch `dashboard-data/`): loop
   `python -m dashboard.data_pipeline.pipeline contract --date <d>` over
   the new risk JSONs, then `scripts/publish_dashboard_data.sh` and a
   `deploy-web` dispatch. Storymap pages generate automatically from the
   refreshed `index.json` at the next Pages build.
5. The daily real-time pipeline (`daily_update.yaml`, 08:00 UTC,
   `ecmwf_direct`) is independent and keeps running during the backfill;
   the merge dedup makes any overlap harmless.
