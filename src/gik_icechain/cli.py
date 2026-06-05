"""GIK-IceChain command-line interface.

Entry points:
    gik-icechain convert       -- C1: ingest ECMWF IFS GRIB2 → IceChunk virtual store
    gik-icechain convert-aifs  -- C1: ingest ECMWF AIFS ENS GRIB2 → IceChunk virtual store
    gik-icechain exceedance    -- C2: compute exceedance probabilities
    gik-icechain compare       -- compare AIFS vs IFS exceedance (Innovation 4)
    gik-icechain risk          -- C3: run CRMA risk batch
    gik-icechain run-all       -- run the full pipeline end-to-end
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

if TYPE_CHECKING:
    import xarray as xr

    from gik_icechain.shared.config import GIKConfig

import structlog
import typer

from gik_icechain.shared.config import DEFAULT_CONFIG_PATH

log = structlog.get_logger(__name__)


def _exit_on_error(command: str, exc: Exception) -> None:
    """Log a pipeline error and exit with code 1 (DRY error handler for all commands)."""
    log.error("pipeline_command_failed", command=command, error=str(exc), exc_info=True)
    typer.echo(f"ERROR [{command}]: {exc}", err=True)
    raise typer.Exit(code=1)


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise typer.BadParameter(f"Date must be YYYY-MM-DD, got: {value!r}") from None

app = typer.Typer(
    name="gik-icechain",
    help="GIK-IceChain v2.0 — zero-cost flood risk pipeline for East Africa.",
    no_args_is_help=True,
)

_DEFAULT_CONFIG = DEFAULT_CONFIG_PATH


def _bootstrap(config_path: Path) -> GIKConfig:
    from gik_icechain.shared.config import load_config
    from gik_icechain.shared.logging import configure_logging

    resolved = config_path if config_path.exists() else _DEFAULT_CONFIG
    cfg = load_config(resolved)
    configure_logging(level=cfg.logging.level, fmt=cfg.logging.format)
    return cfg


def _run_convert(cfg: GIKConfig, start: date, end: date) -> str:
    """Run C1 ingest. Returns the last IceChunk commit hash (empty string if nothing ingested)."""
    from gik_icechain.conversion.gik_loader import GIKCatalog
    from gik_icechain.conversion.icechunk_writer import IceChainStore
    from gik_icechain.conversion.virtualizer import parquet_to_virtual_dataset

    catalog = GIKCatalog(
        cfg.sources.gik_hf_dataset,
        catalog_file=cfg.sources.gik_catalog_file,
    )
    catalog.load_catalog()
    store = IceChainStore(
        cfg.outputs.icechunk_store_uri,
        region=cfg.outputs.icechunk_store_region,
        endpoint_url=cfg.outputs.endpoint_url or None,
        branch=cfg.component1.icechunk.branch,
        commit_message_template=cfg.component1.icechunk.commit_message_template,
        tag_format=cfg.component1.icechunk.tag_format,
        manifest_splitting=cfg.component1.icechunk.manifest_splitting,
        manifest_split_dim=cfg.component1.icechunk.manifest_split_dim,
        manifest_split_size=cfg.component1.icechunk.manifest_split_size,
    )
    store.create_or_open()

    last_commit = ""
    current = start
    while current <= end:
        for run_hour in cfg.component1.run_hours:
            paths = catalog.get_parquet_paths(
                start=current,
                end=current,
                run_hours=(run_hour,),
                variables=cfg.component1.variables,
            )
            if not paths:
                continue
            vds = parquet_to_virtual_dataset(paths, variables=cfg.component1.variables)
            last_commit = store.commit_day(current, vds, run_hour)
        current += timedelta(days=1)

    return last_commit


def _run_convert_aifs(cfg: GIKConfig, start: date, end: date) -> str:
    """Run C1 AIFS ingest. Returns the last IceChunk commit hash."""
    from gik_icechain.conversion.aifs_discovery import aifs_to_virtual_dataset
    from gik_icechain.conversion.icechunk_writer import IceChainStore

    aifs = cfg.aifs_track
    store = IceChainStore(
        aifs.aifs_store_uri,
        region=cfg.outputs.icechunk_store_region,
        endpoint_url=cfg.outputs.endpoint_url or None,
        commit_message_template="AIFS ingest: {date}T{run_hour:02d}Z",
        tag_format="aifs-{date}T{run_hour:02d}Z",
    )
    store.create_or_open()

    last_commit = ""
    current = start
    while current <= end:
        for run_hour in aifs.run_hours:
            try:
                vds = aifs_to_virtual_dataset(
                    current,
                    run_hour=run_hour,
                    variables=aifs.variables,
                    max_step_h=aifs.max_step_h,
                    step_resolution_h=aifs.step_resolution_h,
                    n_members=aifs.n_members,
                    s3_region=cfg.sources.ecmwf_s3_region,
                )
                last_commit = store.commit_day(current, vds, run_hour)
            except FileNotFoundError:
                log.warning("aifs_no_data", date=current.isoformat(), run_hour=run_hour)
            except Exception:
                log.warning(
                    "aifs_ingest_failed",
                    date=current.isoformat(), run_hour=run_hour, exc_info=True,
                )
        current += timedelta(days=1)

    return last_commit


def _subset_to_bbox(
    ds: xr.Dataset,
    bbox: tuple[float, float, float, float],
) -> xr.Dataset:
    """Subset a dataset to a geographic bounding box.

    Handles both proper geographic coordinates and raw integer indices
    (as produced by VirtualiZarr when lat/lon aren't assigned).

    For a 0.25° ECMWF global grid with integer indices:
      latitude[i]  = 90 - i * 0.25   (721 values, 90N to 90S)
      longitude[j] = j * 0.25        (1440 values, 0E to 359.75E)
    """
    lat_min, lat_max, lon_min, lon_max = bbox
    lat_name = "latitude" if "latitude" in ds.dims else "lat"
    lon_name = "longitude" if "longitude" in ds.dims else "lon"
    nlat = ds.sizes[lat_name]
    nlon = ds.sizes[lon_name]

    lat_vals = ds[lat_name].values
    if float(lat_vals.max()) <= nlat:
        # Integer indices — convert geographic bounds to index bounds.
        # Infer grid: 0.25° global grid (721×1440) or similar.
        dlat = 180.0 / (nlat - 1)
        dlon = 360.0 / nlon
        # ECMWF grids: latitude[0]=90°N (descending), longitude[0]=0°E
        i_min = max(0, int((90.0 - lat_max) / dlat))
        i_max = min(nlat - 1, int((90.0 - lat_min) / dlat))
        j_min = max(0, int(lon_min / dlon))
        j_max = min(nlon - 1, int(lon_max / dlon))
        subset = ds.isel({lat_name: slice(i_min, i_max + 1), lon_name: slice(j_min, j_max + 1)})
        # Reassign real geographic coordinate values so downstream operations
        # (regionmask, threshold alignment) work in degrees, not integer indices.
        import numpy as np
        real_lats = 90.0 - (i_min + np.arange(subset.sizes[lat_name])) * dlat
        real_lons = (j_min + np.arange(subset.sizes[lon_name])) * dlon
        return subset.assign_coords({lat_name: real_lats, lon_name: real_lons})

    # Proper geographic coordinates — use .sel()
    if float(lat_vals[0]) > float(lat_vals[-1]):
        return ds.sel({lat_name: slice(lat_max, lat_min), lon_name: slice(lon_min, lon_max)})
    return ds.sel({lat_name: slice(lat_min, lat_max), lon_name: slice(lon_min, lon_max)})


def _threshold_bbox(
    thresholds: object,
    buffer: float = 1.0,
) -> tuple[float, float, float, float] | None:
    """Extract the spatial bounding box from loaded GEV thresholds.

    Returns (lat_min, lat_max, lon_min, lon_max) with a buffer, or None if
    the threshold object has no spatial data.
    """
    try:
        for mode_key in thresholds._thresholds:  # type: ignore[attr-defined]
            for wh in thresholds._thresholds[mode_key]:  # type: ignore[attr-defined]
                for _rp, da in thresholds._thresholds[mode_key][wh].items():  # type: ignore[attr-defined]
                    lat_name = next((c for c in da.coords if c in ("lat", "latitude")), None)
                    lon_name = next((c for c in da.coords if c in ("lon", "longitude")), None)
                    if lat_name and lon_name:
                        lat_min = float(da[lat_name].min()) - buffer
                        lat_max = float(da[lat_name].max()) + buffer
                        lon_min = float(da[lon_name].min()) - buffer
                        lon_max = float(da[lon_name].max()) + buffer
                        return (lat_min, lat_max, lon_min, lon_max)
    except Exception:
        log.warning("threshold_bbox_extraction_failed", exc_info=True)
    return None


def _resolve_climate_mode(
    day: date,
    enso_iod: object,
    enso_thr: float,
    iod_thr: float,
) -> object:
    """Map a forecast date to a ClimateMode via ENSO/IOD index lookup."""
    import pandas as pd

    from gik_icechain.exceedance.thresholds import (
        ClimateMode,
        ENSOPhase,
        IODPhase,
        classify_enso,
        classify_iod,
        get_season,
    )

    season = get_season(day.month)
    try:
        row = enso_iod.loc[pd.Timestamp(day)]  # type: ignore[attr-defined]
        return ClimateMode(
            season,
            classify_enso(float(row["nino34"]), threshold=enso_thr),
            classify_iod(float(row["dmi"]), threshold=iod_thr),
        )
    except KeyError:
        return ClimateMode(season, ENSOPhase.NEUTRAL, IODPhase.NEUTRAL)


def _process_exceedance_day(args: dict) -> dict:
    """Compute exceedance for one forecast date. Module-level for multiprocessing pickling."""
    import pandas as pd
    import xarray as xr

    from gik_icechain.conversion.icechunk_writer import IceChainStore
    from gik_icechain.exceedance.accumulations import compute_rolling_accumulations
    from gik_icechain.exceedance.exceedance import (
        compute_ensemble_confidence,
        compute_exceedance_probabilities,
    )
    from gik_icechain.exceedance.thresholds import AdaptiveGEVThresholds
    from gik_icechain.exceedance.writer import build_exceedance_dataset
    from gik_icechain.shared.codec_registry import register_grib_codecs

    register_grib_codecs()

    date_str = args["date_str"]
    day = date.fromisoformat(date_str)

    try:
        store_obj = IceChainStore(
            args["store_uri"],
            region=args["region"],
            endpoint_url=args.get("endpoint_url"),
        )
        store_obj.create_or_open()
        session = store_obj.readonly_session()

        manifest_aware_enabled = args.get("manifest_aware_enabled", False)
        compute_vars = args.get("compute_variables", ["tp"])
        bbox = args.get("bbox")
        max_steps = args.get("max_steps")

        if manifest_aware_enabled:
            from gik_icechain.exceedance.manifest_store import (
                load_day_manifest_aware,
            )

            coalescing = args.get("coalescing_enabled", True)
            day_ds = load_day_manifest_aware(
                session,
                date_str,
                variables=compute_vars,
                max_step_h=args.get("max_step_h", 168),
                step_resolution_h=args.get("step_resolution_h", 6),
                step_buffer=args.get("step_buffer", 1),
                bbox=bbox,
                max_gap_bytes=(
                    args.get("max_gap_bytes", 65536) if coalescing else 0
                ),
                max_merged_bytes=(
                    args.get("max_merged_bytes", 5242880)
                    if coalescing
                    else 0
                ),
                fetch_workers=args.get("fetch_workers", 8),
                min_members=args.get("min_members", 10),
                s3_region=args.get("s3_region", "eu-central-1"),
            )
            # Data is already concrete (in-memory), no chunking needed
        else:
            day_ds = xr.open_zarr(session.store, group=date_str, consolidated=False)

            # Dimension 1: Variable pre-selection
            available = [v for v in compute_vars if v in day_ds.data_vars]
            if not available:
                return {
                    "date_str": date_str,
                    "success": False,
                    "error": f"none of {compute_vars} found in group {date_str}",
                }
            day_ds = day_ds[available]

            # Dimension 2: Step slicing — only load steps needed for max window
            if max_steps is not None and "step" in day_ds.dims:
                n_steps = day_ds.sizes["step"]
                if max_steps < n_steps:
                    day_ds = day_ds.isel(step=slice(0, max_steps))

            # Dimension 4: Spatial subsetting
            if bbox is not None:
                day_ds = _subset_to_bbox(day_ds, bbox)

            day_ds = day_ds.chunk(args["chunk_dims"])
    except Exception as exc:
        return {"date_str": date_str, "success": False, "error": str(exc)[:200]}

    thresholds = AdaptiveGEVThresholds.load(Path(args["thresholds_path"]))
    enso_iod = pd.read_csv(
        args["enso_iod_path"], parse_dates=["date"]
    ).set_index("date")
    mode = _resolve_climate_mode(day, enso_iod, args["enso_thr"], args["iod_thr"])

    acc_ds = compute_rolling_accumulations(day_ds, windows_h=args["windows_h"])
    member_dim = "member" if "member" in day_ds.dims else "number"

    day_results: dict[tuple[int, int], xr.DataArray] = {}
    for w in args["windows_h"]:
        for rp in args["return_periods"]:
            try:
                thr = thresholds.get(w, rp, mode)  # type: ignore[arg-type]
                day_results[(w, rp)] = compute_exceedance_probabilities(
                    acc_ds, xr.Dataset({f"rp_{rp}y": thr}),
                    window_h=w, return_period=rp, member_dim=member_dim,
                )
            except Exception as exc:
                log.warning(
                    "exceedance_window_rp_failed",
                    window_h=w, return_period=rp, date=date_str, error=str(exc),
                )
                continue

    if not day_results:
        return {"date_str": date_str, "success": False, "error": "no window/rp produced results"}

    exceedance_da = build_exceedance_dataset(day_results, day)
    tmp_path = str(Path(args["tmp_dir"]) / f"{date_str}.zarr")
    exceedance_da.to_dataset(name="exceedance_prob").to_zarr(tmp_path, mode="w")

    conf_path = None
    try:
        conf_da = compute_ensemble_confidence(acc_ds, window_h=24, member_dim=member_dim)
        conf_da = conf_da.assign_coords(date=pd.Timestamp(day)).expand_dims("date")
        conf_out = str(Path(args["tmp_dir"]) / f"{date_str}_conf.zarr")
        conf_da.to_dataset(name="ensemble_confidence").to_zarr(conf_out, mode="w")
        conf_path = conf_out
    except Exception:
        log.warning("ensemble_confidence_failed", date=date_str, exc_info=True)
        conf_path = None

    return {"date_str": date_str, "success": True, "path": tmp_path, "conf_path": conf_path}


def _run_exceedance(
    cfg: GIKConfig,
    store_uri: str,
    output_uri: str,
    start: date | None,
    end: date | None,
) -> int:
    import os
    import shutil
    import tempfile
    from concurrent.futures import ProcessPoolExecutor, as_completed

    import xarray as xr

    from gik_icechain.conversion.icechunk_writer import IceChainStore
    from gik_icechain.exceedance.writer import write_exceedance_store

    store_obj = IceChainStore(
        store_uri,
        region=cfg.outputs.icechunk_store_region,
        endpoint_url=cfg.outputs.endpoint_url or None,
    )
    store_obj.create_or_open()
    snapshots = store_obj.list_snapshots()
    committed_dates = sorted({s["forecast_date"] for s in snapshots if s["forecast_date"]})
    if start:
        committed_dates = [d for d in committed_dates if d >= start.isoformat()]
    if end:
        committed_dates = [d for d in committed_dates if d <= end.isoformat()]

    if not committed_dates:
        log.warning("no_committed_dates_in_range", start=start, end=end)
        return 0

    from gik_icechain.exceedance.thresholds import AdaptiveGEVThresholds

    c2 = cfg.component2
    thresholds = AdaptiveGEVThresholds.load(Path(c2.thresholds.cmorph_path))

    # Dimension 4: Prefer explicit spatial config; fall back to threshold bbox
    bbox = c2.spatial.as_tuple if c2.spatial.bbox is not None else _threshold_bbox(thresholds)

    # Dimension 3+7: Resolve effective windows/return_periods from profile or top-level
    effective_windows = c2.effective_windows_h
    effective_rps = c2.effective_return_periods
    max_steps = c2.max_steps_needed

    log.info(
        "exceedance_config_resolved",
        profile=c2.active_profile,
        windows_h=effective_windows,
        return_periods=effective_rps,
        max_steps=max_steps,
        bbox=bbox,
    )

    # Manifest-aware config
    ma = c2.manifest_aware
    brc = c2.byte_range_coalescing

    tmp_dir = tempfile.mkdtemp(prefix="gik_exc_")
    worker_args = [
        {
            "date_str": d,
            "store_uri": store_uri,
            "region": cfg.outputs.icechunk_store_region,
            "endpoint_url": cfg.outputs.endpoint_url or None,
            "thresholds_path": str(Path(c2.thresholds.cmorph_path)),
            "enso_iod_path": c2.thresholds.enso_iod_index_path,
            "enso_thr": c2.thresholds.enso_nino34_threshold,
            "iod_thr": c2.thresholds.iod_dmi_threshold,
            "windows_h": effective_windows,
            "return_periods": effective_rps,
            "max_steps": max_steps,
            "chunk_dims": dict(c2.dask.chunk_dims),
            "bbox": bbox,
            "compute_variables": c2.compute_variables,
            "tmp_dir": tmp_dir,
            # Manifest-aware params
            "manifest_aware_enabled": ma.enabled,
            "coalescing_enabled": brc.enabled,
            "max_gap_bytes": brc.max_gap_bytes,
            "max_merged_bytes": brc.max_merged_bytes,
            "fetch_workers": ma.fetch_workers,
            "min_members": ma.min_members,
            "max_step_h": c2.effective_max_forecast_h,
            "step_resolution_h": c2.step_resolution_h,
            "step_buffer": c2.step_buffer,
            "s3_region": cfg.sources.ecmwf_s3_region,
        }
        for d in committed_dates
    ]

    # Dimension 6: Parallel config
    par = c2.parallel
    if par.multiprocessing:
        auto_workers = os.cpu_count() or 4
        n_workers = min(par.max_workers or auto_workers, len(committed_dates), auto_workers)
    else:
        n_workers = 1
    log.info("exceedance_parallel_start", n_dates=len(committed_dates), n_workers=n_workers)

    succeeded: list[dict] = []
    if n_workers <= 1 or len(committed_dates) == 1:
        for args in worker_args:
            result = _process_exceedance_day(args)
            if result["success"]:
                succeeded.append(result)
            else:
                log.warning("exceedance_day_failed", **result)
    else:
        with ProcessPoolExecutor(max_workers=n_workers) as pool:
            futures = {pool.submit(_process_exceedance_day, a): a["date_str"] for a in worker_args}
            for future in as_completed(futures):
                result = future.result()
                if result["success"]:
                    succeeded.append(result)
                else:
                    log.warning("exceedance_day_failed", **result)

    if not succeeded:
        log.error("exceedance_all_days_failed", n_dates=len(committed_dates))
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return 0

    results: dict[date, xr.DataArray] = {}
    confidence_results: dict[date, xr.DataArray] = {}
    for r in sorted(succeeded, key=lambda x: x["date_str"]):
        day = date.fromisoformat(r["date_str"])
        ds = xr.open_zarr(r["path"], consolidated=False)
        results[day] = ds["exceedance_prob"]
        if r.get("conf_path"):
            cds = xr.open_zarr(r["conf_path"], consolidated=False)
            confidence_results[day] = cds["ensemble_confidence"]

    write_exceedance_store(
        results, output_uri,
        chunks=dict(cfg.component2.output_chunks),
        append=True,
        confidence_dict=confidence_results or None,
        endpoint_url=cfg.outputs.endpoint_url or None,
    )

    if cfg.outputs.exceedance_icechunk_uri:
        from gik_icechain.exceedance.icechunk_output import DecisionStore

        decision = DecisionStore(
            cfg.outputs.exceedance_icechunk_uri,
            region=cfg.outputs.icechunk_store_region,
            endpoint_url=cfg.outputs.endpoint_url or None,
        )
        decision.create_or_open()
        for day, exc_da in results.items():
            ds = exc_da.to_dataset(name="exceedance_prob")
            if day in confidence_results:
                ds["ensemble_confidence"] = confidence_results[day]
            decision.commit_day(day, ds)
        log.info("decision_store_committed", n_dates=len(results))

    shutil.rmtree(tmp_dir, ignore_errors=True)
    log.info("exceedance_complete", n_dates=len(results))
    return len(results)


def _run_risk(
    cfg: GIKConfig,
    exc_uri: str,
    output: Path,
    start: date,
    end: date,
) -> list[Path]:
    from gik_icechain.risk.crma_model import CRMAModel, EastAfricaCluster
    from gik_icechain.risk.risk_engine import run_risk_batch

    crma_models: dict[EastAfricaCluster, CRMAModel] = {}
    for cluster in EastAfricaCluster:
        m = CRMAModel(cluster=cluster, crma_cfg=cfg.component3.crma_model)
        m.build()
        if cfg.component3.crma.use_refined_cpts and cfg.component3.crma.cpt_path:
            m.load_cpts(Path(cfg.component3.crma.cpt_path))
        crma_models[cluster] = m

    crma_cfg = cfg.component3.crma_model
    return run_risk_batch(
        exceedance_store_uri=exc_uri,
        gpm_dir=Path(cfg.sources.gpm_imerg_path),
        admin_boundaries_path=Path(cfg.sources.admin_boundaries_path),
        crma_models=crma_models,
        output_dir=output,
        start=start,
        end=end,
        api_decay=cfg.component3.api.decay_factor,
        initial_api_mm=cfg.component3.api.initial_api_mm,
        signal_threshold=crma_cfg.signal_threshold_prob,
        rp_signal=crma_cfg.rp_signal,
        endpoint_url=cfg.outputs.endpoint_url or None,
    )


@app.command()
def convert(
    start: Annotated[str, typer.Option("--start", help="First forecast date (YYYY-MM-DD).")],
    end: Annotated[str, typer.Option("--end", help="Last forecast date (YYYY-MM-DD).")],
    config: Annotated[Path, typer.Option(help="Path to YAML config file.")] = _DEFAULT_CONFIG,
    output_store: Annotated[
        str | None, typer.Option("--output-store", help="Override IceChunk store URI.")
    ] = None,
    hf_dataset: Annotated[
        str | None, typer.Option("--hf-dataset", help="Override HuggingFace dataset ID.")
    ] = None,
    mode: Annotated[
        str, typer.Option("--mode", help="append or overwrite (create_or_open handles both).")
    ] = "append",
    output_json: Annotated[
        Path | None,
        typer.Option("--output-json", help="Write ingest result JSON (commit_hash, processed_date)."),  # noqa: E501
    ] = None,
) -> None:
    """Ingest ECMWF IFS ensemble GRIB2 files into an IceChunk virtual store (C1)."""
    from gik_icechain.shared.validation import validate_date_range

    s, e = _parse_date(start), _parse_date(end)
    validate_date_range(s, e)

    cfg = _bootstrap(config)
    if output_store:
        cfg.outputs.icechunk_store_uri = output_store
    if hf_dataset:
        cfg.sources.gik_hf_dataset = hf_dataset

    try:
        commit_hash = _run_convert(cfg, s, e)
    except Exception as exc:
        _exit_on_error("convert", exc)

    if output_json is not None:
        output_json.write_text(
            json.dumps({"commit_hash": commit_hash, "processed_date": e.isoformat()})
        )

    short = commit_hash[:12] if commit_hash else "none"
    typer.echo(f"Convert complete: {s} -> {e}  commit={short}")


@app.command("convert-aifs")
def convert_aifs(
    start: Annotated[str, typer.Option("--start", help="First forecast date (YYYY-MM-DD).")],
    end: Annotated[str, typer.Option("--end", help="Last forecast date (YYYY-MM-DD).")],
    config: Annotated[Path, typer.Option(help="Path to YAML config file.")] = _DEFAULT_CONFIG,
    output_store: Annotated[
        str | None, typer.Option("--output-store", help="Override AIFS IceChunk store URI.")
    ] = None,
) -> None:
    """Ingest ECMWF AIFS ENS GRIB2 files into an IceChunk virtual store (Innovation 4)."""
    from gik_icechain.shared.validation import validate_date_range

    s, e = _parse_date(start), _parse_date(end)
    validate_date_range(s, e)

    cfg = _bootstrap(config)
    if not cfg.aifs_track.enabled:
        typer.echo("AIFS track is disabled in config (aifs_track.enabled=false). Skipping.")
        return

    if output_store:
        cfg.aifs_track.aifs_store_uri = output_store

    try:
        commit_hash = _run_convert_aifs(cfg, s, e)
    except Exception as exc:
        _exit_on_error("convert-aifs", exc)

    typer.echo(
        f"AIFS convert complete: {s} → {e}  "
        f"commit={commit_hash[:12] if commit_hash else 'none'}"
    )


@app.command()
def compare(
    ifs_store: Annotated[
        str, typer.Option("--ifs-store", help="IFS exceedance Zarr store URI."),
    ],
    aifs_store: Annotated[
        str, typer.Option("--aifs-store", help="AIFS exceedance Zarr store URI."),
    ],
    config: Annotated[
        Path, typer.Option(help="Path to YAML config file."),
    ] = _DEFAULT_CONFIG,
    output: Annotated[
        str | None,
        typer.Option("--output", help="Output directory for comparison results."),
    ] = None,
    ifs_ensemble_store: Annotated[
        str | None,
        typer.Option("--ifs-ensemble-store", help="IFS raw ensemble IceChunk URI."),
    ] = None,
    aifs_ensemble_store: Annotated[
        str | None,
        typer.Option("--aifs-ensemble-store", help="AIFS raw ensemble IceChunk URI."),
    ] = None,
) -> None:
    """Compare AIFS vs IFS exceedance probabilities (Innovation 4)."""
    from gik_icechain.exceedance.aifs_track import (
        compare_ensemble_spreads,
        compute_aifs_ifs_delta,
        seasonal_comparison,
    )

    cfg = _bootstrap(config)
    output_dir = output or cfg.aifs_track.comparison_output_dir

    try:
        typer.echo("[1/3] Computing AIFS-IFS delta ...")
        compute_aifs_ifs_delta(ifs_store, aifs_store, output_dir)

        typer.echo("[2/3] Seasonal comparison ...")
        enso_path = cfg.component2.thresholds.enso_iod_index_path
        results = seasonal_comparison(
            ifs_store, aifs_store, enso_iod_path=enso_path,
        )
        for season_key, ds in results.items():
            out_path = str(Path(output_dir) / f"seasonal_{season_key}.zarr")
            ds.to_zarr(out_path, mode="w")

        typer.echo("[3/3] Ensemble spread comparison ...")
        ifs_ens = ifs_ensemble_store or cfg.outputs.icechunk_store_uri
        aifs_ens = aifs_ensemble_store or cfg.aifs_track.aifs_store_uri
        if ifs_ens and aifs_ens:
            spread_ds = compare_ensemble_spreads(
                ifs_ens, aifs_ens,
                region=cfg.outputs.icechunk_store_region,
                endpoint_url=cfg.outputs.endpoint_url or None,
            )
            spread_path = str(Path(output_dir) / "spread_comparison.zarr")
            spread_ds.to_zarr(spread_path, mode="w")
        else:
            typer.echo("  Skipped (no ensemble store URIs configured).")
    except Exception as exc:
        _exit_on_error("compare", exc)

    typer.echo(f"Comparison complete. Results in {output_dir}")


@app.command()
def exceedance(
    store: Annotated[str, typer.Option(help="URI of the IceChunk virtual store.")],
    output: Annotated[str, typer.Option(help="URI for the output exceedance Zarr store.")],
    config: Annotated[Path, typer.Option(help="Path to YAML config file.")] = _DEFAULT_CONFIG,
    workers: Annotated[int | None, typer.Option(help="Override parallel max_workers.")] = None,
    start: Annotated[str | None, typer.Option(help="First date (YYYY-MM-DD).")] = None,
    end: Annotated[str | None, typer.Option(help="Last date (YYYY-MM-DD).")] = None,
    thresholds: Annotated[
        str | None, typer.Option("--thresholds", help="Override CMORPH thresholds URI/path.")
    ] = None,
    profile: Annotated[
        str | None,
        typer.Option("--profile", help="Window profile name."),
    ] = None,
) -> None:
    """Compute adaptive GEV exceedance probabilities for all accumulation windows (C2)."""
    cfg = _bootstrap(config)
    if thresholds:
        cfg.component2.thresholds.cmorph_path = thresholds
    if profile is not None:
        cfg.component2.active_profile = profile
    if workers is not None:
        cfg.component2.parallel.max_workers = workers

    s = _parse_date(start) if start else None
    e = _parse_date(end) if end else None

    dask_cfg = cfg.component2.dask
    dask_workers = workers if workers is not None else dask_cfg.n_workers
    if dask_workers > 1:
        try:
            from dask.distributed import Client

            Client(
                n_workers=dask_workers,
                threads_per_worker=dask_cfg.threads_per_worker,
                silence_logs=True,
            )
        except ImportError:
            log.warning("dask_not_available", workers=dask_workers, msg="running single-threaded")

    try:
        n = _run_exceedance(cfg, store, output, s, e)
    except Exception as exc:
        _exit_on_error("exceedance", exc)

    typer.echo(f"Exceedance complete: {n} days written to {output}")


@app.command()
def risk(
    exceedance_store: Annotated[str, typer.Option(help="URI of the exceedance Zarr store.")],
    output: Annotated[Path, typer.Option(help="Output directory for GeoJSON files.")],
    config: Annotated[Path, typer.Option(help="Path to YAML config file.")] = _DEFAULT_CONFIG,
    start: Annotated[str | None, typer.Option(help="First date (YYYY-MM-DD).")] = None,
    end: Annotated[str | None, typer.Option(help="Last date (YYYY-MM-DD).")] = None,
) -> None:
    """Run CRMA Bayesian Network risk inference for all admin-1 units (C3)."""
    import xarray as xr

    cfg = _bootstrap(config)
    s = _parse_date(start) if start else None
    e = _parse_date(end) if end else None

    if s is None or e is None:
        exc_ds = xr.open_zarr(exceedance_store, consolidated=False)
        dates = sorted(str(d)[:10] for d in exc_ds["date"].values)
        if s is None:
            s = date.fromisoformat(dates[0])
        if e is None:
            e = date.fromisoformat(dates[-1])

    try:
        written = _run_risk(cfg, exceedance_store, output, s, e)
    except Exception as exc:
        _exit_on_error("risk", exc)

    typer.echo(f"Risk complete: {len(written)} GeoJSON files in {output}")


@app.command("run-all")
def run_all(
    start: Annotated[str, typer.Option("--start", help="Pipeline start date (YYYY-MM-DD).")],
    end: Annotated[str, typer.Option("--end", help="Pipeline end date (YYYY-MM-DD).")],
    config: Annotated[Path, typer.Option(help="Path to YAML config file.")] = _DEFAULT_CONFIG,
    output: Annotated[Path, typer.Option(help="Root output directory.")] = Path("results/"),
) -> None:
    """Run C1 -> C2 -> C3 end-to-end for the given date range (with optional AIFS track)."""
    from gik_icechain.shared.validation import validate_date_range

    s, e = _parse_date(start), _parse_date(end)
    validate_date_range(s, e)
    cfg = _bootstrap(config)
    exc_uri = cfg.outputs.exceedance_store_uri or str(output / "exceedance-zarr")
    aifs_enabled = cfg.aifs_track.enabled and bool(cfg.aifs_track.aifs_store_uri)
    n_steps = 5 if aifs_enabled else 3
    step = 0

    try:
        step += 1
        typer.echo(f"[{step}/{n_steps}] convert (IFS) ...")
        _run_convert(cfg, s, e)

        if aifs_enabled:
            step += 1
            typer.echo(f"[{step}/{n_steps}] convert (AIFS) ...")
            _run_convert_aifs(cfg, s, e)

        step += 1
        typer.echo(f"[{step}/{n_steps}] exceedance (IFS) ...")
        _run_exceedance(cfg, cfg.outputs.icechunk_store_uri, exc_uri, s, e)

        if aifs_enabled:
            aifs_exc_uri = (
                cfg.aifs_track.exceedance_store_uri
                or str(output / "aifs-exceedance-zarr")
            )
            step += 1
            typer.echo(f"[{step}/{n_steps}] exceedance (AIFS) + comparison ...")
            _run_exceedance(
                cfg, cfg.aifs_track.aifs_store_uri, aifs_exc_uri, s, e,
            )
            if cfg.aifs_track.comparison_enabled:
                from gik_icechain.exceedance.aifs_track import (
                    compute_aifs_ifs_delta,
                    seasonal_comparison,
                )

                comparison_dir = cfg.aifs_track.comparison_output_dir
                compute_aifs_ifs_delta(exc_uri, aifs_exc_uri, comparison_dir)
                enso_path = cfg.component2.thresholds.enso_iod_index_path
                results = seasonal_comparison(
                    exc_uri, aifs_exc_uri, enso_iod_path=enso_path,
                )
                for season_key, ds in results.items():
                    out_path = str(
                        Path(comparison_dir) / f"seasonal_{season_key}.zarr"
                    )
                    ds.to_zarr(out_path, mode="w")

        step += 1
        typer.echo(f"[{step}/{n_steps}] risk ...")
        _run_risk(cfg, exc_uri, output / "admin1_risk", s, e)
    except Exception as exc:
        _exit_on_error("run-all", exc)

    typer.echo(f"Pipeline complete. Results in {output}")


if __name__ == "__main__":
    app()
