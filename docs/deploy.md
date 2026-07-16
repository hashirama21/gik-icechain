# Production Deployment Guide

End-to-end setup for the GIK-IceChain pipeline in production: storage, secrets,
reference data, the daily forecast pipeline (C1 → C2 → C3), the dashboard
(GitHub Pages + S3), validation, and local development.

> Quick map: `ci.yaml` (tests/lint), `daily_update.yaml` (daily C1→C2→C3 + dashboard,
> cron `0 8 * * *`), `e2e_run.yaml` (manual full-pipeline test), `deploy-web.yaml`
> (dashboard → GitHub Pages, on push to `main`), `compact.yaml` (monthly IceChunk GC),
> `build-thresholds.yaml` (GEV thresholds). Per-component CLI details:
> [`daily_update_setup.md`](daily_update_setup.md).

---

## 1. Prerequisites

| Tool | Why | Notes |
|------|-----|-------|
| Python 3.12 | pipeline runtime | `pip install -e ".[dev]"` (extras: `dev`, `cloud`, `viz`, `full`) |
| eccodes (C library) | decode ECMWF GRIB2 | Linux: `apt-get install -y libeccodes0 libeccodes-tools`; set `ECCODES_PYTHON_USE_FINDLIBS=1`. Windows: place the eccodes DLLs in `.venv/Scripts/` (extract from the `eccodes` cp312 wheel) |
| Node 20+ | build the dashboard | only for `dashboard/web` |
| An AWS S3 bucket | pipeline outputs | `s3://gik-icechain` on `eu-north-1` (see §2) |

The only **external read source** is the public ECMWF IFS ENS archive on
`s3://ecmwf-forecasts` (anonymous); everything the pipeline *writes* goes to your store.

---

## 2. Storage backend (AWS S3)

Three logical stores are needed (one bucket with prefixes, or separate buckets):

| Store | Default URI (`configs/default.yaml`) | Holds |
|-------|--------------------------------------|-------|
| IceChunk virtual store | `s3://gik-icechain/gik-icechain-store` | C1 chunk manifests |
| Exceedance Zarr | `s3://gik-icechain/exceedance-zarr` | C2 exceedance probabilities |
| Bucket (`$GIK_BUCKET`) | `gik-icechain` | C3 risk JSON (`admin1_risk/`), CMORPH thresholds, `dashboard-data/`, COGs |

C2 can also read the published E4DRR full-archive store on Source Cooperative
instead of the C1 store (`configs/published_store.yaml`, anonymous, no bucket
needed on your side).

Create the bucket(s) in your region (the workflows use `eu-north-1`). For the public
dashboard/COG assets, apply a public-read policy - see
[`deploy/aws/public_store_bucket_policy.json`](../deploy/aws/public_store_bucket_policy.json)
and add a CORS rule allowing `GET` from the Pages origin (needed for `dashboard-data/`,
see §6).

Local credentials: export your access keys from the AWS console to
`develop_accessKeys.csv` at the repo root (gitignored), then
`python scripts/load_aws_credentials.py` prints the exports
(`--powershell` for PowerShell) and notebooks/scripts load it directly.

---

## 3. GitHub Secrets

**Settings → Secrets and variables → Actions → New repository secret.**

| Secret | Used by | Value |
|--------|---------|-------|
| `AWS_ACCESS_KEY_ID` | daily, e2e, ci, deploy syncs | write key for your store |
| `AWS_SECRET_ACCESS_KEY` | "" | matching secret |
| `GIK_ICECHUNK_STORE_URI` | C1/C2 | e.g. `s3://gik-icechain/gik-icechain-store` |
| `GIK_EXCEEDANCE_STORE_URI` | C2/C3 | e.g. `s3://gik-icechain/exceedance-zarr` |
| `GIK_BUCKET` | C3, dashboard | bucket name only, e.g. `gik-icechain` |
| `EARTHDATA_USER` / `EARTHDATA_PASSWORD` | GPM IMERG (NASA) download | optional; without them C3 falls back to CHIRPS |

> Note: ECMWF virtual-chunk reads are forced to public AWS S3 internally
> (`icechunk_writer._build_repo_config`), so a custom `AWS_ENDPOINT_URL` does
> not misroute them.

---

## 4. GitHub Variables & Environment

**Variables** (Settings → Secrets and variables → Actions → *Variables*) - consumed by
`deploy-web.yaml` at build time:

| Variable | Value | Effect |
|----------|-------|--------|
| `DATA_BASE` | `https://<bucket>.s3.<region>.amazonaws.com/dashboard-data` | where the web app fetches the risk contract (else falls back to the empty bundled `/data`) |
| `COG_BASE` | `https://<cog-bucket>.s3.<region>.amazonaws.com/cogs` | TiTiler COG base |
| `TITILER_BASE` | TiTiler endpoint URL | raster tiles |

**Environment**: create a `production` environment (Settings → Environments). The
`daily_update.yaml` jobs declare `environment: production` and will not start without it.

---

## 5. Reference data + the pipeline run

All prerequisites come from public sources and are idempotent:

