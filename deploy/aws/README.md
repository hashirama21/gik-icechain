# AWS deployment - GIK gap-fill (ISSUE-9)

Deployment target is **AWS** (the former GCP Cloud Run / Lithops config has been
removed). The `gap-fill` CLI is infrastructure-agnostic and resume-safe (it
skips dates already committed to the IceChunk store), so it can run anywhere
that has the package + AWS credentials. Two supported paths:

The image `ENTRYPOINT` is `gik-icechain`, so a Batch `command` is args to that
CLI. `gap-fill` (resume-safe skip logic) lives in `scripts/tools.py`, not the
`gik-icechain` CLI - so Batch runs `gik-icechain convert` over the range, and
the resume-safe path is the CLI option below.

## Option 1 - AWS Batch (C1 ingest over a range)
```bash
aws batch register-job-definition --cli-input-json file://deploy/aws/batch_job_definition.json
aws batch submit-job \
  --job-name gik-ingest-2023 \
  --job-queue gik-icechain-queue \
  --job-definition gik-icechain-gapfill \
  --parameters start=2023-05-01,end=2024-02-29,store=s3://gik-icechain/gik-icechain-store
```
Replace `ACCOUNT_ID` and the IAM role ARNs in `batch_job_definition.json`. The
job role needs read on `s3://ecmwf-forecasts` (public) and read/write on the
IceChunk store bucket. Submit only missing dates, or re-run (commits are
idempotent per date).

## Option 2 - Lithops on AWS Lambda (fan-out, one Lambda per day)
Edit `deploy/aws/lithops_config.yaml` (`ACCOUNT_ID`, role, bucket), then drive
the ingest through Lithops. AWS Lambda's 15-min limit is fine for single-day C1.

## Option 3 - resume-safe gap-fill CLI (EC2 / local)
```bash
python scripts/tools.py gap-fill --start 2023-05-01 --end 2024-02-29 \
  --store s3://gik-icechain/gik-icechain-store
# In the Docker image (entrypoint override):
docker run --entrypoint python ghcr.io/hashirama21/gik-icechain:latest \
  scripts/tools.py gap-fill --start 2023-05-01 --end 2024-02-29 --store s3://...
```
Skips dates already committed; re-running continues where it stopped.

## Credentials
Use the standard AWS chain (instance role, `~/.aws`, or `AWS_ACCESS_KEY_ID` /
`AWS_SECRET_ACCESS_KEY` env). Never commit keys.

## Deliverables hosting (ISSUE-19)
- **Dashboard** → GitHub Pages via `.github/workflows/deploy-dashboard.yaml`
  (publishes `dashboard/calendar_map/`; the daily pipeline writes its `data/`).
  Enable Pages once: repo Settings → Pages → Source = GitHub Actions.
- **Public IceChunk store** → apply `public_store_bucket_policy.json` to the
  store bucket (adjust the bucket name), or register it on AWS Open Data.
- **TiTiler tiles** → deploy the official TiTiler AWS Lambda stack
  (https://developmentseed.org/titiler/deployment/aws/) and point the storymap
  `titiler_config.yaml` endpoint at it.
