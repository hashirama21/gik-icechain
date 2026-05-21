# `daily_update.yaml` — Configuration Checklist

Everything required for the daily pipeline workflow to execute end-to-end.

---

## 1. GitHub Secrets

Go to **Settings → Secrets and variables → Actions → New repository secret**.

### Production pipeline (`daily_update.yaml`)

| Secret name | Used in | Value to set |
|---|---|---|
| `AWS_ACCESS_KEY_ID` | C1, C2, C3, Dashboard | IAM key with **write** access to your S3 bucket |
| `AWS_SECRET_ACCESS_KEY` | C1, C2, C3, Dashboard | Matching IAM secret |
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

## 3. CLI Flag Mismatches

The workflow calls the CLI with flags that **do not yet exist** in `cli.py`.
They must be added before the workflow can run.

### C1 — `convert` command

Workflow invocation:

```bash
python -m gik_icechain convert \
  --hf-dataset E4DRR/gik-ecmwf-par \
  --output-store ${{ secrets.GIK_ICECHUNK_STORE_URI }} \
  --start "$TARGET_DATE" \
  --end   "$TARGET_DATE" \
  --mode append \
  --output-json /tmp/ingest_result.json
```

Current `convert` only accepts `--start`, `--end`, `--config`. Missing flags to add:

| Flag | Type | Purpose |
|---|---|---|
| `--output-store` | `str` | Override `cfg.outputs.icechunk_store_uri` |
| `--hf-dataset` | `str` | Override `cfg.sources.gik_hf_dataset` |
| `--mode` | `str` | `append` / `overwrite` (`create_or_open` already defaults to append) |
| `--output-json` | `Path` | Write `{"commit_hash": "...", "processed_date": "..."}` for downstream steps |

> `--output-json` is critical: the workflow extracts `commit_hash` from this file
> and passes it to C2 via `$GITHUB_OUTPUT`.

### C2 — `exceedance` command

Workflow invocation:

```bash
python -m gik_icechain exceedance \
  --store   ${{ secrets.GIK_ICECHUNK_STORE_URI }} \
  --output  ${{ secrets.GIK_EXCEEDANCE_STORE_URI }} \
  --start   ... \
  --end     ... \
  --thresholds s3://${{ secrets.GIK_BUCKET }}/cmorph_thresholds/ \
  --region east_africa \
  --mode append
```

`--store`, `--output`, `--start`, `--end` already exist. Missing flags:

| Flag | Purpose |
|---|---|
| `--thresholds` | Override `cfg.sources.cmorph_thresholds_path` |
| `--region` | Spatial domain filter (can be a no-op initially; GEV thresholds cover all lats/lons) |
| `--mode` | `append` is already the default in `write_exceedance_store` — flag can be a no-op |

### C3 — `risk` command

Workflow invocation:

```bash
python -m gik_icechain risk \
  --exceedance-store ${{ secrets.GIK_EXCEEDANCE_STORE_URI }} \
  --output s3://${{ secrets.GIK_BUCKET }}/admin1_risk/ \
  --start  ... \
  --end    ...
```

All four flags already exist in `cli.py`. **C3 is ready as-is.** ✓

---

## 4. Missing File

The Dashboard job calls:

```bash
python dashboard/storymaps/generate_storymaps.py \
  --exceedance-store ... \
  --risk-dir ... \
  --output dashboard/calendar_map/data/ \
  --date ...
```

`dashboard/storymaps/generate_storymaps.py` does not exist in the repository.
Either create it or comment out the entire Dashboard job until it is implemented.

---

## 5. Implementation Priority

| # | Action | Blocks |
|---|---|---|
| 1 | Add `--output-store`, `--hf-dataset`, `--output-json` to `convert` | C1 → everything downstream |
| 2 | Create `production` environment in GitHub Settings | C1, C2, C3 job startup |
| 3 | Add all 5 production Secrets | C1, C2, C3, Dashboard |
| 4 | Add `--thresholds` (and optionally `--region`, `--mode`) to `exceedance` | C2 |
| 5 | Create `dashboard/storymaps/generate_storymaps.py` (or comment out Dashboard job) | Dashboard step |

---

## 6. MinIO / On-Premises S3 Storage

If you run the pipeline against a self-hosted MinIO instance instead of AWS S3,
no code changes are needed — everything is driven by environment variables.

### How it works

Two env vars are read at runtime:

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
  --config configs/local.yaml
```

`configs/local.yaml` should set `outputs.icechunk_store_uri: s3://my-minio-bucket/gik-store`.

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

Or set it at the workflow level under the top-level `env:` block so all jobs inherit it.

### MinIO bucket setup

```bash
# Create buckets (mc = MinIO Client)
mc alias set local http://localhost:9000 minioadmin minioadmin
mc mb local/gik-icechain-store
mc mb local/exceedance-zarr
mc mb local/gik-data              # for admin1_risk/, cmorph_thresholds/, etc.

# Optional: make IceChunk store publicly readable
mc anonymous set download local/gik-icechain-store
```

### ECMWF mirror (optional)

If you want to mirror ECMWF GRIB2 data locally (removes dependency on `s3://ecmwf-forecasts`):

```bash
# Sync one forecast day to MinIO (large — 51 members × ~200 MB each)
aws s3 sync s3://ecmwf-forecasts/2024/10/01/00z/ \
  s3://ecmwf-forecasts/2024/10/01/00z/ \
  --endpoint-url http://localhost:9000

# Then set:
export GIK_ECMWF_ENDPOINT_URL="http://localhost:9000"
```

---

## 7. Notes

- **macOS local testing**: the C1 step uses `date -u -d "yesterday"` (GNU `date`, Linux-only).
  On macOS use `date -u -v-1d +%Y-%m-%d` instead. GitHub Actions runners are Ubuntu so this
  does not affect CI.
- **`create_or_open`** already handles the append/overwrite distinction for the IceChunk store,
  so `--mode append` for C1 can be accepted and silently ignored if the store exists.