```bash
python scripts/tools.py download --component all      # admin boundaries, CMORPH RPs, ENSO/IOD
python scripts/tools.py download-thresholds           # pre-fitted GEV threshold NetCDFs
python scripts/tools.py download-gpm --source chirps --start <D-14> --end <D>   # C3 observed rain
```

Run the full pipeline (C1 → C2 → C3) for a date range:

```bash
export ECCODES_PYTHON_USE_FINDLIBS=1
gik-icechain run-all --start <D> --end <D> --config configs/default.yaml --output results/
```

Or per component: `gik-icechain convert` (C1), `gik-icechain exceedance` (C2),
`gik-icechain risk` (C3). The CLI flags are documented in
[`daily_update_setup.md`](daily_update_setup.md).

**Automated (cron `0 8 * * *`)** - `daily_update.yaml` chains:
`ingest-daily-gik` (C1) → `update-exceedance` (C2) → `update-risk` (C3, syncs
`admin1_risk/` to `s3://$GIK_BUCKET/`) → `update-dashboard` → `notify-failure`.
The date is "yesterday"; ECMWF S3 retention is ~15 months.

**Manual full-pipeline test** - run the `E2E Pipeline Run` workflow
(`workflow_dispatch`, `start_date`/`end_date`). It uses the **isolated**
`configs/ci_e2e.yaml` (writes to `e2e-test-store`, never the production store),
then validates EM-DAT, then renders a dashboard preview into the artifact.

---

## 6. Dashboard → GitHub Pages

The Next.js dashboard is a **static export**; the per-day risk **contract** is built
by `dashboard/data_pipeline` and **served from S3** (never committed).

**One-time repo setup:**
1. **Enable Pages**: Settings → Pages → **Source = "GitHub Actions"** (without this
   `deploy-pages` fails with `404`).
2. Set the `DATA_BASE` variable (§4) to the public `dashboard-data` URL.
3. Make `s3://$GIK_BUCKET/dashboard-data/` **public-read + CORS** (`GET` from the Pages origin).

**Publish flow** (automated in `daily_update.yaml` `update-dashboard`):
```bash
# pull existing contract (keeps index.json history) + the day's risk, rebuild, push:
aws s3 sync s3://$GIK_BUCKET/dashboard-data/ dashboard/web/public/data/
aws s3 sync s3://$GIK_BUCKET/admin1_risk/    results/admin1_risk/
python -m dashboard.data_pipeline.pipeline contract --exceedance-store $GIK_EXCEEDANCE_STORE_URI \
    --results results/admin1_risk --out dashboard/web/public --date <D>
python -m dashboard.data_pipeline.pipeline geojson \
    --boundaries results/admin1_risk/admin1_boundaries.geojson --out dashboard/web/public
aws s3 sync dashboard/web/public/data/ s3://$GIK_BUCKET/dashboard-data/
```

**Deploy** (`deploy-web.yaml`, on push to `main`): `npm ci` → `npm run build` (static
export to `out/`, with `NEXT_PUBLIC_BASE_PATH=/gik-icechain` + `NEXT_PUBLIC_DATA_BASE`)
→ `.nojekyll` → `upload-pages-artifact` → `deploy-pages`.

**Live URL:** `https://<owner>.github.io/<repo>/` - e.g.
**https://hashirama21.github.io/gik-icechain/**.

---

## 7. Validation

```bash
python scripts/tools.py validate-emdat --risk-dir results/admin1_risk \
    --start <D> --end <D> --event-level --lead-days 1
```
Outputs precision/recall/F1/AUC (unit-day, vs `data/emdat/east_africa_floods.csv`) plus
event-level early-detection recall. See [`ISSUES.md`](ISSUES.md) for the interpretation
(structural recall ceiling, ISSUE-22…24).

---

## 8. Local development

```bash
pip install -e ".[dev]"
pre-commit install            # REQUIRED once per clone - enables the ruff + mypy hooks
```

Local credentials: the code reads the standard `AWS_*` env vars and does
**not** auto-load any file. Export your keys from `develop_accessKeys.csv`
(repo root, gitignored):

```bash
eval "$(python scripts/load_aws_credentials.py)"
export ECCODES_PYTHON_USE_FINDLIBS=1
```

`mypy` is pinned (`mypy==2.1.0` + `pandas-stubs`) so the pre-commit hook matches CI.
The single end-to-end notebook is `notebooks/gik_icechain_walkthrough.ipynb`.

---

## 9. Deployment checklist

- [ ] S3 buckets created (§2)
- [ ] 8-10 repo Secrets set (§3)
- [ ] 3 repo Variables set (§4)
- [ ] `production` environment created (§4)
- [ ] Reference data + thresholds downloaded / available to the runners (§5)
- [ ] `daily_update.yaml` green (or run once manually)
- [ ] GitHub Pages **Source = "GitHub Actions"** (§6)
- [ ] `dashboard-data` bucket public-read + CORS, `DATA_BASE` variable set (§6)
- [ ] Dashboard reachable at the Pages URL with a populated calendar
