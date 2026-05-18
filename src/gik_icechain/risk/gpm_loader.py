"""Load GPM IMERG v7 daily precipitation and compute Antecedent Precipitation Index."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import structlog
import xarray as xr

log = structlog.get_logger(__name__)

_GPM_PATTERNS = (
    "3B-DAY.MS.MRG.3IMERG.{date_str}-S000000-E235959.1440.V07B.HDF5",
    "3B-DAY.MS.MRG.3IMERG.{date_str}.V07B.nc4",
    "*{date_str}*.nc4",
    "*{date_str}*.HDF5",
)
_PRECIP_VARS = ("precipitationCal", "precipitation", "HQprecipitation")


def load_gpm_daily(gpm_dir: Path, day: date) -> xr.DataArray | None:
    """Load GPM IMERG v7 daily precipitation for a single date.

    Tries standard HDF5 and nc4 naming conventions then falls back to glob.

    Args:
        gpm_dir: Directory (or directory tree) containing GPM IMERG files.
        day:     Observation date.

    Returns:
        ``precipitationCal`` DataArray in mm/day, squeezed to (lat, lon),
        or None if no file is found.
    """
    date_str = day.strftime("%Y%m%d")
    for pattern_tpl in _GPM_PATTERNS:
        pattern = pattern_tpl.format(date_str=date_str)
        matches = list(gpm_dir.glob(f"**/{pattern}"))
        if not matches:
            continue
        for engine in ("netcdf4", "h5netcdf"):
            try:
                ds = xr.open_dataset(matches[0], engine=engine)
                for var in _PRECIP_VARS:
                    if var in ds:
                        return ds[var].squeeze()
            except Exception:
                continue
    log.debug("gpm_file_not_found", date=day)
    return None


def load_gpm_range(gpm_dir: Path, start: date, end: date) -> xr.Dataset:
    """Load and concatenate GPM daily files into a Dataset along the ``time`` axis.

    Missing dates are silently skipped; the resulting Dataset may not cover
    the full [start, end] range if files are absent.

    Args:
        gpm_dir: Directory containing GPM IMERG files.
        start:   First date (inclusive).
        end:     Last date (inclusive).

    Returns:
        Dataset with variable ``precipitationCal`` and dimension ``time``.
    """
    arrays: list[xr.DataArray] = []
    dates: list[date] = []

    current = start
    while current <= end:
        da = load_gpm_daily(gpm_dir, current)
        if da is not None:
            arrays.append(da)
            dates.append(current)
        current += timedelta(days=1)

    if not arrays:
        return xr.Dataset()

    combined = xr.concat(
        arrays,
        dim=xr.DataArray(
            [pd.Timestamp(d) for d in dates],
            dims="time",
            name="time",
        ),
    )
    return xr.Dataset({"precipitationCal": combined})


def compute_api_series(
    gpm_ds: xr.Dataset,
    decay: float = 0.8,
    initial_mm: float = 20.0,
    precip_var: str = "precipitationCal",
) -> xr.DataArray:
    """Compute the Antecedent Precipitation Index day-by-day over a gridded dataset.

    ``API(t) = obs(t) + decay * API(t-1)``

    Each time step is processed sequentially; the result has the same
    spatial dimensions as *gpm_ds* and a ``time`` dimension.

    Args:
        gpm_ds:     Dataset with *precip_var* as (time, lat, lon).
        decay:      Exponential decay factor (0–1); default 0.8.
        initial_mm: API value for the step before the first observation.
        precip_var: Name of the precipitation variable in *gpm_ds*.

    Returns:
        DataArray of API values with dims (time, lat, lon).
    """
    if precip_var not in gpm_ds:
        raise KeyError(f"Variable '{precip_var}' not found in dataset")

    da = gpm_ds[precip_var]
    times = da["time"].values
    spatial_shape = da.isel(time=0).shape
    spatial_coords = {k: v for k, v in da.coords.items() if k != "time"}

    api = np.full(spatial_shape, initial_mm, dtype=np.float32)
    api_slices: list[np.ndarray] = []

    for t in times:
        obs = da.sel(time=t).values.astype(np.float32)
        obs = np.where(np.isfinite(obs), obs, 0.0)
        api = obs + decay * api
        api_slices.append(api.copy())

    api_stack = np.stack(api_slices, axis=0)
    return xr.DataArray(
        api_stack,
        dims=("time", *[d for d in da.dims if d != "time"]),
        coords={"time": da["time"], **spatial_coords},
        attrs={"long_name": "Antecedent Precipitation Index", "units": "mm", "decay": decay},
    )
