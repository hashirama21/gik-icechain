"""Chantier 2 - EM-DAT CPT refinement (GPM-driven training set).

Builds the EM-DAT-labeled training set directly from observed GPM IMERG over its
data-complete window (2015-2023), spinning up API / soil-memory per unit day by
day exactly as production does (exceedance=0), then refines the Risk_State CPT
via Bayesian (Dirichlet) MLE. Decoupled from the handful of risk_scores dates and
from the ECMWF archive, so positives scale from ~5 to several hundred.
"""
from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import numpy as np

from gik_icechain.risk.cpt_refinement import (
    build_training_dataset_from_gpm,
    load_emdat_east_africa,
    refine_cpts_with_emdat,
)
from gik_icechain.risk.crma_model import CRMAModel, EastAfricaCluster
from gik_icechain.risk.emdat_matching import load_pcode_aliases
from gik_icechain.shared.logging import configure_logging

configure_logging("INFO")  # silence per-step dynamic-BN debug logs (155k+ steps)

ADMIN = Path("data/admin_boundaries/east_africa_admin1.geojson")
GPM = Path("data/gpm_imerg")
EMDAT = Path("data/emdat/east_africa_floods.csv")
ALIASES = Path("data/emdat/pcode_aliases.csv")
OUT = Path("results/validation/refined_cpts.json")
TRAIN_OUT = Path("results/validation/emdat_training_set.parquet")

# GPM IMERG on disk is complete through 2023; cap events to that window.
recs = [r for r in load_emdat_east_africa(EMDAT) if r.start_date.year <= 2023]
admin = gpd.read_file(ADMIN)

m = CRMAModel(cluster=EastAfricaCluster.EQUATORIAL_EAST)
m.build()

train = build_training_dataset_from_gpm(
    recs, admin, GPM, m, spinup_days=30, negative_sample_ratio=3.0, rp=5, graded_labels=True,
    pcode_aliases=load_pcode_aliases(ALIASES),
)
print(f"training rows: {len(train)}")
if not train.empty:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    train.to_parquet(TRAIN_OUT)
    print(f"training set saved -> {TRAIN_OUT}")
    print("Risk_State distribution:", train["Risk_State"].value_counts().sort_index().to_dict())
    print("source distribution:", train["source"].value_counts().to_dict())

if train.empty or train["Risk_State"].nunique() < 2:
    print("INSUFFICIENT training data (need >=2 Risk_State classes) -> abort refinement")
    raise SystemExit(0)

OUT.parent.mkdir(parents=True, exist_ok=True)
refine_cpts_with_emdat(m, train, laplace_alpha=1.0, output_path=OUT)
print(f"refined CPTs saved -> {OUT}")

rs = np.array(json.loads(OUT.read_text())["Risk_State"])
print("refined Risk_State CPD (rows=Green/Yellow/Orange/Red, cols=Compound None/Low/Mod/High):")
print(np.round(rs, 3))
