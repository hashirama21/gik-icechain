"""Integration tests for C2 - exceedance probability computation and Zarr output."""

from __future__ import annotations

from datetime import date

import numpy as np
import pytest
import xarray as xr

pytestmark = pytest.mark.integration

NLAT, NLON, NMEMBERS, NSTEPS = 6, 6, 5, 8
LAT = np.linspace(0.0, 3.0, NLAT, dtype=np.float32)
LON = np.linspace(35.0, 38.0, NLON, dtype=np.float32)
STEPS = np.arange(0, NSTEPS * 6, 6, dtype=np.int32)
TEST_DATE = date(2024, 10, 15)
WINDOWS_H = [24, 72]
RETURN_PERIODS = [5, 20]


@pytest.fixture
def forecast_ds() -> xr.Dataset:
    rng = np.random.default_rng(42)
    increments = rng.exponential(0.004, (NMEMBERS, NSTEPS, NLAT, NLON)).astype(np.float32)
    tp = np.cumsum(increments, axis=1)
    return xr.Dataset({"tp": xr.DataArray(
        tp,
        dims=["member", "step", "latitude", "longitude"],
        coords={"member": np.arange(NMEMBERS), "step": STEPS, "latitude": LAT, "longitude": LON},
        attrs={"units": "m"},
    )})


@pytest.fixture
def thresholds():
    from gik_icechain.exceedance.thresholds import (
        AdaptiveGEVThresholds,
        ClimateMode,
        ENSOPhase,
        IODPhase,
        Season,
    )
    inst = AdaptiveGEVThresholds()
    template = xr.DataArray(
        np.full((NLAT, NLON), 0.001, dtype=np.float32),
        dims=["latitude", "longitude"],
        coords={"latitude": LAT, "longitude": LON},
    )
    for season in Season:
        for enso in ENSOPhase:
            for iod in IODPhase:
                mode = ClimateMode(season, enso, iod)
                inst._thresholds[mode.key] = {}
                for w in WINDOWS_H:
                    inst._thresholds[mode.key][w] = {
                        rp: template * (rp / 5.0) for rp in RETURN_PERIODS
                    }
    return inst


class TestRollingAccumulations:
    def test_produces_all_windows(self, forecast_ds):
        from gik_icechain.exceedance.accumulations import compute_rolling_accumulations
        acc = compute_rolling_accumulations(forecast_ds, windows_h=WINDOWS_H)
        for w in WINDOWS_H:
            assert f"tp_{w}h" in acc
            assert acc[f"tp_{w}h"].shape == forecast_ds["tp"].shape

    def test_values_non_negative(self, forecast_ds):
        from gik_icechain.exceedance.accumulations import compute_rolling_accumulations
        acc = compute_rolling_accumulations(forecast_ds, windows_h=WINDOWS_H)
        for w in WINDOWS_H:
            assert float(acc[f"tp_{w}h"].min()) >= 0.0


class TestExceedanceProbabilities:
    def test_probabilities_valid_range(self, forecast_ds, thresholds):
        from gik_icechain.exceedance.accumulations import compute_rolling_accumulations
        from gik_icechain.exceedance.exceedance import compute_exceedance_probabilities
        from gik_icechain.exceedance.thresholds import ClimateMode, ENSOPhase, IODPhase, Season

        acc = compute_rolling_accumulations(forecast_ds, windows_h=WINDOWS_H)
        mode = ClimateMode(Season.OND, ENSOPhase.NEUTRAL, IODPhase.NEUTRAL)
        thr = thresholds.get(24, 5, mode)
        p = compute_exceedance_probabilities(
            acc, xr.Dataset({"rp_5y": thr}), window_h=24, return_period=5, member_dim="member",
        )
        assert float(p.min()) >= 0.0
        assert float(p.max()) <= 1.0
        assert p.shape == (NLAT, NLON)

    def test_ensemble_confidence_states(self, forecast_ds):
        from gik_icechain.exceedance.accumulations import compute_rolling_accumulations
        from gik_icechain.exceedance.exceedance import compute_ensemble_confidence

        acc = compute_rolling_accumulations(forecast_ds, windows_h=WINDOWS_H)
        conf = compute_ensemble_confidence(acc, window_h=24, member_dim="member")
        unique = set(int(v) for v in np.unique(conf.values))
        assert unique.issubset({0, 1, 2})


class TestExceedanceWriter:
    def test_write_and_read_zarr(self, forecast_ds, thresholds, tmp_path):
        import pandas as pd

        from gik_icechain.exceedance.accumulations import compute_rolling_accumulations
        from gik_icechain.exceedance.exceedance import (
            compute_ensemble_confidence,
            compute_exceedance_probabilities,
        )
        from gik_icechain.exceedance.thresholds import ClimateMode, ENSOPhase, IODPhase, Season
        from gik_icechain.exceedance.writer import build_exceedance_dataset, write_exceedance_store
        acc = compute_rolling_accumulations(forecast_ds, windows_h=WINDOWS_H)
        mode = ClimateMode(Season.OND, ENSOPhase.NEUTRAL, IODPhase.NEUTRAL)

        day_results = {}
        for w in WINDOWS_H:
            for rp in RETURN_PERIODS:
                thr = thresholds.get(w, rp, mode)
                day_results[(w, rp)] = compute_exceedance_probabilities(
                    acc, xr.Dataset({f"rp_{rp}y": thr}), w, rp, member_dim="member",
                )

        exc_da = build_exceedance_dataset(day_results, TEST_DATE)
        conf = compute_ensemble_confidence(acc, window_h=24, member_dim="member")
        conf_da = conf.assign_coords(date=pd.Timestamp(TEST_DATE)).expand_dims("date")

        output_uri = str(tmp_path / "exceedance.zarr")
        write_exceedance_store(
            {TEST_DATE: exc_da},
            output_uri,
            confidence_dict={TEST_DATE: conf_da},
        )

        ds = xr.open_zarr(output_uri, consolidated=False)

        assert "exceedance_prob" in ds
        assert "ensemble_confidence" in ds
        assert "date" in ds.dims
        assert "window" in ds.dims
        assert "return_period" in ds.dims

        p = ds["exceedance_prob"].values
        p_finite = p[np.isfinite(p)]
        assert p_finite.min() >= 0.0
        assert p_finite.max() <= 1.0

        conf_vals = {
            int(v) for v in np.unique(ds["ensemble_confidence"].values) if np.isfinite(v)
        }
        assert conf_vals.issubset({0, 1, 2})

    def test_zarr_threshold_roundtrip(self, thresholds, tmp_path):
        from gik_icechain.exceedance.thresholds import (
            AdaptiveGEVThresholds,
            ClimateMode,
            ENSOPhase,
            IODPhase,
            Season,
        )

        zarr_path = str(tmp_path / "thresholds.zarr")
        thresholds.save_zarr(zarr_path)
        thr2 = AdaptiveGEVThresholds.load_zarr(zarr_path)

        assert len(thr2._thresholds) == len(thresholds._thresholds)

        mode = ClimateMode(Season.OND, ENSOPhase.NEUTRAL, IODPhase.NEUTRAL)
        orig = thresholds.get(24, 5, mode)
        rt   = thr2.get(24, 5, mode)
        np.testing.assert_allclose(orig.values, rt.values, rtol=1e-4)
