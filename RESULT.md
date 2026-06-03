# GIK-IceChain — Résultats de run (2025-01-01 / 2025-01-02)

## Configuration

| Paramètre | Valeur |
|---|---|
| Config | `configs/default.yaml` |
| Stockage | MinIO `http://20.116.218.195:9000` |
| Plage | 2025-01-01 → 2025-01-02 (2 jours) |
| Variables | `tp, 2t, 10u, 10v, ro` |
| Fenêtres | 3, 6, 12, 24, 48, 72, 168 h |
| Périodes de retour | 2, 5, 10, 20, 40, 100 ans |
| Membres ensemble | 50 (1 membre filtré — step count divergent) |

---

## Durées d'exécution

| Étape | Description | Durée |
|---|---|---|
| **C1** — convert | ECMWF GRIB2 → IceChunk virtual store | ~5 min 35 s |
| **C2** — exceedance | Probabilités d'exceedance GEV adaptatives | ~13 min 44 s |
| **C3** — risk | Réseau bayésien CRMA → GeoJSON admin-1 | ~23 min |
| **Total pipeline** | | **~42 min** |

---

## Données produites

### C1 — IceChunk store (MinIO)

| | |
|---|---|
| URI | `s3://gik-icechain/gik-icechain-store` |
| Objets | 228 (manifests VirtualChunkRef) |
| Taille | 8.8 MB |
| Nature | Metadata uniquement — les GRIB2 restent sur ECMWF S3 public |

### C2 — Exceedance Zarr (MinIO)

| | |
|---|---|
| URI | `s3://gik-icechain/exceedance-zarr` |
| Objets | 38 |
| Taille | 17.3 KB (compression Zarr v3) |
| Dimensions | `(date=2, latitude, longitude, window=6, return_period=5)` |
| Note | La période de retour 40 ans absente des seuils CMORPH pour DJF → skippée |

### C3 — GeoJSON risk (local)

| | |
|---|---|
| Répertoire | `results/jan2025/admin1_risk/` |
| Fichiers | `2025-01-01_admin1_risk.geojson`, `2025-01-02_admin1_risk.geojson` |
| Taille | 17 MB / fichier — **33 MB total** |
| Unités | 155 features / jour (admin-1 Est Afrique) |
| Warnings | NaN sur quelques zones (Djibouti, Érythrée Mer Rouge) — faible couverture grille dans le bbox |

---

## Corrections appliquées lors du run

| Fichier | Correction |
|---|---|
| `configs/default.yaml` | `endpoint_url` renseigné avec l'adresse MinIO |
| `src/gik_icechain/cli.py` | Remplacement `→` par `->` (encodage Windows cp1252) |
| `src/gik_icechain/cli.py` | `exc_info=True` retiré du bloc except C2 (crash workers subprocess Python 3.14) |
| `src/gik_icechain/exceedance/writer.py` | Ajout `endpoint_url` + `storage_options` sur `xr.open_zarr` / `to_zarr` |
| `src/gik_icechain/risk/risk_engine.py` | Ajout `endpoint_url` + `storage_options` sur `xr.open_zarr` |

---

## Extrapolation production (1 200 jours)

| Étape | 2 jours | 1 200 jours (estimé) |
|---|---|---|
| C1 | ~6 min | ~60 h → Cloud Run / Lithops (50 workers) |
| C2 | ~14 min | ~140 h → parallélisme max_workers |
| C3 | ~23 min | ~230 h |
| **Stockage C3** | 33 MB | ~20 GB GeoJSON |
