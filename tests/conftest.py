"""
tests/conftest.py
Shared pytest fixtures for GIK-IceChain test suite.
"""

import numpy as np
import pandas as pd
import pytest
import xarray as xr


def pytest_configure(config):
    # Pre-register pyarrow's 'file' filesystem scheme before torch/obstore can
    # register it first; on macOS the dyld load order causes ArrowKeyError otherwise.
    import pyarrow.fs
    import pyarrow.parquet  # noqa: F401


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


@pytest.fixture(scope="session")
def square_admin_gdf():
    """Two 2x2-cell square admin-1 units on the 1-degree test grid.

    AA1 (KEN) covers lat 0-1 x lon 10-11; BB2 (SOM) covers lat 2-3 x lon 12-13.
    Shared by aggregator / geojson_writer / risk_engine tests (DRY).
    """
    import geopandas as gpd
    from shapely.geometry import box

    return gpd.GeoDataFrame(
        {
            "admin1_pcode": ["AA1", "BB2"],
            "shapeName": ["Alpha", "Beta"],
            "shapeGroup": ["KEN", "SOM"],
        },
        geometry=[box(9.5, -0.5, 11.5, 1.5), box(11.5, 1.5, 13.5, 3.5)],
        crs="EPSG:4326",
    )


@pytest.fixture
def square_grid_da():
    """4x4 1-degree grid matching square_admin_gdf.

    AA1 cells hold [1, 2, 3, 4] (mean 2.5, max 4); BB2 cells hold
    [10, 20, 30, 40] (mean 25, max 40); cells outside both units are 0.
    """
    lat = np.array([0.0, 1.0, 2.0, 3.0])
    lon = np.array([10.0, 11.0, 12.0, 13.0])
    data = np.zeros((4, 4))
    data[0, 0], data[0, 1], data[1, 0], data[1, 1] = 1.0, 2.0, 3.0, 4.0
    data[2, 2], data[2, 3], data[3, 2], data[3, 3] = 10.0, 20.0, 30.0, 40.0
    return xr.DataArray(
        data,
        dims=("latitude", "longitude"),
        coords={"latitude": lat, "longitude": lon},
    )


@pytest.fixture
def make_evidence():
    """Factory fixture for creating CRMAEvidence with sensible defaults (DRY)."""
    from gik_icechain.risk.crma_model import CRMAEvidence

    def _factory(**kwargs):
        defaults = dict(
            exceedance_prob_24h=0.0,
            exceedance_prob_72h=0.0,
            exceedance_prob_7d=0.0,
            gpm_obs_24h=0.0,
            api_mm=15.0,
            spatial_coverage_fraction=0.1,
            consecutive_signal_days=0,
            sat_consecutive_days=0,
        )
        defaults.update(kwargs)
        return CRMAEvidence(**defaults)

    return _factory
