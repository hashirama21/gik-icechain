"""Pre-fetch East Africa domain chunks for low-latency access.

Reads a spatial sub-selection of the IceChunk virtual store into the
obstore in-process cache so that downstream C2/C3 computations see local
read speeds instead of S3 latency.

East Africa bounding box (degrees):
    latitude:   -12° → 23°N
    longitude:   25° → 52°E

Call warm_east_africa_cache() once at process startup (or via the
``gik-icechain warm-cache`` CLI sub-command) before starting the
exceedance or risk pipeline.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from typing import TYPE_CHECKING

import numpy as np
import structlog

if TYPE_CHECKING:
    import xarray as xr

log = structlog.get_logger(__name__)

# East Africa bounding box (WGS-84 degrees)
_LAT_MIN: float = -12.0
_LAT_MAX: float = 23.0
_LON_MIN: float = 25.0
_LON_MAX: float = 52.0

_DEFAULT_N_WORKERS: int = 8


def _slice_east_africa(ds: xr.Dataset) -> xr.Dataset:
    """Return a spatial sub-selection covering the East Africa domain."""
    lat_name = next((c for c in ds.coords if c in ("lat", "latitude")), None)
    lon_name = next((c for c in ds.coords if c in ("lon", "longitude")), None)

    if lat_name is None or lon_name is None:
        return ds

    lat = ds[lat_name].values
    lon = ds[lon_name].values

    lat_mask = (lat >= _LAT_MIN) & (lat <= _LAT_MAX)
    lon_mask = (lon >= _LON_MIN) & (lon <= _LON_MAX)

    return ds.isel(
        {lat_name: np.where(lat_mask)[0], lon_name: np.where(lon_mask)[0]}
    )


def _load_variable(ds: xr.Dataset, var: str) -> int:
    """Trigger a compute on one variable and return the number of bytes loaded."""
    try:
        arr = _slice_east_africa(ds[[var]]).compute()
        n_bytes = int(sum(arr[v].nbytes for v in arr.data_vars))
        log.debug("cache_warmed_var", var=var, mb=round(n_bytes / 1e6, 1))
        return n_bytes
    except Exception as exc:
        log.warning("cache_warm_var_failed", var=var, error=str(exc)[:120])
        return 0


def warm_east_africa_cache(
    ds: xr.Dataset,
    variables: list[str] | None = None,
    n_workers: int = _DEFAULT_N_WORKERS,
) -> dict[str, int]:
    """Pre-fetch East Africa domain chunks into the obstore in-process cache.

    Iterates over the requested variables (or all data_vars) and triggers
    a parallel compute inside the East Africa bounding box. Because obstore
    caches reads at the chunk level, subsequent accesses in the same process
    are served from memory rather than S3.

    Args:
        ds:        Virtual xr.Dataset opened from the IceChunk store.
        variables: Variable names to warm (default: all data_vars).
        n_workers: Thread-pool size for parallel pre-fetching.

    Returns:
        Dict mapping variable name → bytes loaded (0 on failure).
    """
    vars_to_warm = variables or list(ds.data_vars)
    log.info(
        "cache_warm_start",
        n_vars=len(vars_to_warm),
        lat_range=f"{_LAT_MIN}°–{_LAT_MAX}°N",
        lon_range=f"{_LON_MIN}°–{_LON_MAX}°E",
    )

    results: dict[str, int] = {}

    with ThreadPoolExecutor(max_workers=n_workers) as pool:
        futures = {pool.submit(_load_variable, ds, v): v for v in vars_to_warm}
        for future in as_completed(futures):
            var = futures[future]
            results[var] = future.result()

    total_mb = sum(results.values()) / 1e6
    log.info(
        "cache_warm_complete",
        n_vars=len(results),
        total_mb=round(total_mb, 1),
    )
    return results


def warm_date_range(
    store_uri: str,
    start: date,
    end: date,
    variables: list[str] | None = None,
    n_workers: int = _DEFAULT_N_WORKERS,
) -> dict[str, dict[str, int]]:
    """Warm the cache for every committed date in [start, end].

    Opens each date's zarr group from the IceChunk store and pre-fetches
    the East Africa domain for the requested variables.

    Args:
        store_uri:  URI of the IceChunk store.
        start:      First forecast date (inclusive).
        end:        Last forecast date (inclusive).
        variables:  Variable names to warm (default: all data_vars).
        n_workers:  Thread-pool size passed to warm_east_africa_cache().

    Returns:
        Dict mapping date ISO string → per-variable byte counts.
    """
    import xarray as xr

    from gik_icechain.conversion.icechunk_writer import IceChainStore

    store_obj = IceChainStore(store_uri)
    store_obj.create_or_open()
    session = store_obj._repo.readonly_session(branch=store_obj.branch)

    snapshots = store_obj.list_snapshots()
    date_strs = sorted(
        s["forecast_date"]
        for s in snapshots
        if s["forecast_date"] and start.isoformat() <= s["forecast_date"] <= end.isoformat()
    )

    log.info("cache_warm_date_range", n_dates=len(date_strs), start=start, end=end)

    all_results: dict[str, dict[str, int]] = {}
    for date_str in date_strs:
        try:
            ds = xr.open_zarr(session.store, group=date_str, consolidated=False)
            all_results[date_str] = warm_east_africa_cache(ds, variables, n_workers)
        except Exception as exc:
            log.warning("cache_warm_date_failed", date=date_str, error=str(exc)[:120])
            all_results[date_str] = {}

    return all_results
