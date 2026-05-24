# End-to-End Testing Guide

This guide walks through running the full GIK-IceChain pipeline (C1 → C2 → C3) from scratch,
first against a **local MinIO instance** (zero-cost, no AWS account needed), then against
**Amazon S3 in production**.

---

## Prerequisites

```bash
# Clone and install
git clone https://github.com/hashirama21/gik-icechain.git
cd gik-icechain
pip install -e ".[cloud]"

# Verify install
python -m gik_icechain --help
```

Required reference data in `data/`:

| Path | What it is | How to get it |
|------|-----------|---------------|
| `data/admin_boundaries/east_africa_admin1.gpkg` | Admin-1 boundaries | Already committed |
| `data/cmorph_thresholds/` | Pre-computed GEV thresholds (252 NetCDF files **or** single Zarr) | Already committed |
| `data/enso_iod_index.csv` | ENSO/IOD phase index | Already committed |
| `data/gpm_imerg/` | GPM IMERG daily precipitation | Download per instructions below |

GPM IMERG download (one day of data is enough for a smoke test):

```bash
# From NASA GES DISC (requires free Earthdata account)
# https://disc.gsfc.nasa.gov/datasets/GPM_3IMERGDF_07/summary
# Download 3B-DAY.MS.MRG.3IMERG.YYYYMMDD-S000000-E235959.V07B.nc4
# Place files in data/gpm_imerg/
```

---

## Part 1 — MinIO (local testing)

### 1.1 Start MinIO

```bash
# Option A: Docker (recommended)
docker run -d \
  --name minio-gik \
  -p 9000:9000 \
  -p 9001:9001 \
  -e MINIO_ROOT_USER=minioadmin \
  -e MINIO_ROOT_PASSWORD=minioadmin123 \
  -v "$PWD/.minio-data:/data" \
  minio/minio server /data --console-address ":9001"

# Option B: native binary
# https://min.io/docs/minio/linux/reference/minio-server.html
minio server .minio-data --console-address :9001
```

MinIO console is available at http://localhost:9001 (login: `minioadmin` / `minioadmin123`).

### 1.2 Create buckets

```bash
# Install MinIO Client if not present
pip install minio   # Python SDK (used below)
# or: https://min.io/docs/minio/linux/reference/minio-mc.html

mc alias set gik http://localhost:9000 minioadmin minioadmin123
mc mb gik/gik-icechain-store    # IceChunk virtual store
mc mb gik/gik-exceedance-zarr   # Exceedance Zarr store
mc mb gik/gik-data              # CMORPH thresholds + risk output
```

### 1.3 Upload reference data to MinIO

```bash
mc cp --recursive data/cmorph_thresholds/ gik/gik-data/cmorph_thresholds/
```

### 1.4 Set environment variables

```bash
export AWS_ENDPOINT_URL="http://localhost:9000"
export AWS_ACCESS_KEY_ID="minioadmin"
export AWS_SECRET_ACCESS_KEY="minioadmin123"
export AWS_DEFAULT_REGION="us-east-1"   # dummy — required by the AWS SDK

# Pipeline URIs
export GIK_ICECHUNK_STORE_URI="s3://gik-icechain-store"
export GIK_EXCEEDANCE_STORE_URI="s3://gik-exceedance-zarr"
export GIK_BUCKET="gik-data"
```

> **Note:** ECMWF source data (`s3://ecmwf-forecasts`) is a **public AWS bucket** read
> directly from AWS regardless of where you store outputs. Only C1/C2/C3 outputs go to MinIO.

### 1.5 Run C1 — Ingest one forecast day

```bash
python -m gik_icechain convert \
  --start 2024-10-15 \
  --end   2024-10-15 \
  --hf-dataset    E4DRR/gik-ecmwf-par \
  --output-store  "$GIK_ICECHUNK_STORE_URI" \
  --output-json   /tmp/c1_result.json \
  --config        configs/test_e2e.yaml

cat /tmp/c1_result.json
# Expected: {"commit_hash": "<sha>", "processed_date": "2024-10-15"}
```

