"""Integration tests for C3 - CRMA risk inference and GeoJSON output."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

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
def dual_rp_exceedance_store(tmp_path):
    """Exceedance store with RP5 saturated and RP20 ~zero, so risk_by_rp must differ."""
    p_data = np.zeros((1, NLAT, NLON, len(WINDOWS_H), len(RETURN_PERIODS)), dtype=np.float32)
    p_data[..., RETURN_PERIODS.index(5)] = 0.85   # Extreme hazard
    p_data[..., RETURN_PERIODS.index(20)] = 0.05   # Low hazard
    conf_data = np.full((1, NLAT, NLON), 2, dtype=np.int8)

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
    out = str(tmp_path / "exceedance_dual_rp.zarr")
    ds.to_zarr(out, mode="w", consolidated=True)
    return out


@pytest.fixture
def storyline_exceedance_store(tmp_path):
    """Store with a strong worst world (p95 tail) but a weak median world (p50),
    so the per-member storyline spread must be positive."""
    shape = (1, NLAT, NLON, len(WINDOWS_H), len(RETURN_PERIODS))
    coords = {
        "date": [pd.Timestamp(TEST_DATE)],
        "latitude": LAT,
        "longitude": LON,
        "window": np.array(WINDOWS_H, dtype=np.int16),
        "return_period": np.array(RETURN_PERIODS, dtype=np.int16),
    }
    dims = ["date", "latitude", "longitude", "window", "return_period"]

    def _da(value):
        return xr.DataArray(np.full(shape, value, dtype=np.float32), dims=dims, coords=coords)

    ds = xr.Dataset(
        {
            "exceedance_prob": _da(0.05),   # low ensemble fraction
            "tail_ratio": _da(1.5),         # worst world ≫ return level → Extreme
            "median_ratio": _da(0.3),       # median world well below → Low
            "ensemble_confidence": xr.DataArray(
                np.full((1, NLAT, NLON), 2, dtype=np.int8),
                dims=["date", "latitude", "longitude"],
                coords={"date": [pd.Timestamp(TEST_DATE)], "latitude": LAT, "longitude": LON},
            ),
        }
    )
    out = str(tmp_path / "exceedance_storyline.zarr")
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
            exceedance_prob_24h=0.3,
            exceedance_prob_72h=0.2,
            exceedance_prob_7d=0.15,
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
            exceedance_prob_24h=0.2,
            exceedance_prob_72h=0.15,
            exceedance_prob_7d=0.10,
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
        data = json.loads(Path(written[0]).read_text())
        assert "date" in data
        units = data["units"]
        assert len(units) == 2  # two fake admin-1 units

        for p in units.values():
            assert p["risk_label"] in {"Green", "Yellow", "Orange", "Red", "No_Data"}
            total = p["p_green"] + p["p_yellow"] + p["p_orange"] + p["p_red"]
            assert abs(total - 1.0) < 1e-4 or p["risk_label"] == "No_Data"

    def test_dual_rp_risk_by_rp(
        self, built_crma, dual_rp_exceedance_store, fake_gpm_dir, fake_admin_boundaries, tmp_path
    ):
        pytest.importorskip("regionmask", reason="regionmask not installed")
        from gik_icechain.risk.risk_engine import run_risk_batch

        written = run_risk_batch(
            exceedance_store_uri=dual_rp_exceedance_store,
            gpm_dir=fake_gpm_dir,
            admin_boundaries_path=fake_admin_boundaries,
            crma_models=built_crma,
            output_dir=tmp_path / "risk_dual_rp",
            start=TEST_DATE,
            end=TEST_DATE,
            rp_signal=5,
            rp_signal_options=[5, 20],
        )

        assert len(written) == 1
        units = json.loads(Path(written[0]).read_text())["units"]
        prob_keys = ("p_green", "p_yellow", "p_orange", "p_red")

        any_differs = False
        for u in units.values():
            by_rp = u["risk_by_rp"]
            assert set(by_rp) == {"5", "20"}
            for rp_risk in by_rp.values():
                if rp_risk["risk_label"] != "No_Data":
                    assert abs(sum(rp_risk[k] for k in prob_keys) - 1.0) < 1e-4
            assert u["risk_state"] == by_rp["5"]["risk_state"]
            if any(by_rp["5"][k] != by_rp["20"][k] for k in prob_keys):
                any_differs = True
        # exceedances are random across the RP dim → at least one unit differs
        assert any_differs

    def test_storyline_worst_median_spread(
        self, built_crma, storyline_exceedance_store, fake_gpm_dir, fake_admin_boundaries, tmp_path
    ):
        pytest.importorskip("regionmask", reason="regionmask not installed")
        from gik_icechain.risk.risk_engine import run_risk_batch

        written = run_risk_batch(
            exceedance_store_uri=storyline_exceedance_store,
            gpm_dir=fake_gpm_dir,
            admin_boundaries_path=fake_admin_boundaries,
            crma_models=built_crma,
            output_dir=tmp_path / "risk_storyline",
            start=TEST_DATE,
            end=TEST_DATE,
            rp_signal=5,
            rp_signal_options=[5, 20],
        )
        units = json.loads(Path(written[0]).read_text())["units"]
        spreads = []
        for u in units.values():
            if u["risk_label"] == "No_Data":
                continue
            assert "storyline_median_state" in u
            assert "storyline_spread" in u
            assert u["storyline_spread"] >= 0
            # the worst world (= risk_state) is at least as severe as the median
            assert u["risk_state"] >= u["storyline_median_state"]
            spreads.append(u["storyline_spread"])
        # strong p95 tail + weak p50 median ⇒ at least one unit has a real gap
        assert any(s > 0 for s in spreads)

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
