"""Input validation at system boundaries."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import geopandas as gpd
    import xarray as xr


def validate_date_range(start: date, end: date) -> None:
    """Raise ValueError if *start* > *end* or either is in the future."""
    today = date.today()
    if start > end:
        raise ValueError(f"start={start} must be <= end={end}")
    if start > today:
        raise ValueError(f"start={start} is in the future")


def validate_ensemble_dims(ds: xr.Dataset, required_dims: list[str]) -> None:
    """Raise ValueError if any of *required_dims* is absent from *ds*.

    Args:
        ds:            Dataset to inspect.
        required_dims: Dimension names that must be present.
    """
    missing = [d for d in required_dims if d not in ds.dims]
    if missing:
        raise ValueError(
            f"Dataset is missing required dimensions: {missing}. Available: {list(ds.dims)}"
        )


def validate_exceedance_array(da: xr.DataArray) -> None:
    """Raise ValueError if *da* contains values outside [0, 1].

    Ignores NaN values (they are allowed - masked ocean / no-data cells).
    """
    import numpy as np

    arr = da.values
    finite = arr[np.isfinite(arr)]
    if len(finite) == 0:
        return
    if finite.min() < 0.0 or finite.max() > 1.0:
        raise ValueError(
            f"Exceedance probabilities must be in [0, 1]; "
            f"got min={finite.min():.4f}, max={finite.max():.4f}"
        )


def validate_admin_gdf(
    gdf: gpd.GeoDataFrame,
    required_cols: list[str],
) -> None:
    """Raise ValueError if any of *required_cols* is absent from *gdf*.

    Args:
        gdf:           GeoDataFrame to inspect.
        required_cols: Column names that must be present.
    """
    missing = [c for c in required_cols if c not in gdf.columns]
    if missing:
        raise ValueError(
            f"Admin GeoDataFrame is missing required columns: {missing}. "
            f"Available: {list(gdf.columns)}"
        )
