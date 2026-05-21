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
| `--start` / `--end` | ✅ | Always existed |
| `--output-store` | ✅ | Overrides `cfg.outputs.icechunk_store_uri` |
| `--hf-dataset` | ✅ | Overrides `cfg.sources.gik_hf_dataset` |
| `--mode` | ✅ | Accepted; `create_or_open` handles append semantics |
| `--output-json` | ✅ | Writes `{"commit_hash": "...", "processed_date": "..."}` |

The `--output-json` file is what the workflow reads to extract `commit_hash`
and pass it downstream via `$GITHUB_OUTPUT`.

### C2 — `exceedance`

| Flag | Status | Notes |
|---|---|---|
| `--store` / `--output` / `--start` / `--end` | ✅ | Always existed |
| `--thresholds` | ✅ | Overrides `cfg.sources.cmorph_thresholds_path` |
| `--region` | ✅ | Accepted (informational; spatial filtering not yet applied) |
| `--mode` | ✅ | Accepted; append is always the default |

### C3 — `risk`

| Flag | Status | Notes |
|---|---|---|
| `--exceedance-store` / `--output` / `--start` / `--end` | ✅ | Always existed |

---

## 4. Dashboard Job

`dashboard/storymaps/generate_storymaps.py` does not exist yet.  
The Dashboard job is **commented out** in `daily_update.yaml` until the script is implemented.  
`notify-failure` has been updated to only depend on C1, C2, C3.

---

## 5. Implementation Priority

| # | Action | Status |
|---|---|---|
| 1 | Add missing CLI flags to `convert` and `exceedance` | ✅ Done |
| 2 | Create `production` environment in GitHub Settings | Pending |
| 3 | Add all 5 production Secrets | Pending |
| 4 | Comment out Dashboard job | ✅ Done |
| 5 | Create `dashboard/storymaps/generate_storymaps.py` | Pending (re-enable job when ready) |

---

## 6. MinIO / On-Premises S3 Storage

If you run the pipeline against a self-hosted MinIO instance instead of AWS S3,
no code changes are needed — everything is driven by environment variables.

### How it works

| Variable | Read by | Purpose |
|---|---|---|
| `AWS_ENDPOINT_URL` | `IceChainStore` (`icechunk_writer.py`) | Redirect IceChunk store writes to MinIO |
| `GIK_ECMWF_ENDPOINT_URL` | `_build_ecmwf_registry` (`virtualizer.py`) | Redirect ECMWF GRIB2 reads to a local mirror |

`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` are reused as-is — MinIO accepts the same format.  
`force_path_style` is enabled automatically when `AWS_ENDPOINT_URL` is set (required by MinIO).

### Local usage

```bash
export AWS_ENDPOINT_URL="http://localhost:9000"
export AWS_ACCESS_KEY_ID="minioadmin"
export AWS_SECRET_ACCESS_KEY="minioadmin"
export AWS_REGION="us-east-1"           # dummy value — required by some SDKs

# Optional: only if ECMWF data is mirrored on your MinIO instance
# export GIK_ECMWF_ENDPOINT_URL="http://localhost:9000"

python -m gik_icechain convert \
  --start 2024-10-01 --end 2024-10-01 \
  --output-store s3://my-minio-bucket/gik-store \
  --hf-dataset E4DRR/gik-ecmwf-par
```

### GitHub Actions — MinIO secrets

Replace the AWS secrets with MinIO equivalents:

| Secret | Value |
|---|---|
| `AWS_ACCESS_KEY_ID` | MinIO access key |
| `AWS_SECRET_ACCESS_KEY` | MinIO secret key |
| `MINIO_ENDPOINT_URL` | e.g. `https://minio.your-infra.com` |

Then add to each job in `daily_update.yaml`:

```yaml
env:
  AWS_ENDPOINT_URL: ${{ secrets.MINIO_ENDPOINT_URL }}
```

### MinIO bucket setup

```bash
# mc = MinIO Client
mc alias set local http://localhost:9000 minioadmin minioadmin
mc mb local/gik-icechain-store
mc mb local/exceedance-zarr
mc mb local/gik-data              # for admin1_risk/, cmorph_thresholds/, etc.
```

---

## 7. Notes

- **macOS local testing**: the C1 step uses `date -u -d "yesterday"` (GNU `date`, Linux-only).
  On macOS use `date -u -v-1d +%Y-%m-%d` instead. GitHub Actions runners are Ubuntu so this
  does not affect CI.
- **`create_or_open`** handles append/overwrite automatically, so `--mode append` is accepted
  but has no additional effect.
