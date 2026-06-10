"""Integration tests for C3 — CRMA risk inference and GeoJSON output."""

from __future__ import annotations

import json
from datetime import date

import numpy as np
import pandas as pd
import pytest
import xarray as xr

pytestmark = pytest.mark.integration

NLAT, NLON = 6, 6
LAT = np.linspace(0.0, 3.0, NLAT, dtype=np.float32)
LON = np.linspace(35.0, 38.0, NLON, dtype=np.float32)
TEST_DATE = date(2024, 10, 15)
WINDOWS_H = [24, 72, 168]
RETURN_PERIODS = [5, 20]


@pytest.fixture(scope="module")
def built_crma():
    pytest.importorskip("pgmpy", reason="pgmpy not installed")
    from gik_icechain.risk.crma_model import CRMAModel, EastAfricaCluster

    models = {}
    for cluster in EastAfricaCluster:
        m = CRMAModel(cluster=cluster)
        m.build()
        models[cluster] = m
    return models


@pytest.fixture
def fake_exceedance_store(tmp_path):
    """Write a minimal exceedance Zarr store for TEST_DATE."""
    rng = np.random.default_rng(0)
    shape = (1, NLAT, NLON, len(WINDOWS_H), len(RETURN_PERIODS))
    p_data = rng.uniform(0.0, 0.5, shape).astype(np.float32)
    conf_data = rng.integers(0, 3, (1, NLAT, NLON), dtype=np.int8)

    ds = xr.Dataset(
        {
            "exceedance_prob": xr.DataArray(
                p_data,
                dims=["date", "latitude", "longitude", "window", "return_period"],
                coords={
                    "date": [pd.Timestamp(TEST_DATE)],
                    "latitude": LAT,
                    "longitude": LON,
                    "window": np.array(WINDOWS_H, dtype=np.int16),
                    "return_period": np.array(RETURN_PERIODS, dtype=np.int16),
                },
            ),
            "ensemble_confidence": xr.DataArray(
                conf_data,
                dims=["date", "latitude", "longitude"],
                coords={
                    "date": [pd.Timestamp(TEST_DATE)],
                    "latitude": LAT,
                    "longitude": LON,
                },
            ),
        }
    )
    out = str(tmp_path / "exceedance.zarr")
    ds.to_zarr(out, mode="w", consolidated=True)
    return out


@pytest.fixture
def fake_admin_boundaries(tmp_path):
    pytest.importorskip("geopandas")
    import geopandas as gpd
    from shapely.geometry import box

    gdf = gpd.GeoDataFrame(
        {
            "admin1_pcode": ["KE001", "KE002"],
            "admin1_name": ["West", "East"],
            "country_code": ["KE", "KE"],
        },
        geometry=[box(35.2, 0.2, 36.8, 1.8), box(37.0, 0.2, 38.5, 1.8)],
        crs="EPSG:4326",
    )
    path = tmp_path / "admin1.gpkg"
    gdf.to_file(path, driver="GPKG")
    return path


@pytest.fixture
def fake_gpm_dir(tmp_path):
    gpm_dir = tmp_path / "gpm"
    gpm_dir.mkdir()
    ds = xr.Dataset(
        {
            "precipitationCal": xr.DataArray(
                np.random.default_rng(1).exponential(5.0, (NLAT, NLON)).astype(np.float32),
                dims=["lat", "lon"],
                coords={"lat": LAT, "lon": LON},
            )
        }
    )
    ds.to_netcdf(gpm_dir / f"3B-DAY.MS.MRG.3IMERG.{TEST_DATE.strftime('%Y%m%d')}.V07B.nc4")
    return gpm_dir


class TestCRMAModelInference:
    def test_infer_returns_required_keys(self, built_crma):
        from gik_icechain.risk.crma_model import CRMAEvidence

        model = next(iter(built_crma.values()))
        ev = CRMAEvidence(
            exceedance_prob_24h_5y=0.3,
            exceedance_prob_72h_5y=0.2,
            exceedance_prob_7d_5y=0.15,
            gpm_obs_24h=10.0,
            api_mm=40.0,
            spatial_coverage_fraction=0.4,
            consecutive_signal_days=1,
            sat_consecutive_days=0,
        )
        result = model.infer(ev)
        required = {"risk_state", "risk_label", "p_green", "p_yellow", "p_orange", "p_red"}
        assert required.issubset(result.keys())

    def test_probabilities_sum_to_one(self, built_crma):
        from gik_icechain.risk.crma_model import CRMAEvidence

        model = next(iter(built_crma.values()))
        ev = CRMAEvidence(
            exceedance_prob_24h_5y=0.2,
            exceedance_prob_72h_5y=0.15,
            exceedance_prob_7d_5y=0.10,
            gpm_obs_24h=5.0,
            api_mm=30.0,
            spatial_coverage_fraction=0.3,
            consecutive_signal_days=0,
            sat_consecutive_days=0,
        )
        result = model.infer(ev)
        total = result["p_green"] + result["p_yellow"] + result["p_orange"] + result["p_red"]
        assert abs(total - 1.0) < 1e-5


class TestRiskBatch:
    def test_geojson_output_structure(
        self, built_crma, fake_exceedance_store, fake_gpm_dir, fake_admin_boundaries, tmp_path
    ):
        pytest.importorskip("regionmask", reason="regionmask not installed")
        from gik_icechain.risk.risk_engine import run_risk_batch

        output_dir = tmp_path / "risk_output"
        written = run_risk_batch(
            exceedance_store_uri=fake_exceedance_store,
            gpm_dir=fake_gpm_dir,
            admin_boundaries_path=fake_admin_boundaries,
            crma_models=built_crma,
            output_dir=output_dir,
            start=TEST_DATE,
            end=TEST_DATE,
        )

        assert len(written) == 1
        data = json.loads(written[0].read_text())
        assert "date" in data
        units = data["units"]
        assert len(units) == 2  # two fake admin-1 units

        for p in units.values():
            assert p["risk_label"] in {"Green", "Yellow", "Orange", "Red", "No_Data"}
            total = p["p_green"] + p["p_yellow"] + p["p_orange"] + p["p_red"]
            assert abs(total - 1.0) < 1e-4 or p["risk_label"] == "No_Data"

    def test_missing_exceedance_date_skipped(
        self, built_crma, fake_exceedance_store, fake_gpm_dir, fake_admin_boundaries, tmp_path
    ):
        pytest.importorskip("regionmask", reason="regionmask not installed")
        from datetime import timedelta

        from gik_icechain.risk.risk_engine import run_risk_batch

        wrong_date = TEST_DATE + timedelta(days=30)
        output_dir = tmp_path / "risk_skip"
        written = run_risk_batch(
            exceedance_store_uri=fake_exceedance_store,
            gpm_dir=fake_gpm_dir,
            admin_boundaries_path=fake_admin_boundaries,
            crma_models=built_crma,
            output_dir=output_dir,
            start=wrong_date,
            end=wrong_date,
        )
        assert len(written) == 0
