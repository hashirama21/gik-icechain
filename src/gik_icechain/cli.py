"""GIK-IceChain command-line interface.

Entry points:
    gik-icechain convert      -- C1: ingest ECMWF GRIB2 → IceChunk virtual store
    gik-icechain exceedance   -- C2: compute exceedance probabilities
    gik-icechain risk         -- C3: run CRMA risk batch
    gik-icechain run-all      -- run the full pipeline end-to-end
    gik-icechain dashboard    -- start the dashboard data-prep server
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Annotated

import typer

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


def _run_convert(cfg: GIKConfig, start: date, end: date) -> None:  # noqa: F821
    from gik_icechain.conversion.gik_loader import GIKCatalog, load_gik_parquet
    from gik_icechain.conversion.icechunk_writer import IceChainStore
    from gik_icechain.conversion.virtualizer import parquet_to_virtual_dataset

    catalog = GIKCatalog(cfg.sources.gik_hf_dataset)
    catalog.load_catalog()
    store = IceChainStore(cfg.outputs.icechunk_store_uri)

    current = start
    while current <= end:
        for run_hour in cfg.component1.run_hours:
            paths = catalog.get_parquet_paths(
                start=current,
                end=current,
                run_hours=(run_hour,),
                variables=cfg.component1.variables,
            )
            for path in paths:
                manifest = load_gik_parquet(path, variables=cfg.component1.variables)
                vds = parquet_to_virtual_dataset(manifest)
                store.commit_day(current, run_hour, vds)
        current += timedelta(days=1)


def _run_exceedance(
    cfg: GIKConfig,  # noqa: F821
    store_uri: str,
    output_uri: str,
    start: date | None,
    end: date | None,
) -> int:
    import pandas as pd
    import xarray as xr

    from gik_icechain.exceedance.accumulations import compute_rolling_accumulations
    from gik_icechain.exceedance.exceedance import compute_exceedance_probabilities
    from gik_icechain.exceedance.loader import open_icechunk_store
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

    ds = open_icechunk_store(store_uri, chunks=cfg.component2.dask.chunk_dims)
    thresholds = AdaptiveGEVThresholds.load(Path(cfg.sources.cmorph_thresholds_path))
    acc_ds = compute_rolling_accumulations(ds, windows_h=cfg.component2.windows_h)

    enso_iod = (
        pd.read_csv(cfg.component2.thresholds.enso_iod_index_path, parse_dates=["date"])
        .set_index("date")
    )

    def _mode_for(d: date) -> ClimateMode:
        season = get_season(d.month)
        try:
            row = enso_iod.loc[pd.Timestamp(d)]
            return ClimateMode(
                season, classify_enso(float(row["nino34"])), classify_iod(float(row["dmi"]))
            )
        except KeyError:
            return ClimateMode(season, ENSOPhase.NEUTRAL, IODPhase.NEUTRAL)

    all_dates = sorted(str(t)[:10] for t in ds["time"].values)
    if start:
        all_dates = [d for d in all_dates if d >= start.isoformat()]
    if end:
        all_dates = [d for d in all_dates if d <= end.isoformat()]

    results: dict[date, xr.DataArray] = {}
    for d_str in all_dates:
        day = date.fromisoformat(d_str)
        mode = _mode_for(day)
        day_results: dict[tuple[int, int], xr.DataArray] = {}

        for w in cfg.component2.windows_h:
            for rp in cfg.component2.return_periods:
                try:
                    thr_ds = xr.Dataset({f"rp_{rp}y": thresholds.get(w, rp, mode)})
                    day_results[(w, rp)] = compute_exceedance_probabilities(
                        acc_ds.sel(time=pd.Timestamp(d_str)), thr_ds, window_h=w, return_period=rp
                    )
                except Exception:
                    pass

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

    crma = CRMAModel()
    crma.build()
    if cfg.component3.crma.use_refined_cpts and cfg.component3.crma.cpt_path:
        crma.load_cpts(Path(cfg.component3.crma.cpt_path))

    return run_risk_batch(
        exceedance_store_uri=exc_uri,
        gpm_dir=Path(cfg.sources.gpm_imerg_path),
        admin_boundaries_path=Path(cfg.sources.admin_boundaries_path),
        crma_model=crma,
        output_dir=output,
        start=start,
        end=end,
        api_decay=cfg.component3.api.decay_factor,
    )


@app.command()
def convert(
    start: Annotated[date, typer.Option("--start", help="First forecast date (YYYY-MM-DD).")],
    end: Annotated[date, typer.Option("--end", help="Last forecast date (YYYY-MM-DD).")],
    config: Annotated[Path, typer.Option(help="Path to YAML config file.")] = _DEFAULT_CONFIG,
) -> None:
    """Ingest ECMWF IFS ensemble GRIB2 files into an IceChunk virtual store (C1)."""
    from gik_icechain.shared.validation import validate_date_range

    validate_date_range(start, end)
    _run_convert(_bootstrap(config), start, end)
    typer.echo(f"Convert complete: {start} → {end}")


@app.command()
def exceedance(
    store: Annotated[str, typer.Option(help="URI of the IceChunk virtual store.")],
    output: Annotated[str, typer.Option(help="URI for the output exceedance Zarr store.")],
    config: Annotated[Path, typer.Option(help="Path to YAML config file.")] = _DEFAULT_CONFIG,
    workers: Annotated[int, typer.Option(help="Dask distributed workers.")] = 16,
    start: Annotated[date | None, typer.Option(help="First date (YYYY-MM-DD).")] = None,
    end: Annotated[date | None, typer.Option(help="Last date (YYYY-MM-DD).")] = None,
) -> None:
    """Compute adaptive GEV exceedance probabilities for all accumulation windows (C2)."""
    cfg = _bootstrap(config)

    if workers > 1:
        try:
            from dask.distributed import Client
            Client(n_workers=workers, threads_per_worker=2, silence_logs=True)
        except ImportError:
            pass

    n = _run_exceedance(cfg, store, output, start, end)
    typer.echo(f"Exceedance complete: {n} days written to {output}")


@app.command()
def risk(
    exceedance_store: Annotated[str, typer.Option(help="URI of the exceedance Zarr store.")],
    output: Annotated[Path, typer.Option(help="Output directory for GeoJSON files.")],
    config: Annotated[Path, typer.Option(help="Path to YAML config file.")] = _DEFAULT_CONFIG,
    start: Annotated[date | None, typer.Option(help="First date (YYYY-MM-DD).")] = None,
    end: Annotated[date | None, typer.Option(help="Last date (YYYY-MM-DD).")] = None,
) -> None:
    """Run CRMA Bayesian Network risk inference for all admin-1 units (C3)."""
    import xarray as xr

    cfg = _bootstrap(config)

    if start is None or end is None:
        exc_ds = xr.open_zarr(exceedance_store, consolidated=False)
        dates = sorted(str(d)[:10] for d in exc_ds["date"].values)
        if start is None:
            start = date.fromisoformat(dates[0])
        if end is None:
            end = date.fromisoformat(dates[-1])

    written = _run_risk(cfg, exceedance_store, output, start, end)
    typer.echo(f"Risk complete: {len(written)} GeoJSON files in {output}")


@app.command("run-all")
def run_all(
    start: Annotated[date, typer.Option("--start", help="Pipeline start date (YYYY-MM-DD).")],
    end: Annotated[date, typer.Option("--end", help="Pipeline end date (YYYY-MM-DD).")],
    config: Annotated[Path, typer.Option(help="Path to YAML config file.")] = _DEFAULT_CONFIG,
    output: Annotated[Path, typer.Option(help="Root output directory.")] = Path("results/"),
) -> None:
    """Run C1 → C2 → C3 end-to-end for the given date range."""
    from gik_icechain.shared.validation import validate_date_range

    validate_date_range(start, end)
    cfg = _bootstrap(config)
    exc_uri = cfg.outputs.exceedance_store_uri or str(output / "exceedance-zarr")

    typer.echo("[1/3] convert …")
    _run_convert(cfg, start, end)

    typer.echo("[2/3] exceedance …")
    _run_exceedance(cfg, cfg.outputs.icechunk_store_uri, exc_uri, start, end)

    typer.echo("[3/3] risk …")
    _run_risk(cfg, exc_uri, output / "admin1_risk", start, end)

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
        import uvicorn  # type: ignore[import-untyped]
        from dashboard.app import create_app  # type: ignore[import-not-found]

        uvicorn.run(create_app(cfg), host="0.0.0.0", port=port)
    except ImportError:
        typer.echo(
            "Dashboard dependencies not installed. "
            "Run: pip install gik-icechain[dashboard]",
            err=True,
        )
        sys.exit(1)
