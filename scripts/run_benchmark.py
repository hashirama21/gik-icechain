"""CLI: benchmark GIK+IceChunk virtual store vs a conventional Zarr store.

Usage:
    python scripts/run_benchmark.py \\
        --gik-store s3://gik-icechain/gik-icechain-store \\
        --n-days 30 \\
        --workers 4 \\
        --output-dir results/benchmarks/
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(name="run-benchmark", add_completion=False)


@app.command()
def main(
    gik_store: Annotated[str, typer.Option("--gik-store", help="URI of the GIK IceChunk store.")],
    dynamical_store: Annotated[
        str | None,
        typer.Option("--dynamical-store", help="URI of the conventional Zarr store to compare."),
    ] = None,
    domain: Annotated[str, typer.Option(help="Domain label for the output CSV.")] = "east_africa",
    n_days: Annotated[int, typer.Option(help="Number of forecast day-groups to benchmark.")] = 30,
    workers: Annotated[int, typer.Option(help="Dask workers for the full-scan measurement.")] = 4,
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
        typer.echo("No benchmark results produced. Check that the store URI is accessible.")
        raise typer.Exit(1)

    cols = f"{'Approach':<20} {'Store (GB)':>12} {'TTFB (s)':>10} {'Scan (s)':>10} {'Egress $':>10}"
    typer.echo(f"\n{cols}")
    typer.echo("-" * 66)
    for r in results.values():
        typer.echo(
            f"{r.approach:<20} {r.store_size_gb:>12,.0f} {r.time_to_first_byte_s:>10.3f}"
            f" {r.full_scan_elapsed_s:>10.1f} {r.estimated_egress_usd:>10.4f}"
        )

    if "GIK+IceChunk" in results and "dynamical.org" in results:
        gik = results["GIK+IceChunk"]
        dyn = results["dynamical.org"]
        compression = dyn.store_size_gb / gik.store_size_gb if gik.store_size_gb else 0
        typer.echo(f"\nStorage compression ratio: {compression:,.0f}×")

    typer.echo(f"\nResults saved to {output_dir}")


if __name__ == "__main__":
    app()
