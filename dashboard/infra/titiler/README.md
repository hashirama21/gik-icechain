# TiTiler - raster tiling for GIK-IceChain storymaps

TiTiler turns the COGs produced by `data_pipeline/pipeline.py cogs` into on-the-fly
`{z}/{x}/{y}` tiles consumed by MapLibre in the storymaps. **Only raster layers**
(exceedance, GPM) go through TiTiler - the admin-1 risk shell is vector GeoJSON.

## Local development

```bash
# 1. Generate COGs into web/public/cogs/
python -m dashboard.data_pipeline.pipeline cogs \
  --results results/oneday_20251119/admin1_risk \
  --exceedance-store s3://gik-icechain/exceedance-zarr \
  --out dashboard/web/public --date 2025-11-19

# 2. Start TiTiler
docker compose -f dashboard/infra/titiler/docker-compose.yml up -d

# 3. Test
open "http://localhost:8000/cog/viewer?url=/data/cogs/risk_2025-11-19.tif"
```

Point the frontend at it with `NEXT_PUBLIC_TITILER_BASE=http://localhost:8000`.

## Production (AWS Lambda) - zero idle cost

Config lives in [`../../storymaps/titiler_config.yaml`](../../storymaps/titiler_config.yaml)
(Lambda `gik-icechain-titiler`, GDAL layer, COG bucket `gik-icechain-cogs`,
colormaps `risk_levels` + `ylorrd`, CORS `*`).

Deploy with the official TiTiler Lambda recipe
(<https://developmentseed.org/titiler/deployment/aws/lambda/>):

```bash
# Mirror COGs to the public bucket the storymap reads
aws s3 sync dashboard/web/public/cogs s3://gik-icechain-cogs/cogs/

# Then deploy the TiTiler Lambda + API Gateway (CDK/SAM stack) and set
# NEXT_PUBLIC_TITILER_BASE to the API Gateway URL.
```

Lambda is billed per request → ~$0 when idle, consistent with the project's
zero-cost framing.
