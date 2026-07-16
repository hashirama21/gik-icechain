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

import contextlib
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

import structlog
import xarray as xr

log = structlog.get_logger(__name__)

from gik_icechain.shared.codec_registry import register_grib_codecs  # noqa: E402
from gik_icechain.shared.grid import DEFAULT_SHAPE, shape_for_uri  # noqa: E402

register_grib_codecs()

_ECMWF_BUCKET = "ecmwf-forecasts"
_ECMWF_S3_PREFIX = f"s3://{_ECMWF_BUCKET}/"
_ECMWF_REGION = "eu-central-1"

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

_OLD_ECMWF_PATH_RE = re.compile(
    r"^(s3://ecmwf-forecasts/\d{8}/\d{2}z/)(?!ifs/|aifs/)(\dp[^/]+/)"
)


def _fix_ecmwf_uri(ref: str | list) -> str | list:
    """Fix ECMWF S3 URIs for two known issues in GIK parquet references.

    1. Append ``.grib2`` when the file extension is missing.
    2. Insert ``ifs/`` into the path when the old pre-2024 layout is used.

    The ECMWF S3 bucket was restructured: the resolution directory (e.g.
    ``0p25/``) moved from ``{date}/{tz}/0p25/`` to ``{date}/{tz}/ifs/0p25/``.
    The GIK flat-parquet files on HuggingFace still reference the old paths.
    """
    if isinstance(ref, list) and len(ref) >= 3:
        uri = str(ref[0])
        if uri.startswith(_ECMWF_S3_PREFIX):
            uri = _OLD_ECMWF_PATH_RE.sub(r"\1ifs/\2", uri)
            if not uri.endswith((".grib2", ".index")):
                uri += ".grib2"
            return [uri, *list(ref[1:])]
    elif isinstance(ref, str):
        try:
            parsed = json.loads(ref)
            if isinstance(parsed, list) and len(parsed) >= 3:
                uri = str(parsed[0])
                if uri.startswith(_ECMWF_S3_PREFIX):
                    uri = _OLD_ECMWF_PATH_RE.sub(r"\1ifs/\2", uri)
                    if not uri.endswith((".grib2", ".index")):
                        uri += ".grib2"
                    parsed[0] = uri
                    return parsed
        except (json.JSONDecodeError, ValueError):
            pass
    return ref


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

    def __init__(self, variables: list[str] | None = None) -> None:
        self.step_hours: list[int] = []
        self._variables: set[str] | None = set(variables) if variables else None

    def __call__(self, url: str, registry: object) -> object:
        import fsspec
        import pandas as pd
        from virtualizarr.manifests import ManifestStore
        from virtualizarr.parsers.kerchunk.translator import manifestgroup_from_kerchunk_refs
        from virtualizarr.types.kerchunk import KerchunkStoreRefs

        with fsspec.open(url, "rb") as f:
            df = pd.read_parquet(f)

        df["key"] = df["key"].astype(str)

        extracted = df["key"].str.extract(_SFC_STEP_RE, expand=True)
        sfc_mask = extracted[0].notna()

        if not sfc_mask.any():
            log.warning("gik_parquet_no_sfc_chunk_refs", url=url)
            self.step_hours = []
            store_refs = KerchunkStoreRefs({"refs": {".zgroup": '{"zarr_format": 2}'}})
            return ManifestStore(
                group=manifestgroup_from_kerchunk_refs(store_refs), registry=registry  # type: ignore[arg-type]
            )

        chunk_df = pd.DataFrame({
            "step_num": extracted.loc[sfc_mask, 0].astype(int).values,
            "var": extracted.loc[sfc_mask, 1].values,
            "value": df.loc[sfc_mask, "value"].values,
        })

        # Infer spatial dimensions.  The parquet .zarray entries often carry
        # incorrect spatial shape (e.g. 181×360 for 1° while the actual GRIB2
        # messages on s3://ecmwf-forecasts/ are 0.25° = 721×1440).  We detect
        # the grid resolution from the S3 URI pattern and override when possible.
        nlat, nlon = DEFAULT_SHAPE
        default_dtype = "<f4"
        first_uri = ""
        for val in chunk_df["value"].head(3):
            ref = _to_ref_value(val)
            if isinstance(ref, str):
                with contextlib.suppress(json.JSONDecodeError, ValueError):
                    ref = json.loads(ref)
            if isinstance(ref, list) and len(ref) >= 1:
                first_uri = str(ref[0])
                break
        uri_shape = shape_for_uri(first_uri)
        if uri_shape is not None:
            nlat, nlon = uri_shape
        else:
            # Fall back to parquet .zarray metadata
            for v in df.loc[df["key"].str.endswith(".zarray"), "value"].head(5):
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
                    log.debug("zarray_meta_parse_skipped", exc_info=True)
        log.debug("gik_grid_resolution", nlat=nlat, nlon=nlon, uri_hint=first_uri[:80])

        # Pre-filter to requested variables before building refs so that
        # variables with inconsistent step counts (e.g. 10fg vs tp) don't
        # cause a conflicting-dimension error in xr.Dataset construction.
        if self._variables:
            chunk_df = chunk_df[chunk_df["var"].isin(self._variables)]

        self.step_hours = sorted(chunk_df["step_num"].unique().tolist())

        refs: dict[str, object] = {".zgroup": '{"zarr_format": 2}'}

        for var, grp in chunk_df.groupby("var"):
            grp = grp.sort_values("step_num").reset_index(drop=True)
            n_steps = len(grp)

            # Flat key so find_var_names picks it up; GRIBCodec filter decodes GRIB2 byte-ranges.
            refs[f"{var}/.zarray"] = json.dumps(
                {
                    "chunks": [1, nlat, nlon],
                    "compressor": None,
                    "dtype": default_dtype,
                    "fill_value": "NaN",
                    "filters": [{"id": "grib", "var": var}],
                    "order": "C",
                    "shape": [n_steps, nlat, nlon],
                    "zarr_format": 2,
                }
            )
            refs[f"{var}/.zattrs"] = json.dumps(
                {"_ARRAY_DIMENSIONS": ["step", "latitude", "longitude"]}
            )

            for step_pos, val in enumerate(grp["value"]):
                refs[f"{var}/{step_pos}.0.0"] = _fix_ecmwf_uri(_to_ref_value(val))

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
        return ManifestStore(group=manifestgroup, registry=registry)  # type: ignore[arg-type]


