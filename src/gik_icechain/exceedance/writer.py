"""Write exceedance probability results to a multi-dimensional Zarr store.

Output Zarr schema::

    dimensions: (date, latitude, longitude, window, return_period)
    variables:
        exceedance_prob      — float32 in [0, 1]
        ensemble_confidence  — int8 in {0=Low, 1=Medium, 2=High}
                               derived from inter-member IQR/median (24h window)
    coords:
        date          — datetime64[D]
        latitude      — float32, degrees N
        longitude     — float32, degrees E
        window        — int16, accumulation window in hours
        return_period — int16, return period in years
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import structlog
import xarray as xr

log = structlog.get_logger(__name__)

_DEFAULT_CHUNKS: dict[str, int | None] = {
    "date": 30,
    "latitude": 100,
    "longitude": 100,
    "window": None,
    "return_period": None,
}


def write_exceedance_store(
    exceedance_dict: dict[date, xr.DataArray],
    output_uri: str,
    chunks: dict | None = None,
    append: bool = True,
    confidence_dict: dict[date, xr.DataArray] | None = None,
) -> None:
    """Write or extend the exceedance Zarr store with new forecast dates.

    Each DataArray in *exceedance_dict* must have dimensions
    ``(latitude, longitude, window, return_period)``.

    Args:
        exceedance_dict: Mapping forecast date → exceedance DataArray.
        output_uri:      S3 or local URI for the output Zarr store.
        chunks:          Override default Zarr chunking.
        append:          If True, append along the ``date`` dimension when
                         the store already exists; otherwise overwrite.
        confidence_dict: Optional mapping forecast date → ensemble_confidence
                         DataArray (lat × lon, int8 0/1/2). Written as a
                         second variable ``ensemble_confidence``.
    """
    if not exceedance_dict:
        log.warning("write_exceedance_store_empty")
        return

    effective_chunks = chunks or _DEFAULT_CHUNKS

    ds = _build_dataset(exceedance_dict, confidence_dict)
    ds = ds.chunk({k: v for k, v in effective_chunks.items() if k in ds.dims})

    try:
        existing = xr.open_zarr(output_uri, consolidated=False)
        if append:
            existing_dates = set(str(d)[:10] for d in existing["date"].values)
            new_dates = {
                d.isoformat(): d for d in exceedance_dict if d.isoformat() not in existing_dates
            }
            if not new_dates:
                log.info("write_exceedance_store_no_new_dates")
                return
            new_conf = (
                {d: confidence_dict[d] for d in new_dates.values() if d in confidence_dict}
                if confidence_dict else None
            )
            new_ds = _build_dataset(
                {d: exceedance_dict[d] for d in new_dates.values()}, new_conf
            ).chunk({k: v for k, v in effective_chunks.items() if k in ds.dims})
            new_ds.to_zarr(output_uri, mode="a", append_dim="date")
            log.info("exceedance_store_appended", n_dates=len(new_dates), uri=output_uri)
            return
    except (FileNotFoundError, KeyError):
        pass

    ds.to_zarr(output_uri, mode="w", consolidated=True)
    log.info("exceedance_store_written", n_dates=len(exceedance_dict), uri=output_uri)


def build_exceedance_dataset(
    results: dict[tuple[int, int], xr.DataArray],
    forecast_date: date,
) -> xr.DataArray:
    """Assemble per-(window, return_period) DataArrays into a single DataArray.

    Args:
        results:       Mapping (window_h, return_period) → DataArray (lat × lon).
        forecast_date: The forecast date these results correspond to.

    Returns:
        DataArray with dimensions (date, latitude, longitude, window, return_period).
    """
    windows = sorted({w for w, _ in results})
    rps = sorted({rp for _, rp in results})

    stacked = xr.concat(
        [
            xr.concat(
                [results[(w, rp)] for rp in rps],
                dim=xr.DataArray(rps, dims="return_period", name="return_period"),
            )
            for w in windows
        ],
        dim=xr.DataArray(windows, dims="window", name="window"),
    )
    stacked = stacked.assign_coords(date=pd.Timestamp(forecast_date)).expand_dims("date")
    stacked.attrs.update({"long_name": "Exceedance probability", "units": "1"})
    return stacked.astype(np.float32)


def _build_dataset(
    exceedance_dict: dict[date, xr.DataArray],
    confidence_dict: dict[date, xr.DataArray] | None = None,
) -> xr.Dataset:
    sorted_dates = sorted(exceedance_dict)
    combined = xr.concat([exceedance_dict[d] for d in sorted_dates], dim="date")
    ds = xr.Dataset(
        {"exceedance_prob": combined.astype(np.float32)},
        attrs={
            "title": "GIK-IceChain exceedance probabilities",
            "source": "ECMWF IFS ENS via GIK-IceChain v2.0",
            "conventions": "CF-1.8",
        },
    )
    if confidence_dict:
        conf_arrays = [
            confidence_dict[d] for d in sorted_dates if d in confidence_dict
        ]
        if len(conf_arrays) == len(sorted_dates):
            conf_combined = xr.concat(conf_arrays, dim="date")
            ds["ensemble_confidence"] = conf_combined.astype(np.int8)
            ds["ensemble_confidence"].attrs = {
                "long_name": "Ensemble confidence level (24h window)",
                "flag_values": [0, 1, 2],
                "flag_meanings": "low_confidence medium_confidence high_confidence",
                "definition": "IQR/max(median,1mm) — ICPAC EGU26-18323",
            }
    return ds
