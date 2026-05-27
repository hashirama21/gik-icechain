"""Unit tests for accumulation and exceedance computation."""

from __future__ import annotations

import numpy as np
import pytest
import xarray as xr

from gik_icechain.exceedance.accumulations import (
    WINDOWS_H,
    accumulation_for_window,
    compute_rolling_accumulations,
)
from gik_icechain.exceedance.exceedance import (
    compute_ensemble_confidence,
    compute_exceedance_probabilities,
)


def _make_forecast(nmembers=4, nsteps=10, nlat=5, nlon=5, seed=0) -> xr.Dataset:
    rng = np.random.default_rng(seed)
    increments = rng.exponential(0.005, (nmembers, nsteps, nlat, nlon)).astype(np.float32)
    tp = np.cumsum(increments, axis=1)
    return xr.Dataset(
        {"tp": xr.DataArray(
            tp,
            dims=["member", "step", "latitude", "longitude"],
            coords={
                "member": np.arange(nmembers),
                "step": np.arange(0, nsteps * 6, 6),
                "latitude": np.linspace(0, 4, nlat),
                "longitude": np.linspace(35, 39, nlon),
            },
        )}
    )


class TestAccumulationForWindow:
    def test_output_shape_unchanged(self):
        ds = _make_forecast(nsteps=8)
        result = accumulation_for_window(ds["tp"], window_h=24, step_hours=6)
        assert result.shape == ds["tp"].shape

    def test_non_negative(self):
        ds = _make_forecast()
        result = accumulation_for_window(ds["tp"], window_h=24, step_hours=6)
        assert float(result.min()) >= 0.0

    def test_window_larger_than_steps_returns_raw(self):
        ds = _make_forecast(nsteps=3)
        result = accumulation_for_window(ds["tp"], window_h=168, step_hours=6)
        # n_back=28 > nsteps=3 → returns raw copy
        xr.testing.assert_equal(result, ds["tp"])

    def test_invalid_window_raises(self):
        ds = _make_forecast()
        with pytest.raises(ValueError):
            accumulation_for_window(ds["tp"], window_h=3, step_hours=6)


class TestComputeRollingAccumulations:
    def test_output_variables(self):
        ds = _make_forecast()
        acc = compute_rolling_accumulations(ds, windows_h=[24, 72])
        assert "tp_24h" in acc
        assert "tp_72h" in acc

    def test_missing_tp_raises(self):
        ds = xr.Dataset({"t2m": xr.DataArray(np.zeros((3, 5, 5)))})
        with pytest.raises(KeyError):
            compute_rolling_accumulations(ds, windows_h=[24])

    def test_all_default_windows(self):
        # Build a forecast with 3-hour steps so all default windows (min=3h) are valid
        rng = np.random.default_rng(7)
        nsteps = 60
        increments = rng.exponential(0.002, (4, nsteps, 5, 5)).astype(np.float32)
        tp = np.cumsum(increments, axis=1)
        ds = xr.Dataset({"tp": xr.DataArray(
            tp,
            dims=["member", "step", "latitude", "longitude"],
            coords={
                "member": np.arange(4),
                "step": np.arange(0, nsteps * 3, 3),  # 3-hour steps
                "latitude": np.linspace(0, 4, 5),
                "longitude": np.linspace(35, 39, 5),
            },
        )})
        acc = compute_rolling_accumulations(ds)
        for w in WINDOWS_H:
            assert f"tp_{w}h" in acc


class TestComputeExceedanceProbabilities:
    def test_probabilities_in_0_1(self):
        ds = _make_forecast(nmembers=10)
        acc = compute_rolling_accumulations(ds, windows_h=[24])
        thr = xr.Dataset({"rp_5y": xr.DataArray(
            np.full((5, 5), 0.01, dtype=np.float32),
            dims=["latitude", "longitude"],
            coords={"latitude": np.linspace(0, 4, 5), "longitude": np.linspace(35, 39, 5)},
        )})
        p = compute_exceedance_probabilities(acc, thr, window_h=24, return_period=5,
                                             member_dim="member")
        assert float(p.min()) >= 0.0
        assert float(p.max()) <= 1.0

    def test_high_threshold_gives_low_prob(self):
        ds = _make_forecast(nmembers=10, seed=1)
        acc = compute_rolling_accumulations(ds, windows_h=[24])
        # Very high threshold — almost nothing exceeds it
        thr = xr.Dataset({"rp_5y": xr.DataArray(
            np.full((5, 5), 9999.0, dtype=np.float32),
            dims=["latitude", "longitude"],
            coords={"latitude": np.linspace(0, 4, 5), "longitude": np.linspace(35, 39, 5)},
        )})
        p = compute_exceedance_probabilities(acc, thr, window_h=24, return_period=5,
                                             member_dim="member")
        assert float(p.mean()) < 0.05

    def test_zero_threshold_gives_prob_one(self):
        ds = _make_forecast(nmembers=10)
        acc = compute_rolling_accumulations(ds, windows_h=[24])
        thr = xr.Dataset({"rp_5y": xr.DataArray(
            np.zeros((5, 5), dtype=np.float32),
            dims=["latitude", "longitude"],
            coords={"latitude": np.linspace(0, 4, 5), "longitude": np.linspace(35, 39, 5)},
        )})
        p = compute_exceedance_probabilities(acc, thr, window_h=24, return_period=5,
                                             member_dim="member")
        assert float(p.mean()) == pytest.approx(1.0, abs=0.01)


class TestComputeEnsembleConfidence:
    def test_states_in_0_1_2(self):
        ds = _make_forecast(nmembers=8)
        acc = compute_rolling_accumulations(ds, windows_h=[24])
        conf = compute_ensemble_confidence(acc, window_h=24, member_dim="member")
        unique = set(int(v) for v in np.unique(conf.values))
        assert unique.issubset({0, 1, 2})

    def test_output_spatial_shape(self):
        ds = _make_forecast(nlat=6, nlon=7)
        acc = compute_rolling_accumulations(ds, windows_h=[24])
        conf = compute_ensemble_confidence(acc, window_h=24, member_dim="member")
        assert conf.shape == (6, 7)

    def test_high_agreement_gives_high_confidence(self):
        # All members identical → IQR = 0 → ratio = 0 → High (2)
        tp = np.ones((8, 5, 4, 4), dtype=np.float32) * 0.02
        ds = xr.Dataset({"tp": xr.DataArray(
            tp, dims=["member", "step", "latitude", "longitude"],
            coords={"member": np.arange(8), "step": np.arange(0, 30, 6),
                    "latitude": np.arange(4), "longitude": np.arange(4)},
        )})
        acc = compute_rolling_accumulations(ds, windows_h=[24])
        conf = compute_ensemble_confidence(acc, window_h=24, member_dim="member")
        # Most cells should be High (2) when all members agree
        assert int(conf.mean().round()) == 2
