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
from datetime import date
from pathlib import Path
from typing import Annotated, Optional

import typer

app = typer.Typer(
    name="gik-icechain",
    help="GIK-IceChain v2.0 — zero-cost flood risk pipeline for East Africa.",
    no_args_is_help=True,
)


def _bootstrap(config_path: Path, log_level: str = "INFO") -> "GIKConfig":  # noqa: F821
    from gik_icechain.shared.config import load_config
    from gik_icechain.shared.logging import configure_logging

    cfg = load_config(config_path if config_path.exists() else None)
    configure_logging(level=log_level, fmt=cfg.logging.format)
    return cfg


@app.command()
def convert(
    start: Annotated[date, typer.Option("--start", help="First forecast date (YYYY-MM-DD).")],
    end: Annotated[date, typer.Option("--end", help="Last forecast date (YYYY-MM-DD).")],
    config: Annotated[Path, typer.Option(help="Path to YAML config file.")] = Path("configs/default.yaml"),
    workers: Annotated[int, typer.Option(help="Dask workers for parallel ingest.")] = 16,
) -> None:
    """Ingest ECMWF IFS ensemble GRIB2 files into an IceChunk virtual store (C1)."""
    from gik_icechain.shared.validation import validate_date_range

    validate_date_range(start, end)
    cfg = _bootstrap(config)

    from gik_icechain.conversion.gik_loader import load_gik_parquet, GIKCatalog
    from gik_icechain.conversion.virtualizer import parquet_to_virtual_dataset
    from gik_icechain.conversion.icechunk_writer import IceChainStore

    catalog = load_gik_parquet(
        hf_dataset=cfg.sources.gik_hf_dataset,
        catalog_file=cfg.sources.gik_catalog_file,
    )

    store = IceChainStore(cfg.outputs.icechunk_store_uri)
    current = start
    from datetime import timedelta

    while current <= end:
        for run_hour in cfg.component1.run_hours:
            manifest = catalog.for_date(current, run_hour)
            if manifest is None:
                continue
            vds = parquet_to_virtual_dataset(manifest)
            store.commit_day(current, run_hour, vds)
        current += timedelta(days=1)

    typer.echo(f"Convert complete: {start} → {end}")


@app.command()
def exceedance(
    store: Annotated[str, typer.Option(help="URI of the IceChunk virtual store.")],
    output: Annotated[str, typer.Option(help="URI for the output exceedance Zarr store.")],
    config: Annotated[Path, typer.Option(help="Path to YAML config file.")] = Path("configs/default.yaml"),
    workers: Annotated[int, typer.Option(help="Dask workers.")] = 32,
    start: Annotated[Optional[date], typer.Option(help="First date (YYYY-MM-DD).")] = None,
    end: Annotated[Optional[date], typer.Option(help="Last date (YYYY-MM-DD).")] = None,
) -> None:
    """Compute adaptive GEV exceedance probabilities for all accumulation windows (C2)."""
    cfg = _bootstrap(config)

    from gik_icechain.exceedance.loader import open_icechunk_store
    from gik_icechain.exceedance.accumulations import compute_rolling_accumulations
    from gik_icechain.exceedance.thresholds import AdaptiveGEVThresholds
    from gik_icechain.exceedance.exceedance import compute_exceedance_probabilities
    from gik_icechain.exceedance.writer import write_exceedance_store

    ds = open_icechunk_store(store, chunks=cfg.component2.dask.chunk_dims)
    thresholds = AdaptiveGEVThresholds.from_cmorph(cfg.sources.cmorph_thresholds_path)
    acc_ds = compute_rolling_accumulations(
        ds,
        windows_h=cfg.component2.windows_h,
    )

    dates = sorted(str(t)[:10] for t in ds["time"].values)
    if start:
        dates = [d for d in dates if d >= start.isoformat()]
    if end:
        dates = [d for d in dates if d <= end.isoformat()]

    from datetime import date as date_type
    import pandas as pd

    results: dict[date_type, "xr.DataArray"] = {}  # type: ignore[name-defined]
    for d_str in dates:
        day = date_type.fromisoformat(d_str)
        exc = compute_exceedance_probabilities(
            acc_ds.sel(time=pd.Timestamp(d_str)),
            thresholds,
            return_periods=cfg.component2.return_periods,
        )
        results[day] = exc

    write_exceedance_store(results, output, append=True)
    typer.echo(f"Exceedance complete: {len(results)} days written to {output}")


