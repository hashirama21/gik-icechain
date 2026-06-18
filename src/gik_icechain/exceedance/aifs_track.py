"""AIFS vs IFS ensemble comparison — Innovation 4.

Computes delta exceedance probabilities, ensemble spread ratios, and
seasonal stratifications between the IFS and AIFS parallel tracks.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import structlog
import xarray as xr

from gik_icechain.exceedance.thresholds import (
    ClimateMode,
    Season,
    get_season,
)

log = structlog.get_logger(__name__)


def compute_aifs_ifs_delta(
    ifs_exceedance_uri: str,
    aifs_exceedance_uri: str,
    output_dir: str,
) -> xr.Dataset:
    """Compute per-cell delta between AIFS and IFS exceedance probabilities.

    Produces:
        - ``delta_prob``: AIFS - IFS (positive = AIFS predicts higher risk)
        - ``abs_delta_prob``: absolute delta
        - ``ifs_higher_fraction``: fraction of cells where IFS > AIFS

    Args:
        ifs_exceedance_uri: Zarr store URI for IFS exceedance.
        aifs_exceedance_uri: Zarr store URI for AIFS exceedance.
        output_dir: Directory to write the delta Zarr store.

    Returns:
        :class:`xr.Dataset` with delta fields.
    """
    ifs_ds = xr.open_zarr(ifs_exceedance_uri, consolidated=False)
    aifs_ds = xr.open_zarr(aifs_exceedance_uri, consolidated=False)

    # Align on common dates / coordinates
    ifs_prob = ifs_ds["exceedance_prob"]
    aifs_prob = aifs_ds["exceedance_prob"]

    ifs_prob, aifs_prob = xr.align(ifs_prob, aifs_prob, join="inner")

    delta = aifs_prob - ifs_prob
    abs_delta = np.abs(delta)

    # Fraction of grid cells where IFS predicts higher probability
    ifs_higher = (delta < 0).astype(np.float32)
    spatial_dims = [d for d in delta.dims if d in ("latitude", "longitude")]
    ifs_higher_frac = ifs_higher.mean(dim=spatial_dims) if spatial_dims else ifs_higher.mean()

    result = xr.Dataset(
        {
            "delta_prob": delta,
            "abs_delta_prob": abs_delta,
            "ifs_higher_fraction": ifs_higher_frac,
        },
        attrs={
            "description": "AIFS minus IFS exceedance probability delta",
            "convention": "positive = AIFS predicts higher exceedance",
        },
    )

    out_path = str(Path(output_dir) / "aifs_ifs_delta.zarr")
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    result.to_zarr(out_path, mode="w")
    log.info("aifs_ifs_delta_written", output=out_path, n_dates=delta.sizes.get("date", 0))

    return result


def compare_ensemble_spreads(
    ifs_store_uri: str,
    aifs_store_uri: str,
    forecast_date: Any | None = None,
    window_h: int = 24,
    region: str | None = None,
    endpoint_url: str | None = None,
) -> xr.Dataset:
    """Compare inter-member spread (IQR) between IFS and AIFS ensembles.

    Opens raw ensemble stores (not exceedance) and computes the IQR across
    the member dimension for the given accumulation window.

    Args:
        ifs_store_uri: IceChunk store URI for IFS ensemble data.
        aifs_store_uri: IceChunk store URI for AIFS ensemble data.
        forecast_date: Optional date (or ISO string) to filter to a single
            forecast snapshot; ``None`` = use latest snapshot.
        window_h: Accumulation window in hours for spread comparison.
        region: AWS region for the IceChunk S3 stores.
        endpoint_url: Custom S3 endpoint (e.g. MinIO).

    Returns:
        :class:`xr.Dataset` with ``ifs_iqr``, ``aifs_iqr``, ``spread_ratio``.
    """
    from datetime import date as date_type

    from gik_icechain.conversion.icechunk_writer import IceChainStore

    def _open_store(uri: str) -> xr.Dataset:
        store = IceChainStore(
            uri, region=region, endpoint_url=endpoint_url,
        )
        store.create_or_open()
        if forecast_date is not None:
            d = (
                forecast_date
                if isinstance(forecast_date, date_type)
                else date_type.fromisoformat(str(forecast_date))
            )
            return store.checkout_as_of(d)
        return store.open_latest()

    ifs_ds = _open_store(ifs_store_uri)
    aifs_ds = _open_store(aifs_store_uri)

    member_dim = "member" if "member" in ifs_ds.dims else "number"

    # Select precipitation variable
    precip_var = "tp"
    if precip_var not in ifs_ds.data_vars:
        available = list(ifs_ds.data_vars)
        if not available:
            raise ValueError(f"IFS store has no data variables: {ifs_store_uri}")
        precip_var = str(available[0])
        log.warning(
            "spread_precip_fallback",
            expected="tp", using=precip_var, store="ifs",
        )

    ifs_da = ifs_ds[precip_var]
    if precip_var in aifs_ds.data_vars:
        aifs_da = aifs_ds[precip_var]
    else:
        aifs_available = list(aifs_ds.data_vars)
        if not aifs_available:
            raise ValueError(
                f"AIFS store has no data variables: {aifs_store_uri}"
            )
        aifs_var = aifs_available[0]
        log.warning(
            "spread_precip_fallback",
            expected=precip_var, using=aifs_var, store="aifs",
        )
        aifs_da = aifs_ds[aifs_var]

    # Compute IQR across members
    ifs_q75 = ifs_da.quantile(0.75, dim=member_dim)
    ifs_q25 = ifs_da.quantile(0.25, dim=member_dim)
    ifs_iqr = ifs_q75 - ifs_q25

    aifs_member_dim = "member" if "member" in aifs_ds.dims else "number"
    aifs_q75 = aifs_da.quantile(0.75, dim=aifs_member_dim)
    aifs_q25 = aifs_da.quantile(0.25, dim=aifs_member_dim)
    aifs_iqr = aifs_q75 - aifs_q25

    # Align grids
    ifs_iqr, aifs_iqr = xr.align(ifs_iqr, aifs_iqr, join="inner")

    # Spread ratio: >1 means AIFS is more spread than IFS
    spread_ratio = aifs_iqr / ifs_iqr.where(ifs_iqr > 0, other=np.nan)

    result = xr.Dataset(
        {
            "ifs_iqr": ifs_iqr.drop_vars("quantile", errors="ignore"),
            "aifs_iqr": aifs_iqr.drop_vars("quantile", errors="ignore"),
            "spread_ratio": spread_ratio.drop_vars("quantile", errors="ignore"),
        },
        attrs={
            "description": "Ensemble spread comparison (IQR)",
            "window_h": window_h,
            "spread_ratio_interpretation": ">1 = AIFS more spread than IFS",
        },
    )

    log.info("ensemble_spread_comparison_done", window_h=window_h, dims=dict(result.sizes))
    return result


def seasonal_comparison(
    ifs_exceedance_uri: str,
    aifs_exceedance_uri: str,
    enso_iod_path: str | None = None,
) -> dict[str, xr.Dataset]:
    """Stratify AIFS-vs-IFS delta by season and optionally ENSO/IOD phase.

    Produces one :class:`xr.Dataset` per season (MAM, OND, JJAS, DJF),
    each containing the mean delta and cell-count for that stratum.

    Args:
        ifs_exceedance_uri: Zarr store URI for IFS exceedance.
        aifs_exceedance_uri: Zarr store URI for AIFS exceedance.
        enso_iod_path: CSV with columns ``date, nino34, dmi`` for climate
            mode stratification.  ``None`` = season-only stratification.

    Returns:
        ``{season_key: xr.Dataset}`` mapping.
    """
    import pandas as pd

    from gik_icechain.exceedance.thresholds import (
        ENSOPhase,
        IODPhase,
        classify_enso,
        classify_iod,
    )

    ifs_ds = xr.open_zarr(ifs_exceedance_uri, consolidated=False)
    aifs_ds = xr.open_zarr(aifs_exceedance_uri, consolidated=False)

    ifs_prob, aifs_prob = xr.align(
        ifs_ds["exceedance_prob"], aifs_ds["exceedance_prob"], join="inner",
    )
    delta = aifs_prob - ifs_prob

    if "date" not in delta.dims:
        log.warning("seasonal_comparison_no_date_dim")
        return {}

    dates = pd.to_datetime(delta["date"].values)

    # Load ENSO/IOD index if available
    enso_iod: pd.DataFrame | None = None
    if enso_iod_path and Path(enso_iod_path).exists():
        enso_iod = pd.read_csv(enso_iod_path, parse_dates=["date"]).set_index("date")

    results: dict[str, xr.Dataset] = {}

    for season in Season:
        # Identify dates belonging to this season
        season_mask = np.array([get_season(d.month) == season for d in dates])
        if not season_mask.any():
            continue

        season_delta = delta.isel(date=season_mask)
        season_dates = dates[season_mask]

        mean_delta = season_delta.mean(dim="date")
        n_dates = int(season_mask.sum())

        ds = xr.Dataset(
            {
                "mean_delta_prob": mean_delta,
            },
            attrs={
                "season": season.value,
                "n_dates": n_dates,
            },
        )

        # Sub-stratify by ClimateMode if ENSO/IOD data is available
        if enso_iod is not None:
            climate_deltas: dict[str, list[xr.DataArray]] = {}
            for d_idx, d in enumerate(season_dates):
                ts = pd.Timestamp(d)
                try:
                    row = enso_iod.loc[ts]
                    enso = classify_enso(float(row["nino34"]))  # type: ignore[arg-type]
                    iod = classify_iod(float(row["dmi"]))  # type: ignore[arg-type]
                except (KeyError, TypeError):
                    enso = ENSOPhase.NEUTRAL
                    iod = IODPhase.NEUTRAL

                mode = ClimateMode(season, enso, iod)
                key = mode.key
                if key not in climate_deltas:
                    climate_deltas[key] = []
                climate_deltas[key].append(season_delta.isel(date=d_idx))

            for mode_key, da_list in climate_deltas.items():
                if da_list:
                    stacked = xr.concat(da_list, dim="date")
                    ds[f"mean_delta_{mode_key}"] = stacked.mean(dim="date")
                    ds.attrs[f"n_dates_{mode_key}"] = len(da_list)

        results[season.value] = ds
        log.info(
            "seasonal_comparison_stratum",
            season=season.value,
            n_dates=n_dates,
        )

    return results