def _build_ecmwf_registry() -> object:
    """Return an ObjectStoreRegistry for ECMWF GRIB2 byte-range reads.

    By default, targets the public AWS S3 bucket (anonymous access).
    Set GIK_ECMWF_ENDPOINT_URL to redirect to an S3-compatible mirror instead,
    e.g. GIK_ECMWF_ENDPOINT_URL=http://mirror.example.com:9000
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
        log.info("ecmwf_registry_custom_endpoint", endpoint=endpoint_url)
    else:
        store = S3Store(_ECMWF_BUCKET, region=_ECMWF_REGION, skip_signature=True)
    return ObjectStoreRegistry({_ECMWF_S3_PREFIX: store})


def _open_one_virtual(url: str, variables: list[str] | None, registry: object) -> xr.Dataset:
    """Open a single GIK flat-parquet as a virtual xr.Dataset with step coordinates."""
    import numpy as np
    from virtualizarr import open_virtual_dataset

    parser = GIKFlatParquetParser(variables=variables)
    vds = open_virtual_dataset(url=url, registry=registry, parser=parser, loadable_variables=[])  # type: ignore[arg-type]

    if variables:
        keep = [v for v in variables if v in vds.data_vars]
        if keep:
            vds = vds[keep]

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

    # Preserve insertion order for stable member indexing.
    datasets: list[xr.Dataset | None] = [None] * len(parquet_paths)

    def _fetch(idx: int, path: str) -> tuple[int, xr.Dataset | None]:
        try:
            return idx, _open_one_virtual(path, variables, registry)
        except (OSError, ValueError, KeyError, RuntimeError):
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
        from collections import Counter

        import pandas as pd

        # Group by step count (ManifestArrays can't reindex); keep the majority.
        step_sizes = Counter(ds.dims.get("step", 0) for ds in valid)
        majority_n = step_sizes.most_common(1)[0][0]
        majority = [ds for ds in valid if ds.dims.get("step", 0) == majority_n]
        if len(majority) < len(valid):
            log.warning(
                "member_step_count_filtered",
                total=len(valid),
                kept=len(majority),
                dropped=len(valid) - len(majority),
                step_count=majority_n,
            )

        member_idx = pd.Index(range(len(majority)), name="member")
        vds = xr.concat(majority, dim=member_idx, join="override", coords="minimal")
    except Exception as exc:
        log.warning(
            "member_concat_failed_returning_first",
            n_files=len(valid),
            error=str(exc)[:300],
        )
        vds = valid[0]

    log.info("virtualization_complete", variables=list(vds.data_vars), dims=dict(vds.dims))
    return vds