@app.command()
def risk(
    exceedance_store: Annotated[str, typer.Option(help="URI of the exceedance Zarr store.")],
    output: Annotated[Path, typer.Option(help="Output directory for GeoJSON files.")],
    config: Annotated[Path, typer.Option(help="Path to YAML config file.")] = Path("configs/default.yaml"),
    start: Annotated[Optional[date], typer.Option(help="First date (YYYY-MM-DD).")] = None,
    end: Annotated[Optional[date], typer.Option(help="Last date (YYYY-MM-DD).")] = None,
) -> None:
    """Run CRMA Bayesian Network risk inference for all admin-1 units (C3)."""
    cfg = _bootstrap(config)

    from gik_icechain.risk.crma_model import CRMAModel
    from gik_icechain.risk.risk_engine import run_risk_batch

    crma_model = CRMAModel()
    crma_model.build()
    if cfg.component3.crma.use_refined_cpts and cfg.component3.crma.cpt_path:
        crma_model = CRMAModel.load(Path(cfg.component3.crma.cpt_path))

    import xarray as xr

    if start is None or end is None:
        exc_ds = xr.open_zarr(exceedance_store, consolidated=False)
        dates = sorted(str(d)[:10] for d in exc_ds["date"].values)
        if start is None:
            from datetime import date as date_type
            start = date_type.fromisoformat(dates[0])
        if end is None:
            from datetime import date as date_type
            end = date_type.fromisoformat(dates[-1])

    written = run_risk_batch(
        exceedance_store_uri=exceedance_store,
        gpm_dir=Path(cfg.sources.gpm_imerg_path),
        admin_boundaries_path=Path(cfg.sources.admin_boundaries_path),
        crma_model=crma_model,
        output_dir=output,
        start=start,
        end=end,
        api_decay=cfg.component3.api.decay_factor,
    )
    typer.echo(f"Risk complete: {len(written)} GeoJSON files in {output}")


@app.command("run-all")
def run_all(
    start: Annotated[date, typer.Option("--start", help="Pipeline start date (YYYY-MM-DD).")],
    end: Annotated[date, typer.Option("--end", help="Pipeline end date (YYYY-MM-DD).")],
    config: Annotated[Path, typer.Option(help="Path to YAML config file.")] = Path("configs/default.yaml"),
    output: Annotated[Path, typer.Option(help="Root output directory.")] = Path("results/"),
) -> None:
    """Run C1 → C2 → C3 end-to-end for the given date range."""
    from gik_icechain.shared.validation import validate_date_range

    validate_date_range(start, end)
    cfg = _bootstrap(config)

    typer.echo("[1/3] convert …")
    ctx = typer.Context(app)
    ctx.invoke(convert, start=start, end=end, config=config, workers=cfg.component2.dask.n_workers)

    typer.echo("[2/3] exceedance …")
    exc_uri = cfg.outputs.exceedance_store_uri or str(output / "exceedance-zarr")
    ctx.invoke(exceedance, store=cfg.outputs.icechunk_store_uri, output=exc_uri, config=config)

    typer.echo("[3/3] risk …")
    ctx.invoke(risk, exceedance_store=exc_uri, output=output / "admin1_risk", config=config, start=start, end=end)

    typer.echo(f"Pipeline complete. Results in {output}")


@app.command()
def dashboard(
    port: Annotated[int, typer.Option(help="HTTP port for the dashboard server.")] = 8080,
    config: Annotated[Path, typer.Option(help="Path to YAML config file.")] = Path("configs/default.yaml"),
) -> None:
    """Start the GIK-IceChain dashboard data-prep server."""
    cfg = _bootstrap(config)
    typer.echo(f"Starting dashboard on port {port} …")
    typer.echo(f"TiTiler endpoint: {cfg.dashboard.titiler.endpoint}")
    typer.echo(f"VEDA UI base URL: {cfg.dashboard.veda_ui.base_url}")

    try:
        import uvicorn
        from dashboard.app import create_app

        uvicorn.run(create_app(cfg), host="0.0.0.0", port=port)
    except ImportError:
        typer.echo(
            "Dashboard dependencies not installed. "
            "Run: pip install gik-icechain[dashboard]",
            err=True,
        )
        sys.exit(1)
