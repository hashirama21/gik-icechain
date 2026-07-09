"""Unit tests for GPM IMERG loading and the Antecedent Precipitation Index."""

from datetime import date

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from gik_icechain.risk.gpm_loader import (
    compute_api_series,
    load_gpm_daily,
    load_gpm_range,
)


def _gpm_ds(values: list[np.ndarray]) -> xr.Dataset:
    """Dataset with precipitationCal (time, lat, lon) from per-day 2x2 grids."""
    times = pd.date_range("2024-01-01", periods=len(values), freq="D")
    data = np.stack(values, axis=0).astype(np.float32)
    return xr.Dataset(
        {"precipitationCal": (("time", "lat", "lon"), data)},
        coords={
            "time": times,
            "lat": np.array([0.0, 1.0]),
            "lon": np.array([10.0, 11.0]),
        },
    )


def _write_gpm_file(gpm_dir, name: str, var: str = "precipitationCal") -> None:
    ds = xr.Dataset(
        {var: (("lat", "lon"), np.full((2, 2), 7.5, dtype=np.float32))},
        coords={"lat": np.array([0.0, 1.0]), "lon": np.array([10.0, 11.0])},
    )
    ds.to_netcdf(gpm_dir / name)


class TestComputeApiSeries:
    def test_recurrence_exact(self):
        """API(t) = obs(t) + decay * API(t-1) with decay=0.5, initial=10."""
        obs = [np.full((2, 2), 4.0), np.full((2, 2), 0.0), np.full((2, 2), 8.0)]
        api = compute_api_series(_gpm_ds(obs), decay=0.5, initial_mm=10.0)
        # day 0: 4 + 0.5*10 = 9; day 1: 0 + 0.5*9 = 4.5; day 2: 8 + 0.5*4.5 = 10.25
        assert float(api.isel(time=0).mean()) == pytest.approx(9.0)
        assert float(api.isel(time=1).mean()) == pytest.approx(4.5)
        assert float(api.isel(time=2).mean()) == pytest.approx(10.25)
        assert api.dims == ("time", "lat", "lon")

    def test_nan_obs_treated_as_zero(self):
        day0 = np.full((2, 2), np.nan)
        api = compute_api_series(_gpm_ds([day0]), decay=0.5, initial_mm=10.0)
        # NaN obs contributes 0, decay still applies: 0 + 0.5*10 = 5.
        assert float(api.isel(time=0).mean()) == pytest.approx(5.0)

    def test_missing_variable_raises(self):
        ds = _gpm_ds([np.zeros((2, 2))]).rename({"precipitationCal": "other"})
        with pytest.raises(KeyError, match="precipitationCal"):
            compute_api_series(ds)


class TestLoadGpmDaily:
    def test_missing_file_returns_none(self, tmp_path):
        assert load_gpm_daily(tmp_path, date(2024, 1, 1)) is None

    def test_standard_nc4_name(self, tmp_path):
        _write_gpm_file(tmp_path, "3B-DAY.MS.MRG.3IMERG.20240101.V07B.nc4")
        da = load_gpm_daily(tmp_path, date(2024, 1, 1))
        assert da is not None
        assert float(da.mean()) == pytest.approx(7.5)

    def test_glob_fallback_and_var_fallback(self, tmp_path):
        """Non-standard file name matched by glob; 'precipitation' accepted."""
        _write_gpm_file(tmp_path, "custom_20240102_daily.nc4", var="precipitation")
        da = load_gpm_daily(tmp_path, date(2024, 1, 2))
        assert da is not None
        assert float(da.mean()) == pytest.approx(7.5)


class TestLoadGpmRange:
    def test_empty_dir_returns_empty_dataset(self, tmp_path):
        ds = load_gpm_range(tmp_path, date(2024, 1, 1), date(2024, 1, 2))
        assert len(ds.data_vars) == 0

    def test_missing_days_skipped(self, tmp_path):
        _write_gpm_file(tmp_path, "3B-DAY.MS.MRG.3IMERG.20240101.V07B.nc4")
        ds = load_gpm_range(tmp_path, date(2024, 1, 1), date(2024, 1, 3))
        assert ds["precipitationCal"].sizes["time"] == 1
