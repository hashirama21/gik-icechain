"""GIK-IceChain command-line interface.

Entry points:
    gik-icechain convert      -- C1: ingest ECMWF GRIB2 → IceChunk virtual store
    gik-icechain exceedance   -- C2: compute exceedance probabilities
    gik-icechain risk         -- C3: run CRMA risk batch
    gik-icechain run-all      -- run the full pipeline end-to-end
    gik-icechain dashboard    -- start the dashboard data-prep server
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Annotated

import structlog
import typer

log = structlog.get_logger(__name__)


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

_DEFAULT_CONFIG = Path("configs/default.yaml")


def _bootstrap(config_path: Path) -> GIKConfig:  # noqa: F821
    from gik_icechain.shared.config import load_config
    from gik_icechain.shared.logging import configure_logging

    cfg = load_config(config_path if config_path.exists() else None)
    configure_logging(level=cfg.logging.level, fmt=cfg.logging.format)
    return cfg


def _run_convert(cfg: GIKConfig, start: date, end: date) -> str:  # noqa: F821
    """Run C1 ingest. Returns the last IceChunk commit hash (empty string if nothing ingested)."""
    from gik_icechain.conversion.gik_loader import GIKCatalog
    from gik_icechain.conversion.icechunk_writer import IceChainStore
    from gik_icechain.conversion.virtualizer import parquet_to_virtual_dataset

    catalog = GIKCatalog(cfg.sources.gik_hf_dataset)
    catalog.load_catalog()
    store = IceChainStore(cfg.outputs.icechunk_store_uri)
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
            # Pass the raw Parquet paths directly to VirtualiZarr; do not
            # pre-load them as DataFrames (parquet_to_virtual_dataset
            # needs the file paths, not the loaded content).
            vds = parquet_to_virtual_dataset(paths, variables=cfg.component1.variables)
            last_commit = store.commit_day(current, vds, run_hour)
        current += timedelta(days=1)

    return last_commit


def _run_exceedance(
    cfg: GIKConfig,  # noqa: F821
    store_uri: str,
    output_uri: str,
    start: date | None,
    end: date | None,
) -> int:
    import pandas as pd
    import xarray as xr

    from gik_icechain.conversion.icechunk_writer import IceChainStore
    from gik_icechain.exceedance.accumulations import compute_rolling_accumulations
    from gik_icechain.exceedance.exceedance import compute_exceedance_probabilities
    from gik_icechain.exceedance.thresholds import (
        AdaptiveGEVThresholds,
        ClimateMode,
        ENSOPhase,
        IODPhase,
        classify_enso,
        classify_iod,
        get_season,
    )
    from gik_icechain.exceedance.writer import build_exceedance_dataset, write_exceedance_store

    # Iterate over committed date groups directly — IceChunk stores one zarr
    # group per forecast date (step-based), not a time-series dataset.
    store_obj = IceChainStore(store_uri)
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

    thresholds = AdaptiveGEVThresholds.load(Path(cfg.sources.cmorph_thresholds_path))
    enso_iod = pd.read_csv(
        cfg.component2.thresholds.enso_iod_index_path, parse_dates=["date"]
    ).set_index("date")

    def _mode_for(d: date) -> ClimateMode:
        season = get_season(d.month)
        try:
            row = enso_iod.loc[pd.Timestamp(d)]
            return ClimateMode(
                season, classify_enso(float(row["nino34"])), classify_iod(float(row["dmi"]))
            )
        except KeyError:
            return ClimateMode(season, ENSOPhase.NEUTRAL, IODPhase.NEUTRAL)

    results: dict[date, xr.DataArray] = {}
    session = store_obj._repo.readonly_session(branch=store_obj.branch)

    for date_str in committed_dates:
        day = date.fromisoformat(date_str)
        mode = _mode_for(day)

        try:
            day_ds = xr.open_zarr(session.store, group=date_str, consolidated=False)
            day_ds = day_ds.chunk(cfg.component2.dask.chunk_dims)
        except Exception as exc:
            log.warning("exceedance_date_open_failed", date=date_str, error=str(exc)[:120])
            continue

        acc_ds = compute_rolling_accumulations(day_ds, windows_h=cfg.component2.windows_h)

        day_results: dict[tuple[int, int], xr.DataArray] = {}
        for w in cfg.component2.windows_h:
            for rp in cfg.component2.return_periods:
                try:
                    thr = thresholds.get(w, rp, mode)
                    p_exceed = compute_exceedance_probabilities(
                        acc_ds,
                        xr.Dataset({f"rp_{rp}y": thr}),
                        window_h=w,
                        return_period=rp,
                        member_dim="member",
                    )
                    day_results[(w, rp)] = p_exceed
                except Exception as exc:
                    log.debug("exceedance_skip", window=w, rp=rp, error=str(exc)[:80])

        if day_results:
            results[day] = build_exceedance_dataset(day_results, day)

    write_exceedance_store(results, output_uri, append=True)
    return len(results)


def _run_risk(
    cfg: GIKConfig,  # noqa: F821
    exc_uri: str,
    output: Path,
    start: date,
    end: date,
) -> list[Path]:
    from gik_icechain.risk.crma_model import CRMAModel
    from gik_icechain.risk.risk_engine import run_risk_batch

    crma = CRMAModel(crma_cfg=cfg.component3.crma_model)
    crma.build()
    if cfg.component3.crma.use_refined_cpts and cfg.component3.crma.cpt_path:
        crma.load_cpts(Path(cfg.component3.crma.cpt_path))

    crma_cfg = cfg.component3.crma_model
    return run_risk_batch(
        exceedance_store_uri=exc_uri,
        gpm_dir=Path(cfg.sources.gpm_imerg_path),
        admin_boundaries_path=Path(cfg.sources.admin_boundaries_path),
        crma_model=crma,
        output_dir=output,
        start=start,
        end=end,
        api_decay=cfg.component3.api.decay_factor,
        initial_api_mm=cfg.component3.api.initial_api_mm,
        signal_threshold=crma_cfg.signal_threshold_prob,
        rp_signal=crma_cfg.rp_signal,
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

    commit_hash = _run_convert(cfg, s, e)

    if output_json is not None:
        output_json.write_text(
            json.dumps({"commit_hash": commit_hash, "processed_date": e.isoformat()})
        )

    typer.echo(f"Convert complete: {s} → {e}  commit={commit_hash[:12] if commit_hash else 'none'}")


@app.command()
def exceedance(
    store: Annotated[str, typer.Option(help="URI of the IceChunk virtual store.")],
    output: Annotated[str, typer.Option(help="URI for the output exceedance Zarr store.")],
    config: Annotated[Path, typer.Option(help="Path to YAML config file.")] = _DEFAULT_CONFIG,
    workers: Annotated[int, typer.Option(help="Dask distributed workers.")] = 16,
    start: Annotated[str | None, typer.Option(help="First date (YYYY-MM-DD).")] = None,
    end: Annotated[str | None, typer.Option(help="Last date (YYYY-MM-DD).")] = None,
    thresholds: Annotated[
        str | None, typer.Option("--thresholds", help="Override CMORPH thresholds URI/path.")
    ] = None,
    region: Annotated[
        str | None, typer.Option("--region", help="Spatial domain label (informational).")
    ] = None,
    mode: Annotated[
        str, typer.Option("--mode", help="append or overwrite (append is always the default).")
    ] = "append",
) -> None:
    """Compute adaptive GEV exceedance probabilities for all accumulation windows (C2)."""
    cfg = _bootstrap(config)
    if thresholds:
        cfg.sources.cmorph_thresholds_path = thresholds

    s = _parse_date(start) if start else None
    e = _parse_date(end) if end else None

    if workers > 1:
        try:
            from dask.distributed import Client

            Client(n_workers=workers, threads_per_worker=2, silence_logs=True)
        except ImportError:
            pass

    n = _run_exceedance(cfg, store, output, s, e)
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

    written = _run_risk(cfg, exceedance_store, output, s, e)
    typer.echo(f"Risk complete: {len(written)} GeoJSON files in {output}")


@app.command("run-all")
def run_all(
    start: Annotated[str, typer.Option("--start", help="Pipeline start date (YYYY-MM-DD).")],
    end: Annotated[str, typer.Option("--end", help="Pipeline end date (YYYY-MM-DD).")],
    config: Annotated[Path, typer.Option(help="Path to YAML config file.")] = _DEFAULT_CONFIG,
    output: Annotated[Path, typer.Option(help="Root output directory.")] = Path("results/"),
) -> None:
    """Run C1 → C2 → C3 end-to-end for the given date range."""
    from gik_icechain.shared.validation import validate_date_range

    s, e = _parse_date(start), _parse_date(end)
    validate_date_range(s, e)
    cfg = _bootstrap(config)
    exc_uri = cfg.outputs.exceedance_store_uri or str(output / "exceedance-zarr")

    typer.echo("[1/3] convert …")
    _run_convert(cfg, s, e)

    typer.echo("[2/3] exceedance …")
    _run_exceedance(cfg, cfg.outputs.icechunk_store_uri, exc_uri, s, e)

    typer.echo("[3/3] risk …")
    _run_risk(cfg, exc_uri, output / "admin1_risk", s, e)

    typer.echo(f"Pipeline complete. Results in {output}")


@app.command()
def dashboard(
    port: Annotated[int, typer.Option(help="HTTP port for the dashboard server.")] = 8080,
    config: Annotated[Path, typer.Option(help="Path to YAML config file.")] = _DEFAULT_CONFIG,
) -> None:
    """Start the GIK-IceChain dashboard data-prep server."""
    cfg = _bootstrap(config)
    typer.echo(f"Starting dashboard on port {port} …")
    typer.echo(f"TiTiler endpoint: {cfg.dashboard.titiler.endpoint}")
    typer.echo(f"VEDA UI base URL:  {cfg.dashboard.veda_ui.base_url}")

    try:
        import uvicorn
        from dashboard.app import create_app

        uvicorn.run(create_app(cfg), host="0.0.0.0", port=port)
    except ImportError:
        typer.echo(
            "Dashboard dependencies not installed. Run: pip install gik-icechain[dashboard]",
            err=True,
        )
        sys.exit(1)


if __name__ == "__main__":
    app()
