"""Open IceChunk and AIFS virtual stores as lazy Dask-backed xarray Datasets."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    import xarray as xr

log = structlog.get_logger(__name__)

_DEFAULT_CHUNKS: dict[str, int] = {"time": 10, "latitude": 100, "longitude": 100}


def open_icechunk_store(
    store_uri: str,
    as_of_date: date | None = None,
    chunks: dict | None = None,
) -> xr.Dataset:
    """Open the GIK IceChunk virtual store as a lazy Dask-backed Dataset.

    Args:
        store_uri:   Full URI of the IceChunk store (S3, GCS, or local path).
        as_of_date:  If provided, checkout the store snapshot corresponding to
                     this forecast date (time-travel). Uses the latest commit
                     when omitted.
        chunks:      Dask chunk spec; defaults to ``{time:10, lat:100, lon:100}``.

    Returns:
        Dask-backed xr.Dataset with the IFS ensemble precipitation variables.
    """
    from gik_icechain.conversion.icechunk_writer import IceChainStore

    effective_chunks = chunks or _DEFAULT_CHUNKS

    store_obj = IceChainStore(store_uri)
    if as_of_date is not None:
        ds = store_obj.checkout_as_of(as_of_date)
        log.info("icechunk_store_opened", uri=store_uri, as_of=as_of_date)
    else:
        ds = store_obj.open_latest()
        log.info("icechunk_store_opened", uri=store_uri, as_of="latest")

    if hasattr(ds, "chunk"):
        ds = ds.chunk(effective_chunks)
    return ds


def open_aifs_store(
    store_uri: str,
    chunks: dict | None = None,
) -> xr.Dataset:
    """Open an AIFS ENS Zarr store for parallel exceedance computation.

    Expects the same schema as the IFS IceChunk store (variable ``tp``,
    dimensions ``time``, ``latitude``, ``longitude``, ``number``).

    Args:
        store_uri: Full URI to the AIFS IceChunk or Zarr store.
        chunks:    Dask chunk spec.

    Returns:
        Dask-backed xr.Dataset.
    """
    import xarray as xr

    effective_chunks = chunks or _DEFAULT_CHUNKS
    ds = xr.open_zarr(store_uri, consolidated=False, chunks=effective_chunks)
    log.info("aifs_store_opened", uri=store_uri)
    return ds
