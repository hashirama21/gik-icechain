#!/usr/bin/env bash
# Run Component 3 (CRMA Bayesian-Network risk) locally, reading the exceedance
# store on AWS S3 (eu-north-1).
#
# Usage (git bash):
#   bash scripts/run_c3_local.sh [START] [END]
#   bash scripts/run_c3_local.sh 2024-09-01 2025-01-31
#
# AWS credentials come from develop_accessKeys.csv at the repo root
# (gitignored) via scripts/load_aws_credentials.py.
set -u

PROJ="$(cd "$(dirname "$0")/.." && pwd)"
GIK="$PROJ/.venv/Scripts/gik-icechain.exe"
CONFIG="$PROJ/configs/default.yaml"
EXC_STORE="s3://gik-icechain/exceedance-zarr"
OUT="$PROJ/results/admin1_risk"

START="${1:-2024-09-01}"
END="${2:-2025-01-31}"

eval "$("$PROJ/.venv/Scripts/python.exe" "$PROJ/scripts/load_aws_credentials.py")"
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
