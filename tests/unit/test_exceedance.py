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
    compute_member_ratio,
    compute_tail_ratio,
)


class TestComputeTailRatio:
    @staticmethod
    def _acc_single_cell(member_maxima: np.ndarray) -> xr.Dataset:
        """Build a tp_24h dataset (member, step, lat, lon) whose per-member
        max-over-step equals *member_maxima* at the single (lat, lon) cell."""
        n = len(member_maxima)
        tp = np.broadcast_to(
            member_maxima.reshape(n, 1, 1, 1), (n, 3, 1, 1)
        ).astype(np.float32)
        da = xr.DataArray(
            tp,
            dims=["member", "step", "latitude", "longitude"],
            coords={
                "member": np.arange(n),
                "step": [0, 6, 12],
                "latitude": [0.0],
                "longitude": [35.0],
            },
        )
        return xr.Dataset({"tp_24h": da})

    @staticmethod
    def _threshold(value: float) -> xr.Dataset:
        return xr.Dataset(
            {"rp_5y": xr.DataArray(
                [[value]], dims=["latitude", "longitude"],
                coords={"latitude": [0.0], "longitude": [35.0]},
            )}
        )

    def test_ratio_matches_member_quantile(self):
        members = np.arange(1, 21, dtype=np.float32)  # 1..20 mm
        acc = self._acc_single_cell(members)
        thr = self._threshold(10.0)
        out = compute_tail_ratio(
            acc, thr, window_h=24, return_period=5,
            member_dim="member", tail_quantile=0.95,
        )
        assert set(out.dims) == {"latitude", "longitude"}
        assert "quantile" not in out.coords
        expected = float(np.quantile(members, 0.95)) / 10.0
        assert float(out.isel(latitude=0, longitude=0)) == pytest.approx(expected)

    def test_tail_sees_signal_below_mean_exceedance(self):
        # 1 of 20 members at 100 mm, rest at 5 mm, threshold 50 mm.
        members = np.full(20, 5.0, dtype=np.float32)
        members[-1] = 100.0
        acc = self._acc_single_cell(members)
        thr = self._threshold(50.0)
        # Mean exceedance fraction is only 1/20 = 0.05 (below the 0.15 Medium bar)…
        frac = compute_exceedance_probabilities(
            acc, thr, window_h=24, return_period=5, member_dim="member",
        )
        assert float(frac.isel(latitude=0, longitude=0)) == pytest.approx(0.05)
        # …but a tail at p99 lifts the ratio toward / past the return level.
        tail = compute_tail_ratio(
            acc, thr, window_h=24, return_period=5,
            member_dim="member", tail_quantile=0.99,
        )
        assert float(tail.isel(latitude=0, longitude=0)) > 1.0

    def test_dry_cell_threshold_zero_is_nan(self):
        acc = self._acc_single_cell(np.full(10, 3.0, dtype=np.float32))
        thr = self._threshold(0.0)
        out = compute_tail_ratio(
            acc, thr, window_h=24, return_period=5, member_dim="member",
        )
        assert bool(np.isnan(out.isel(latitude=0, longitude=0)))

    def test_median_world_is_p50_member(self):
        members = np.arange(1, 21, dtype=np.float32)  # 1..20 mm
        acc = self._acc_single_cell(members)
        thr = self._threshold(10.0)
        out = compute_member_ratio(
            acc, thr, window_h=24, return_period=5, member_dim="member", quantile=0.5,
        )
        expected = float(np.quantile(members, 0.5)) / 10.0
        assert float(out.isel(latitude=0, longitude=0)) == pytest.approx(expected)

    def test_tail_ratio_delegates_to_member_ratio(self):
        members = np.arange(1, 21, dtype=np.float32)
        acc = self._acc_single_cell(members)
        thr = self._threshold(10.0)
        tail = compute_tail_ratio(
            acc, thr, window_h=24, return_period=5, member_dim="member", tail_quantile=0.9,
        )
        member = compute_member_ratio(
            acc, thr, window_h=24, return_period=5, member_dim="member", quantile=0.9,
        )
        assert float(tail.isel(latitude=0, longitude=0)) == pytest.approx(
            float(member.isel(latitude=0, longitude=0))
        )

    def test_median_below_worst(self):
        # 1 of 20 members at 100 mm, rest at 5 mm → median world stays low,
        # worst (p95) world spikes - the storyline gap the BN exploits.
        members = np.full(20, 5.0, dtype=np.float32)
        members[-1] = 100.0
        acc = self._acc_single_cell(members)
        thr = self._threshold(50.0)
        med = float(
            compute_member_ratio(
                acc, thr, window_h=24, return_period=5, member_dim="member", quantile=0.5,
            ).isel(latitude=0, longitude=0)
        )
        worst = float(
            compute_member_ratio(
                acc, thr, window_h=24, return_period=5, member_dim="member", quantile=0.99,
            ).isel(latitude=0, longitude=0)
        )
        assert med < worst


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

    def test_subresolution_window_yields_nan(self):
        """A window finer than the step spacing has no valid lagged hour ->
        NaN where h - window is positive but not a stored step (e.g. a 3 h
        window on 6-hourly data). It no longer raises."""
        ds = _make_forecast()
        result = accumulation_for_window(ds["tp"], window_h=3, step_hours=6)
        assert result.shape == ds["tp"].shape
        # step=6 -> lookback hour 3 is not stored -> NaN
        assert bool(np.all(np.isnan(result.sel(step=6).values)))

    def test_nonuniform_steps_3h_window(self):
        """3 h window is valid in the 3-hourly region, NaN in the 6-hourly region."""
        hours = np.array([0, 3, 6, 9, 12, 18, 24], dtype=np.int32)
        tp = np.array([0.0, 2.0, 4.0, 6.0, 8.0, 13.0, 18.0], dtype=np.float32)
        da = xr.DataArray(tp, dims="step", coords={"step": hours})
        acc = accumulation_for_window(da, window_h=3)
        # 3-hourly region: constant 2 mm increments
        assert acc.sel(step=6).item() == pytest.approx(2.0)
        assert acc.sel(step=12).item() == pytest.approx(2.0)
        # 6-hourly region: lookback hour 15/21 not stored -> NaN
        assert np.isnan(acc.sel(step=18).item())


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
        # Very high threshold - almost nothing exceeds it
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

    def test_flood_floor_suppresses_arid_false_alarm(self):
        """A near-zero (arid) threshold + small forecast → floor suppresses it."""
        ds = _make_forecast(nmembers=10)
        acc = compute_rolling_accumulations(ds, windows_h=[24])
        thr = xr.Dataset({"rp_5y": xr.DataArray(
            np.full((5, 5), 0.01, dtype=np.float32),
            dims=["latitude", "longitude"],
            coords={"latitude": np.linspace(0, 4, 5), "longitude": np.linspace(35, 39, 5)},
        )})
        no_floor = compute_exceedance_probabilities(
            acc, thr, window_h=24, return_period=5, member_dim="member", flood_floor_mm=0.0)
        floored = compute_exceedance_probabilities(
            acc, thr, window_h=24, return_period=5, member_dim="member", flood_floor_mm=30.0)
        assert float(no_floor.mean()) > 0.9
        assert float(floored.mean()) == pytest.approx(0.0, abs=0.01)


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