Verify the IceChunk commit landed in MinIO:

```bash
mc ls gik/gik-icechain-store/
```

### 1.6 Run C2 — Compute exceedance probabilities

```bash
python -m gik_icechain exceedance \
  --store      "$GIK_ICECHUNK_STORE_URI" \
  --output     "$GIK_EXCEEDANCE_STORE_URI" \
  --thresholds "s3://$GIK_BUCKET/cmorph_thresholds/" \
  --start 2024-10-15 \
  --end   2024-10-15 \
  --config configs/test_e2e.yaml
```

Spot-check the output:

```python
import xarray as xr, os
ds = xr.open_zarr(os.environ["GIK_EXCEEDANCE_STORE_URI"], consolidated=False)
print(ds)
# Expected dims: (date, latitude, longitude, window, return_period)
# Expected vars: exceedance_prob (float32), ensemble_confidence (int8)
assert "exceedance_prob"     in ds
assert "ensemble_confidence" in ds
assert ds["exceedance_prob"].values.min() >= 0.0
assert ds["exceedance_prob"].values.max() <= 1.0
```

### 1.7 Run C3 — CRMA risk inference

```bash
python -m gik_icechain risk \
  --exceedance-store "$GIK_EXCEEDANCE_STORE_URI" \
  --output           results/test_admin1_risk/ \
  --start 2024-10-15 \
  --end   2024-10-15 \
  --config configs/test_e2e.yaml
```

Inspect the GeoJSON output:

```bash
ls -lh results/test_admin1_risk/
# Expected: risk_2024-10-15.geojson (~500 KB, ~150 admin-1 features)

python - <<'EOF'
import json
with open("results/test_admin1_risk/risk_2024-10-15.geojson") as f:
    fc = json.load(f)
features = fc["features"]
print(f"Features: {len(features)}")
for f in features[:3]:
    p = f["properties"]
    print(f"  {p['admin1_pcode']}  risk={p['risk_label']}  p_red={p['p_red']:.3f}")
EOF
```

### 1.8 Run the full pipeline in one command

```bash
python -m gik_icechain run-all \
  --start 2024-10-15 \
  --end   2024-10-17 \
  --config configs/test_e2e.yaml \
  --output results/minio_demo/
```

### 1.9 Validate the IceChunk store

```python
from gik_icechain.conversion.icechunk_writer import IceChainStore
import os

store = IceChainStore(os.environ["GIK_ICECHUNK_STORE_URI"])
store.create_or_open()
report = store.validate()
print(report)
# Expected:
# {
#   "committed_days": 1,
#   "date_range": "2024-10-15 to 2024-10-15",
#   "gaps_detected": 0,
#   "variables_present": ["tp", "2t", "10u", "10v", "ro"],
#   ...
# }
```

### 1.10 Test time-travel checkout

```python
from datetime import date
ds_historical = store.checkout_as_of(date(2024, 10, 15))
print(ds_historical)   # Read-only view of the store as of that date
```

### 1.11 Test consolidated Zarr thresholds (Fix 4)

```python
from gik_icechain.exceedance.thresholds import AdaptiveGEVThresholds
from pathlib import Path

# Load existing per-file thresholds and re-save as consolidated Zarr
thr = AdaptiveGEVThresholds.load(Path("data/cmorph_thresholds/"))
thr.save_zarr("/tmp/thresholds_consolidated.zarr")

# Round-trip: load back from Zarr
thr2 = AdaptiveGEVThresholds.load_zarr("/tmp/thresholds_consolidated.zarr")
print(f"Modes loaded: {len(thr2._thresholds)}")
assert len(thr2._thresholds) == len(thr._thresholds)
```

### 1.12 Run the unit test suite

```bash
pytest tests/unit/ -v --tb=short
# Expected: 49 passed
```

---

## Part 2 — Amazon S3 (production)

