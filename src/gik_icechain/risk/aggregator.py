"""Spatial aggregation of gridded fields to admin-1 units.

Uses ``regionmask.from_geopandas()`` to rasterise all admin-1 polygons into a
single mask in one pass, then vectorised groupby operations for mean/max.
Only ``area_weighted`` falls back to a per-region loop.
"""

from __future__ import annotations

from typing import Literal

import geopandas as gpd
import numpy as np
import pandas as pd
import structlog
import xarray as xr

from gik_icechain.shared.xarray_utils import find_lat_dim, find_lon_dim

try:
    import regionmask
    _REGIONMASK_AVAILABLE = True
except ImportError:
    regionmask = None  # type: ignore[assignment]
    _REGIONMASK_AVAILABLE = False

log = structlog.get_logger(__name__)


def _build_multi_region_mask(
    da: xr.DataArray,
    admin_gdf: gpd.GeoDataFrame,
    pcode_col: str = "admin1_pcode",
) -> tuple[xr.DataArray, dict[int, str]]:
    """Rasterise all admin-1 polygons into a single integer mask (O(1) per region).

    Returns:
        Tuple of (mask DataArray with integer region IDs, mapping of region number → pcode).
    """
    lat_name = find_lat_dim(da)
    lon_name = find_lon_dim(da)
    regions = regionmask.from_geopandas(admin_gdf)
    mask = regions.mask(da, lon_name=lon_name, lat_name=lat_name)
    mask.name = "region"
    num_to_pcode = dict(
        zip(regions.numbers, admin_gdf[pcode_col].astype(str))
    )
    return mask, num_to_pcode


def aggregate_to_admin1(
    da: xr.DataArray,
    admin_gdf: gpd.GeoDataFrame,
    stat: Literal["mean", "max", "area_weighted"] = "mean",
    min_coverage: float = 0.5,
    pcode_col: str = "admin1_pcode",
) -> pd.Series:
    """Aggregate a (lat, lon) DataArray to admin-1 polygon statistics.

    Grid cells that fall outside an admin-1 boundary are excluded.
    Admin-1 units whose coverage fraction is below *min_coverage* receive
    NaN rather than a potentially misleading statistic.

    Uses a single ``regionmask.from_geopandas()`` call for all regions
    (vectorised), then xarray groupby for mean/max.

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
    try:
        mask, num_to_pcode = _build_multi_region_mask(da, admin_gdf, pcode_col)
    except Exception as exc:
        log.warning("multi_region_mask_failed", error=str(exc))
        return pd.Series(dtype=float, name=stat)

    if stat == "area_weighted":
        return _aggregate_area_weighted(da, mask, num_to_pcode, min_coverage, stat)

    # Vectorised path for mean/max
    da_masked = da.where(mask.notnull())

    if stat == "mean":
        grouped_stat = da_masked.groupby(mask).mean(skipna=True)
    elif stat == "max":
        grouped_stat = da_masked.groupby(mask).max(skipna=True)
    else:
        raise ValueError(f"Unknown stat: {stat!r}")

    # Coverage: valid data cells / total cells inside each region
    total_per_region = mask.notnull().groupby(mask).sum()
    valid_per_region = da_masked.notnull().groupby(mask).sum()

    results: dict[str, float] = {}
    for region_num, pcode in num_to_pcode.items():
        try:
            total = float(total_per_region.sel(region=region_num))
        except (KeyError, ValueError):
            results[pcode] = float("nan")
            continue

        if total == 0:
            results[pcode] = float("nan")
            continue

        valid = float(valid_per_region.sel(region=region_num))
        if (valid / total) < min_coverage:
            results[pcode] = float("nan")
            continue

        val = float(grouped_stat.sel(region=region_num))
        results[pcode] = val if np.isfinite(val) else float("nan")

    return pd.Series(results, name=stat)


def coverage_fraction(
    da: xr.DataArray,
    admin_gdf: gpd.GeoDataFrame,
    threshold: float,
    pcode_col: str = "admin1_pcode",
) -> pd.Series:
    """Fraction of grid cells within each admin-1 boundary exceeding *threshold*.

    Uses a single ``regionmask.from_geopandas()`` call for all regions
    (vectorised).

    Args:
        da:        DataArray with spatial dimensions (lat, lon).
        admin_gdf: GeoDataFrame with admin-1 boundaries.
        threshold: Exceedance threshold value.
        pcode_col: Column in *admin_gdf* used as the Series index.

    Returns:
        Series indexed by *pcode_col* with values in [0, 1].
    """
    try:
        mask, num_to_pcode = _build_multi_region_mask(da, admin_gdf, pcode_col)
    except Exception as exc:
        log.warning("multi_region_mask_failed", error=str(exc))
        return pd.Series(dtype=float, name="coverage_fraction")

    total_per_region = mask.notnull().groupby(mask).sum()
    above_per_region = (da.where(mask.notnull()) > threshold).groupby(mask).sum(skipna=True)

    results: dict[str, float] = {}
    for region_num, pcode in num_to_pcode.items():
        try:
            total = float(total_per_region.sel(region=region_num))
        except (KeyError, ValueError):
            results[pcode] = 0.0
            continue

        if total == 0:
            results[pcode] = 0.0
            continue

        above = float(above_per_region.sel(region=region_num))
        results[pcode] = above / total

    return pd.Series(results, name="coverage_fraction")


def _aggregate_area_weighted(
    da: xr.DataArray,
    mask: xr.DataArray,
    num_to_pcode: dict[int, str],
    min_coverage: float,
    stat_name: str,
) -> pd.Series:
    """Cosine-latitude-weighted mean per region (loop-based for weighting)."""
    lat_name = find_lat_dim(da)

    results: dict[str, float] = {}
    for region_num, pcode in num_to_pcode.items():
        region_mask = mask == region_num
        total = int(region_mask.sum())
        if total == 0:
            results[pcode] = float("nan")
            continue

        inside = da.where(region_mask)
        valid = int(inside.count())
        if (valid / total) < min_coverage:
            results[pcode] = float("nan")
            continue

        val = _area_weighted_mean(inside, lat_name)
        results[pcode] = val if np.isfinite(val) else float("nan")

    return pd.Series(results, name=stat_name)


def _area_weighted_mean(da: xr.DataArray, lat_name: str) -> float:
    """Cosine-latitude-weighted mean over valid cells."""
    lat_vals = da[lat_name].values
    weights = np.cos(np.deg2rad(lat_vals))
    weights_2d = np.broadcast_to(weights[:, None], da.shape) if da.ndim == 2 else weights
    arr = da.values
    mask_valid = np.isfinite(arr)
    if not mask_valid.any():
        return float("nan")
    return float(
        np.sum(arr[mask_valid] * weights_2d[mask_valid])
        / np.sum(weights_2d[mask_valid])
    )
