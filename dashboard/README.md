# GIK-IceChain - Dashboard

Composant dashboard complet, aligné sur [`../DASHBOARD.md`](DASHBOARD.md).
Deux surfaces, **un seul contrat de données** alimenté par le pipeline (C1→C2→C3).

```
dashboard/
├── web/                  # Frontend Next.js (shell + storymaps MDX + MapLibre/TiTiler)
│   ├── src/app/          #   routes : shell (/) et storymaps (/stories/[slug])
│   ├── src/components/   #   map/ story/ ui/
│   ├── src/lib/          #   titiler · stac · api · risk · config
│   └── src/content/      #   storymaps MDX (1 par événement)
├── data_pipeline/        # Exporteur Python (sorties pipeline → contrat dashboard)
│   └── pipeline.py       #   Typer CLI : contract · cogs · gpm · emdat · stac · all
├── infra/titiler/        # TiTiler (Docker local + déploiement Lambda)
├── storymaps/            # titiler_config.yaml (config TiTiler Lambda)
└── GIK-IceChain-Dashboard-v4.html   # TEMPLATE de référence (ne pas modifier)
```

## Contrat de données (généré par `data_pipeline/`)

Statique (servi par GitHub Pages depuis `web/public/data/`) :
- `geojson/{code}.json` - boundaries admin-1 par pays (16 pays), `properties.name`.
- `{date}/region_risks.json` - `{pcode: {risk_state, risk_label, p_*}}`.
- `{date}/dependency.json` - par unité : `win[]`, `gev[win][rp]`, `confidence m/51`.
- `index.json` - dates disponibles + pire risque (calendrier).

Raster (S3 public + TiTiler) :
- `cogs/risk_{date}.tif`, `cogs/exceedance_{date}_{win}_{rp}.tif`, `cogs/gpm_{date}.tif`.
- `stac/catalog.json` + items (collections `gik-icechain-risk`, `gik-icechain-exceedance`).

## Démarrage rapide

```bash
# 1. Générer le contrat data depuis les sorties pipeline
python -m dashboard.data_pipeline.pipeline all \
  --results results/oneday_20251119/admin1_risk \
  --exceedance-store s3://gik-icechain/exceedance-zarr \
  --out dashboard/web/public --date 2025-11-19

# 2. TiTiler local (rasters)
docker compose -f dashboard/infra/titiler/docker-compose.yml up -d

# 3. Frontend
cd dashboard/web && npm install && npm run dev   # http://localhost:3000
```

## Niveaux d'ambition (incrémental, rien à jeter)

1. **Statique** : shell v4 + GeoJSON réels (aucun serveur).
2. **+ TiTiler** : rasters exceedance/GPM servis dynamiquement (Lambda ≈ 0 € idle).
3. **+ VEDA-UI/MDX** : storymaps scrollytelling par événement.