### 2.1 IAM setup

Create an IAM user (or role) with the following policy:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"],
      "Resource": [
        "arn:aws:s3:::YOUR-BUCKET",
        "arn:aws:s3:::YOUR-BUCKET/*"
      ]
    }
  ]
}
```

Replace `YOUR-BUCKET` with your actual bucket name (e.g. `gik-icechain`).

### 2.2 Create S3 buckets

```bash
aws s3 mb s3://gik-icechain          --region eu-west-1
# Use one bucket with prefixes — or separate buckets, both approaches work.
# The config below uses prefixes inside a single bucket.
```

### 2.3 Upload reference data

```bash
aws s3 cp data/cmorph_thresholds/ s3://gik-icechain/cmorph_thresholds/ --recursive
aws s3 cp data/admin_boundaries/  s3://gik-icechain/admin_boundaries/   --recursive
```

### 2.4 Set environment variables

```bash
# Remove MinIO endpoint override (use AWS directly)
unset AWS_ENDPOINT_URL

export AWS_ACCESS_KEY_ID="AKIA..."
export AWS_SECRET_ACCESS_KEY="..."
export AWS_DEFAULT_REGION="eu-west-1"

export GIK_ICECHUNK_STORE_URI="s3://gik-icechain/gik-icechain-store"
export GIK_EXCEEDANCE_STORE_URI="s3://gik-icechain/exceedance-zarr"
export GIK_BUCKET="gik-icechain"
```

Or use AWS profiles (recommended):

```bash
# ~/.aws/credentials
[gik-production]
aws_access_key_id     = AKIA...
aws_secret_access_key = ...
region                = eu-west-1

export AWS_PROFILE=gik-production
```

### 2.5 Create a production config override

Create `configs/production.yaml` (not committed — add to `.gitignore`):

```yaml
# configs/production.yaml
outputs:
  icechunk_store_uri: "s3://gik-icechain/gik-icechain-store"
  exceedance_store_uri: "s3://gik-icechain/exceedance-zarr"

sources:
  cmorph_thresholds_path: "s3://gik-icechain/cmorph_thresholds/"
  admin_boundaries_path:  "s3://gik-icechain/admin_boundaries/east_africa_admin1.gpkg"
  gpm_imerg_path:         "s3://gik-icechain/gpm_imerg/"

component2:
  dask:
    scheduler: "distributed"
    n_workers: 16

logging:
  level: "INFO"
  format: "json"
```

### 2.6 Run C1 in production

```bash
python -m gik_icechain convert \
  --start 2024-10-15 \
  --end   2024-10-15 \
  --hf-dataset   E4DRR/gik-ecmwf-par \
  --output-store "$GIK_ICECHUNK_STORE_URI" \
  --output-json  /tmp/c1_result.json \
  --config       configs/production.yaml
```

### 2.7 Run C2 in production

```bash
python -m gik_icechain exceedance \
  --store      "$GIK_ICECHUNK_STORE_URI" \
  --output     "$GIK_EXCEEDANCE_STORE_URI" \
  --thresholds "s3://$GIK_BUCKET/cmorph_thresholds/" \
  --start 2024-10-15 \
  --end   2024-10-15 \
  --workers 16 \
  --config configs/production.yaml
```

### 2.8 Run C3 in production

```bash
python -m gik_icechain risk \
  --exceedance-store "$GIK_EXCEEDANCE_STORE_URI" \
  --output           "s3://$GIK_BUCKET/admin1_risk/" \
  --start 2024-10-15 \
  --end   2024-10-15 \
  --config configs/production.yaml
```

### 2.9 Run the full pipeline (production, 30-day window)

```bash
python -m gik_icechain run-all \
  --start 2024-10-01 \
  --end   2024-10-31 \
  --config configs/production.yaml \
  --output results/production_oct2024/
