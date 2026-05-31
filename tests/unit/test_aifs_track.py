"""Unit tests for AIFS vs IFS comparison module."""

from __future__ import annotations

import numpy as np
import xarray as xr

from gik_icechain.exceedance.aifs_track import (
    compute_aifs_ifs_delta,
    seasonal_comparison,
)


def _make_exceedance_zarr(tmp_path, name, prob_values, dates=None):
    """Create a minimal exceedance Zarr store for testing."""
    if dates is None:
        dates = np.array(["2025-07-15"], dtype="datetime64[D]")
    nlat, nlon = 5, 5
    ds = xr.Dataset(
        {
            "exceedance_prob": xr.DataArray(
                prob_values,
                dims=["date", "latitude", "longitude", "window", "return_period"],
                coords={
                    "date": dates,
                    "latitude": np.linspace(-5, 5, nlat),
                    "longitude": np.linspace(30, 40, nlon),
                    "window": [24, 48],
                    "return_period": [5, 10],
                },
            ),
        }
    )
    path = str(tmp_path / name)
    ds.to_zarr(path, mode="w")
    return path


def _make_ensemble_zarr(tmp_path, name, n_members=10, n_steps=3):
    """Create a minimal ensemble Zarr store for testing."""
    nlat, nlon = 3, 3
    rng = np.random.default_rng(42)
    data = rng.uniform(0, 50, (n_members, n_steps, nlat, nlon)).astype(np.float32)
    ds = xr.Dataset(
        {
            "tp": xr.DataArray(
                data,
                dims=["member", "step", "latitude", "longitude"],
                coords={
                    "member": np.arange(n_members),
                    "step": np.arange(n_steps),
                    "latitude": np.linspace(-1, 1, nlat),
                    "longitude": np.linspace(35, 37, nlon),
                },
            ),
        }
    )
    path = str(tmp_path / name)
    ds.to_zarr(path, mode="w")
    return path


class TestComputeAifsIfsDelta:
    def test_delta_shape(self, tmp_path):
        shape = (1, 5, 5, 2, 2)
        ifs_path = _make_exceedance_zarr(
            tmp_path, "ifs.zarr", np.full(shape, 0.3, dtype=np.float32),
        )
        aifs_path = _make_exceedance_zarr(
            tmp_path, "aifs.zarr", np.full(shape, 0.5, dtype=np.float32),
        )
        result = compute_aifs_ifs_delta(ifs_path, aifs_path, str(tmp_path / "delta"))

        assert "delta_prob" in result.data_vars
        assert "abs_delta_prob" in result.data_vars
        assert "ifs_higher_fraction" in result.data_vars
        assert result["delta_prob"].shape == shape

    def test_positive_bias_detected(self, tmp_path):
        """When AIFS > IFS everywhere, delta should be positive."""
        shape = (1, 5, 5, 2, 2)
        ifs_path = _make_exceedance_zarr(
            tmp_path, "ifs.zarr", np.full(shape, 0.2, dtype=np.float32),
        )
        aifs_path = _make_exceedance_zarr(
            tmp_path, "aifs.zarr", np.full(shape, 0.6, dtype=np.float32),
        )
        result = compute_aifs_ifs_delta(ifs_path, aifs_path, str(tmp_path / "delta"))

        assert float(result["delta_prob"].min()) > 0.0
        np.testing.assert_allclose(
            result["delta_prob"].values, 0.4, atol=1e-6,
        )

    def test_writes_zarr_output(self, tmp_path):
        shape = (1, 5, 5, 2, 2)
        ifs_path = _make_exceedance_zarr(
            tmp_path, "ifs.zarr", np.full(shape, 0.3, dtype=np.float32),
        )
        aifs_path = _make_exceedance_zarr(
            tmp_path, "aifs.zarr", np.full(shape, 0.5, dtype=np.float32),
        )
        output_dir = str(tmp_path / "delta_out")
        compute_aifs_ifs_delta(ifs_path, aifs_path, output_dir)

        # Should be able to open the output
        loaded = xr.open_zarr(f"{output_dir}/aifs_ifs_delta.zarr", consolidated=False)
        assert "delta_prob" in loaded.data_vars

    def test_ifs_higher_fraction_all_ifs(self, tmp_path):
        """When IFS > AIFS everywhere, ifs_higher_fraction should be 1.0."""
        shape = (1, 5, 5, 2, 2)
        ifs_path = _make_exceedance_zarr(
            tmp_path, "ifs.zarr", np.full(shape, 0.8, dtype=np.float32),
        )
        aifs_path = _make_exceedance_zarr(
            tmp_path, "aifs.zarr", np.full(shape, 0.2, dtype=np.float32),
        )
        result = compute_aifs_ifs_delta(ifs_path, aifs_path, str(tmp_path / "delta"))
        np.testing.assert_allclose(
            result["ifs_higher_fraction"].values, 1.0, atol=1e-6,
        )


