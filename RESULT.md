# GIK-IceChain — Résultats de run

## Configuration

| Paramètre | Valeur |
|---|---|
| Config | `configs/default.yaml` |
| Stockage | MinIO `http://20.116.218.195:9000` |
| Variables | `tp, 2t, 10u, 10v, ro` |
| Fenêtres C2 | 3, 6, 12, 24, 48, 72, 168 h |
| Périodes de retour | 2, 5, 10, 20, 40, 100 ans |
| Membres ensemble | 50/51 (1 filtré — step count divergent sur certains jours) |
| Unités admin-1 | 155 (Est Afrique) |

---

## Runs effectués

### Run 2 jours — 2025-01-01/02

| Étape | Durée | Workers |
|---|---|---|
| C1 convert | 5 min 35 s | 1 (séquentiel) |
| C2 exceedance | 13 min 44 s | 2 |
| C3 risk | 23 min | 1 |
| **Total** | **~42 min** | |

### Run 7 jours — 2025-01-01/07

| Étape | Durée | Workers |
|---|---|---|
| C1 convert | 2 min 21 s | 1 (parquets en cache HF) |
| C2 exceedance | 58 min | 4 |
| C3 risk | ~28 min | 1 |
| **Total** | **~89 min** | |

---

## Données produites (état final)

### C1 — IceChunk store (MinIO)

| | |
|---|---|
| URI | `s3://gik-icechain/gik-icechain-store` |
| Snapshots | 10 (2024-10-01, 2024-10-15, 2024-12-01, 2025-01-01 → 2025-01-07) |
| Objets MinIO | 303 (manifests VirtualChunkRef) |
| Taille | 11.7 MB |
| Nature | Metadata uniquement — les GRIB2 restent sur ECMWF S3 public |

### C2 — Exceedance Zarr (MinIO)

| | |
|---|---|
| URI | `s3://gik-icechain/exceedance-zarr` |
| Objets | 26 |
| Taille | ~20 KB (compression Zarr v3) |
| Dimensions | `(date=7, latitude, longitude, window=6, return_period=5)` |
| Note | Période de retour 40 ans absente des seuils CMORPH DJF → skippée (warning, non bloquant) |

### C3 — Scores risk (local)

Format refactorisé : géométries séparées des scores journaliers.

| | |
|---|---|
| Répertoire | `results/week_jan2025/admin1_risk/` |
| `admin1_boundaries.geojson` | 16.1 MB — **1 seul fichier partagé** |
| `{date}_risk_scores.json` | ~38.7 KB / jour × 7 jours = 271 KB |
| **Total 7 jours** | **16.4 MB** (vs 113 MB ancien format) |
| Warnings | NaN sur Djibouti + Érythrée Mer Rouge (faible couverture grille) |

---

## Bugs corrigés

| Commit | Fichier | Correction |
|---|---|---|
| `5bab24b` | `configs/default.yaml` | `endpoint_url` MinIO renseigné |
| `5bab24b` | `cli.py` | `→` → `->` (encodage cp1252 Windows) |
| `5bab24b` | `cli.py` | `exc_info=True` retiré (crash workers Python 3.14 via traceback Unicode) |
| `5bab24b` | `exceedance/writer.py` | `endpoint_url` + `storage_options` sur `xr.open_zarr` / `to_zarr` |
| `5bab24b` | `risk/risk_engine.py` | `endpoint_url` + `storage_options` sur `xr.open_zarr` |
| `e9c4860` | `risk/geojson_writer.py` | `admin1_name` : `admin1_name` → `shapeName` (champ GADM réel) |
| `e9c4860` | `risk/geojson_writer.py` | `country` : `adm0_name` → `shapeGroup` (champ GADM réel) |
| `e9c4860` | `risk/geojson_writer.py` | Séparation géométries / scores — `write_boundaries()` + `write_risk_scores()` |
| `e9c4860` | `risk/risk_engine.py` | `.load()` après `.sel(date=...)` — pré-charge en mémoire, évite 4 round-trips MinIO |

---

## CI/CD refactorisé

| Commit | Fichier | Correction |
|---|---|---|
| `3a21f0b` | `.github/actions/pipeline-setup/action.yml` | Action composite DRY (checkout + Python + pip install) |
| `3a21f0b` | `ci.yaml` | `--cov-fail-under=80`, double checkout retiré, `PYTHONIOENCODING=utf-8` |
| `3a21f0b` | `daily_update.yaml` | `timeout-minutes` sur tous les jobs, `[skip ci]` git push, CLI corrigée |
| `3a21f0b` | `compact.yaml` | Workflow dédié compaction mensuelle |
| `3a21f0b` | `release.yaml` | Validation tag vs `pyproject.toml` |
| `3a21f0b` | `Dockerfile` | `eccodes` ajouté, user non-root, `pip install` sans `-e`, `configs/` copié |
| `3a21f0b` | `job_c1/c2.yaml` | Commandes CLI corrigées + `TARGET_DATE`, `default.yaml` |
| `3a21f0b` | `docker-compose.yml` | `service_completed_successfully` sur `depends_on` |

---

## Extrapolation production (1 200 jours)

| Étape | 7 jours mesuré | 1 200 jours estimé |
|---|---|---|
| C1 | 2 min 21 s | ~6.7 h (parquets en cache) / ~24 h (cold) |
| C2 | 58 min / 4 workers | ~166 h → Cloud Run Lithops (50 workers) |
| C3 | ~28 min | ~80 h |
| **Stockage C1** | 11.7 MB | ~2 GB manifests |
| **Stockage C2** | ~20 KB | ~3.4 MB Zarr compressé |
| **Stockage C3** | 16.4 MB | **62 MB** (vs 19.3 GB ancien format — 311×) |
