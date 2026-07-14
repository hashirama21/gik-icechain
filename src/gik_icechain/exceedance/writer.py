"""Write exceedance probability results to a multi-dimensional Zarr store.

Output Zarr schema::

    dimensions: (date, latitude, longitude, window, return_period)
    variables:
        exceedance_prob      - float32 in [0, 1]
        ensemble_confidence  - int8 in {0=Low, 1=Medium, 2=High}
                               derived from inter-member IQR/median (24h window)
        tail_ratio           - float32, pXX member accumulation / GEV return level
        median_ratio         - float32, p50 member accumulation / GEV return level
        source_grid_deg      - float32 (date,), native ECMWF grid step: 0.25, or
                               0.4 for pre-2024-02 `0p4-beta` dates
    coords:
        date          - datetime64[D]
        latitude      - float32, degrees N
        longitude     - float32, degrees E
        window        - int16, accumulation window in hours
        return_period - int16, return period in years
"""

from __future__ import annotations

from datetime import date
from typing import Any

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
    source_grid_deg: dict[date, float] | None = None,
    endpoint_url: str | None = None,
    allow_grid_reset: bool = False,
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
        source_grid_deg: Optional mapping forecast date → the native ECMWF grid
                         step (0.25, or 0.4 for pre-2024-02 ``0p4-beta`` dates).
                         Written as the ``(date,)`` variable ``source_grid_deg``
                         so downstream analysis can stratify or exclude an era.
        allow_grid_reset: Permit destroying an existing store whose spatial grid
                         differs (e.g. a deliberate bbox change). Off by default:
                         a silent overwrite would drop every date already written.

    Raises:
        ValueError: When appending onto a store with a different spatial grid
                    and *allow_grid_reset* is False.
    """
    if not exceedance_dict:
        log.warning("write_exceedance_store_empty")
        return

    effective_chunks = chunks or _DEFAULT_CHUNKS
    storage_options = {"endpoint_url": endpoint_url} if endpoint_url else None

    ds = _build_dataset(exceedance_dict, confidence_dict, tail_dict, median_dict, source_grid_deg)
    ds = ds.chunk({k: v for k, v in effective_chunks.items() if k in ds.dims})

    zarr_kw: dict = {}
    if storage_options:
        zarr_kw["storage_options"] = storage_options

    try:
        existing = xr.open_zarr(output_uri, consolidated=False, **zarr_kw)
        compatible, mismatch = _spatial_grids_compatible(ds, existing)
        if append and not compatible and not allow_grid_reset:
            raise ValueError(
                f"Spatial grid differs from the existing store at {output_uri}: {mismatch}. "
                f"Appending would overwrite it and destroy every date already written. "
                f"Causes: a changed bbox, or mixing ECMWF eras (0.4 deg before ~2024-02, "
                f"0.25 deg after). Write the era to its own store, or pass "
                f"allow_grid_reset=True to recreate this one from scratch."
            )
        if append and not compatible:
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
            keep = list(new_dates.values())
            new_ds = _build_dataset(
                {d: exceedance_dict[d] for d in keep},
                _subset_by_date(confidence_dict, keep),
                _subset_by_date(tail_dict, keep),
                _subset_by_date(median_dict, keep),
                _subset_by_date(source_grid_deg, keep),
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


def _fill_value_for(dtype: np.dtype):
    """Missing-data sentinel: NaN for floats, -1 for integer flag variables."""
    return -1 if np.issubdtype(dtype, np.integer) else np.nan


def _subset_by_date(values: dict[date, Any] | None, keep: list[date]) -> dict[date, Any] | None:
    """Restrict a per-date mapping to *keep*; None when nothing is left."""
    if not values:
        return None
    subset = {d: values[d] for d in keep if d in values}
    return subset or None


def _align_append_schema(new_ds: xr.Dataset, existing: xr.Dataset) -> xr.Dataset:
    """Reconcile the non-append dims and variables of *new_ds* with a store.

    Appending along ``date`` requires every other dimension to match the store
    exactly. When a run produces a different set of accumulation windows or
    return periods (e.g. a 3 h window skipped on 6-hourly data - 6 windows vs
    the store's 7), a naive ``to_zarr(append_dim="date")`` raises a cryptic
    shape error and can corrupt the store.

    This reindexes *new_ds* onto the store's ``window``/``return_period``
    coordinates - NaN-filling values the run did not produce - and fails loudly
    if the run introduces *new* coordinate values the store cannot represent.

    Variables get the same treatment: zarr appends each variable independently
    along ``date``, so a store variable absent from this run would silently
    stay short and desync the date dimension across variables (observed on the
    OND-2024 backfill: ``median_ratio``/``tail_ratio`` at 4 dates vs
    ``exceedance_prob`` at 24). Store variables the run did not produce are
    fill-value-padded; variables the store cannot represent fail loudly.
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

    new_vars = {str(v) for v in new_ds.data_vars}
    store_vars = {str(v) for v in existing.data_vars}
    extra_vars = sorted(new_vars - store_vars)
    if extra_vars:
        raise ValueError(
            f"Cannot append: run produced variables {extra_vars} absent from "
            f"the existing store {sorted(store_vars)}. Rewrite the "
            f"store (append=False) to add variables."
        )
    for name in sorted(store_vars - new_vars):
        template = existing[name]
        fill = _fill_value_for(template.dtype)
        log.warning(
            "append_schema_var_filled",
            variable=name,
            fill_value=fill,
            reason="store variable absent from this run; padded to keep "
            "the date dimension in sync across variables",
        )
        sizes = {
            d: (new_ds.sizes[d] if d == "date" else existing.sizes[d])
            for d in template.dims
        }
        new_ds[name] = xr.DataArray(
            np.full(tuple(sizes.values()), fill, dtype=template.dtype),
            dims=tuple(sizes),
            coords={
                d: (new_ds[d].values if d == "date" else existing[d].values)
                for d in template.dims
                if d == "date" or d in existing.coords
            },
            attrs=dict(template.attrs),
        )
    return new_ds


_OPTIONAL_VAR_ATTRS: dict[str, dict] = {
    "tail_ratio": {
        "long_name": "Forecast tail ratio (pXX member accumulation / GEV return level)",
        "units": "1",
        "definition": "possible-worlds tail signal - see exceedance.compute_tail_ratio",
    },
    "median_ratio": {
        "long_name": "Median member ratio (p50 member accumulation / GEV return level)",
        "units": "1",
        "definition": "median-world storyline signal - see exceedance.compute_member_ratio",
    },
    "ensemble_confidence": {
        "long_name": "Ensemble confidence level (24h window)",
        "flag_values": [0, 1, 2],
        "flag_meanings": "low_confidence medium_confidence high_confidence",
        "definition": "IQR/max(median,1mm) - ICPAC EGU26-18323",
    },
    "source_grid_deg": {
        "long_name": "Native ECMWF grid step of the source forecast",
        "units": "degree",
        "definition": "0.25 from ~2024-02; 0.4 for earlier 0p4-beta dates. A coarser "
        "cell smooths extremes, so exceedance is not directly comparable across eras.",
    },
}


def _build_dataset(
    exceedance_dict: dict[date, xr.DataArray],
    confidence_dict: dict[date, xr.DataArray] | None = None,
    tail_dict: dict[date, xr.DataArray] | None = None,
    median_dict: dict[date, xr.DataArray] | None = None,
    source_grid_deg: dict[date, float] | None = None,
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
    if source_grid_deg:
        ds["source_grid_deg"] = xr.DataArray(
            np.array(
                [source_grid_deg.get(d, np.nan) for d in sorted_dates], dtype=np.float32
            ),
            dims=("date",),
            coords={"date": ds["date"]},
            attrs=dict(_OPTIONAL_VAR_ATTRS["source_grid_deg"]),
        )
    optional: dict[str, tuple[dict[date, xr.DataArray] | None, np.dtype]] = {
        "tail_ratio": (tail_dict, np.dtype(np.float32)),
        "median_ratio": (median_dict, np.dtype(np.float32)),
        "ensemble_confidence": (confidence_dict, np.dtype(np.int8)),
    }
    for name, (var_dict, dtype) in optional.items():
        if not var_dict:
            continue
        present = [d for d in sorted_dates if d in var_dict]
        if not present:
            continue
        var = xr.concat([var_dict[d] for d in present], dim="date").astype(dtype)
        if len(present) < len(sorted_dates):
            # A partially-covered variable must still span every date in the
            # batch: writing it short (or dropping it) desyncs the date
            # dimension across variables on append and corrupts the store.
            fill = _fill_value_for(dtype)
            log.warning(
                "exceedance_var_partial_dates",
                variable=name,
                n_present=len(present),
                n_dates=len(sorted_dates),
                fill_value=fill,
            )
            var = var.reindex(date=ds["date"].values, fill_value=fill)
        ds[name] = var
        ds[name].attrs = dict(_OPTIONAL_VAR_ATTRS[name])
    return ds
