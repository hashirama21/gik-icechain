"""Write exceedance probability results to a multi-dimensional Zarr store.

Output Zarr schema::

    dimensions: (date, latitude, longitude, window, return_period)
    variables:
        exceedance_prob      - float32 in [0, 1]
        ensemble_confidence  - int8 in {0=Low, 1=Medium, 2=High}
                               derived from inter-member IQR/median (24h window)
    coords:
        date          - datetime64[D]
        latitude      - float32, degrees N
        longitude     - float32, degrees E
        window        - int16, accumulation window in hours
        return_period - int16, return period in years
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
    tail_dict: dict[date, xr.DataArray] | None = None,
    median_dict: dict[date, xr.DataArray] | None = None,
    endpoint_url: str | None = None,
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
        tail_dict:       Optional mapping forecast date → tail_ratio DataArray
                         (same (latitude, longitude, window, return_period) dims
                         as exceedance). Written as the variable ``tail_ratio``
                         (the possible-worlds tail / worst-world signal).
        median_dict:     Optional mapping forecast date → median_ratio DataArray
                         (same dims). Written as ``median_ratio`` - the p50
                         member world for the per-member storyline.
    """
    if not exceedance_dict:
        log.warning("write_exceedance_store_empty")
        return

    effective_chunks = chunks or _DEFAULT_CHUNKS
    storage_options = {"endpoint_url": endpoint_url} if endpoint_url else None

    ds = _build_dataset(exceedance_dict, confidence_dict, tail_dict, median_dict)
    ds = ds.chunk({k: v for k, v in effective_chunks.items() if k in ds.dims})

    zarr_kw: dict = {}
    if storage_options:
        zarr_kw["storage_options"] = storage_options

    try:
        existing = xr.open_zarr(output_uri, consolidated=False, **zarr_kw)
        compatible, mismatch = _spatial_grids_compatible(ds, existing)
        if append and not compatible:
            # The store was written with a different spatial grid (the bbox
            # changed between runs). Dates already in the store were computed on
            # the old grid, so they cannot be concatenated along `date` and are
            # no longer consistent with the new domain. Recreate the store.
            log.warning(
                "exceedance_store_grid_changed",
                mismatch=mismatch,
                uri=output_uri,
                action="bbox changed; recreating store (mode=w)",
            )
        elif append:
            existing_dates = set(str(d)[:10] for d in existing["date"].values)
            new_dates = {
                d.isoformat(): d for d in exceedance_dict if d.isoformat() not in existing_dates
            }
            if not new_dates:
                log.info("write_exceedance_store_no_new_dates")
                return
            new_conf = (
                {d: confidence_dict[d] for d in new_dates.values() if d in confidence_dict}
                if confidence_dict
                else None
            )
            new_tail = (
                {d: tail_dict[d] for d in new_dates.values() if d in tail_dict}
                if tail_dict
                else None
            )
            new_median = (
                {d: median_dict[d] for d in new_dates.values() if d in median_dict}
                if median_dict
                else None
            )
            new_ds = _build_dataset(
                {d: exceedance_dict[d] for d in new_dates.values()},
                new_conf, new_tail, new_median,
            )
            new_ds = _align_append_schema(new_ds, existing)
            new_ds = new_ds.chunk(
                {k: v for k, v in effective_chunks.items() if k in new_ds.dims}
            )
            new_ds.to_zarr(output_uri, mode="a", append_dim="date", **zarr_kw)
            log.info("exceedance_store_appended", n_dates=len(new_dates), uri=output_uri)
            return
    except (FileNotFoundError, KeyError):
        pass

    ds.to_zarr(output_uri, mode="w", consolidated=True, **zarr_kw)
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


