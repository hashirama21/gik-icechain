#!/usr/bin/env bash
# Publish the dashboard data contract to S3 with per-tier Cache-Control.
#
# The browser refetched every file on every visit because `aws s3 sync` sets no
# cache metadata. The three tiers below match how often each file actually
# changes. Note that sync skips unchanged objects, so a header change alone does
# not re-upload: run once with --metadata-directive REPLACE to backfill.
#
# Usage: publish_dashboard_data.sh <local-data-dir> <s3-uri> [extra aws args...]
set -euo pipefail

SRC="${1:?usage: publish_dashboard_data.sh <local-data-dir> <s3-uri> [aws args...]}"
DEST="${2:?usage: publish_dashboard_data.sh <local-data-dir> <s3-uri> [aws args...]}"
shift 2

# Admin-1 boundaries: same source every run, and the bulk of the payload.
aws s3 sync "$SRC/geojson/" "${DEST%/}/geojson/" "$@" \
  --cache-control "public, max-age=604800"

# Per-date contract: fixed once produced, but a date can be re-scored.
aws s3 sync "$SRC" "$DEST" "$@" \
  --exclude "geojson/*" --exclude "index.json" \
  --cache-control "public, max-age=3600"

# index.json gains a date on every run.
aws s3 sync "$SRC" "$DEST" "$@" \
  --exclude "*" --include "index.json" \
  --cache-control "public, max-age=300, must-revalidate"
