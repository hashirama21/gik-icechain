# GIK-IceChain v2.0

**Zero-Cost Cloud-Native Pipeline for Retrospective Flood Risk  
Decision Support in East Africa using the ECMWF IFS Ensemble**

> **ECMWF Code for Earth 2026 — Africa Stream (ArcX)**  
> Challenge 41: Missed Opportunities in Flood Disaster Risk Management  
> Mentors: Nishadh Kalladath · Masilin Gudoshava · Ahmed Amdihun · Anthony Mwanthi · Katherine Egan · Jessica Keune · Hillary Koros

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-green.svg)](https://python.org)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-black.svg)](https://github.com/astral-sh/ruff)
[![CI](https://github.com/code4earth-2026/gik-icechain/actions/workflows/ci.yaml/badge.svg)](https://github.com/code4earth-2026/gik-icechain/actions)
[![pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen)](https://pre-commit.com/)

---

## Overview

ECMWF's open IFS ensemble archive on AWS S3 (`s3://ecmwf-forecasts`) holds over
**1 petabyte** of GRIB2 weather forecast data: 51 ensemble members, 85 forecast steps
(0–360 h), and more than 1 000 forecast days since May 2023 — all freely available.
Yet this data is almost completely inaccessible to the xarray/Dask ecosystem that
East African disaster risk managers rely on.

GIK-IceChain v2.0 solves this in three components:

| Component | What it does | Key output |
|-----------|-------------|-----------|
| **C1 — Conversion** | GIK Parquet manifests → VirtualiZarr → IceChunk Zarr v3 virtual store | Single `zarr.open()` access to 1 000+ days, zero data duplication |
| **C2 — Exceedance** | Retrospective rainfall exceedance probabilities for all ~1 000 days | Multi-dim Zarr: `date × lat × lon × window × return_period` |
| **C3 — Risk (CRMA)** | Admin-1 daily flood risk using ICPAC's CRMA Bayesian Network | Daily GeoJSON risk layer, integrated in calendar-map storymaps |

### Key numbers

| Metric | Value |
|--------|-------|
| Archive size | ~1 PB (raw GRIB2) |
| Virtual store size | ~18.5 GB (metadata only, **48 000× compression**) |
| Days covered | ~1 200 (May 2023 – Aug 2026) |
| Ensemble members | 51 |
| Accumulation windows | 7 (3 h → 7 days) |
| Return-period thresholds | 6 (2, 5, 10, 20, 40, 100 years) |
| Admin-1 units | ~300 (11 East African countries) |

---

## Architecture

```
╔══════════════════════════════════════════════════════════════════════════╗
║                    GIK-IceChain v2.0 — Pipeline Overview                 ║
╠══════════════════════════════════════════════════════════════════════════╣
║                                                                          ║
║  AWS S3 (s3://ecmwf-forecasts)          HuggingFace                     ║
║  ┌─────────────────────────────┐        ┌─────────────────────────┐     ║
║  │  ~1 PB GRIB2 (raw, static) │        │ E4DRR/gik-ecmwf-par     │     ║
║  │  51 members × 85 steps     │        │ 150 246 Parquet files   │     ║
║  │  ~1 000 days since May 2023│        │ 18.5 GB (metadata only) │     ║
║  └─────────────────────────────┘        └────────────┬────────────┘     ║
║           ▲ byte-range reads only                    │                  ║
║           │                             C1: VirtualiZarr + IceChunk     ║
║           │                                          │                  ║
║           │◄─────────────────────────────────────────┘                  ║
║                                                      │                  ║
║                              ┌───────────────────────▼───────────────┐  ║
║                              │  IceChunk Zarr v3 Virtual Store       │  ║
║                              │  (public S3 bucket — metadata only)   │  ║
║                              │  zarr.open(store)  ← single call      │  ║
║                              │  Full time-travel version history      │  ║
║                              └───────────────┬───────────────────────┘  ║
║                                              │                          ║
║                    ┌─────────────────────────┼─────────────────┐        ║
║                    │                         │                 │        ║
║          C2: Exceedance                   CMORPH            GPM IMERG   ║
║          ┌──────────────────┐          (thresholds)       (observations)║
║          │ Dask + xarray    │               │                 │        ║
║          │ 7 windows        │◄──────────────┘                 │        ║
║          │ 6 return periods │                                  │        ║
║          │ GEV adaptive     │                                  │        ║
║          └────────┬─────────┘                                  │        ║
║                   │                                            │        ║
║                   │         C3: CRMA-Live                      │        ║
║          ┌────────▼──────────────────────────────────┐         │        ║
║          │ Dynamic Bayesian Network (pgmpy)           │◄────────┘        ║
║          │ API node (soil saturation persistence)    │                  ║
║          │ EM-DAT CPT refinement                     │                  ║
║          │ → Admin-1 traffic-light risk (300 units)  │                  ║
║          └────────────────────┬──────────────────────┘                  ║
║                               │                                         ║
║          ┌────────────────────▼──────────────────────┐                  ║
║          │  Dashboard: Calendar-Map + VEDA Storymaps  │                  ║
║          │  GitHub Pages + TiTiler (AWS Lambda)       │                  ║
║          │  One cell per day → click → storymap       │                  ║
║          └───────────────────────────────────────────┘                  ║
╚══════════════════════════════════════════════════════════════════════════╝
```

---

## Components

### Component 1 — GIK to IceChunk Conversion

Converts the 150 246 GIK Parquet reference files (already available on
[HuggingFace E4DRR/gik-ecmwf-par](https://huggingface.co/datasets/E4DRR/gik-ecmwf-par))
into a fully interoperable **IceChunk Zarr v3 virtual store** — with zero data
duplication. The original GRIB2 objects on S3 are never copied; only byte-range
references are stored.

**Innovation 1 — IceChunk Time-Travel Audit Trail**: every daily GIK batch
is committed as a new IceChunk snapshot. Users can check out the store as it
existed on any past date, enabling reproducible "what would we have known on
date X?" retrospective queries — directly applicable to anticipatory action protocols.

### Component 2 — Retrospective Exceedance Analysis

For each of ~1 000 forecast days, 51 ensemble members, 7 accumulation windows,
and 6 return-period thresholds:

- Loads the virtual store lazily via Dask
- Computes rolling precipitation accumulations
- Compares against **adaptive GEV thresholds** stratified by season and IOD/ENSO
  phase (Innovation 2 — substantially reduces false alarm rates in dry regimes)
- Outputs a multi-dimensional Zarr v3 store

**Innovation 3 — AIFS vs IFS ENS Parallel Track**: runs the identical pipeline
on AIFS ENS output (available via ICPAC's SEWAA-forecasts pipeline), producing
the first systematic comparison of AI-NWP vs physics-based NWP flood signal over
East Africa.

### Component 3 — Admin-1 Risk Assessment (CRMA-Live)

Integrates ICPAC's [CRMA prototype](https://meetingorganizer.copernicus.org/EGU24/EGU24-6843.html)
(Bayesian Network, pgmpy) with two innovations:

**Innovation 4 — Dynamic BN with API persistence**: adds an Antecedent
Precipitation Index (API) node that carries soil moisture state across days,
capturing multi-day compound flood risk.

**Innovation 5 — EM-DAT CPT refinement**: uses historical EM-DAT flood events
to refine the Bayesian Network Conditional Probability Tables via maximum
likelihood estimation.

Output: daily admin-1 GeoJSON risk files, integrated directly into the
calendar-map storymaps and exportable to the
[East Africa Hazard Watch Portal](https://www.icpac.net/hazard-watch/).

---

## Quick Start

```bash
# Clone
git clone https://github.com/code4earth-2026/gik-icechain.git
cd gik-icechain

# Create environment
conda env create -f environment.yml
conda activate gik-icechain

# Or with pip
pip install -e ".[dev]"

# Run the full pipeline for a 30-day test window
python -m gik_icechain run-all \
  --start 2024-10-01 \
  --end   2024-10-31 \
  --region east_africa \
  --output results/

# Run only Component 1 (conversion)
python -m gik_icechain convert --help

# Run only Component 2 (exceedance)
python -m gik_icechain exceedance --help

# Run only Component 3 (risk assessment)
python -m gik_icechain risk --help

# Launch the dashboard locally
python -m gik_icechain dashboard --port 8080
```

---

## Installation

### Requirements

- Python 3.11+
- (Optional) GPU: not required — pipeline runs on CPU
- Storage: ~50 GB for calibration data (ERA5 subset) + ~5 GB for results

### Full installation

```bash
# 1. Clone
git clone https://github.com/code4earth-2026/gik-icechain.git
cd gik-icechain

# 2. Install with all dependencies
pip install -e ".[full]"

# 3. Configure AWS credentials (read-only access to s3://ecmwf-forecasts)
aws configure --profile ecmwf-open
# AWS Access Key: <your key>
# AWS Secret Key: <your secret>
# Region: eu-west-1
# Output: json

# 4. (Optional) Configure output S3 bucket for IceChunk store
export GIK_OUTPUT_BUCKET="s3://your-bucket/gik-icechain"

# 5. Run tests
pytest tests/ -v

# 6. Download admin-1 boundaries and CMORPH thresholds
python scripts/download_data.py --component all
```

### Docker

```bash
docker build -t gik-icechain:latest .
docker run --rm \
  -e AWS_PROFILE=ecmwf-open \
  -v ~/.aws:/root/.aws:ro \
  -v $(pwd)/results:/results \
  gik-icechain:latest \
  python -m gik_icechain run-all --start 2024-10-01 --end 2024-10-31
```

---

## Data Sources

| Dataset | Description | Access | Used in |
|---------|-------------|--------|---------|
| `s3://ecmwf-forecasts` | ECMWF IFS ensemble (51 members, 85 steps, GRIB2) | Public, no auth | C1, C2 |
| `E4DRR/gik-ecmwf-par` | GIK Parquet references (HuggingFace) | Public | C1 |
| `dynamical.org IceChunk` | Full-copy Zarr v3 IFS ENS (benchmark baseline) | Public | C1 benchmark |
| `E4DRR/virtualizarr-stores` | CMORPH return-period thresholds | Public | C2 |
| GPM IMERG v7 | Satellite precipitation estimates | NASA, public | C2, C3 |
| EM-DAT | Global disaster database (flood events) | Free registration | C2 validation, C3 |
| OCHA/GADM admin-1 | East Africa administrative boundaries | Public | C3 |
| ERA5 (CDS) | Reanalysis for ENSO/IOD phase classification | Free (CDS account) | C2 thresholds |

---

## Running the Full Pipeline

### Step-by-step (production)

```bash
# 1. Convert GIK Parquet → IceChunk (Component 1)
python -m gik_icechain convert \
  --hf-dataset E4DRR/gik-ecmwf-par \
  --output-store s3://your-bucket/gik-icechain-store \
  --start 2024-03-01 \
  --end   2026-03-07 \
  --workers 16

# 2. Fill archive gap (May 2023 – Feb 2024)
python scripts/run_gap_fill.py \
  --s3-source s3://ecmwf-forecasts \
  --output-parquet s3://your-bucket/gik-parquet-gap \
  --start 2023-05-01 \
  --end   2024-02-28

# 3. Compute exceedance probabilities (Component 2)
python -m gik_icechain exceedance \
  --store s3://your-bucket/gik-icechain-store \
  --thresholds data/cmorph_thresholds/ \
  --output s3://your-bucket/exceedance-zarr \
  --region east_africa \
  --workers 32

# 4. Run CRMA-Live risk assessment (Component 3)
python -m gik_icechain risk \
  --exceedance-store s3://your-bucket/exceedance-zarr \
  --gpm-path data/gpm_imerg/ \
  --admin-boundaries data/admin_boundaries/east_africa_admin1.gpkg \
  --emdat data/emdat/east_africa_floods.csv \
  --output results/admin1_risk/

# 5. Generate dashboard
python dashboard/storymaps/generate_storymaps.py \
  --exceedance-store s3://your-bucket/exceedance-zarr \
  --risk-dir results/admin1_risk/ \
  --output dashboard/calendar_map/data/
```

### Dask configuration

For large-scale runs, configure Dask via `configs/default.yaml`:

```yaml
dask:
  scheduler: distributed
  n_workers: 32
  threads_per_worker: 2
  memory_limit: "8GB"
  dashboard_address: ":8787"
```

---

## Innovations

| # | Name | Description | Benefit |
|---|------|-------------|---------|
| 1 | **IceChunk Time-Travel Audit** | Daily IceChunk commits → queryable historical store snapshots | Enables 'as-of date X' retrospective queries for anticipatory action |
| 2 | **Adaptive GEV Thresholds** | Return-period thresholds stratified by season + IOD/ENSO phase | Reduces false alarm rate; improves detection in wet regimes |
| 3 | **AIFS vs IFS ENS Parallel** | Same pipeline applied to AIFS ENS | First systematic AI-NWP flood signal evaluation in East Africa |
| 4 | **CRMA-Live Dynamic BN** | API persistence node in Bayesian Network | Captures multi-day compound flood risk |
| 5 | **EM-DAT CPT Refinement** | Learn CPTs from historical EM-DAT events | Data-driven improvement of risk model over time |

---

## Benchmarks

Preliminary benchmarks (30-day window, East Africa domain):

| Approach | Storage | Time-to-first-byte | Full-scan (30 days) | S3 egress cost |
|----------|---------|--------------------|---------------------|---------------|
| GIK + IceChunk | **18.5 GB** (metadata) | < 2 s | ~45 min (32 vCPU) | ~$0.02/day |
| dynamical.org full copy | ~242 TB | < 1 s | ~20 min (32 vCPU) | ~$0.02/day |
| Herbie (on-the-fly) | 0 | > 60 s | Not feasible | High |

Full benchmark results: [`results/benchmarks/`](results/benchmarks/)  
Benchmark notebook: [`notebooks/04_benchmark_report.ipynb`](notebooks/04_benchmark_report.ipynb)

---

## Dashboard

The interactive calendar-map is deployed at:
**https://code4earth-2026.github.io/gik-icechain/**

- Each cell = one forecast day, coloured by peak East Africa exceedance signal
- Click any day → VEDA-UI storymap showing:
  - Spatial exceedance map (colour-graded by return period)
  - GPM IMERG observed rainfall
  - EM-DAT flood event overlays
  - Admin-1 CRMA-Live risk layer
  - ENSO/IOD phase metadata

---

## Contributing

Contributions are welcome. Please see [CONTRIBUTING.md](CONTRIBUTING.md).

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Install pre-commit hooks
pre-commit install

# Run full test suite
make test

# Run linters
make lint
```

---

## Citation

```bibtex
@software{gik_icechain_2026,
  title   = {{GIK-IceChain v2.0}: Zero-Cost Cloud-Native Pipeline for
             Retrospective Flood Risk Decision Support in East Africa},
  author  = {{Code for Earth 2026 Team}},
  year    = {2026},
  url     = {https://github.com/code4earth-2026/gik-icechain},
  license = {Apache-2.0},
  note    = {ECMWF Code for Earth 2026 — Challenge 41, Africa Stream (ArcX)}
}
```

---

## License

Apache License 2.0 — see [LICENSE](LICENSE)

This project was developed as part of the
[ECMWF Code for Earth 2026](https://codeforearth.ecmwf.int/) programme,
Africa Stream, funded by the EU ArcX programme.