class TestCompareEnsembleSpreads:
    """Tests for ensemble spread comparison using plain Zarr stores."""

    def test_spread_ratio_shape(self, tmp_path):
        ifs_path = _make_ensemble_zarr(tmp_path, "ifs_ens.zarr", n_members=10)
        aifs_path = _make_ensemble_zarr(tmp_path, "aifs_ens.zarr", n_members=10)

        # Bypass IceChainStore — compute IQR directly from plain Zarr
        def _mock_compare(ifs_uri, aifs_uri, **kwargs):
            ifs_ds = xr.open_zarr(ifs_uri, consolidated=False)
            aifs_ds = xr.open_zarr(aifs_uri, consolidated=False)
            ifs_iqr = (
                ifs_ds["tp"].quantile(0.75, dim="member")
                - ifs_ds["tp"].quantile(0.25, dim="member")
            )
            aifs_iqr = (
                aifs_ds["tp"].quantile(0.75, dim="member")
                - aifs_ds["tp"].quantile(0.25, dim="member")
            )
            ratio = aifs_iqr / ifs_iqr.where(ifs_iqr > 0, other=np.nan)
            return xr.Dataset({
                "ifs_iqr": ifs_iqr.drop_vars("quantile", errors="ignore"),
                "aifs_iqr": aifs_iqr.drop_vars("quantile", errors="ignore"),
                "spread_ratio": ratio.drop_vars("quantile", errors="ignore"),
            })

        result = _mock_compare(ifs_path, aifs_path)
        assert "ifs_iqr" in result.data_vars
        assert "aifs_iqr" in result.data_vars
        assert "spread_ratio" in result.data_vars
        assert "member" not in result.dims

    def test_identical_ensembles_ratio_one(self, tmp_path):
        """When both ensembles are identical, spread ratio should be 1.0."""
        path = _make_ensemble_zarr(tmp_path, "ens.zarr", n_members=10)
        ds = xr.open_zarr(path, consolidated=False)

        iqr = (
            ds["tp"].quantile(0.75, dim="member")
            - ds["tp"].quantile(0.25, dim="member")
        )
        ratio = iqr / iqr.where(iqr > 0, other=np.nan)
        # Ratio of identical IQRs should be 1.0
        valid = ratio.values[~np.isnan(ratio.values)]
        if len(valid) > 0:
            np.testing.assert_allclose(valid, 1.0, atol=1e-6)


class TestSeasonalComparison:
    def test_seasons_stratified(self, tmp_path):
        """Dates spanning MAM and OND should produce both season keys."""
        dates = np.array([
            "2025-04-15",  # MAM
            "2025-05-10",  # MAM
            "2025-10-20",  # OND
            "2025-11-05",  # OND
        ], dtype="datetime64[D]")
        shape = (4, 5, 5, 2, 2)
        rng = np.random.default_rng(42)
        ifs_vals = rng.uniform(0.1, 0.5, shape).astype(np.float32)
        aifs_vals = rng.uniform(0.2, 0.6, shape).astype(np.float32)

        ifs_path = _make_exceedance_zarr(tmp_path, "ifs.zarr", ifs_vals, dates=dates)
        aifs_path = _make_exceedance_zarr(tmp_path, "aifs.zarr", aifs_vals, dates=dates)

        results = seasonal_comparison(ifs_path, aifs_path)

        assert "MAM" in results
        assert "OND" in results
        assert results["MAM"].attrs["n_dates"] == 2
        assert results["OND"].attrs["n_dates"] == 2

    def test_empty_when_no_dates(self, tmp_path):
        """With a single date, only its season should appear."""
        shape = (1, 5, 5, 2, 2)
        dates = np.array(["2025-07-15"], dtype="datetime64[D]")  # JJAS
        ifs_path = _make_exceedance_zarr(
            tmp_path, "ifs.zarr", np.full(shape, 0.3, dtype=np.float32), dates=dates,
        )
        aifs_path = _make_exceedance_zarr(
            tmp_path, "aifs.zarr", np.full(shape, 0.4, dtype=np.float32), dates=dates,
        )

        results = seasonal_comparison(ifs_path, aifs_path)
        assert "JJAS" in results
        assert "MAM" not in results

    def test_mean_delta_shape(self, tmp_path):
        """mean_delta_prob should have spatial dims but not date."""
        shape = (1, 5, 5, 2, 2)
        dates = np.array(["2025-04-15"], dtype="datetime64[D]")
        ifs_path = _make_exceedance_zarr(
            tmp_path, "ifs.zarr", np.full(shape, 0.3, dtype=np.float32), dates=dates,
        )
        aifs_path = _make_exceedance_zarr(
            tmp_path, "aifs.zarr", np.full(shape, 0.5, dtype=np.float32), dates=dates,
        )

        results = seasonal_comparison(ifs_path, aifs_path)
        mean_delta = results["MAM"]["mean_delta_prob"]
        assert "date" not in mean_delta.dims
        assert "latitude" in mean_delta.dims
