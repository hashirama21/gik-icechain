"""Open IceChunk and AIFS virtual stores as lazy Dask-backed xarray Datasets."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    import xarray as xr

log = structlog.get_logger(__name__)

_DEFAULT_CHUNKS: dict[str, int] = {"member": -1, "step": -1, "latitude": 50, "longitude": 50}
_AIFS_DEFAULT_CHUNKS: dict[str, int] = {"number": -1, "step": -1, "latitude": 50, "longitude": 50}


def open_icechunk_store(
    store_uri: str,
    as_of_date: date | None = None,
    chunks: dict | None = None,
) -> xr.Dataset:
    """Open the GIK IceChunk virtual store as a lazy Dask-backed Dataset.

    Each date lives in its own zarr group inside the IceChunk repo.
    The store is opened at the snapshot matching *as_of_date*, or the
    most recent snapshot when omitted.

    Args:
        store_uri:   Full URI of the IceChunk store (S3, GCS, or local path).
        as_of_date:  If provided, time-travel to the snapshot for this date.
                     Uses the latest commit when omitted.
        chunks:      Dask chunk spec. Defaults to all-members and all-steps in
                     one chunk, spatial blocks of 50×50.

    Returns:
        Dask-backed xr.Dataset for the resolved date group.
    """
    from gik_icechain.conversion.icechunk_writer import IceChainStore

    effective_chunks = chunks or _DEFAULT_CHUNKS
    store_obj = IceChainStore(store_uri)
    store_obj.create_or_open()

    if as_of_date is not None:
        ds = store_obj.checkout_as_of(as_of_date)
        log.info("icechunk_store_opened", uri=store_uri, as_of=as_of_date)
    else:
        ds = store_obj.open_latest()
        log.info("icechunk_store_opened", uri=store_uri, as_of="latest")

    return ds.chunk(effective_chunks)


def open_aifs_store(
    store_uri: str,
    chunks: dict | None = None,
) -> xr.Dataset:
    """Open an AIFS ENS Zarr store for parallel exceedance computation.

    Args:
        store_uri: Full URI to the AIFS IceChunk or conventional Zarr store.
        chunks:    Dask chunk spec.

    Returns:
        Dask-backed xr.Dataset.
    """
    import xarray as xr

    effective_chunks = chunks or _AIFS_DEFAULT_CHUNKS
    ds = xr.open_zarr(store_uri, consolidated=False).chunk(effective_chunks)
    log.info("aifs_store_opened", uri=store_uri)
    return ds
