"""
tests/conftest.py
Shared pytest fixtures for GIK-IceChain test suite.
"""

import numpy as np
import pandas as pd
import pytest
import xarray as xr


@pytest.fixture(scope="session")
def synthetic_cmorph_ds():
    """Minimal CMORPH-like precipitation dataset for threshold tests."""
    time = pd.date_range("2000-01-01", periods=365 * 10, freq="3h")
    lat = np.arange(-5, 5, 0.5)
    lon = np.arange(35, 42, 0.5)
    rng = np.random.default_rng(42)
    data = rng.exponential(scale=1.0, size=(len(time), len(lat), len(lon)))
    return xr.Dataset(
        {"precip": (["time", "lat", "lon"], data)},
        coords={"time": time, "lat": lat, "lon": lon},
    )


@pytest.fixture(scope="session")
def synthetic_enso_iod_index():
    """Synthetic ENSO/IOD index for 2000-2022."""
    dates = pd.date_range("2000-01-01", "2022-12-31", freq="D")
    rng = np.random.default_rng(0)
    return pd.DataFrame(
        {
            "date": dates.date,
            "nino34": rng.normal(0, 0.6, len(dates)),
            "dmi": rng.normal(0, 0.4, len(dates)),
        }
    )


@pytest.fixture(scope="session")
def sample_exceedance_da():
    """Synthetic exceedance probability DataArray (lat x lon)."""
    lat = np.arange(-5, 5, 1.0)
    lon = np.arange(35, 42, 1.0)
    data = np.random.uniform(0, 1, (len(lat), len(lon)))
    return xr.DataArray(data, dims=["lat", "lon"], coords={"lat": lat, "lon": lon})
