"""
conversion/virtualizer.py
======================================
VirtualiZarr integration: convert GIK Parquet byte-range references
into a virtual xarray DataTree ready for IceChunk commit.
"""
from __future__ import annotations
import xarray as xr
import structlog

log = structlog.get_logger(__name__)

def parquet_to_virtual_dataset(
    parquet_paths: list[str],
    variables: list[str] | None = None,
) -> xr.Dataset:
    """
    Convert GIK Parquet reference files to a VirtualiZarr virtual dataset.

    Args:
        parquet_paths: List of HuggingFace or local paths to GIK Parquet files.
        variables:     Optional variable filter.

    Returns:
        xr.Dataset with virtual (ManifestArray) data variables.
        No data is downloaded — only byte-range references are stored.
    """
    try:
        import virtualizarr
        from virtualizarr import open_virtual_mfdataset
    except ImportError:
        raise ImportError("virtualizarr required: pip install virtualizarr")

    log.info("virtualizing_parquets", n_files=len(parquet_paths))

    vds = open_virtual_mfdataset(
        parquet_paths,
        format="kerchunk-parquet",
        engine="fsspec",
        consolidated=True,
        combine="nested",
        concat_dim="time",
    )

    if variables:
        vds = vds[variables]

    log.info("virtualization_complete", variables=list(vds.data_vars), dims=dict(vds.dims))
    return vds
