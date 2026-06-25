#!/usr/bin/env bash
# Sequential auto-chained backfill of OND-2024 in ~4-day blocks.
# Each block runs C1->C2->C3 (run-all); a failed block is logged and SKIPPED so
# the sequence keeps going (non-blocking for the user). Idempotent-ish: IceChunk
# convert appends, exceedance/risk overwrite per date.
set -u

PROJ="/c/Users/BKNV2795/Documents/Projects/gik-icechain"
GIK="$PROJ/.venv/Scripts/gik-icechain.exe"
LOG="$PROJ/backfill_ond2024.log"
PROG="$PROJ/backfill_ond2024.progress"

# Credentials/endpoint come from the environment (no secrets in source).
# Export these before running, e.g. in a local untracked .env or your shell:
#   AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_ENDPOINT_URL
export AWS_ACCESS_KEY_ID="${AWS_ACCESS_KEY_ID:?set AWS_ACCESS_KEY_ID}"
export AWS_SECRET_ACCESS_KEY="${AWS_SECRET_ACCESS_KEY:?set AWS_SECRET_ACCESS_KEY}"
export AWS_ENDPOINT_URL="${AWS_ENDPOINT_URL:?set AWS_ENDPOINT_URL}"
export ECCODES_PYTHON_USE_FINDLIBS=1

WIN_START="2024-10-01"
WIN_END="2024-12-31"
STEP=4

# Generate "START END" block boundaries.
BLOCKS=$("$PROJ/.venv/Scripts/python.exe" - "$WIN_START" "$WIN_END" "$STEP" <<'PY'
import sys
from datetime import date, timedelta
s = date.fromisoformat(sys.argv[1]); end = date.fromisoformat(sys.argv[2]); step = int(sys.argv[3])
while s <= end:
    e = min(s + timedelta(days=step - 1), end)
    print(s.isoformat(), e.isoformat())
    s = e + timedelta(days=1)
PY
)

N=$(printf '%s\n' "$BLOCKS" | grep -c .)
echo "backfill OND-2024 | $N blocks of <=$STEP days | start $(date)" | tee "$PROG"
i=0
printf '%s\n' "$BLOCKS" | tr -d '\r' | while read -r S E; do
  [ -z "$S" ] && continue
  i=$((i+1))
  t0=$(date +%s)
  echo "=== [$i/$N] BLOCK $S .. $E START $(date) ===" >> "$LOG"
  if "$GIK" run-all --start "$S" --end "$E" >> "$LOG" 2>&1; then
    st="OK"
  else
    st="FAILED"
    echo "!!! [$i/$N] BLOCK $S..$E FAILED (continuing) ===" >> "$LOG"
  fi
  dt=$(( $(date +%s) - t0 ))
  echo "[$i/$N] $S..$E  $st  ${dt}s  $(date '+%H:%M:%S')" | tee -a "$PROG"
done
echo "ALL $N BLOCKS DONE $(date)" | tee -a "$PROG"
