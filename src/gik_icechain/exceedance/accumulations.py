"""Rolling precipitation accumulations for all forecast windows.

Computes accumulated totals for the 7 accumulation windows used in C2:
3 h, 6 h, 12 h, 24 h, 48 h, 72 h, 168 h (7 days).

IFS ``tp`` is a step-accumulated field (total since T+0), so a true
window accumulation is obtained by differencing adjacent steps rather
than a rolling sum.
"""

from __future__ import annotations

import structlog
import xarray as xr

log = structlog.get_logger(__name__)

WINDOWS_H: list[int] = [3, 6, 12, 24, 48, 72, 168]

_IFS_STEP_HOURS = 6  # IFS ENS is archived at 6-hourly steps


def compute_rolling_accumulations(
    ds: xr.Dataset,
    windows_h: list[int] = WINDOWS_H,
    precip_var: str = "tp",
    step_hours: int = _IFS_STEP_HOURS,
) -> xr.Dataset:
    """Compute accumulated precipitation for each window.

    Args:
        ds:           Dataset with ``precip_var`` as a function of
                      (time, latitude, longitude[, number]).
        windows_h:    List of accumulation window lengths in hours.
        precip_var:   Name of the precipitation variable.
        step_hours:   Hours between consecutive forecast steps.

    Returns:
        Dataset with one variable per window named ``tp_{w}h``.
        Each variable retains the same dimensions as *ds[precip_var]*;
        values represent the window-accumulated total ending at each step.
    """
    if precip_var not in ds:
        raise KeyError(
            f"Variable '{precip_var}' not found in Dataset. Available: {list(ds.data_vars)}"
        )

    # Auto-detect step resolution from the 'step' coordinate when available
    da = ds[precip_var]
    if "step" in da.coords and da.sizes.get("step", 0) >= 2:
        steps = da.coords["step"].values
        detected = int(steps[1] - steps[0])
        if detected > 0:
            step_hours = detected

    accum_vars: dict[str, xr.DataArray] = {}
    for w in windows_h:
        accum_vars[f"tp_{w}h"] = accumulation_for_window(
            ds[precip_var], window_h=w, step_hours=step_hours
        )
        log.debug("window_accumulation_computed", window_h=w)

    result = xr.Dataset(accum_vars)
    result.attrs.update(
        {
            "source_variable": precip_var,
            "step_hours": step_hours,
            "windows_h": windows_h,
        }
    )
    return result


def accumulation_for_window(
    da: xr.DataArray,
    window_h: int,
    step_hours: int = _IFS_STEP_HOURS,
) -> xr.DataArray:
    """Accumulate *da* over *window_h* hours.

    IFS ``tp`` is a step-accumulated field: each value is total precipitation
    since T+0. A *w*-hour window total at step *n* equals ``tp[n] - tp[n - w//step]``.
    The first few steps where the lookback exceeds the available history fall back
    to the raw value at that step.

    Args:
        da:         DataArray with a ``step`` (or ``time``) dimension measured
                    in hours. Values represent cumulative precipitation since T+0.
        window_h:   Accumulation window in hours.
        step_hours: Hours per step.

    Returns:
        DataArray of window-accumulated values with the same dimensions as *da*.
    """
    n_back = window_h // step_hours
    if n_back <= 0:
        raise ValueError(f"window_h={window_h} must be >= step_hours={step_hours}")

    step_dim = _find_time_dim(da)
    n = da.sizes[step_dim]

    if n_back >= n:
        return da.copy()

    current = da.isel({step_dim: slice(n_back, None)})
    lagged = da.isel({step_dim: slice(0, n - n_back)}).assign_coords({step_dim: current[step_dim]})
    window_accum = (current - lagged).clip(min=0.0)

    # Prepend raw values for the initial n_back steps where no prior window exists
    prefix = da.isel({step_dim: slice(0, n_back)})
    result = xr.concat([prefix, window_accum], dim=step_dim)
    result.attrs.update(da.attrs)
    result.attrs["window_h"] = window_h
    return result


def _find_time_dim(da: xr.DataArray) -> str:
    """Return the name of the step/time dimension in *da*."""
    for candidate in ("step", "time", "forecast_period"):
        if candidate in da.dims:
            return candidate
    raise KeyError(f"Cannot identify a step/time dimension in {list(da.dims)}")
