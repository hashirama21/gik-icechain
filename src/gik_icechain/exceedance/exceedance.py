"""
exceedance/exceedance.py
=====================================
Core exceedance probability computation.

For each forecast day, grid cell, accumulation window, and return-period:
  P_exceedance = fraction of 51 ensemble members where the worst-case
  window accumulation exceeds the GEV threshold.

Callers must pass the output of ``compute_rolling_accumulations``, which
provides variables named ``tp_{window_h}h`` (one per accumulation window).
"""

from __future__ import annotations

import numpy as np
import structlog
import xarray as xr

log = structlog.get_logger(__name__)

_STEP_DIM_CANDIDATES = ("step", "time", "forecast_period")


def _find_step_dim(da: xr.DataArray) -> str | None:
    for candidate in _STEP_DIM_CANDIDATES:
        if candidate in da.dims:
            return candidate
    return None


def compute_exceedance_probabilities(
    acc_ds: xr.Dataset,
    thresholds_ds: xr.Dataset,
    window_h: int,
    return_period: int,
    member_dim: str = "number",
) -> xr.DataArray:
    """Compute empirical exceedance probability for a given window and return period.

    P(X > threshold) = fraction of members whose worst-case window accumulation
    over the forecast horizon exceeds the GEV threshold.

    Args:
        acc_ds:        xr.Dataset produced by ``compute_rolling_accumulations``,
                       containing variable ``tp_{window_h}h`` with dims
                       (step, number, latitude, longitude) — or equivalent.
        thresholds_ds: xr.Dataset with GEV threshold variable ``rp_{return_period}y``
                       (lat × lon).
        window_h:      Accumulation window in hours; selects ``tp_{window_h}h``.
        return_period: Return period in years; selects ``rp_{return_period}y``.
        member_dim:    Name of the ensemble member dimension.

    Returns:
        xr.DataArray (lat × lon) of exceedance probabilities in [0, 1].
    """
    var_name = f"tp_{window_h}h"
    if var_name not in acc_ds:
        raise KeyError(
            f"Variable '{var_name}' not found in accumulated dataset. "
            f"Available: {list(acc_ds.data_vars)}"
        )

    threshold = thresholds_ds[f"rp_{return_period}y"]
    tp = acc_ds[var_name]

    # Worst-case accumulation over the forecast horizon, per member per grid cell
    step_dim = _find_step_dim(tp)
    tp_worst = tp.max(dim=step_dim) if step_dim is not None else tp

    exceedance = (tp_worst > threshold).mean(dim=member_dim)
    exceedance.attrs.update(
        {
            "long_name": f"Exceedance probability ({window_h}h, {return_period}-year RP)",
            "units": "1",
            "window_h": window_h,
            "return_period": return_period,
        }
    )
    return exceedance


def compute_ensemble_confidence(
    acc_ds: xr.Dataset,
    window_h: int,
    member_dim: str = "number",
) -> xr.DataArray:
    """Compute ensemble confidence from inter-member spread (ICPAC EGU26-18323).

    A high IQR/median ratio indicates member divergence → low confidence.
    A low ratio indicates member agreement → high confidence.

    States:
        0 = Low confidence    (IQR / max(median, 1 mm) > 1.0)
        1 = Medium confidence (0.3 < ratio ≤ 1.0)
        2 = High confidence   (ratio ≤ 0.3)

    Args:
        acc_ds:     Accumulated dataset from ``compute_rolling_accumulations``.
                    Variable ``tp_{window_h}h`` must be present.
        window_h:   Accumulation window in hours.
        member_dim: Name of the ensemble member dimension.

    Returns:
        xr.DataArray (lat × lon) of integer confidence states 0, 1, or 2.
    """
    var_name = f"tp_{window_h}h"
    if var_name not in acc_ds:
        raise KeyError(
            f"Variable '{var_name}' not found. Available: {list(acc_ds.data_vars)}"
        )

    tp = acc_ds[var_name]
    step_dim = _find_step_dim(tp)
    tp_worst = tp.max(dim=step_dim) if step_dim is not None else tp

    q25 = tp_worst.quantile(0.25, dim=member_dim)
    q75 = tp_worst.quantile(0.75, dim=member_dim)
    median = tp_worst.median(dim=member_dim)

    # Normalise by max(median, 1 mm) to avoid division-by-zero in dry cells
    iqr_norm = (q75 - q25) / np.maximum(median, 1.0)

    confidence = xr.zeros_like(median, dtype=np.int8)
    confidence = confidence.where(iqr_norm > 1.0, other=1)   # Medium where ratio ≤ 1
    confidence = confidence.where(iqr_norm > 0.3, other=2)   # High where ratio ≤ 0.3

    confidence.attrs = {
        "long_name": "Ensemble confidence level",
        "flag_values": [0, 1, 2],
        "flag_meanings": "low_confidence medium_confidence high_confidence",
        "definition": "IQR / max(median, 1 mm) — ICPAC EGU26-18323",
        "window_h": window_h,
    }
    return confidence