def _spatial_grids_compatible(
    new_ds: xr.Dataset, existing: xr.Dataset
) -> tuple[bool, dict]:
    """Check whether *new_ds* shares the store's ``latitude``/``longitude`` grid.

    Appending along ``date`` is only meaningful when the spatial grid is
    identical. A changed bounding box (e.g. the East Africa coverage extension
    to -14.5°) shifts the grid, so the store must be recreated rather than
    appended. Returns ``(compatible, mismatch_info)``.
    """
    mismatch: dict = {}
    for dim in ("latitude", "longitude"):
        if dim not in existing.dims or dim not in new_ds.dims:
            continue
        same = existing.sizes[dim] == new_ds.sizes[dim] and np.array_equal(
            np.asarray(existing[dim].values), np.asarray(new_ds[dim].values)
        )
        if not same:
            mismatch[dim] = {
                "store": int(existing.sizes[dim]),
                "run": int(new_ds.sizes[dim]),
            }
    return (len(mismatch) == 0, mismatch)


def _align_append_schema(new_ds: xr.Dataset, existing: xr.Dataset) -> xr.Dataset:
    """Reconcile the non-append dims of *new_ds* with an existing store.

    Appending along ``date`` requires every other dimension to match the store
    exactly. When a run produces a different set of accumulation windows or
    return periods (e.g. a 3 h window skipped on 6-hourly data - 6 windows vs
    the store's 7), a naive ``to_zarr(append_dim="date")`` raises a cryptic
    shape error and can corrupt the store.

    This reindexes *new_ds* onto the store's ``window``/``return_period``
    coordinates - NaN-filling values the run did not produce - and fails loudly
    if the run introduces *new* coordinate values the store cannot represent.
    """
    for dim in ("window", "return_period"):
        if dim not in existing.dims or dim not in new_ds.dims:
            continue
        existing_coord = existing[dim].values
        extra = sorted(set(new_ds[dim].values.tolist()) - set(existing_coord.tolist()))
        if extra:
            raise ValueError(
                f"Cannot append along {dim!r}: run produced new values {extra} "
                f"absent from the existing store {existing_coord.tolist()}. "
                f"Rewrite the store (append=False) or use a consistent "
                f"window/return_period configuration."
            )
        if not np.array_equal(new_ds[dim].values, existing_coord):
            log.warning(
                "append_schema_reindexed",
                dim=dim,
                run_values=new_ds[dim].values.tolist(),
                store_values=existing_coord.tolist(),
            )
            new_ds = new_ds.reindex({dim: existing_coord})
    return new_ds


def _build_dataset(
    exceedance_dict: dict[date, xr.DataArray],
    confidence_dict: dict[date, xr.DataArray] | None = None,
    tail_dict: dict[date, xr.DataArray] | None = None,
    median_dict: dict[date, xr.DataArray] | None = None,
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
    if tail_dict:
        tail_arrays = [tail_dict[d] for d in sorted_dates if d in tail_dict]
        if len(tail_arrays) == len(sorted_dates):
            tail_combined = xr.concat(tail_arrays, dim="date")
            ds["tail_ratio"] = tail_combined.astype(np.float32)
            ds["tail_ratio"].attrs = {
                "long_name": "Forecast tail ratio (pXX member accumulation / GEV return level)",
                "units": "1",
                "definition": "possible-worlds tail signal - see exceedance.compute_tail_ratio",
            }
    if median_dict:
        median_arrays = [median_dict[d] for d in sorted_dates if d in median_dict]
        if len(median_arrays) == len(sorted_dates):
            median_combined = xr.concat(median_arrays, dim="date")
            ds["median_ratio"] = median_combined.astype(np.float32)
            ds["median_ratio"].attrs = {
                "long_name": "Median member ratio (p50 member accumulation / GEV return level)",
                "units": "1",
                "definition": "median-world storyline signal - see exceedance.compute_member_ratio",
            }
    if confidence_dict:
        conf_arrays = [confidence_dict[d] for d in sorted_dates if d in confidence_dict]
        if len(conf_arrays) == len(sorted_dates):
            conf_combined = xr.concat(conf_arrays, dim="date")
            ds["ensemble_confidence"] = conf_combined.astype(np.int8)
            ds["ensemble_confidence"].attrs = {
                "long_name": "Ensemble confidence level (24h window)",
                "flag_values": [0, 1, 2],
                "flag_meanings": "low_confidence medium_confidence high_confidence",
                "definition": "IQR/max(median,1mm) - ICPAC EGU26-18323",
            }
    return ds
