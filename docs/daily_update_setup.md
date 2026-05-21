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

## 6. Notes

- **macOS local testing**: the C1 step uses `date -u -d "yesterday"` (GNU `date`, Linux-only).
  On macOS use `date -u -v-1d +%Y-%m-%d` instead. GitHub Actions runners are Ubuntu so this
  does not affect CI.
- **`create_or_open`** already handles the append/overwrite distinction for the IceChunk store,
  so `--mode append` for C1 can be accepted and silently ignored if the store exists.
