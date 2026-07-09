#!/usr/bin/env python3
"""Repair a date-dimension desync in the exceedance Zarr store.

Appends made with the pre-guard writer could extend ``exceedance_prob`` and the
``date`` coordinate while silently leaving the optional variables
(``tail_ratio``, ``median_ratio``, ``ensemble_confidence``) short - after which
``xr.open_zarr`` fails with "conflicting sizes for dimension 'date'"
(observed on s3://gik-icechain/exceedance-zarr: confidence at 7 dates vs
exceedance at 38). The writer now pads on append (_align_append_schema); this
script applies the same convention retroactively to an already-desynced store:

  * every date-indexed variable is resized to the full date-axis length;
  * the extension reads as the missing-data sentinel (NaN for floats, -1 for
    integer flag variables - the engine maps negatives back to its default);
  * existing data is never touched (the extension covers only positions the
    short variable had never written).

Dry-run by default; pass --apply to modify the store.

Usage:
    python scripts/repair_exceedance_store.py                 # dry-run
    python scripts/repair_exceedance_store.py --apply
"""

from __future__ import annotations

import math
import os
from typing import Annotated

import numpy as np
import typer
import zarr

from gik_icechain.exceedance.writer import _fill_value_for

app = typer.Typer(add_completion=False)


def _date_axis(array: zarr.Array) -> int | None:
    """Index of the ``date`` dimension, or None if the array has none."""
    names = array.metadata.dimension_names or ()
    for i, name in enumerate(names):
        if name == "date":
            return i
    return None


@app.command()
def main(
    store_uri: Annotated[
        str, typer.Option(help="Zarr store URI.")
    ] = "s3://gik-icechain/exceedance-zarr",
    endpoint_url: Annotated[
        str | None, typer.Option(help="S3 endpoint (defaults to AWS_ENDPOINT_URL).")
    ] = None,
    apply: Annotated[
        bool, typer.Option("--apply", help="Modify the store (default: dry-run).")
    ] = False,
) -> None:
    endpoint = endpoint_url or os.environ.get("AWS_ENDPOINT_URL")
    is_s3 = store_uri.startswith("s3://")
    storage_options = {"endpoint_url": endpoint} if (endpoint and is_s3) else None
    mode = "r+" if apply else "r"
    root = zarr.open_group(store_uri, mode=mode, storage_options=storage_options)

    n_dates = root["date"].shape[0]
    typer.echo(f"date axis length: {n_dates}  ({'APPLY' if apply else 'dry-run'})")

    repaired = 0
    for name in sorted(root.array_keys()):
        if name == "date":
            continue
        arr = root[name]
        axis = _date_axis(arr)
        if axis is None:
            continue
        short = arr.shape[axis]
        if short == n_dates:
            typer.echo(f"  {name}: OK ({short} dates)")
            continue
        if short > n_dates:
            typer.echo(
                f"ERROR: {name} has MORE dates ({short}) than the date coord "
                f"({n_dates}) - not a padding repair, aborting."
            )
            raise typer.Exit(code=1)
        if axis != 0:
            typer.echo(f"ERROR: {name}: date is axis {axis}, only axis 0 is supported.")
            raise typer.Exit(code=1)

        fill = _fill_value_for(arr.dtype)
        typer.echo(
            f"  {name}: SHORT {short} -> {n_dates} dates "
            f"(pad positions {short}..{n_dates - 1} with {fill})"
        )
        repaired += 1
        if not apply:
            continue

        arr.resize((n_dates, *arr.shape[1:]))
        # Unwritten chunks already read as the array's fill_value; write the
        # sentinel explicitly only when it differs (e.g. int8 stores fill 0).
        native_fill = arr.fill_value
        same = (
            isinstance(fill, float)
            and math.isnan(fill)
            and native_fill is not None
            and isinstance(native_fill, float | np.floating)
            and math.isnan(float(native_fill))
        ) or fill == native_fill
        if not same:
            arr[short:n_dates] = np.full(
                (n_dates - short, *arr.shape[1:]), fill, dtype=arr.dtype
            )

    if apply and repaired:
        zarr.consolidate_metadata(root.store)
        typer.echo("metadata re-consolidated")

    if apply:
        import xarray as xr

        ds = xr.open_zarr(store_uri, consolidated=False, storage_options=storage_options)
        typer.echo(f"verify: open_zarr OK - {len(ds['date'])} dates, vars {list(ds.data_vars)}")
    typer.echo(f"{repaired} variable(s) {'repaired' if apply else 'need repair'}")


if __name__ == "__main__":
    app()
