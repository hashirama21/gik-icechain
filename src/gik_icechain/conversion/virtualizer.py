"""VirtualiZarr integration for GIK Kerchunk flat-parquet reference files.

GIK Parquet files use a custom key schema:
  - Chunk refs:  step_{NNN}/{var}/{level_type}/{member}/{chunk_idx}  ->  ["s3://...", offset, len]
  - Zarr meta:   {var_longname}/{typeOfLevel}/{level}/.zarray|.zattrs  ->  JSON dict
  - Root keys:   zarr_consolidated_format, metadata, .zgroup  (ignored)

The kerchunk translator (find_var_names) requires variables at depth 1: {var}/.zarray.
We remap to flat keys using the GRIB2 shortname as the zarr variable name.
Only surface (sfc) variables are virtualised; pressure-level (pl) variables are
skipped because their level-count cannot be reliably inferred from the metadata.
"""

from __future__ import annotations

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

import structlog
import xarray as xr

log = structlog.get_logger(__name__)

_ECMWF_BUCKET = "ecmwf-forecasts"
_ECMWF_S3_PREFIX = f"s3://{_ECMWF_BUCKET}/"
_ECMWF_REGION = "eu-west-1"

# Matches sfc chunk refs only: step_NNN/{var}/sfc/{member}/{chunk_idx}
_SFC_STEP_RE = re.compile(r"^step_(\d+)/([^/]+)/sfc/[^/]+/(.+)$")

_DEFAULT_N_WORKERS = 10


def _to_ref_value(v: object) -> str | list:
    """Normalise a parquet value cell to a kerchunk-compatible str or list."""
    if isinstance(v, (bytes, bytearray)):
        try:
            return v.decode("utf-8")
        except (UnicodeDecodeError, AttributeError):
            import base64

            return "base64:" + base64.b64encode(v).decode("ascii")
    if isinstance(v, (list, str)):
        return v
    return json.dumps(v)


class GIKFlatParquetParser:
    """Parse a GIK flat-parquet file into a virtualizarr ManifestStore.

    Translates the GIK step_NNN/{var}/sfc/{member}/{chunk} key schema into
    standard kerchunk refs ({var}/{step_pos}.0.0) so that virtualizarr's
    kerchunk translator can build ManifestArray objects.

    The resulting virtual dataset has one variable per GRIB2 shortname with
    dimensions [step, latitude, longitude].  Pressure-level variables are
    excluded because the combined level count per chunk is not encoded in the
    parquet metadata.

    After each call, ``self.step_hours`` holds the sorted list of forecast step
    hour values (e.g. [0, 3, 6, …, 360]) for use as dimension coordinates.
    """

    def __init__(self) -> None:
        self.step_hours: list[int] = []

    def __call__(self, url: str, registry: object) -> object:
        import fsspec
        import pandas as pd
        from virtualizarr.manifests import ManifestStore
        from virtualizarr.parsers.kerchunk.translator import manifestgroup_from_kerchunk_refs
        from virtualizarr.types.kerchunk import KerchunkStoreRefs

        with fsspec.open(url, "rb") as f:
            df = pd.read_parquet(f)

        df["key"] = df["key"].astype(str)

        chunk_rows: list[dict] = []
        for _, row in df.iterrows():
            m = _SFC_STEP_RE.match(row["key"])
            if m:
                step_num, var, _chunk_idx = m.groups()
                chunk_rows.append({"step_num": int(step_num), "var": var, "value": row["value"]})

        if not chunk_rows:
            log.warning("gik_parquet_no_sfc_chunk_refs", url=url)
            self.step_hours = []
            store_refs = KerchunkStoreRefs({"refs": {".zgroup": '{"zarr_format": 2}'}})
            return ManifestStore(
                group=manifestgroup_from_kerchunk_refs(store_refs), registry=registry
            )

        # Infer spatial dimensions from any sfc .zarray entry in the metadata
        nlat, nlon, default_dtype = 181, 360, "<f4"
        for _, row in df[df["key"].str.endswith(".zarray")].head(5).iterrows():
            v = row["value"]
            if isinstance(v, (bytes, bytearray)):
                v = v.decode()
            try:
                meta = json.loads(v)
                sh = meta.get("shape", [])
                if len(sh) >= 2:
                    nlat, nlon = int(sh[-2]), int(sh[-1])
                    default_dtype = meta.get("dtype", default_dtype)
                    break
            except Exception:
                pass

        chunk_df = pd.DataFrame(chunk_rows)
        # Persist step hours so _open_one_virtual can assign step coordinates
        self.step_hours = sorted(chunk_df["step_num"].unique().tolist())

        refs: dict[str, object] = {".zgroup": '{"zarr_format": 2}'}

        for var, grp in chunk_df.groupby("var"):
            grp = grp.sort_values("step_num").reset_index(drop=True)
            n_steps = len(grp)

            # Flat key: {var}/.zarray so that find_var_names picks it up at depth 1
            refs[f"{var}/.zarray"] = json.dumps(
                {
                    "chunks": [1, nlat, nlon],
                    "compressor": None,
                    "dtype": default_dtype,
                    "fill_value": "NaN",
                    "filters": None,
                    "order": "C",
                    "shape": [n_steps, nlat, nlon],
                    "zarr_format": 2,
                }
            )
            refs[f"{var}/.zattrs"] = json.dumps(
                {"_ARRAY_DIMENSIONS": ["step", "latitude", "longitude"]}
            )

            for step_pos, (_, row) in enumerate(grp.iterrows()):
                refs[f"{var}/{step_pos}.0.0"] = _to_ref_value(row["value"])

        log.debug(
            "gik_refs_built",
            url=url,
            n_vars=len(chunk_df["var"].unique()),
            n_steps=int(chunk_df["step_num"].nunique()),
            nlat=nlat,
            nlon=nlon,
        )

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
    """Open a single GIK flat-parquet as a virtual xr.Dataset with step coordinates."""
    import numpy as np
    from virtualizarr import open_virtual_dataset

    parser = GIKFlatParquetParser()
    vds = open_virtual_dataset(url=url, registry=registry, parser=parser, loadable_variables=[])

    if variables:
        keep = [v for v in variables if v in vds.data_vars]
        if keep:
            vds = vds[keep]

    # Assign step-hour coordinates so xr.concat(join="inner") can align
    # members that differ by one step (e.g. 84 vs 85 steps in the ENFO archive).
    if parser.step_hours:
        step_coord = np.array(parser.step_hours, dtype=np.int32)
        vds = vds.assign_coords(step=("step", step_coord))
        vds["step"].attrs.update({"units": "hours", "long_name": "forecast_step"})

    return vds


