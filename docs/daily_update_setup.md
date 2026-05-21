# `daily_update.yaml` — Configuration Checklist

Everything required for the daily pipeline workflow to execute end-to-end.

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

## 3. CLI Flags — Status

All flags used in the workflow are now implemented in `cli.py`.

### C1 — `convert`

| Flag | Status | Notes |
|---|---|---|
| `--start` / `--end` | done | Always existed |
| `--output-store` | done | Overrides `cfg.outputs.icechunk_store_uri` |
| `--hf-dataset` | done | Overrides `cfg.sources.gik_hf_dataset` |
| `--mode` | done | Accepted; `create_or_open` handles append semantics |
| `--output-json` | done | Writes `{"commit_hash": "...", "processed_date": "..."}` |

The `--output-json` file is what the workflow reads to extract `commit_hash`
and pass it downstream via `$GITHUB_OUTPUT`.

### C2 — `exceedance`

| Flag | Status | Notes |
|---|---|---|
| `--store` / `--output` / `--start` / `--end` | done | Always existed |
| `--thresholds` | done | Overrides `cfg.sources.cmorph_thresholds_path` |
| `--region` | done | Accepted (informational; spatial filtering not yet applied) |
| `--mode` | done | Accepted; append is always the default |

### C3 — `risk`

| Flag | Status | Notes |
|---|---|---|
| `--exceedance-store` / `--output` / `--start` / `--end` | done | Always existed |

---

## 4. Dashboard Job

`dashboard/storymaps/generate_storymaps.py` does not exist yet.  
The Dashboard job is **commented out** in `daily_update.yaml` until the script is implemented.  
`notify-failure` has been updated to only depend on C1, C2, C3.

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

## 6. MinIO / On-Premises S3 Storage

Switching from AWS S3 to MinIO requires **no code changes** and **no new variable names**.
You reuse the same 5 secrets with MinIO values and add exactly one extra secret for the
endpoint URL.

### Complete example — MinIO on `192.168.1.50:9000`

#### Step 1 — Create the buckets once

```bash
# Install MinIO Client: https://min.io/docs/minio/linux/reference/minio-mc.html
mc alias set gik http://192.168.1.50:9000 minioadmin minioadmin123

mc mb gik/gik-icechain-store     # IceChunk virtual store  (→ GIK_ICECHUNK_STORE_URI)
mc mb gik/gik-exceedance-zarr    # Exceedance Zarr         (→ GIK_EXCEEDANCE_STORE_URI)
mc mb gik/gik-data               # Thresholds + risk output (→ GIK_BUCKET)
```

#### Step 2 — GitHub Secrets (same 5 names, MinIO values)

| Secret name | Value for MinIO |
|---|---|
| `AWS_ACCESS_KEY_ID` | `minioadmin` |
| `AWS_SECRET_ACCESS_KEY` | `minioadmin123` |
| `GIK_ICECHUNK_STORE_URI` | `s3://gik-icechain-store` |
| `GIK_EXCEEDANCE_STORE_URI` | `s3://gik-exceedance-zarr` |
| `GIK_BUCKET` | `gik-data` |

Add **one extra secret** — the MinIO endpoint:

| Secret name | Value |
|---|---|
| `MINIO_ENDPOINT_URL` | `http://192.168.1.50:9000` |

#### Step 3 — `daily_update.yaml` (already configured)

`AWS_ENDPOINT_URL` is declared at the top-level `env:` block and reads from
`MINIO_ENDPOINT_URL`. When the secret is empty it has no effect (standard AWS).
When set, `IceChainStore` automatically enables `force_path_style` as required by MinIO.

#### Step 4 — Full local run against MinIO

```bash
export AWS_ENDPOINT_URL="http://192.168.1.50:9000"
export AWS_ACCESS_KEY_ID="minioadmin"
export AWS_SECRET_ACCESS_KEY="minioadmin123"
export AWS_REGION="us-east-1"          # dummy — required by the AWS SDK

# C1 — Ingest
python -m gik_icechain convert \
  --start 2024-10-01 --end 2024-10-01 \
  --output-store  s3://gik-icechain-store \
  --hf-dataset    E4DRR/gik-ecmwf-par \
  --output-json   /tmp/result.json

# C2 — Exceedance
python -m gik_icechain exceedance \
  --store       s3://gik-icechain-store \
  --output      s3://gik-exceedance-zarr \
  --thresholds  s3://gik-data/cmorph_thresholds/ \
  --start 2024-10-01 --end 2024-10-01

# C3 — Risk
python -m gik_icechain risk \
  --exceedance-store s3://gik-exceedance-zarr \
  --output           s3://gik-data/admin1_risk/ \
  --start 2024-10-01 --end 2024-10-01
```

> **Note:** ECMWF source data (`s3://ecmwf-forecasts`) is a public AWS bucket read
> directly from AWS regardless of your output storage. Only the pipeline outputs
> (IceChunk store, exceedance Zarr, risk GeoJSON) go to MinIO.

---

## 7. Notes

- **macOS local testing**: the C1 step uses `date -u -d "yesterday"` (GNU `date`, Linux-only).
  On macOS use `date -u -v-1d +%Y-%m-%d` instead. GitHub Actions runners are Ubuntu so this
  does not affect CI.
- **`create_or_open`** handles append/overwrite automatically, so `--mode append` is accepted
  but has no additional effect.
