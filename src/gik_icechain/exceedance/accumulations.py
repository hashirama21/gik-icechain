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

from gik_icechain.shared.xarray_utils import find_step_dim

log = structlog.get_logger(__name__)

WINDOWS_H: list[int] = [3, 6, 12, 24, 48, 72, 168]

_IFS_STEP_HOURS = 6  # IFS ENS is archived at 6-hourly steps


def compute_rolling_accumulations(
    ds: xr.Dataset,
    windows_h: list[int] = WINDOWS_H,
    precip_var: str = "tp",
    step_hours: int = _IFS_STEP_HOURS,
    skip_subresolution_windows: bool = True,
) -> xr.Dataset:
    """Compute accumulated precipitation for each window.

    Args:
        ds:           Dataset with ``precip_var`` as a function of
                      (time, latitude, longitude[, number]).
        windows_h:    List of accumulation window lengths in hours.
        precip_var:   Name of the precipitation variable.
        step_hours:   Hours between consecutive forecast steps.
        skip_subresolution_windows:
                      When a window is finer than ``step_hours``: True skips it
                      with a warning, False raises ValueError (abort the day).

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
        # Handle windows finer than the step resolution (e.g. a 3 h window when
        # the loaded data is 6-hourly) per the configured policy.
        if w < step_hours:
            if skip_subresolution_windows:
                log.warning(
                    "window_smaller_than_step_skipped",
                    window_h=w,
                    step_hours=step_hours,
                )
                continue
            raise ValueError(
                f"window_h={w} must be >= step_hours={step_hours} "
                f"(set component2.skip_subresolution_windows=true to skip instead)"
            )
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
    since T+0. A *w*-hour window total at the step whose forecast hour is ``h``
    equals ``tp[h] - tp[h - w]``.

    This implementation looks the lagged value up by **actual forecast hour**,
    not by a fixed index offset, so it is correct for the GIK IFS ENS grid
    whose step spacing is non-uniform (3-hourly to 144 h, then 6-hourly to
    360 h).  Consequences:
      * ``h - w <= 0`` (window covers the whole history so far) -> raw value.
      * ``h - w`` falls on a valid step  -> ``tp[h] - tp[h - w]``.
      * ``h - w`` positive but not a stored step (e.g. a 3 h window in the
        6-hourly region) -> NaN (genuinely undefined at that lead time).

    Args:
        da:         DataArray with a ``step`` dimension whose coordinate values
                    are forecast hours. Values are cumulative precip since T+0.
        window_h:   Accumulation window in hours.
        step_hours: Unused for the lookback (kept for API compatibility); the
                    real per-step hours are read from the coordinate.

    Returns:
        DataArray of window-accumulated values with the same dimensions as *da*.
    """
    step_dim = find_step_dim(da)
    hours = da[step_dim].values

    # Lagged forecast hour for each step.
    lookback = hours - window_h
    lookback_da = xr.DataArray(lookback, dims=step_dim, coords={step_dim: hours})

    # tp at the lagged hour (exact match); hours not stored -> NaN.
    lagged = da.reindex({step_dim: lookback}).assign_coords({step_dim: hours})
    # Before T+0 nothing has accumulated: lookback <= 0 -> lagged = 0.
    lagged = lagged.where(lookback_da > 0, 0.0)

    window_accum = (da - lagged).clip(min=0.0)
    window_accum.attrs.update(da.attrs)
    window_accum.attrs["window_h"] = window_h
    return window_accum