def parquet_to_virtual_dataset(
    parquet_paths: list[str],
    variables: list[str] | None = None,
    n_workers: int = _DEFAULT_N_WORKERS,
) -> xr.Dataset:
    """Convert GIK flat-parquet Kerchunk files into a virtual xarray Dataset.

    Each path is one (date, run_hour, member) file on HuggingFace. Multiple
    files are concatenated along a new 'member' dimension. Downloads are
    parallelized with a thread pool (I/O-bound).

    Args:
        parquet_paths: HuggingFace hf:// or local paths to GIK Parquet files.
        variables:     Optional GRIB2 shortNames to retain (applied per file).
        n_workers:     Thread pool size for parallel downloads (default: 10).

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

    # Parallel I/O — preserve insertion order for stable member indexing
    datasets: list[xr.Dataset | None] = [None] * len(parquet_paths)

    def _fetch(idx: int, path: str) -> tuple[int, xr.Dataset | None]:
        try:
            return idx, _open_one_virtual(path, variables, registry)
        except Exception:
            log.warning("parquet_skipped", path=path, exc_info=True)
            return idx, None

    with ThreadPoolExecutor(max_workers=n_workers) as pool:
        futures = {pool.submit(_fetch, i, p): i for i, p in enumerate(parquet_paths)}
        for future in as_completed(futures):
            idx, ds = future.result()
            datasets[idx] = ds

    valid = [ds for ds in datasets if ds is not None]
    n_failed = len(parquet_paths) - len(valid)

    if not valid:
        raise RuntimeError("All parquet files failed to virtualize")
    if n_failed:
        log.warning("parquet_partial_failure", failed=n_failed, succeeded=len(valid))

    if len(valid) == 1:
        vds = valid[0]
        log.info("virtualization_complete", variables=list(vds.data_vars), dims=dict(vds.dims))
        return vds

    try:
        import pandas as pd

        member_idx = pd.Index(range(len(valid)), name="member")
        # join="inner" aligns on shared step-hour coordinates, handling members
        # with different step counts (84 vs 85 steps common in the ENFO archive).
        vds = xr.concat(valid, dim=member_idx, join="inner", coords="minimal")
    except Exception as exc:
        log.warning(
            "member_concat_failed_returning_first",
            n_files=len(valid),
            error=str(exc)[:300],
        )
        vds = valid[0]

    log.info("virtualization_complete", variables=list(vds.data_vars), dims=dict(vds.dims))
    return vds
