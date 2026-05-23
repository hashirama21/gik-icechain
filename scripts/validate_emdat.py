"""CLI: validate CRMA risk outputs against EM-DAT historical flood events.

Computes precision, recall, F1, AUC-ROC, and exports a per-event hit/miss CSV.

Usage:
    python scripts/validate_emdat.py \\
        --risk-dir results/admin1_risk/ \\
        --emdat-csv data/emdat/east_africa_floods.csv \\
        --output results/validation/emdat_validation.csv
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import pandas as pd
import typer

app = typer.Typer(name="validate-emdat", add_completion=False)


def _load_risk_results(risk_dir: Path) -> pd.DataFrame:
    """Load all per-day GeoJSON files into a flat DataFrame."""
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


@app.command()
def main(
    risk_dir: Annotated[
        Path, typer.Option(help="Directory containing per-day GeoJSON risk files.")
    ] = Path("results/admin1_risk/"),
    emdat_csv: Annotated[
        Path, typer.Option(help="EM-DAT flood CSV (export from emdat.be).")
    ] = Path("data/emdat/east_africa_floods.csv"),
    output: Annotated[
        Path, typer.Option(help="Output CSV for the per-event hit/miss table.")
    ] = Path("results/validation/emdat_validation.csv"),
    risk_threshold: Annotated[
        int, typer.Option(help="Min risk_state for a flood prediction (default 2=Orange).")
    ] = 2,
) -> None:
    """Validate CRMA risk outputs against EM-DAT historical flood events."""
    from gik_icechain.risk.cpt_refinement import load_emdat_east_africa, run_validation

    if not emdat_csv.exists():
        typer.echo(f"EM-DAT CSV not found: {emdat_csv}", err=True)
        raise typer.Exit(1)

    typer.echo(f"Loading EM-DAT records from {emdat_csv} ...")
    emdat_records = load_emdat_east_africa(emdat_csv)
    typer.echo(f"  {len(emdat_records)} flood events loaded.")

    typer.echo(f"Loading CRMA risk results from {risk_dir} ...")
    risk_df = _load_risk_results(risk_dir)
    typer.echo(f"  {len(risk_df)} day × unit records loaded.")

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


if __name__ == "__main__":
    app()
