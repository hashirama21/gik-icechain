"""AIFS ENS discovery and virtualisation from ECMWF S3.

Deterministically constructs S3 URIs for AIFS ensemble forecast GRIB2 files,
scans them with kerchunk, and assembles a ManifestArray-backed virtual xarray
Dataset suitable for ingestion into an IceChunk store via ``commit_day()``.

AIFS ENS files live on the ECMWF open-data bucket (anonymous, no subscription)
under the ``aifs-ens`` product prefix::

    s3://ecmwf-forecasts/{YYYYMMDD}/{HH}z/aifs-ens/0p25/enfo/
        {YYYYMMDDHH}0000-{step}h-enfo-pf.grib2   (50 perturbed members)
        {YYYYMMDDHH}0000-{step}h-enfo-cf.grib2   (1 control member)

The open AIFS ENS archive begins mid-2025; earlier dates 404. Use OND 2025
onward for validation.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from typing import Any

import structlog
import xarray as xr

log = structlog.get_logger(__name__)


_ECMWF_BUCKET = "ecmwf-forecasts"
_ECMWF_S3_PREFIX = f"s3://{_ECMWF_BUCKET}/"
_AIFS_PATH_TEMPLATE = "{date_str}/{run_hour:02d}z/aifs-ens/0p25/enfo/"
# Perturbed forecast (pf) holds all 50 perturbed members; control (cf) holds 1.
_AIFS_PF_FILENAME = "{date_str}{run_hour:02d}0000-{step}h-enfo-pf.grib2"
_AIFS_CF_FILENAME = "{date_str}{run_hour:02d}0000-{step}h-enfo-cf.grib2"
_DEFAULT_S3_REGION = "eu-central-1"
_VALID_RUN_HOURS = (0, 12)
_SCAN_ATTEMPTS = 4
_SCAN_BACKOFF_S = 3.0


def discover_aifs_files(
    forecast_date: date,
    run_hour: int = 0,
    max_step_h: int = 360,
    step_resolution_h: int = 6,
    include_control: bool = True,
) -> list[str]:
    """Build deterministic S3 URIs for AIFS ENS GRIB2 files.

    No S3 listing is performed - paths follow ECMWF's deterministic naming
    convention.

    Args:
        forecast_date: Forecast initialisation date.
        run_hour: Model run hour (0 or 12).
        max_step_h: Maximum lead-time in hours (inclusive).
        step_resolution_h: Step interval in hours (6 for AIFS ENS).
        include_control: Whether to include ``-cf.grib2`` (control forecast).

    Returns:
        Sorted list of ``s3://`` URIs.

    Raises:
        ValueError: If *run_hour* is not 0 or 12.
    """
    if run_hour not in _VALID_RUN_HOURS:
        raise ValueError(f"run_hour must be one of {_VALID_RUN_HOURS}, got {run_hour}")

    date_str = forecast_date.strftime("%Y%m%d")
    prefix = _ECMWF_S3_PREFIX + _AIFS_PATH_TEMPLATE.format(
        date_str=date_str,
        run_hour=run_hour,
    )

    uris: list[str] = []
    for step in range(0, max_step_h + 1, step_resolution_h):
        pf_name = _AIFS_PF_FILENAME.format(
            date_str=date_str,
            run_hour=run_hour,
            step=step,
        )
        uris.append(prefix + pf_name)
        if include_control:
            cf_name = _AIFS_CF_FILENAME.format(
                date_str=date_str,
                run_hour=run_hour,
                step=step,
            )
            uris.append(prefix + cf_name)

    log.debug(
        "aifs_discover_complete",
        forecast_date=date_str,
        run_hour=run_hour,
        n_files=len(uris),
        max_step_h=max_step_h,
    )
    return uris


def scan_aifs_grib(
    uri: str,
    variables: list[str] | None = None,
    s3_region: str = _DEFAULT_S3_REGION,
) -> list[dict[str, Any]]:
    """Scan a single AIFS GRIB2 file and return kerchunk reference dicts.

    Each ``-pf.grib2`` file contains all 50 perturbed ensemble members for
    every requested variable at a single forecast step.

    Args:
        uri: Full ``s3://`` URI of the GRIB2 file.
        variables: Short-name filter (e.g. ``["tp"]``).  ``None`` = all.
        s3_region: AWS region for the ECMWF bucket.

    Returns:
        List of kerchunk reference dicts (one per GRIB message group).
    """
    import time

    from kerchunk.grib2 import scan_grib

    so: dict[str, Any] = {"anon": True, "default_fill_cache": False}
    if s3_region:
        so["client_kwargs"] = {"region_name": s3_region}

    last_exc: Exception | None = None
    for attempt in range(_SCAN_ATTEMPTS):
        try:
            refs = scan_grib(uri, storage_options=so)
            break
        except Exception as exc:
            last_exc = exc
            if attempt + 1 < _SCAN_ATTEMPTS:
                time.sleep(_SCAN_BACKOFF_S * (attempt + 1))
    else:
        raise last_exc if last_exc else RuntimeError(f"scan_grib failed: {uri}")

    if variables:
        var_set = set(variables)
        refs = [r for r in refs if _ref_matches_variables(r, var_set)]

    log.debug("aifs_scan_grib_done", uri=uri, n_refs=len(refs))
    return refs


def aifs_to_virtual_dataset(
    forecast_date: date,
    run_hour: int = 0,
    variables: list[str] | None = None,
    max_step_h: int = 360,
    step_resolution_h: int = 6,
    n_members: int = 51,
    n_workers: int = 4,
    s3_region: str = _DEFAULT_S3_REGION,
) -> xr.Dataset:
    """Build a ManifestArray-backed virtual xarray Dataset from AIFS GRIB2.

    The returned dataset is compatible with
    :meth:`IceChainStore.commit_day` (which calls
    ``virtual_ds.virtualize.to_icechunk()``).

    Workflow:
        1. :func:`discover_aifs_files` builds deterministic URIs.
        2. :func:`scan_aifs_grib` runs in parallel via ThreadPoolExecutor.
        3. ``kerchunk.combine.MultiZarrToZarr`` merges all reference sets.
        4. VirtualiZarr converts merged refs into a ManifestArray-backed
           dataset with dims ``(member, step, latitude, longitude)``.

    Args:
        forecast_date: Forecast initialisation date.
        run_hour: Model run hour (0 or 12).
        variables: Variable short-name filter.
        max_step_h: Maximum lead-time in hours.
        step_resolution_h: Step interval in hours.
        n_members: Expected total members (50 perturbed + 1 control = 51).
        n_workers: Parallel scan threads.
        s3_region: AWS region for the ECMWF bucket.

    Returns:
        :class:`xr.Dataset` backed by ManifestArray variables (no data
        loaded).

    Raises:
        FileNotFoundError: If no GRIB2 files produce valid references.
        ValueError: If fewer than 10 ensemble members are found.
    """
    from gik_icechain.shared.codec_registry import register_grib_codecs

    register_grib_codecs()

    uris = discover_aifs_files(
        forecast_date,
        run_hour=run_hour,
        max_step_h=max_step_h,
        step_resolution_h=step_resolution_h,
        include_control=True,
    )

    # Parallel scan
    all_refs: list[dict[str, Any]] = []
    errors: list[str] = []

    with ThreadPoolExecutor(max_workers=min(n_workers, len(uris))) as pool:
        future_to_uri = {
            pool.submit(scan_aifs_grib, uri, variables, s3_region): uri for uri in uris
        }
        for future in as_completed(future_to_uri):
            uri = future_to_uri[future]
            try:
                refs = future.result()
                all_refs.extend(refs)
            except Exception as exc:
                errors.append(uri)
                log.warning(
                    "aifs_scan_failed",
                    uri=uri,
                    error=str(exc)[:200],
                )

    if not all_refs:
        raise FileNotFoundError(
            f"No valid AIFS GRIB2 references found for "
            f"{forecast_date.isoformat()} {run_hour:02d}Z "
            f"({len(errors)} scan errors)"
        )

    log.info(
        "aifs_scan_complete",
        forecast_date=forecast_date.isoformat(),
        n_refs=len(all_refs),
        n_errors=len(errors),
    )

    # Heavy imports deferred until after scan validation
    from kerchunk.combine import MultiZarrToZarr
    from virtualizarr import open_virtual_dataset
    from virtualizarr.manifests import ManifestStore
    from virtualizarr.parsers.kerchunk.translator import (
        manifestgroup_from_kerchunk_refs,
    )
    from virtualizarr.types.kerchunk import KerchunkStoreRefs

    from gik_icechain.conversion.virtualizer import _build_ecmwf_registry

    # Combine references into unified kerchunk dict
    mzz = MultiZarrToZarr(
        all_refs,
        concat_dims=["step", "number"],
        identical_dims=["latitude", "longitude"],
    )
    combined = mzz.translate()

    # Convert to VirtualiZarr ManifestStore
    store_refs = KerchunkStoreRefs(combined)
    mg = manifestgroup_from_kerchunk_refs(store_refs)
    registry = _build_ecmwf_registry()
    ms = ManifestStore(group=mg, registry=registry)  # type: ignore[arg-type]

    vds = open_virtual_dataset(
        url="aifs-combined",
        registry=registry,  # type: ignore[arg-type]
        parser=_PassthroughParser(ms),  # type: ignore[arg-type]
        loadable_variables=[],
    )

    # Rename 'number' -> 'member' for consistency with IFS pipeline
    if "number" in vds.dims:
        vds = vds.rename({"number": "member"})

    # Validate member count
    if "member" in vds.dims:
        actual_members = vds.sizes["member"]
        if actual_members < 10:
            raise ValueError(
                f"Only {actual_members} ensemble members found, "
                f"expected >= 10 (target: {n_members})"
            )
        log.info(
            "aifs_members_found",
            expected=n_members,
            actual=actual_members,
        )

    log.info(
        "aifs_virtual_dataset_ready",
        forecast_date=forecast_date.isoformat(),
        dims=dict(vds.sizes),
        variables=list(vds.data_vars),
    )
    return vds



class _PassthroughParser:
    """Parser that returns a pre-built ManifestStore.

    Used with :func:`virtualizarr.open_virtual_dataset` when the combined
    kerchunk references have already been converted to a ManifestStore.
    """

    def __init__(self, manifest_store: object) -> None:
        self._ms = manifest_store

    def __call__(self, url: str, registry: object) -> object:
        return self._ms


def _ref_matches_variables(
    ref_dict: dict[str, Any],
    var_set: set[str],
) -> bool:
    """Check if a kerchunk ref dict contains any variable in *var_set*.

    Checks three levels (in priority order):
    1. Variable names inferred from ``{var}/.zarray`` ref keys.
    2. GRIB shortName / cfVarName stored in ``.zattrs`` JSON.
    3. Legacy flat keys (``shortName``, ``cfVarName``) for test compatibility.
    """
    refs = ref_dict.get("refs", {})

    # 1. Variable name from .zarray keys (standard kerchunk layout)
    for key in refs:
        if key.endswith("/.zarray"):
            varname = key.rsplit("/", 1)[0]
            if varname in var_set:
                return True

    # 2. GRIB metadata in .zattrs JSON
    for key in refs:
        if key.endswith("/.zattrs"):
            try:
                raw = refs[key]
                attrs = json.loads(raw) if isinstance(raw, str) else raw
                for attr_key in (
                    "GRIB_shortName",
                    "shortName",
                    "GRIB_cfVarName",
                    "cfVarName",
                ):
                    if attrs.get(attr_key) in var_set:
                        return True
            except (json.JSONDecodeError, TypeError, AttributeError):
                pass

    # 3. Legacy flat keys (backward-compat with simple test mocks)
    return any(refs.get(key) in var_set for key in ("shortName", "cfVarName", "parameterName"))


def _extract_shortname(ref_dict: dict[str, Any]) -> str | None:
    """Extract the GRIB shortName from a kerchunk reference dict.

    Checks (in order): ``{var}/.zarray`` key names, ``.zattrs`` JSON
    attributes, and legacy flat keys.
    """
    refs = ref_dict.get("refs", {})

    # 1. From .zarray key names
    for key in refs:
        if key.endswith("/.zarray"):
            return key.rsplit("/", 1)[0]

    # 2. From .zattrs JSON
    for key in refs:
        if key.endswith("/.zattrs"):
            try:
                raw = refs[key]
                attrs = json.loads(raw) if isinstance(raw, str) else raw
                for attr_key in (
                    "GRIB_shortName",
                    "shortName",
                    "GRIB_cfVarName",
                    "cfVarName",
                ):
                    if attr_key in attrs:
                        return str(attrs[attr_key])
            except (json.JSONDecodeError, TypeError, AttributeError):
                pass

    # 3. Legacy flat keys
    for key in ("shortName", "cfVarName", "parameterName"):
        if key in refs:
            return str(refs[key])

    return None
