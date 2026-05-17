## Project Structure

```
gik-icechain/
│
├── README.md
├── LICENSE
├── CITATION.cff
├── CHANGELOG.md
├── pyproject.toml                   # Project metadata + dependencies
├── environment.yml                  # Conda environment
├── Makefile                         # Common tasks
├── .pre-commit-config.yaml          # Code quality hooks
│
├── src/
│   └── gik_icechain/
│       ├── __init__.py
│       ├── __main__.py              # CLI entry point
│       ├── cli.py                   # Click CLI definitions
│       │
│       ├── component1_conversion/   # C1: GRIB2 → IceChunk
│       │   ├── __init__.py
│       │   ├── gik_loader.py        # Load GIK Parquet manifests
│       │   ├── virtualizer.py       # VirtualiZarr integration
│       │   ├── icechunk_writer.py   # Write IceChunk store + time-travel
│       │   ├── gap_filler.py        # Fill 2023 archive gap (GIK re-run)
│       │   └── benchmark.py        # GIK+IceChunk vs dynamical.org
│       │
│       ├── component2_exceedance/   # C2: Exceedance analysis
│       │   ├── __init__.py
│       │   ├── loader.py            # Lazy-load IceChunk store via Dask
│       │   ├── accumulations.py     # Rolling precipitation accumulations
│       │   ├── thresholds.py        # Adaptive GEV return-period thresholds
│       │   ├── exceedance.py        # Empirical exceedance computation
│       │   ├── aifs_track.py        # AIFS vs IFS ENS parallel comparison
│       │   └── writer.py            # Output multi-dim Zarr v3
│       │
│       ├── component3_risk/         # C3: CRMA-Live admin-1 risk
│       │   ├── __init__.py
│       │   ├── crma_model.py        # Bayesian Network (pgmpy) definition
│       │   ├── dynamic_bn.py        # Dynamic BN + API persistence node
│       │   ├── cpt_refinement.py    # EM-DAT CPT refinement via MLE
│       │   ├── gpm_loader.py        # GPM IMERG observation loader
│       │   ├── aggregator.py        # Admin-1 spatial aggregation
│       │   ├── risk_engine.py       # Daily risk inference (batch)
│       │   └── geojson_writer.py    # GeoJSON output + EAHW export
│       │
│       └── shared/
│           ├── __init__.py
│           ├── config.py            # Config dataclasses (pydantic)
│           ├── regions.py           # East Africa domain masks
│           ├── logging.py           # Structured logging
│           ├── storage.py           # S3/GCS client wrappers
│           └── validation.py        # Data quality checks
│
├── tests/
│   ├── conftest.py                  # Shared fixtures
│   ├── fixtures/                    # Small test data files
│   │   ├── sample_parquet/          # 5-day GIK Parquet sample
│   │   ├── sample_grib2/            # 1-member GRIB2 excerpt
│   │   └── emdat_east_africa.csv    # EM-DAT sample
│   ├── unit/
│   │   ├── test_gik_loader.py
│   │   ├── test_virtualizer.py
│   │   ├── test_thresholds.py
│   │   ├── test_exceedance.py
│   │   ├── test_crma_model.py
│   │   ├── test_dynamic_bn.py
│   │   └── test_cpt_refinement.py
│   └── integration/
│       ├── test_c1_pipeline.py      # End-to-end 5-day conversion
│       ├── test_c2_pipeline.py      # End-to-end exceedance on sample
│       └── test_c3_pipeline.py      # End-to-end risk assessment
│
├── notebooks/
│   ├── 00_quickstart.ipynb          # Colab-ready demo (5-day window)
│   ├── 01_component1_walkthrough.ipynb
│   ├── 02_component2_walkthrough.ipynb
│   ├── 03_component3_walkthrough.ipynb
│   ├── 04_benchmark_report.ipynb    # GIK+IceChunk vs dynamical.org
│   ├── 05_aifs_vs_ifs_comparison.ipynb
│   └── 06_validation_emdat.ipynb
│
├── configs/
│   ├── default.yaml                 # Default pipeline config
│   ├── east_africa.yaml             # East Africa domain config
│   ├── benchmark.yaml               # Benchmark run config
│   └── cloud_run.yaml               # Cloud Run job config
│
├── scripts/
│   ├── download_data.py             # Download admin-1, CMORPH thresholds
│   ├── run_gap_fill.py              # Fill 2023 GIK archive gap
│   ├── run_benchmark.py             # Full benchmark suite
│   ├── export_eahw.py               # Export admin-1 risk to EAHW Portal format
│   └── validate_store.py            # Validate IceChunk store integrity
│
├── dashboard/
│   ├── calendar_map/
│   │   ├── index.html               # GitHub Pages entry point
│   │   ├── app.js                   # Calendar-map interactive component
│   │   ├── calendar.css
│   │   └── data/                    # Pre-computed daily summaries (JSON)
│   └── storymaps/
│       ├── template.mdx             # VEDA-UI storymap template
│       ├── titiler_config.yaml      # TiTiler (AWS Lambda) configuration
│       └── generate_storymaps.py    # Generate per-day MDX files
│
├── deploy/
│   ├── docker/
│   │   ├── Dockerfile
│   │   └── docker-compose.yml
│   ├── cloud_run/
│   │   ├── job_c1.yaml              # Cloud Run Job — C1 gap fill
│   │   ├── job_c2.yaml              # Cloud Run Job — C2 exceedance batch
│   │   └── lithops_config.yaml      # Lithops serverless config
│   └── github_actions/
│       └── (symlinks to .github/workflows/)
│
├── results/
│   ├── benchmarks/                  # Benchmark CSV + plots
│   └── validation/                  # EM-DAT validation reports
│
├── data/
│   ├── admin_boundaries/            # OCHA/GADM admin-1 shapefiles
│   ├── cmorph_thresholds/           # Pre-computed return-period thresholds
│   └── emdat/                       # EM-DAT flood catalogue (East Africa)
│
└── .github/
    └── workflows/
        ├── ci.yaml                  # Tests + lint on every push/PR
        ├── daily_update.yaml        # Daily IceChunk commit + exceedance update
        └── release.yaml             # Tag-based release + Zenodo DOI
```