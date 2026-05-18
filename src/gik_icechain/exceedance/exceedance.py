"""
exceedance/exceedance.py
=====================================
Core exceedance probability computation.

For each forecast day, grid cell, accumulation window, and return-period:
  P_exceedance = fraction of 51 ensemble members exceeding the threshold.

This is the primary metric for flood early warning signal detection.
"""
from __future__ import annotations

import structlog
import xarray as xr

log = structlog.get_logger(__name__)

def compute_exceedance_probabilities(
    ensemble_ds: xr.Dataset,
    thresholds_ds: xr.Dataset,
    window_h: int,
    return_period: int,
    member_dim: str = "number",
) -> xr.DataArray:
    """
    Compute empirical exceedance probability for a given window and return period.

    P(X > threshold) = count(members > threshold) / total_members

    Args:
        ensemble_ds:  xr.Dataset with precipitation (lat, lon, step, number).
        thresholds_ds: xr.Dataset with GEV thresholds (lat, lon).
        window_h:     Accumulation window in hours.
        return_period: Return period in years.
        member_dim:   Name of the ensemble member dimension.

    Returns:
        xr.DataArray (lat × lon) of exceedance probabilities in [0, 1].
    """
    threshold = thresholds_ds[f"rp_{return_period}y"]

    n_steps = window_h // 6  # IFS forecast steps are 6-hourly
    tp_window = (
        ensemble_ds["tp"]
        .isel(step=slice(0, n_steps))
        .sum(dim="step")
    )

    exceedance = (tp_window > threshold).mean(dim=member_dim)
    exceedance.attrs.update({
        "long_name": f"Exceedance probability ({window_h}h, {return_period}-year RP)",
        "units": "1",
        "window_h": window_h,
        "return_period": return_period,
    })

    return exceedance
