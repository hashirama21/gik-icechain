"""CLI: back-fill the GIK archive gap (May 2023 – Feb 2024) into the IceChunk store.

The ECMWF ENFO archive has ~463 missing days between 2023-05-01 and 2024-03-01.
This script runs C1 convert in daily batches, resuming from the last committed
date so it is safe to interrupt and restart.

Usage:
    # Dry-run: print dates that would be ingested
    python scripts/run_gap_fill.py --dry-run

    # Full gap-fill against MinIO
    AWS_ENDPOINT_URL=http://20.116.218.195:9000 \\
    AWS_ACCESS_KEY_ID=minioadmin \\
    AWS_SECRET_ACCESS_KEY=minioadmin \\
    python scripts/run_gap_fill.py \\
        --config configs/default.yaml \\
        --start 2023-05-01 \\
        --end 2024-02-29
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(name="run-gap-fill", add_completion=False)

_GAP_START = date(2023, 5, 1)
_GAP_END = date(2024, 2, 29)


def _already_committed(store_uri: str, config_path: Path) -> set[str]:
    """Return the set of forecast dates already committed to the IceChunk store."""
    from gik_icechain.conversion.icechunk_writer import IceChainStore
    from gik_icechain.shared.config import load_config

    cfg = load_config(config_path if config_path.exists() else None)
    uri = store_uri or cfg.outputs.icechunk_store_uri
    store = IceChainStore(uri)
    try:
        store.create_or_open()
        return {s["forecast_date"] for s in store.list_snapshots() if s["forecast_date"]}
    except Exception:
        return set()


@app.command()
def main(
    start: Annotated[
        str, typer.Option("--start", help="First date to fill (YYYY-MM-DD).")
    ] = _GAP_START.isoformat(),
    end: Annotated[
        str, typer.Option("--end", help="Last date to fill (YYYY-MM-DD).")
    ] = _GAP_END.isoformat(),
    config: Annotated[
        Path, typer.Option(help="Path to YAML config file.")
    ] = Path("configs/default.yaml"),
    store: Annotated[
        str | None,
        typer.Option("--store", help="Override IceChunk store URI from config."),
    ] = None,
    batch_size: Annotated[
        int, typer.Option(help="Days per batch (resume-safe; smaller = faster restarts).")
    ] = 7,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Print dates to process without ingesting.")
    ] = False,
) -> None:
    """Back-fill the GIK archive gap for a date range into the IceChunk store."""
    from gik_icechain.cli import _bootstrap, _run_convert

    s = date.fromisoformat(start)
    e = date.fromisoformat(end)

    if s > e:
        typer.echo("--start must be before --end", err=True)
        raise typer.Exit(1)

    committed = _already_committed(store or "", config)
    all_dates = []
    current = s
    while current <= e:
        if current.isoformat() not in committed:
            all_dates.append(current)
        current += timedelta(days=1)

    if not all_dates:
        typer.echo(f"No missing dates in [{s}, {e}]. Store is up to date.")
        return

    typer.echo(f"Missing dates: {len(all_dates)} (of {(e - s).days + 1} total)")
    typer.echo(f"Already committed: {len(committed)} dates")

    if dry_run:
        for d in all_dates[:20]:
            typer.echo(f"  {d.isoformat()}")
        if len(all_dates) > 20:
            typer.echo(f"  ... and {len(all_dates) - 20} more")
        return

    cfg = _bootstrap(config)
    if store:
        cfg.outputs.icechunk_store_uri = store

    processed = 0
    batch_start_idx = 0
    while batch_start_idx < len(all_dates):
        batch = all_dates[batch_start_idx: batch_start_idx + batch_size]
        batch_s, batch_e = batch[0], batch[-1]
        typer.echo(f"Ingesting {batch_s} → {batch_e} ({len(batch)} days) ...")
        try:
            _run_convert(cfg, batch_s, batch_e)
            processed += len(batch)
        except Exception as exc:
            typer.echo(f"Batch failed ({batch_s} → {batch_e}): {exc}", err=True)
            typer.echo("Resuming from next batch ...")
        batch_start_idx += batch_size

    typer.echo(f"\nGap-fill complete. Processed {processed} / {len(all_dates)} dates.")


if __name__ == "__main__":
    app()
