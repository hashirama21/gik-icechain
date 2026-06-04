# GIK-IceChain — Run Results

## Configuration

| Parameter | Value |
|---|---|
| Config | `configs/default.yaml` |
| Storage | MinIO `http://20.116.218.195:9000` |
| Variables | `tp, 2t, 10u, 10v, ro` |
| C2 windows | 3, 6, 12, 24, 48, 72, 168 h |
| Return periods | 2, 5, 10, 20, 40, 100 yr |
| Ensemble members | 50/51 (1 filtered — divergent step count on some days) |
| Admin-1 units | 155 (East Africa) |

---

## Runs

### 2-day run — 2025-01-01/02

| Step | Duration | Workers |
|---|---|---|
| C1 convert | 5 min 35 s | 1 (sequential) |
| C2 exceedance | 13 min 44 s | 2 |
| C3 risk | 23 min | 1 |
| **Total** | **~42 min** | |

### 7-day run — 2025-01-01/07

| Step | Duration | Workers |
|---|---|---|
| C1 convert | 2 min 21 s | 1 (parquets cached from HuggingFace) |
| C2 exceedance | 58 min | 4 |
| C3 risk | ~28 min | 1 |
| **Total** | **~89 min** | |

---

## Produced data (final state)

### C1 — IceChunk store (MinIO)

| | |
|---|---|
| URI | `s3://gik-icechain/gik-icechain-store` |
| Snapshots | 10 (2024-10-01, 2024-10-15, 2024-12-01, 2025-01-01 → 2025-01-07) |
| MinIO objects | 303 (VirtualChunkRef manifests) |
| Size | 11.7 MB |
| Nature | Metadata only — GRIB2 files remain on public ECMWF S3 |

### C2 — Exceedance Zarr (MinIO)

| | |
|---|---|
| URI | `s3://gik-icechain/exceedance-zarr` |
| Objects | 26 |
| Size | ~20 KB (Zarr v3 compression) |
| Dimensions | `(date=7, latitude, longitude, window=6, return_period=5)` |
| Note | 40-yr return period absent from DJF CMORPH thresholds — skipped (warning, non-blocking) |

### C3 — Risk scores (local)

Refactored format: geometries separated from daily scores.

| | |
|---|---|
| Directory | `results/week_jan2025/admin1_risk/` |
| `admin1_boundaries.geojson` | 16.1 MB — **one shared file** |
| `{date}_risk_scores.json` | ~38.7 KB/day × 7 days = 271 KB |
| **Total 7 days** | **16.4 MB** (vs 113 MB old format) |
| Warnings | NaN on Djibouti + Eritrea Red Sea (low grid coverage in bbox) |

---

## Bug fixes

| Commit | File | Fix |
|---|---|---|
| `5bab24b` | `configs/default.yaml` | Set MinIO `endpoint_url` |
| `5bab24b` | `cli.py` | Replace `→` with `->` (Windows cp1252 encoding) |
| `5bab24b` | `cli.py` | Remove `exc_info=True` from C2 worker except block (Python 3.14 Unicode traceback crash) |
| `5bab24b` | `exceedance/writer.py` | Pass `endpoint_url` + `storage_options` to `xr.open_zarr` / `to_zarr` |
| `5bab24b` | `risk/risk_engine.py` | Pass `endpoint_url` + `storage_options` to `xr.open_zarr` |
| `e9c4860` | `risk/geojson_writer.py` | `admin1_name`: `admin1_name` → `shapeName` (actual GADM column) |
| `e9c4860` | `risk/geojson_writer.py` | `country`: `adm0_name` → `shapeGroup` (actual GADM column) |
| `e9c4860` | `risk/geojson_writer.py` | Separate geometries from scores: `write_boundaries()` + `write_risk_scores()` |
| `e9c4860` | `risk/risk_engine.py` | `.load()` after `.sel(date=...)` — pre-load into memory, avoids 4 MinIO round-trips |

---

## CI/CD overhaul

| Commit | File | Change |
|---|---|---|
| `3a21f0b` | `.github/actions/pipeline-setup/action.yml` | New DRY composite action (checkout + Python + pip install) |
| `3a21f0b` | `ci.yaml` | Enforce `--cov-fail-under=80`, remove double checkout, add `PYTHONIOENCODING=utf-8` |
| `3a21f0b` | `daily_update.yaml` | `timeout-minutes` on all cloud jobs, `[skip ci]` on git push, fix CLI commands |
| `3a21f0b` | `compact.yaml` | Dedicated workflow for monthly IceChunk compaction |
| `3a21f0b` | `release.yaml` | Validate git tag against `pyproject.toml` version |
| `3a21f0b` | `Dockerfile` | Add `eccodes`, non-root user, `pip install` without `-e`, copy `configs/` |
| `3a21f0b` | `job_c1/c2.yaml` | Fix CLI commands + `TARGET_DATE` env var, switch to `default.yaml` |
| `3a21f0b` | `docker-compose.yml` | Add `service_completed_successfully` condition on `depends_on` |

---

## Production extrapolation (1 200 days)

| Step | 7-day measured | 1 200-day estimate |
|---|---|---|
| C1 | 2 min 21 s | ~6.7 h (cached) / ~24 h (cold) |
| C2 | 58 min / 4 workers | ~166 h → Cloud Run Lithops (50 workers) |
| C3 | ~28 min | ~80 h |
| **C1 storage** | 11.7 MB | ~2 GB manifests |
| **C2 storage** | ~20 KB | ~3.4 MB compressed Zarr |
| **C3 storage** | 16.4 MB | **62 MB** (vs 19.3 GB old format — **311×**) |
