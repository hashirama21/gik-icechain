#!/usr/bin/env bash
# Run Component 3 (CRMA Bayesian-Network risk) LOCALLY, reading the exceedance
# store that C1+C2 produced on Colab/MinIO.
#
# C1 (convert) and C2 (exceedance) run on the Colab notebook
# (notebooks/gik_icechain_colab_run.ipynb) and write s3://gik-icechain/exceedance-zarr.
# Once that window is written, run this on your machine to get admin-1 risk GeoJSON.
#
# Usage (git bash):
#   bash scripts/run_c3_local.sh [START] [END]
#   bash scripts/run_c3_local.sh 2024-09-01 2025-01-31
# Defaults to the full 5-month target window if no dates are given.
#
# MinIO credentials are read from the repo .env (MINIO / MINIO_ACCESS_KEY /
# MINIO_SECRET_KEY) and mapped to the AWS_* env the CLI expects. No secrets in source.
set -u

PROJ="/c/Users/BKNV2795/Documents/Projects/gik-icechain"
GIK="$PROJ/.venv/Scripts/gik-icechain.exe"
CONFIG="$PROJ/configs/default.yaml"
EXC_STORE="s3://gik-icechain/exceedance-zarr"
OUT="$PROJ/results/admin1_risk"

START="${1:-2024-09-01}"
END="${2:-2025-01-31}"

# Load MinIO creds from .env -> AWS_* (the CLI resolves the endpoint via env).
if [ ! -f "$PROJ/.env" ]; then
  echo "ERROR: $PROJ/.env not found (need MINIO / MINIO_ACCESS_KEY / MINIO_SECRET_KEY)." >&2
  exit 1
fi
export AWS_ACCESS_KEY_ID="$(grep '^MINIO_ACCESS_KEY=' "$PROJ/.env" | cut -d= -f2)"
export AWS_SECRET_ACCESS_KEY="$(grep '^MINIO_SECRET_KEY=' "$PROJ/.env" | cut -d= -f2)"
export AWS_ENDPOINT_URL="http://$(grep '^MINIO=' "$PROJ/.env" | cut -d= -f2)"
export ECCODES_PYTHON_USE_FINDLIBS=1

echo "=== C3 risk $START..$END -> $OUT  (store $EXC_STORE) $(date) ==="
if "$GIK" risk \
    --exceedance-store "$EXC_STORE" \
    --output "$OUT" \
    --start "$START" --end "$END" \
    --config "$CONFIG"; then
  echo "C3 OK $START..$END $(date)"
else
  rc=$?
  echo "C3 FAILED (rc=$rc) $(date)" >&2
  exit "$rc"
fi
