"""Shared xarray dimension-finding utilities (DRY).

Centralises the step/time, latitude, and longitude dimension finders
that were previously duplicated in exceedance.py, accumulations.py,
and aggregator.py.
"""

from __future__ import annotations

import xarray as xr

_STEP_DIM_CANDIDATES = ("step", "time", "forecast_period")
_LAT_DIM_CANDIDATES = ("latitude", "lat")
_LON_DIM_CANDIDATES = ("longitude", "lon")


def find_dim(da: xr.DataArray, candidates: tuple[str, ...]) -> str:
    """Return the first dimension name from *candidates* that exists in *da*.

    Checks both ``da.dims`` and ``da.coords`` so that non-dimension
    coordinates (e.g. auxiliary lat/lon) are also discoverable.

    Raises:
        KeyError: If none of the candidates are found.
    """
    for c in candidates:
        if c in da.dims or c in da.coords:
            return c
    raise KeyError(
        f"None of {candidates} found in DataArray dims/coords: {list(da.dims)}"
    )


def find_step_dim(da: xr.DataArray) -> str:
    """Return the name of the step/time dimension in *da*.

    Searches for ``step``, ``time``, or ``forecast_period``.
    """
    for candidate in _STEP_DIM_CANDIDATES:
        if candidate in da.dims:
            return candidate
    raise ValueError(
        f"No step/time dimension found in DataArray (dims={list(da.dims)}). "
        f"Expected one of {_STEP_DIM_CANDIDATES}."
    )


def find_lat_dim(da: xr.DataArray) -> str:
    """Return the name of the latitude dimension in *da*."""
    return find_dim(da, _LAT_DIM_CANDIDATES)


def find_lon_dim(da: xr.DataArray) -> str:
    """Return the name of the longitude dimension in *da*."""
    return find_dim(da, _LON_DIM_CANDIDATES)
