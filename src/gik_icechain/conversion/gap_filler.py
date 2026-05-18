"""Backfill the ECMWF archive gap (May 2023 – Feb 2024) via parallel Cloud Run.

The GIK HuggingFace dataset was initially seeded from March 2024 onwards.
This module identifies the missing days, downloads the corresponding GRIB2
files from s3://ecmwf-forecasts, generates Kerchunk/Parquet byte-range
references, and uploads them to the target Parquet URI so they can be
ingested by the main C1 pipeline.

Requires Lithops configured for Google Cloud Run (deploy/cloud_run/).
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import pandas as pd
import structlog

from gik_icechain.conversion.gik_loader import GIKCatalog, FLOOD_RELEVANT_VARS
from gik_icechain.shared.storage import get_s3_filesystem, list_s3_objects

log = structlog.get_logger(__name__)

_ECMWF_BUCKET = "ecmwf-forecasts"
_ECMWF_REGION = "eu-west-1"
_GRIB2_PREFIX_TEMPLATE = "{year}/{month:02d}/{date_str}/{run_hour:02d}z/"

_DEFAULT_GAP_START = date(2023, 5, 1)
_DEFAULT_GAP_END   = date(2024, 2, 29)


@dataclass
class GapFillConfig:
    s3_source:          str   = f"s3://{_ECMWF_BUCKET}"
    output_parquet_uri: str   = ""
    start:              date  = field(default_factory=lambda: _DEFAULT_GAP_START)
    end:                date  = field(default_factory=lambda: _DEFAULT_GAP_END)
    run_hours:          list[int] = field(default_factory=lambda: [0])
    variables:          list[str] = field(default_factory=lambda: FLOOD_RELEVANT_VARS)
    workers:            int   = 50


def identify_gap(catalog: GIKCatalog, start: date, end: date) -> list[date]:
    """Return dates in [start, end] that are absent from *catalog*.

    Args:
        catalog: Loaded GIKCatalog instance.
        start:   Gap search start (inclusive).
        end:     Gap search end (inclusive).

    Returns:
        Sorted list of missing dates.
    """
    available = set(catalog.list_available_dates())
    span = (end - start).days + 1
    missing = sorted(
        start + timedelta(days=i)
        for i in range(span)
        if (start + timedelta(days=i)) not in available
    )
    log.info("gap_identified", missing_days=len(missing), start=start, end=end)
    return missing


def fill_one_day(
    day: date,
    run_hour: int,
    output_parquet_uri: str,
    variables: list[str] | None = None,
    s3_region: str = _ECMWF_REGION,
) -> str:
    """Download GRIB2 messages for *day/run_hour*, build Parquet references, upload.

    This function is designed to run inside a Lithops Cloud Run worker.
    It is intentionally self-contained (no imports from the caller's scope).

    Args:
        day:               Forecast date to process.
        run_hour:          Run hour (0, 6, 12, or 18).
        output_parquet_uri: S3/GCS URI where the output Parquet is written.
        variables:         GRIB2 shortNames to include; all flood vars if None.
        s3_region:         AWS region of s3://ecmwf-forecasts.

    Returns:
        URI of the written Parquet file.
    """
    try:
        import cfgrib  # type: ignore[import-untyped]
    except ImportError as exc:
        raise RuntimeError("cfgrib is required for gap_filler.fill_one_day") from exc

    vars_to_process = variables or FLOOD_RELEVANT_VARS
    fs = get_s3_filesystem(no_sign=True, region=s3_region)

    date_str = day.strftime("%Y%m%d")
    prefix = _GRIB2_PREFIX_TEMPLATE.format(
        year=day.year,
        month=day.month,
        date_str=date_str,
        run_hour=run_hour,
    )

    grib_keys = list_s3_objects(_ECMWF_BUCKET, prefix, fs=fs)
    if not grib_keys:
        log.warning("no_grib_keys_found", date=day, run_hour=run_hour, prefix=prefix)
        return ""

    rows: list[dict[str, Any]] = []
    for grib_uri in grib_keys:
        try:
            rows.extend(_extract_references(grib_uri, vars_to_process, fs))
        except Exception as exc:
            log.warning("reference_extraction_failed", uri=grib_uri, error=str(exc))

    if not rows:
        log.warning("no_references_extracted", date=day, run_hour=run_hour)
        return ""

    df = pd.DataFrame(rows)
    out_uri = _parquet_output_uri(output_parquet_uri, day, run_hour)
    _upload_parquet(df, out_uri, fs)
    log.info("gap_day_filled", date=day, run_hour=run_hour, rows=len(df), uri=out_uri)
    return out_uri


def run_gap_fill(cfg: GapFillConfig) -> list[str]:
    """Launch fill_one_day in parallel via Lithops for all missing days.

    Args:
        cfg: GapFillConfig with source, output, and worker settings.

    Returns:
        List of written Parquet URIs (one per day × run_hour processed).
    """
    try:
        import lithops  # type: ignore[import-untyped]
    except ImportError as exc:
        raise RuntimeError(
            "lithops is required for run_gap_fill. "
            "Install it with: pip install lithops"
        ) from exc

    catalog = GIKCatalog()
    missing_days = identify_gap(catalog, cfg.start, cfg.end)

    if not missing_days:
        log.info("gap_fill_no_missing_days")
        return []

    tasks: list[tuple[date, int]] = [
        (day, rh)
        for day in missing_days
        for rh in cfg.run_hours
    ]

    def _worker_fn(args: tuple[date, int]) -> str:
        day, rh = args
        return fill_one_day(
            day=day,
            run_hour=rh,
            output_parquet_uri=cfg.output_parquet_uri,
            variables=cfg.variables,
        )

    log.info("gap_fill_starting", n_tasks=len(tasks), workers=cfg.workers)
    fexec = lithops.FunctionExecutor(max_workers=cfg.workers)
    futures = fexec.map(_worker_fn, tasks)
    results: list[str] = fexec.get_result(futures)

    written = [r for r in results if r]
    log.info("gap_fill_complete", total=len(tasks), written=len(written))
    return written


def _extract_references(
    grib_uri: str,
    variables: list[str],
    fs: Any,
) -> list[dict[str, Any]]:
    """Read a GRIB2 file and return byte-range reference dicts for *variables*."""
    import cfgrib  # type: ignore[import-untyped]

    rows: list[dict[str, Any]] = []
    with fs.open(grib_uri, "rb") as raw:
        data = raw.read()

    buf = io.BytesIO(data)
    for ds in cfgrib.open_datasets(buf):
        short_name = ds.attrs.get("GRIB_shortName", "")
        if short_name not in variables:
            continue
        rows.append({
            "uri":         grib_uri,
            "byte_offset": ds.attrs.get("GRIB_offset", 0),
            "byte_length": ds.attrs.get("GRIB_length", 0),
            "variable":    short_name,
            "level":       ds.attrs.get("GRIB_typeOfLevel", None),
            "step":        ds.attrs.get("GRIB_stepRange", "0"),
            "member":      ds.attrs.get("GRIB_perturbationNumber", 0),
        })
    return rows


def _parquet_output_uri(base_uri: str, day: date, run_hour: int) -> str:
    date_str = day.strftime("%Y%m%d")
    fname = f"gik_{date_str}_{run_hour:02d}z.parquet"
    return f"{base_uri.rstrip('/')}/{day.year}/{day.month:02d}/{fname}"


def _upload_parquet(df: pd.DataFrame, uri: str, fs: Any) -> None:
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    buf.seek(0)
    with fs.open(uri, "wb") as f:
        f.write(buf.read())
    log.debug("parquet_uploaded", uri=uri, rows=len(df))
