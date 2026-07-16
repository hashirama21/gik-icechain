# `daily_update.yaml` - Configuration Checklist

Everything required for the daily pipeline workflow to execute end-to-end.

> For the **full production deployment** (storage, dashboard → GitHub Pages,
> validation, local dev), see **[`deploy.md`](deploy.md)**. This file focuses on the
> daily workflow's secrets + per-component CLI flags.

---

## 1. GitHub Secrets

Go to **Settings → Secrets and variables → Actions → New repository secret**.

### Production pipeline (`daily_update.yaml`)

| Secret name | Used in | Value to set |
|---|---|---|
| `AWS_ACCESS_KEY_ID` | C1, C2, C3 | IAM key with **write** access to your S3 bucket |
| `AWS_SECRET_ACCESS_KEY` | C1, C2, C3 | Matching IAM secret |
| `GIK_ICECHUNK_STORE_URI` | C1 (`--output-store`), C2 (`--store`) | e.g. `s3://your-bucket/gik-icechain-store` |
| `GIK_BUCKET` | C2 (`cmorph_thresholds/`), C3 (`admin1_risk/`) | Bucket name only, e.g. `your-bucket` |
| `GIK_EXCEEDANCE_STORE_URI` | C2 (`--output`), C3 (`--exceedance-store`) | e.g. `s3://your-bucket/exceedance-zarr` |

### CI integration tests (`ci.yaml`)

| Secret name | Value |
|---|---|
| `AWS_ACCESS_KEY_ID_READONLY` | Read-only IAM key |
| `AWS_SECRET_ACCESS_KEY_READONLY` | Matching secret |
| `GIK_TEST_BUCKET` | Test bucket name |

---

## 2. GitHub Environment

Create a **`production`** environment at **Settings → Environments → New environment**.

Jobs C1, C2, and C3 all declare `environment: production`. Without this the workflow
will refuse to start those jobs.

---

## 3. CLI Flags - Status

All flags used in the workflow are now implemented in `cli.py`.

### C1 - `convert`

| Flag | Status | Notes |
|---|---|---|
| `--start` / `--end` | done | Always existed |
| `--output-store` | done | Overrides `cfg.outputs.icechunk_store_uri` |
| `--hf-dataset` | done | Overrides `cfg.sources.gik_hf_dataset` |
| `--mode` | done | Accepted; `create_or_open` handles append semantics |
| `--output-json` | done | Writes `{"commit_hash": "...", "processed_date": "..."}` |

The `--output-json` file is what the workflow reads to extract `commit_hash`
and pass it downstream via `$GITHUB_OUTPUT`.

### C2 - `exceedance`

| Flag | Status | Notes |
|---|---|---|
| `--store` / `--output` / `--start` / `--end` | done | Always existed |
| `--thresholds` | done | Overrides `cfg.component2.thresholds.cmorph_path` |
| `--workers` | done | Overrides `cfg.component2.parallel.max_workers` + Dask client workers |
| `--profile` | done | Overrides `cfg.component2.active_profile` (flash_flood, medium_range, full) |

### C3 - `risk`

| Flag | Status | Notes |
|---|---|---|
| `--exceedance-store` / `--output` / `--start` / `--end` | done | Always existed |

---

## 4. Dashboard Job

`update-dashboard` is **active**: it rebuilds the web data contract and publishes it to
S3 (never committed). It pulls the existing contract + the day's risk from
`s3://$GIK_BUCKET/`, runs `dashboard.data_pipeline.pipeline contract` + `geojson`, then
`aws s3 sync`s the result to `s3://$GIK_BUCKET/dashboard-data/`. The web app reads it via
`NEXT_PUBLIC_DATA_BASE` and `deploy-web.yaml` serves the static site on GitHub Pages -
see [`deploy.md` §6](deploy.md). `notify-failure` depends on C1, C2, C3.

---

## 5. Implementation Priority

| # | Action | Status |
|---|---|---|
| 1 | Add missing CLI flags to `convert` and `exceedance` | done Done |
| 2 | Create `production` environment in GitHub Settings | Pending |
| 3 | Add all 5 production Secrets | Pending |
| 4 | Comment out Dashboard job | done Done |
| 5 | Create `dashboard/storymaps/generate_storymaps.py` | Pending (re-enable job when ready) |

---

## 6. Full local run against AWS S3

```bash
eval "$(python scripts/load_aws_credentials.py)"   # keys from develop_accessKeys.csv (repo root)

# C1 - Ingest
python -m gik_icechain convert   --start 2024-10-01 --end 2024-10-01   --output-store  s3://gik-icechain/gik-icechain-store   --hf-dataset    E4DRR/gik-ecmwf-par   --output-json   /tmp/result.json

# C2 - Exceedance
python -m gik_icechain exceedance   --store       s3://gik-icechain/gik-icechain-store   --output      s3://gik-icechain/exceedance-zarr   --start 2024-10-01 --end 2024-10-01

# C2 from the published full-archive store (2023-01-18 -> present, no C1 needed)
python -m gik_icechain exceedance   --store "" --config configs/published_store.yaml   --output s3://gik-icechain/exceedance-zarr   --start 2023-06-15 --end 2023-06-15

# C3 - Risk
python -m gik_icechain risk   --exceedance-store s3://gik-icechain/exceedance-zarr   --output           s3://gik-icechain/admin1_risk/   --start 2024-10-01 --end 2024-10-01
```

> **Note:** ECMWF source data (`s3://ecmwf-forecasts`) and the published E4DRR
> store (source.coop) are public and read anonymously regardless of your output
> storage. Only the pipeline outputs (IceChunk store, exceedance Zarr, risk
> GeoJSON) need credentials.

---

## 7. Notes

- **macOS local testing**: the C1 step uses `date -u -d "yesterday"` (GNU `date`, Linux-only).
  On macOS use `date -u -v-1d +%Y-%m-%d` instead. GitHub Actions runners are Ubuntu so this
  does not affect CI.
- **`create_or_open`** handles append/overwrite automatically, so `--mode append` is accepted
  but has no additional effect.
