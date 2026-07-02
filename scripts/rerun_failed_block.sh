#!/usr/bin/env bash
# Re-run the OND-2024 block that failed in the last backfill.
#
# Last failure (backfill_ond2024.progress / backfill_ond2024.log):
#   [1/23] BLOCK 2024-10-01..2024-10-04  FAILED  3668s
#   cause: OOM in parallel exceedance (3 workers) ->
#          "ECCODES default_buffer_malloc" / "BrokenProcessPool".
#
# Fix: run with configs/rerun_safe.yaml, which forces the exceedance step to
# run sequentially (max_workers=1) so peak RAM stays ~6 GB.
#
# Credentials/endpoint come from the environment (no secrets in source).
# Export these first, e.g.:
#   export AWS_ACCESS_KEY_ID=...  AWS_SECRET_ACCESS_KEY=...  AWS_ENDPOINT_URL=http://<host>:9000
set -u

PROJ="/c/Users/BKNV2795/Documents/Projects/gik-icechain"
GIK="$PROJ/.venv/Scripts/gik-icechain.exe"
CONFIG="$PROJ/configs/rerun_safe.yaml"
LOG="$PROJ/rerun_2024-10-01_04.log"

export AWS_ACCESS_KEY_ID="${AWS_ACCESS_KEY_ID:?set AWS_ACCESS_KEY_ID}"
export AWS_SECRET_ACCESS_KEY="${AWS_SECRET_ACCESS_KEY:?set AWS_SECRET_ACCESS_KEY}"
export AWS_ENDPOINT_URL="${AWS_ENDPOINT_URL:?set AWS_ENDPOINT_URL}"
export ECCODES_PYTHON_USE_FINDLIBS=1

START="2024-10-01"
END="2024-10-04"

echo "=== RERUN BLOCK $START..$END (sequential exceedance) START $(date) ===" | tee "$LOG"
if "$GIK" run-all --config "$CONFIG" --start "$START" --end "$END" >> "$LOG" 2>&1; then
  echo "RERUN $START..$END OK $(date)" | tee -a "$LOG"
else
  rc=$?
  echo "RERUN $START..$END FAILED (rc=$rc) $(date)" | tee -a "$LOG"
  exit "$rc"
fi
