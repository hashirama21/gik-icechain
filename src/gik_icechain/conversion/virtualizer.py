"""VirtualiZarr integration for GIK Kerchunk flat-parquet reference files.

Each GIK Parquet file is a single flat key/value parquet where each row is
one Kerchunk reference entry pointing to a GRIB2 byte range on s3://ecmwf-forecasts.
This is distinct from the multi-file directory format expected by
virtualizarr's built-in KerchunkParquetParser.
"""

from __future__ import annotations

import os
import structlog
import xarray as xr

log = structlog.get_logger(__name__)

_ECMWF_BUCKET = "ecmwf-forecasts"
_ECMWF_S3_PREFIX = f"s3://{_ECMWF_BUCKET}/"
_ECMWF_REGION = "eu-west-1"


class GIKFlatParquetParser:
    """Parse a GIK flat-parquet Kerchunk file into a virtualizarr ManifestStore.

    GIK parquets store all references in a single file with 'key' and 'value'
    columns, unlike the directory-based Kerchunk parquet store format expected
    by virtualizarr's built-in KerchunkParquetParser.
    """

    def __call__(self, url: str, registry: object) -> object:
        import base64

        import fsspec
        import pandas as pd
        from virtualizarr.manifests import ManifestStore
        from virtualizarr.parsers.kerchunk.translator import manifestgroup_from_kerchunk_refs
        from virtualizarr.types.kerchunk import KerchunkStoreRefs

        with fsspec.open(url, "rb") as f:
            df = pd.read_parquet(f)

        refs: dict[str, object] = {}
        for _, row in df.iterrows():
            k = str(row["key"])
            v = row["value"]
            if isinstance(v, (bytes, bytearray)):
                try:
                    v = v.decode("utf-8")
                except (UnicodeDecodeError, AttributeError):
                    v = "base64:" + base64.b64encode(v).decode("ascii")
            refs[k] = v

        store_refs = KerchunkStoreRefs({"refs": refs})
        manifestgroup = manifestgroup_from_kerchunk_refs(store_refs)
        return ManifestStore(group=manifestgroup, registry=registry)


def _build_ecmwf_registry() -> object:
    """Return an ObjectStoreRegistry for ECMWF GRIB2 byte-range reads.

    By default, targets the public AWS S3 bucket (anonymous access).
    Set GIK_ECMWF_ENDPOINT_URL to redirect to a local MinIO mirror instead,
    e.g. GIK_ECMWF_ENDPOINT_URL=http://minio.example.com:9000
    """
    from obspec_utils.registry import ObjectStoreRegistry
    from obstore.store import S3Store

    endpoint_url = os.environ.get("GIK_ECMWF_ENDPOINT_URL")
    if endpoint_url:
        store = S3Store(
            _ECMWF_BUCKET,
            endpoint_url=endpoint_url,
            virtual_hosted_style_request=False,
        )
        log.info("ecmwf_registry_minio", endpoint=endpoint_url)
    else:
        store = S3Store(_ECMWF_BUCKET, region=_ECMWF_REGION, skip_signature=True)
    return ObjectStoreRegistry({_ECMWF_S3_PREFIX: store})


def _open_one_virtual(url: str, variables: list[str] | None, registry: object) -> xr.Dataset:
    """Open a single GIK flat-parquet as a virtual xr.Dataset."""
    from virtualizarr import open_virtual_dataset

    parser = GIKFlatParquetParser()
    vds = open_virtual_dataset(url=url, registry=registry, parser=parser, loadable_variables=[])
    if variables:
        keep = [v for v in variables if v in vds.data_vars]
        if keep:
            vds = vds[keep]
    return vds


def parquet_to_virtual_dataset(
    parquet_paths: list[str],
    variables: list[str] | None = None,
) -> xr.Dataset:
    """Convert GIK flat-parquet Kerchunk files into a virtual xarray Dataset.

    Each path is one (date, run_hour, member) file on HuggingFace. Multiple
    files are concatenated along a new 'member' dimension.

    Args:
        parquet_paths: HuggingFace hf:// or local paths to GIK Parquet files.
        variables:     Optional GRIB2 shortNames to retain (applied per file).

    Returns:
        xr.Dataset with ManifestArray data variables (no data downloaded).
    """
    try:
        from virtualizarr import open_virtual_dataset  # noqa: F401
    except ImportError as exc:
        raise ImportError("pip install virtualizarr") from exc

    if not parquet_paths:
        raise ValueError("parquet_paths must not be empty")

    registry = _build_ecmwf_registry()
    log.info("virtualizing_parquets", n_files=len(parquet_paths))

    if len(parquet_paths) == 1:
        vds = _open_one_virtual(parquet_paths[0], variables, registry)
        log.info("virtualization_complete", variables=list(vds.data_vars), dims=dict(vds.dims))
        return vds

    datasets = []
    for path in parquet_paths:
        try:
            ds = _open_one_virtual(path, variables, registry)
            datasets.append(ds)
        except Exception:
            log.warning("parquet_skipped", path=path, exc_info=True)

    if not datasets:
        raise RuntimeError("All parquet files failed to virtualize")

    try:
        vds = xr.concat(datasets, dim="member")
    except Exception:
        log.warning("member_concat_failed_returning_first", n_files=len(datasets))
        vds = datasets[0]

    log.info("virtualization_complete", variables=list(vds.data_vars), dims=dict(vds.dims))
    return vds
