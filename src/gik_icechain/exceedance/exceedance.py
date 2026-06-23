"""Core exceedance probability computation.

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

from gik_icechain.shared.xarray_utils import find_step_dim

log = structlog.get_logger(__name__)


def _align_threshold_to_forecast(
    threshold: xr.DataArray, forecast: xr.DataArray
) -> xr.DataArray:
    """Rename and interpolate threshold grid to match the forecast spatial grid.

    CMORPH thresholds use (lat, lon) at ~1° over East Africa, while ECMWF
    forecasts use (latitude, longitude) at 0.25° globally.  This function
    standardises dimension names and bilinearly interpolates the threshold
    to the forecast grid so that the comparison broadcasts element-wise.
    Grid cells outside the threshold domain become NaN (→ exceedance = 0).
    """
    thr_lat = next((d for d in threshold.dims if d in ("lat", "latitude")), None)
    thr_lon = next((d for d in threshold.dims if d in ("lon", "longitude")), None)
    fc_lat = next((d for d in forecast.dims if d in ("lat", "latitude")), None)
    fc_lon = next((d for d in forecast.dims if d in ("lon", "longitude")), None)

    if not all([thr_lat, thr_lon, fc_lat, fc_lon]):
        return threshold  # Can't align — return as-is

    if (
        thr_lat == fc_lat
        and thr_lon == fc_lon
        and threshold.sizes[thr_lat] == forecast.sizes[fc_lat]
    ):
        return threshold

    rename_map = {}
    if thr_lat != fc_lat:
        rename_map[thr_lat] = fc_lat
    if thr_lon != fc_lon:
        rename_map[thr_lon] = fc_lon
    if rename_map:
        threshold = threshold.rename(rename_map)

    threshold = threshold.interp(
        {fc_lat: forecast[fc_lat], fc_lon: forecast[fc_lon]},
        method="linear",
    )
    return threshold


def compute_exceedance_probabilities(
    acc_ds: xr.Dataset,
    thresholds_ds: xr.Dataset,
    window_h: int,
    return_period: int,
    member_dim: str = "number",
    flood_floor_mm: float = 0.0,
) -> xr.DataArray:
    """Compute empirical exceedance probability for a given window and return period.

    P(X > threshold) = fraction of members whose worst-case window accumulation
    over the forecast horizon exceeds the GEV threshold.

    Args:
        acc_ds:        xr.Dataset produced by ``compute_rolling_accumulations``,
                       containing variable ``tp_{window_h}h`` with dims
                       (step, number, latitude, longitude) — or equivalent.
        thresholds_ds: xr.Dataset with GEV threshold variable ``rp_{return_period}y``
                       (lat × lon).  Dimension names and grid resolution may
                       differ from acc_ds — they will be aligned automatically.
        window_h:      Accumulation window in hours; selects ``tp_{window_h}h``.
        return_period: Return period in years; selects ``rp_{return_period}y``.
        member_dim:    Name of the ensemble member dimension.

    Returns:
        xr.DataArray (latitude × longitude) of exceedance probabilities in [0, 1].
    """
    var_name = f"tp_{window_h}h"
    if var_name not in acc_ds:
        raise KeyError(
            f"Variable '{var_name}' not found in accumulated dataset. "
            f"Available: {list(acc_ds.data_vars)}"
        )

    threshold = thresholds_ds[f"rp_{return_period}y"]
    tp = acc_ds[var_name]

    step_dim = find_step_dim(tp)
    tp_worst = tp.max(dim=step_dim)

    threshold = _align_threshold_to_forecast(threshold, tp_worst)

    # Flood-relevance floor: arid cells have near-zero GEV thresholds → a few mm
    # would false-alarm. Raise the effective threshold to a flood-capable minimum
    # (equatorial thresholds >> floor are unaffected; NaN stays NaN → exceedance 0).
    if flood_floor_mm > 0:
        threshold = threshold.clip(min=flood_floor_mm)

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


def compute_tail_ratio(
    acc_ds: xr.Dataset,
    thresholds_ds: xr.Dataset,
    window_h: int,
    return_period: int,
    member_dim: str = "number",
    tail_quantile: float = 0.95,
    flood_floor_mm: float = 0.0,
) -> xr.DataArray:
    """Possible-worlds tail signal: the high quantile of member accumulation / threshold.

    Where :func:`compute_exceedance_probabilities` collapses the ensemble to the
    *fraction* of members above the threshold (an expected-value / mean-based
    view), this collapses it to the *tail*: the ``tail_quantile`` (default p95)
    member's worst-case window accumulation expressed as a ratio to the GEV
    return level.

    A ratio ≥ 1.0 means the p95 ensemble member reaches the return level even
    when the *mean* fraction is ~0 — the convective "wet tail" that an
    ensemble-mean trigger is blind to (cf. the Nairobi Mar-2026 flash flood,
    mean ~18 mm but a tail member at 131 mm). This is the forecast-side input to
    the tail-aware ``Forecast_Hazard`` node.

    Args:
        acc_ds:        Dataset from ``compute_rolling_accumulations`` with
                       ``tp_{window_h}h`` (dims include *member_dim* and step).
        thresholds_ds: Dataset with GEV variable ``rp_{return_period}y`` (lat×lon).
        window_h:      Accumulation window in hours.
        return_period: Return period in years.
        member_dim:    Ensemble member dimension name.
        tail_quantile: Upper quantile over members (0.95 = worst plausible world).
        flood_floor_mm: Flood-relevance floor applied to the threshold (mirrors
                       :func:`compute_exceedance_probabilities`).

    Returns:
        xr.DataArray (latitude × longitude) of the tail ratio (≥ 0, unitless).
        Cells outside the threshold domain are NaN (→ no tail signal downstream).
    """
    var_name = f"tp_{window_h}h"
    if var_name not in acc_ds:
        raise KeyError(
            f"Variable '{var_name}' not found in accumulated dataset. "
            f"Available: {list(acc_ds.data_vars)}"
        )

    threshold = thresholds_ds[f"rp_{return_period}y"]
    tp = acc_ds[var_name]

    step_dim = find_step_dim(tp)
    tp_worst = tp.max(dim=step_dim)

    threshold = _align_threshold_to_forecast(threshold, tp_worst)
    if flood_floor_mm > 0:
        threshold = threshold.clip(min=flood_floor_mm)

    # p95 member accumulation, then ratio to the (floored) return level. Divide
    # by a positive-clipped threshold so dry/NaN cells stay NaN rather than inf.
    tp_tail = tp_worst.quantile(tail_quantile, dim=member_dim)
    if "quantile" in tp_tail.coords:
        tp_tail = tp_tail.drop_vars("quantile")
    ratio = tp_tail / threshold.where(threshold > 0)
    ratio.attrs.update(
        {
            "long_name": (
                f"Tail ratio (p{int(tail_quantile * 100)} member / "
                f"{return_period}-year RP, {window_h}h)"
            ),
            "units": "1",
            "window_h": window_h,
            "return_period": return_period,
            "tail_quantile": tail_quantile,
        }
    )
    return ratio


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
    step_dim = find_step_dim(tp)
    tp_worst = tp.max(dim=step_dim)

    q25 = tp_worst.quantile(0.25, dim=member_dim)
    q75 = tp_worst.quantile(0.75, dim=member_dim)
    median = tp_worst.median(dim=member_dim)

    # Normalise by max(median, 1 mm) to avoid division-by-zero in dry cells
    iqr_norm = (q75 - q25) / np.maximum(median, 1.0)

    confidence = xr.where(
        iqr_norm <= 0.3, 2,
        xr.where(iqr_norm <= 1.0, 1, 0),
    ).astype(np.int8)

    confidence.attrs = {
        "long_name": "Ensemble confidence level",
        "flag_values": [0, 1, 2],
        "flag_meanings": "low_confidence medium_confidence high_confidence",
        "definition": "IQR / max(median, 1 mm) — ICPAC EGU26-18323",
        "window_h": window_h,
    }
    return confidence
