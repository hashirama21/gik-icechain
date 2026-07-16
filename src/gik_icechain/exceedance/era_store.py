"""Era-group layout support for C2.

The published E4DRR full-archive store (source.coop) exposes one group per
IFS schema era (``0p4/00z``, ``49r1/00z``, ``50r1/00z``), each with a
``time`` dimension spanning all forecast dates of that era and chunk coords
``(time, number, step)``. This module resolves a forecast date to its era
group, lists available dates, and loads one day as the same in-memory
``xr.Dataset(member, step, latitude, longitude)`` the per-date path produces.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any

import numpy as np
import structlog

if TYPE_CHECKING:
    import xarray as xr

log = structlog.get_logger(__name__)

EraSpec = tuple[str, date, date | None]

_gribberish_registered = False


def register_gribberish_codec() -> None:
    """Register the gribberish Zarr v3 codec (idempotent).

    The published store's chunks decode through the ``gribberish`` codec;
    importing ``gribberish.zarr`` registers it. Missing gribberish is only a
    warning: stores whose arrays use standard codecs still open fine, and
    zarr raises on the codec name if a gribberish array is actually read.
    """
    global _gribberish_registered
    if _gribberish_registered:
        return
    try:
        import gribberish.zarr  # noqa: F401
    except ImportError:
        log.warning(
            "gribberish_unavailable",
            hint="pip install 'gik-icechain[published]' to decode the published store",
        )
        return
    _gribberish_registered = True


def resolve_era_group(day: date, eras: list[EraSpec]) -> str | None:
    """Return the era group covering *day*, or None if outside all eras."""
    for group, start, end in eras:
        if day >= start and (end is None or day <= end):
            return group
    return None


def open_era_dataset(store: Any, group: str) -> xr.Dataset:
    """Open one era group of the published store as an xr.Dataset."""
    import xarray as xr

    register_gribberish_codec()
    return xr.open_zarr(store, group=group, consolidated=False, zarr_format=3)


def list_era_dates(store: Any, eras: list[EraSpec]) -> dict[str, str]:
    """Map each available forecast date (ISO string) to its era group.

    Dates present in a group's ``time`` coordinate but outside the era's
    configured [start, end] range are ignored, so config stays the source
    of truth for era boundaries.
    """
    dates: dict[str, str] = {}
    for group, start, end in eras:
        try:
            ds = open_era_dataset(store, group)
        except Exception as exc:
            log.warning("era_group_open_failed", group=group, error=str(exc)[:120])
            continue
        days = ds["time"].values.astype("datetime64[D]")
        for d in days:
            day = d.astype(date)
            if day < start or (end is not None and day > end):
                continue
            dates[day.isoformat()] = group
    log.info("era_dates_listed", n_dates=len(dates), n_groups=len(eras))
    return dict(sorted(dates.items()))


def load_day_era_fallback(
    store: Any,
    group: str,
    date_str: str,
    variables: list[str],
    aliases: dict[str, str] | None = None,
) -> xr.Dataset:
    """Load one forecast day from an era group via the Zarr codec path.

    Selects the requested variables (translating canonical pipeline names
    through *aliases*), slices the day's ``time`` index, and normalises the
    result to the per-date layout: canonical variable names, ``member``
    dimension, integer-hour ``step`` coordinate. Data stays lazy; values
    decode through the gribberish codec on access.

    Args:
        store:     Zarr-compatible store (e.g. an IceChunk session store).
        group:     Era group name (e.g. ``"0p4/00z"``).
        date_str:  Forecast date, ISO format.
        variables: Canonical variable names (e.g. ``["tp"]``).
        aliases:   Canonical name -> store name (e.g. ``{"2t": "t2m"}``).

    Raises:
        ValueError: If the date is not in the group or no variable matches.
    """
    aliases = aliases or {}
    ds = open_era_dataset(store, group)

    times = ds["time"].values.astype("datetime64[D]")
    idx = np.nonzero(times == np.datetime64(date_str, "D"))[0]
    if idx.size == 0:
        raise ValueError(f"date {date_str} not found in era group {group!r}")

    store_names = {v: aliases.get(v, v) for v in variables}
    available = {v: sv for v, sv in store_names.items() if sv in ds.data_vars}
    if not available:
        raise ValueError(f"none of {list(store_names.values())} found in group {group!r}")

    day_ds = ds[list(available.values())].isel(time=int(idx[0]))
    day_ds = day_ds.drop_vars("time", errors="ignore")

    rename = {sv: v for v, sv in available.items() if sv != v}
    if "number" in day_ds.dims:
        rename["number"] = "member"
    if rename:
        day_ds = day_ds.rename(rename)

    if np.issubdtype(day_ds["step"].dtype, np.timedelta64):
        step_h = (day_ds["step"].values / np.timedelta64(1, "h")).astype("int32")
        day_ds = day_ds.assign_coords(step=step_h)

    if "latitude" in day_ds.sizes:
        from gik_icechain.shared.grid import grid_deg

        day_ds.attrs["source_grid_deg"] = grid_deg(day_ds.sizes["latitude"])

    log.info(
        "era_day_loaded",
        date=date_str,
        group=group,
        variables=list(available),
        n_members=day_ds.sizes.get("member"),
        n_steps=day_ds.sizes.get("step"),
    )
    return day_ds
