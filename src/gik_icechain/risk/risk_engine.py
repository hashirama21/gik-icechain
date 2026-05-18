"""Batch risk engine: CRMA inference for all forecast days and admin-1 units.

Iterates over the requested date range, loads exceedance probabilities from
the C2 Zarr store and GPM IMERG daily observations, propagates the Antecedent
Precipitation Index (API) across days with exponential decay, and writes one
GeoJSON FeatureCollection per day.

Expected exceedance store schema:
  variable:    exceedance_prob
  dimensions:  (date, lat, lon, window, return_period)
  window:      accumulation window in hours, e.g. [3, 6, 12, 24, 48, 72, 168]
  return_period: return period in years, e.g. [2, 5, 10, 20, 40, 100]
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import geopandas as gpd
import pandas as pd
import structlog
import xarray as xr

from gik_icechain.risk.aggregator import aggregate_to_admin1, coverage_fraction
from gik_icechain.risk.crma_model import CRMAEvidence, CRMAModel
from gik_icechain.risk.geojson_writer import build_feature
from gik_icechain.risk.gpm_loader import load_gpm_daily

log = structlog.get_logger(__name__)

_RP_SIGNAL        = 5
_SIGNAL_THRESHOLD = 0.15
_INITIAL_API_MM   = 20.0


def run_risk_batch(
    exceedance_store_uri: str,
    gpm_dir: Path,
    admin_boundaries_path: Path,
    crma_model: CRMAModel,
    output_dir: Path,
    start: date,
    end: date,
    api_decay: float = 0.8,
) -> list[Path]:
    """Run CRMA risk inference for all days in [start, end].

    Args:
        exceedance_store_uri:  URI of the Component-2 exceedance Zarr store.
        gpm_dir:               Directory containing GPM IMERG daily files.
        admin_boundaries_path: GeoPackage/Shapefile of East Africa admin-1 units.
        crma_model:            Built CRMAModel instance.
        output_dir:            Directory for per-day GeoJSON output.
        start:                 First forecast date (inclusive).
        end:                   Last forecast date (inclusive).
        api_decay:             Exponential decay factor for API carry-over.

    Returns:
        Sorted list of written GeoJSON file paths.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    admin    = gpd.read_file(admin_boundaries_path)
    exc_ds   = xr.open_zarr(exceedance_store_uri, consolidated=False)

    pcodes              = [str(row["admin1_pcode"]) for _, row in admin.iterrows()]
    api_state:          dict[str, float] = {p: _INITIAL_API_MM for p in pcodes}
    consecutive_days:   dict[str, int]   = {p: 0               for p in pcodes}

    written: list[Path] = []
    current = start
    while current <= end:
        path = _process_day(
            current, exc_ds, gpm_dir, admin, crma_model,
            output_dir, api_state, consecutive_days, api_decay,
        )
        if path is not None:
            written.append(path)
        current += timedelta(days=1)

    log.info("risk_batch_complete", n_days=len(written), start=start, end=end)
    return written


def _process_day(
    day: date,
    exc_ds: xr.Dataset,
    gpm_dir: Path,
    admin: gpd.GeoDataFrame,
    crma_model: CRMAModel,
    output_dir: Path,
    api_state: dict[str, float],
    consecutive_days: dict[str, int],
    api_decay: float,
) -> Path | None:
    try:
        exc_day = exc_ds.sel(date=pd.Timestamp(day))
    except KeyError:
        log.warning("exceedance_date_missing", date=day)
        return None

    exc_24h = exc_day["exceedance_prob"].sel(window=24,  return_period=_RP_SIGNAL)
    exc_72h = exc_day["exceedance_prob"].sel(window=72,  return_period=_RP_SIGNAL)
    exc_7d  = exc_day["exceedance_prob"].sel(window=168, return_period=_RP_SIGNAL)
    gpm_da  = load_gpm_daily(gpm_dir, day)

    features = []
    for _, unit in admin.iterrows():
        pcode = str(unit["admin1_pcode"])
        geom  = unit.geometry

        p_24h    = _scalar_mean(exc_24h, admin.iloc[[admin.index.get_loc(unit.name)]])
        p_72h    = _scalar_mean(exc_72h, admin.iloc[[admin.index.get_loc(unit.name)]])
        p_7d     = _scalar_mean(exc_7d,  admin.iloc[[admin.index.get_loc(unit.name)]])
        cov      = _scalar_coverage(exc_24h, admin.iloc[[admin.index.get_loc(unit.name)]])
        gpm_24h  = _scalar_mean(gpm_da, admin.iloc[[admin.index.get_loc(unit.name)]]) if gpm_da is not None else 0.0
        cur_api  = api_state[pcode]

        evidence = CRMAEvidence(
            exceedance_prob_24h_5y=p_24h,
            exceedance_prob_72h_5y=p_72h,
            exceedance_prob_7d_5y=p_7d,
            gpm_obs_24h=gpm_24h,
            api_mm=cur_api,
            spatial_coverage_fraction=cov,
            consecutive_signal_days=consecutive_days[pcode],
        )
        result = crma_model.infer(evidence)

        api_state[pcode]       = gpm_24h + api_decay * cur_api
        consecutive_days[pcode] = (
            consecutive_days[pcode] + 1 if p_24h >= _SIGNAL_THRESHOLD else 0
        )
        features.append(build_feature(unit, result, evidence, day))

    out_path = output_dir / f"{day.isoformat()}_admin1_risk.geojson"
    out_path.write_text(
        json.dumps({"type": "FeatureCollection", "features": features})
    )
    log.info("risk_day_written", date=day, n_units=len(features))
    return out_path


def _scalar_mean(da: xr.DataArray | None, unit_gdf: gpd.GeoDataFrame) -> float:
    """Spatial mean within the single admin-1 unit in *unit_gdf*."""
    if da is None:
        return 0.0
    series = aggregate_to_admin1(da, unit_gdf, stat="mean")
    val = series.iloc[0] if len(series) > 0 else 0.0
    import math
    return 0.0 if not math.isfinite(val) else float(val)


def _scalar_coverage(da: xr.DataArray, unit_gdf: gpd.GeoDataFrame) -> float:
    """Coverage fraction within the single admin-1 unit in *unit_gdf*."""
    series = coverage_fraction(da, unit_gdf, threshold=_SIGNAL_THRESHOLD)
    val = series.iloc[0] if len(series) > 0 else 0.0
    import math
    return 0.0 if not math.isfinite(val) else float(val)