```

### 2.10 Configure GitHub Actions for automated daily runs

Add these secrets under **Settings → Secrets and variables → Actions**:

| Secret name | Value |
|-------------|-------|
| `AWS_ACCESS_KEY_ID` | IAM key |
| `AWS_SECRET_ACCESS_KEY` | IAM secret |
| `GIK_ICECHUNK_STORE_URI` | `s3://gik-icechain/gik-icechain-store` |
| `GIK_EXCEEDANCE_STORE_URI` | `s3://gik-icechain/exceedance-zarr` |
| `GIK_BUCKET` | `gik-icechain` |

Leave `MINIO_ENDPOINT_URL` **unset** — the workflow reads it from secrets and falls back
to standard AWS S3 when the secret is empty.

Create a **`production`** environment under **Settings → Environments** (required by the
workflow `environment: production` declaration).

The pipeline will then run automatically every day at **08:00 UTC** and compact the IceChunk
store on the **1st of each month at 02:00 UTC**.

---

## Switching between MinIO and S3 at runtime

No code changes are needed. The only difference is whether `AWS_ENDPOINT_URL` is set:

| Variable | MinIO | AWS S3 |
|----------|-------|--------|
| `AWS_ENDPOINT_URL` | `http://localhost:9000` | unset |
| `AWS_ACCESS_KEY_ID` | `minioadmin` | IAM key |
| `AWS_SECRET_ACCESS_KEY` | `minioadmin123` | IAM secret |
| `AWS_DEFAULT_REGION` | `us-east-1` (dummy) | `eu-west-1` |

`IceChainStore._build_storage()` detects the endpoint automatically and enables
`force_path_style` for MinIO.

---

## Troubleshooting

### `NoCredentialsError` on ECMWF reads

ECMWF source data is a **public** S3 bucket accessed with anonymous credentials.
The `GIKFlatParquetParser` builds its own `S3Store(skip_signature=True)` registry
independently of your AWS credentials. This error should not appear for reads; if it
does, check that `GIK_ECMWF_ENDPOINT_URL` is not set to a private endpoint that blocks
anonymous access.

### IceChunk commit fails with `VirtualChunkContainerNotFound`

The ECMWF bucket must be declared as a trusted virtual chunk container.
`IceChainStore._build_repo_config()` registers `s3://ecmwf-forecasts/` automatically.
If using a MinIO mirror of the ECMWF archive, set:

```bash
export GIK_ECMWF_ENDPOINT_URL="http://localhost:9000"
```

### `FileNotFoundError` on CMORPH thresholds

Run the upload step first:

```bash
# MinIO
mc cp --recursive data/cmorph_thresholds/ gik/gik-data/cmorph_thresholds/

# AWS S3
aws s3 cp data/cmorph_thresholds/ s3://gik-icechain/cmorph_thresholds/ --recursive
```

Alternatively, switch to the consolidated single-file Zarr format (faster to load):

```python
from gik_icechain.exceedance.thresholds import AdaptiveGEVThresholds
from pathlib import Path

thr = AdaptiveGEVThresholds.load(Path("data/cmorph_thresholds/"))
thr.save_zarr("s3://gik-icechain/cmorph_thresholds.zarr")
```

Then point `cmorph_thresholds_path` in your config to `s3://gik-icechain/cmorph_thresholds.zarr`
and use `AdaptiveGEVThresholds.load_zarr()` instead of `load()`.

### C2 runs slowly on a single machine

C2 uses Dask. The `test_e2e.yaml` config sets `scheduler: synchronous` (no parallelism —
safe for local testing). Switch to `distributed` for production or when running on a
multi-core machine:

```yaml
# configs/production.yaml
component2:
  dask:
    scheduler: "distributed"
    n_workers: 8
```

### No GeoJSON output from C3

Check that the exceedance store contains data for the requested date:

```python
import xarray as xr
ds = xr.open_zarr("$GIK_EXCEEDANCE_STORE_URI", consolidated=False)
print(sorted(str(d)[:10] for d in ds["date"].values))
```

If the date is missing, re-run C2 for that date before running C3.
