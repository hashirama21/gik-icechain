#!/usr/bin/env python3
"""GIK-IceChain developer tools.

CLI for benchmarking, store validation, data download,
gap-filling, EAHW export, and EM-DAT validation.

Usage:
    python scripts/tools.py --help
    python scripts/tools.py benchmark --gik-store s3://...
    python scripts/tools.py validate-store --store-uri s3://...
    python scripts/tools.py download --component all
    python scripts/tools.py gap-fill --start 2023-05-01 --end 2024-02-29
    python scripts/tools.py export-eahw --risk-dir results/admin1_risk/ --output results/eahw/
    python scripts/tools.py validate-emdat --risk-dir results/admin1_risk/
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Annotated

import typer

REPO_ROOT = Path(__file__).resolve().parent.parent

app = typer.Typer(
    name="gik-tools",
    help="GIK-IceChain developer and operations tools.",
    no_args_is_help=True,
)


#  benchmark


@app.command()
def benchmark(
    gik_store: Annotated[
        str, typer.Option("--gik-store", help="URI of the GIK IceChunk store.")
    ],
    dynamical_store: Annotated[
        str | None,
        typer.Option(
            "--dynamical-store",
            help="URI of conventional Zarr store to compare.",
        ),
    ] = None,
    domain: Annotated[
        str, typer.Option(help="Domain label for output CSV.")
    ] = "east_africa",
    n_days: Annotated[
        int, typer.Option(help="Forecast day-groups to benchmark.")
    ] = 30,
    workers: Annotated[
        int, typer.Option(help="Dask workers for full-scan measurement.")
    ] = 4,
    output_dir: Annotated[
        Path, typer.Option(help="Directory for benchmark CSV output.")
    ] = Path("results/benchmarks/"),
) -> None:
    """Benchmark storage efficiency and access speed of GIK+IceChunk."""
    from gik_icechain.conversion.benchmark import run_benchmark

    results = run_benchmark(
        gik_store_uri=gik_store,
        dynamical_store_uri=dynamical_store,
        domain=domain,
        n_days=n_days,
        n_workers=workers,
        output_dir=str(output_dir),
    )

    if not results:
        typer.echo(
            "No benchmark results produced. "
            "Check that the store URI is accessible."
        )
        raise typer.Exit(1)

    header = (
        f"{'Approach':<20} {'Store (GB)':>12} "
        f"{'TTFB (s)':>10} {'Scan (s)':>10} {'Egress $':>10}"
    )
    typer.echo(f"\n{header}")
    typer.echo("-" * 66)
    for r in results.values():
        typer.echo(
            f"{r.approach:<20} {r.store_size_gb:>12,.0f} "
            f"{r.time_to_first_byte_s:>10.3f} "
            f"{r.full_scan_elapsed_s:>10.1f} "
            f"{r.estimated_egress_usd:>10.4f}"
        )

    if "GIK+IceChunk" in results and "dynamical.org" in results:
        gik = results["GIK+IceChunk"]
        dyn = results["dynamical.org"]
        ratio = (
            dyn.store_size_gb / gik.store_size_gb
            if gik.store_size_gb
            else 0
        )
        typer.echo(f"\nStorage compression ratio: {ratio:,.0f}x")

    typer.echo(f"\nResults saved to {output_dir}")


#  validate-store


@app.command("validate-store")
def validate_store(
    store_uri: Annotated[
        str,
        typer.Option(
            "--store-uri", help="IceChunk store URI (s3:// or local path)."
        ),
    ],
    output_json: Annotated[
        bool, typer.Option("--json", help="Output results as JSON.")
    ] = False,
) -> None:
    """Validate IceChunk store integrity: committed days, gaps, variables."""
    from gik_icechain.conversion.icechunk_writer import IceChainStore

    store = IceChainStore(store_uri)
    store.create_or_open()
    report = store.validate()

    if output_json:
        typer.echo(json.dumps(report, indent=2))
    else:
        typer.echo(f"Store URI       : {store_uri}")
        typer.echo(f"Committed days  : {report['committed_days']}")
        typer.echo(f"Date range      : {report['date_range']}")
        typer.echo(f"Total snapshots : {report['total_snapshots']}")
        typer.echo(f"Gaps detected   : {report['gaps_detected']}")
        if report["gap_details"]:
            typer.echo("Gap details:")
            for g in report["gap_details"]:
                typer.echo(f"  {g}")
        typer.echo(f"Variables       : {report['variables_present']}")

    if report["gaps_detected"] > 0:
        typer.echo(
            f"\nWARNING: {report['gaps_detected']} gap(s) detected",
            err=True,
        )
        raise typer.Exit(1)

    typer.echo("\nStore is valid.")


#  download


def _download_admin_boundaries(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    dest = output_dir / "east_africa_admin1.gpkg"
    if dest.exists():
        typer.echo(f"  Already exists: {dest}")
        return

    typer.echo("  Downloading admin-1 boundaries from HuggingFace ...")
    from huggingface_hub import hf_hub_download

    path = hf_hub_download(
        repo_id="E4DRR/gik-ecmwf-par",
        filename="admin_boundaries/east_africa_admin1.gpkg",
        repo_type="dataset",
        local_dir=str(output_dir),
    )
    typer.echo(f"  Saved: {path}")


def _download_cmorph_thresholds(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    typer.echo("  Downloading CMORPH GEV thresholds from HuggingFace ...")
    from huggingface_hub import snapshot_download

    snapshot_download(
        repo_id="E4DRR/gik-ecmwf-par",
        repo_type="dataset",
        allow_patterns="cmorph_thresholds/*.nc",
        local_dir=str(output_dir.parent),
    )
    typer.echo(f"  Saved to: {output_dir}")


def _download_enso_iod(output_dir: Path) -> None:
    import urllib.request

    output_dir.mkdir(parents=True, exist_ok=True)
    dest = output_dir / "enso_iod_index.csv"
    if dest.exists():
        typer.echo(f"  Already exists: {dest}")
        return

    typer.echo("  Downloading ENSO/IOD index ...")
    url = (
        "https://huggingface.co/datasets/E4DRR/gik-ecmwf-par"
        "/resolve/main/enso_iod_index.csv"
    )
    urllib.request.urlretrieve(url, dest)
    typer.echo(f"  Saved: {dest}")


@app.command()
def download(
    component: Annotated[
        str,
        typer.Option(
            help="Component to download: all, admin, thresholds, enso_iod."
        ),
    ] = "all",
    output: Annotated[
        Path, typer.Option(help="Base output directory.")
    ] = REPO_ROOT / "data",
) -> None:
    """Download reference data (admin boundaries, thresholds, ENSO/IOD)."""
    typer.echo(f"Downloading: {component}  ->  {output}")

    if component in ("all", "admin"):
        _download_admin_boundaries(output / "admin_boundaries")
    if component in ("all", "thresholds"):
        _download_cmorph_thresholds(output / "cmorph_thresholds")
    if component in ("all", "enso_iod"):
        _download_enso_iod(output)

    typer.echo("Done.")


# ── gap-fill

_GAP_START = date(2023, 5, 1)
_GAP_END = date(2024, 2, 29)


def _already_committed(store_uri: str, config_path: Path) -> set[str]:
    """Return forecast dates already committed to the IceChunk store."""
    from gik_icechain.conversion.icechunk_writer import IceChainStore
    from gik_icechain.shared.config import load_config

    cfg = load_config(config_path if config_path.exists() else None)
    uri = store_uri or cfg.outputs.icechunk_store_uri
    store = IceChainStore(uri)
    try:
        store.create_or_open()
        return {
            s["forecast_date"]
            for s in store.list_snapshots()
            if s["forecast_date"]
        }
    except Exception:
        return set()


@app.command("gap-fill")
def gap_fill(
    start: Annotated[
        str, typer.Option("--start", help="First date (YYYY-MM-DD).")
    ] = _GAP_START.isoformat(),
    end: Annotated[
        str, typer.Option("--end", help="Last date (YYYY-MM-DD).")
    ] = _GAP_END.isoformat(),
    config: Annotated[
        Path, typer.Option(help="Path to YAML config file.")
    ] = Path("configs/default.yaml"),
    store: Annotated[
        str | None,
        typer.Option("--store", help="Override IceChunk store URI."),
    ] = None,
    batch_size: Annotated[
        int, typer.Option(help="Days per batch (resume-safe).")
    ] = 7,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Print dates without ingesting.")
    ] = False,
) -> None:
    """Back-fill the GIK archive gap into the IceChunk store."""
    from gik_icechain.cli import _bootstrap, _run_convert

    s = date.fromisoformat(start)
    e = date.fromisoformat(end)

    if s > e:
        typer.echo("--start must be before --end", err=True)
        raise typer.Exit(1)

    committed = _already_committed(store or "", config)
    missing = []
    current = s
    while current <= e:
        if current.isoformat() not in committed:
            missing.append(current)
        current += timedelta(days=1)

    if not missing:
        typer.echo(f"No missing dates in [{s}, {e}]. Store is up to date.")
        return

    typer.echo(f"Missing dates: {len(missing)} (of {(e - s).days + 1} total)")
    typer.echo(f"Already committed: {len(committed)} dates")

    if dry_run:
        for d in missing[:20]:
            typer.echo(f"  {d.isoformat()}")
        if len(missing) > 20:
            typer.echo(f"  ... and {len(missing) - 20} more")
        return

    cfg = _bootstrap(config)
    if store:
        cfg.outputs.icechunk_store_uri = store

    processed = 0
    idx = 0
    while idx < len(missing):
        batch = missing[idx : idx + batch_size]
        batch_s, batch_e = batch[0], batch[-1]
        typer.echo(
            f"Ingesting {batch_s} -> {batch_e} ({len(batch)} days) ..."
        )
        try:
            _run_convert(cfg, batch_s, batch_e)
            processed += len(batch)
        except Exception as exc:
            typer.echo(
                f"Batch failed ({batch_s} -> {batch_e}): {exc}", err=True
            )
            typer.echo("Resuming from next batch ...")
        idx += batch_size

    typer.echo(
        f"\nGap-fill complete. Processed {processed} / {len(missing)} dates."
    )


#  export-eahw


@app.command("export-eahw")
def export_eahw(
    risk_dir: Annotated[
        Path,
        typer.Option(help="Directory with per-day risk GeoJSON files."),
    ],
    output: Annotated[
        Path, typer.Option(help="Output directory for EAHW GeoJSON files.")
    ],
) -> None:
    """Export admin-1 risk GeoJSON to East Africa Hazard Watch format."""
    from gik_icechain.risk.geojson_writer import export_eahw_format

    risk_files = sorted(risk_dir.glob("*_admin1_risk.geojson"))
    if not risk_files:
        typer.echo(f"No risk GeoJSON files found in: {risk_dir}", err=True)
        raise typer.Exit(1)

    output.mkdir(parents=True, exist_ok=True)
    exported = 0
    for f in risk_files:
        date_str = f.stem[:10]
        out_path = output / f"eahw_{date_str}.geojson"
        export_eahw_format(f, out_path)
        exported += 1

    typer.echo(f"Exported {exported} files to {output}")


#  validate-emdat


def _load_risk_results(risk_dir: Path):
    """Load all per-day GeoJSON files into a flat DataFrame."""
    import pandas as pd

    rows: list[dict] = []
    for geojson_path in sorted(risk_dir.glob("*_admin1_risk.geojson")):
        date_str = geojson_path.stem[:10]
        data = json.loads(geojson_path.read_text())
        for feat in data.get("features", []):
            props = feat["properties"]
            rows.append(
                {
                    "date": date_str,
                    "admin1_pcode": props.get("admin1_pcode", ""),
                    "risk_state": int(props.get("risk_state", 0)),
                    "p_red": float(props.get("p_red", 0.0)),
                }
            )
    if not rows:
        raise ValueError(f"No GeoJSON risk files found in {risk_dir}")
    return pd.DataFrame(rows)


@app.command("validate-emdat")
def validate_emdat(
    risk_dir: Annotated[
        Path, typer.Option(help="Directory with per-day GeoJSON risk files.")
    ] = Path("results/admin1_risk/"),
    emdat_csv: Annotated[
        Path, typer.Option(help="EM-DAT flood CSV (from emdat.be).")
    ] = Path("data/emdat/east_africa_floods.csv"),
    output: Annotated[
        Path, typer.Option(help="Output CSV for per-event hit/miss table.")
    ] = Path("results/validation/emdat_validation.csv"),
    risk_threshold: Annotated[
        int,
        typer.Option(help="Min risk_state for prediction (default 2=Orange)."),
    ] = 2,
) -> None:
    """Validate CRMA risk outputs against EM-DAT historical flood events."""
    from gik_icechain.risk.cpt_refinement import (
        load_emdat_east_africa,
        run_validation,
    )

    if not emdat_csv.exists():
        typer.echo(f"EM-DAT CSV not found: {emdat_csv}", err=True)
        raise typer.Exit(1)

    typer.echo(f"Loading EM-DAT records from {emdat_csv} ...")
    emdat_records = load_emdat_east_africa(emdat_csv)
    typer.echo(f"  {len(emdat_records)} flood events loaded.")

    typer.echo(f"Loading CRMA risk results from {risk_dir} ...")
    risk_df = _load_risk_results(risk_dir)
    typer.echo(f"  {len(risk_df)} day x unit records loaded.")

    typer.echo("Running validation ...")
    metrics = run_validation(
        risk_results_df=risk_df,
        emdat_records=emdat_records,
        output_path=output,
        risk_threshold=risk_threshold,
    )

    typer.echo(f"\n{'Metric':<22} {'Value':>8}")
    typer.echo("-" * 32)
    for k, v in metrics.items():
        typer.echo(f"  {k:<20} {v:>8.4f}")

    typer.echo(f"\nPer-event table saved to {output}")


#  entry point

if __name__ == "__main__":
    app()
