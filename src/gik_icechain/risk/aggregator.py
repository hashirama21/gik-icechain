"""Spatial aggregation of gridded fields to admin-1 units."""

from __future__ import annotations

from typing import Any, Literal

import geopandas as gpd
import numpy as np
import pandas as pd
import regionmask
import structlog
import xarray as xr

log = structlog.get_logger(__name__)


def aggregate_to_admin1(
    da: xr.DataArray,
    admin_gdf: gpd.GeoDataFrame,
    stat: Literal["mean", "max", "area_weighted"] = "mean",
    min_coverage: float = 0.5,
    pcode_col: str = "admin1_pcode",
) -> "pd.Series":
    """Aggregate a (lat, lon) DataArray to admin-1 polygon statistics.

    Grid cells that fall outside an admin-1 boundary are excluded.
    Admin-1 units whose coverage fraction is below *min_coverage* receive
    NaN rather than a potentially misleading statistic.

    Args:
        da:           DataArray with dimensions (latitude, longitude) or
                      (lat, lon).
        admin_gdf:    GeoDataFrame with admin-1 boundaries.
        stat:         Aggregation statistic.
        min_coverage: Minimum fraction of grid cells that must be covered;
                      units with fewer valid cells receive NaN.
        pcode_col:    Column in *admin_gdf* used as the Series index.

    Returns:
        Series indexed by *pcode_col* with one value per admin-1 unit.
    """
    results: dict[str, float] = {}

    for _, unit in admin_gdf.iterrows():
        pcode = str(unit[pcode_col])
        geom  = unit.geometry
        try:
            mask = regionmask.Regions([geom]).mask(
                da,
                lon_name=_find_dim(da, ("longitude", "lon")),
                lat_name=_find_dim(da, ("latitude",  "lat")),
            )
            inside = da.where(mask == 0)
            total  = int((mask == 0).sum())
            valid  = int(inside.count())
            if total == 0 or (valid / total) < min_coverage:
                results[pcode] = float("nan")
                continue

            if stat == "mean":
                val = float(inside.mean(skipna=True))
            elif stat == "max":
                val = float(inside.max(skipna=True))
            elif stat == "area_weighted":
                val = _area_weighted_mean(inside, geom)
            else:
                raise ValueError(f"Unknown stat: {stat!r}")

            results[pcode] = val if np.isfinite(val) else 0.0
        except Exception as exc:
            log.debug("aggregation_failed", pcode=pcode, error=str(exc))
            results[pcode] = 0.0

    return pd.Series(results, name=stat)


def coverage_fraction(
    da: xr.DataArray,
    admin_gdf: gpd.GeoDataFrame,
    threshold: float,
    pcode_col: str = "admin1_pcode",
) -> "pd.Series":
    """Fraction of grid cells within each admin-1 boundary exceeding *threshold*.

    Args:
        da:        DataArray with spatial dimensions (lat, lon).
        admin_gdf: GeoDataFrame with admin-1 boundaries.
        threshold: Exceedance threshold value.
        pcode_col: Column in *admin_gdf* used as the Series index.

    Returns:
        Series indexed by *pcode_col* with values in [0, 1].
    """
    results: dict[str, float] = {}

    for _, unit in admin_gdf.iterrows():
        pcode = str(unit[pcode_col])
        geom  = unit.geometry
        try:
            mask  = regionmask.Regions([geom]).mask(
                da,
                lon_name=_find_dim(da, ("longitude", "lon")),
                lat_name=_find_dim(da, ("latitude",  "lat")),
            )
            inside = da.where(mask == 0)
            total  = int((mask == 0).sum())
            if total == 0:
                results[pcode] = 0.0
                continue
            above = int((inside > threshold).sum(skipna=True))
            results[pcode] = above / total
        except Exception as exc:
            log.debug("coverage_fraction_failed", pcode=pcode, error=str(exc))
            results[pcode] = 0.0

    return pd.Series(results, name="coverage_fraction")


def _find_dim(da: xr.DataArray, candidates: tuple[str, ...]) -> str:
    """Return the first dimension name from *candidates* that exists in *da*."""
    for c in candidates:
        if c in da.dims or c in da.coords:
            return c
    raise KeyError(f"None of {candidates} found in DataArray dims/coords: {list(da.dims)}")


def _area_weighted_mean(da: xr.DataArray, geom: Any) -> float:
    """Cosine-latitude-weighted mean within *geom*."""
    lat_name = _find_dim(da, ("latitude", "lat"))
    lat_vals = da[lat_name].values
    weights = np.cos(np.deg2rad(lat_vals))
    if da.ndim == 2:
        weights_2d = np.broadcast_to(weights[:, None], da.shape)
    else:
        weights_2d = weights
    arr = da.values
    mask_valid = np.isfinite(arr)
    if not mask_valid.any():
        return float("nan")
    return float(
        np.sum(arr[mask_valid] * weights_2d[mask_valid]) / np.sum(weights_2d[mask_valid])
    )
