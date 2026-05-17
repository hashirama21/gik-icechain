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
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
import regionmask
import structlog
import xarray as xr

from gik_icechain.risk.crma_model import CRMAEvidence, CRMAModel

log = structlog.get_logger(__name__)

_RP_SIGNAL = 5
_SIGNAL_THRESHOLD = 0.15   # exceedance fraction → "signal present" for this cell
_INITIAL_API_MM = 20.0


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

    admin = gpd.read_file(admin_boundaries_path)
    exc_ds = xr.open_zarr(exceedance_store_uri, consolidated=False)

    pcodes = [str(row["admin1_pcode"]) for _, row in admin.iterrows()]
    api_state: dict[str, float] = {p: _INITIAL_API_MM for p in pcodes}
    consecutive_days: dict[str, int] = {p: 0 for p in pcodes}

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
    gpm_da = _load_gpm_day(gpm_dir, day)

    features = []
    for _, unit in admin.iterrows():
        pcode = str(unit["admin1_pcode"])
        geom = unit.geometry

        p_24h = _spatial_mean(exc_24h, geom)
        p_72h = _spatial_mean(exc_72h, geom)
        p_7d  = _spatial_mean(exc_7d,  geom)
        coverage = _exceedance_coverage(exc_24h, geom, _SIGNAL_THRESHOLD)
        gpm_24h = _spatial_mean(gpm_da, geom) if gpm_da is not None else 0.0
        current_api = api_state[pcode]

        evidence = CRMAEvidence(
            exceedance_prob_24h_5y=p_24h,
            exceedance_prob_72h_5y=p_72h,
            exceedance_prob_7d_5y=p_7d,
            gpm_obs_24h=gpm_24h,
            api_mm=current_api,
            spatial_coverage_fraction=coverage,
            consecutive_signal_days=consecutive_days[pcode],
        )
        result = crma_model.infer(evidence)

        api_state[pcode] = gpm_24h + api_decay * current_api
        consecutive_days[pcode] = (
            consecutive_days[pcode] + 1 if p_24h >= _SIGNAL_THRESHOLD else 0
        )

        features.append(_build_feature(unit, result, evidence, day))

    out_path = output_dir / f"{day.isoformat()}_admin1_risk.geojson"
    out_path.write_text(
        json.dumps({"type": "FeatureCollection", "features": features})
    )
    log.info("risk_day_written", date=day, n_units=len(features))
    return out_path


def _spatial_mean(da: xr.DataArray, geom: Any) -> float:
    """Spatial mean of a (lat, lon) DataArray within a geometry; 0.0 on failure."""
    try:
        mask = regionmask.Regions([geom]).mask(da, lon_name="lon", lat_name="lat")
        result = float(da.where(mask == 0).mean(skipna=True))
        return result if np.isfinite(result) else 0.0
    except Exception:
        return 0.0


def _exceedance_coverage(
    da: xr.DataArray,
    geom: Any,
    threshold: float,
) -> float:
    """Fraction of grid cells within a geometry where exceedance > threshold."""
    try:
        mask = regionmask.Regions([geom]).mask(da, lon_name="lon", lat_name="lat")
        inside = da.where(mask == 0)
        total = int((mask == 0).sum())
        if total == 0:
            return 0.0
        return int((inside > threshold).sum(skipna=True)) / total
    except Exception:
        return 0.0


def _load_gpm_day(gpm_dir: Path, day: date) -> xr.DataArray | None:
    """Load GPM IMERG v7 daily precipitation for a given date.

    Tries the standard HDF5 and nc4 file naming conventions; falls back to
    wildcard glob. Returns precipitationCal in mm/day, or None if not found.
    """
    date_str = day.strftime("%Y%m%d")
    patterns = (
        f"3B-DAY.MS.MRG.3IMERG.{date_str}-S000000-E235959.1440.V07B.HDF5",
        f"3B-DAY.MS.MRG.3IMERG.{date_str}.V07B.nc4",
        f"*{date_str}*.nc4",
        f"*{date_str}*.HDF5",
    )
    for pattern in patterns:
        matches = list(gpm_dir.glob(f"**/{pattern}"))
        if not matches:
            continue
        for engine in ("netcdf4", "h5netcdf"):
            try:
                ds = xr.open_dataset(matches[0], engine=engine)
                for var in ("precipitationCal", "precipitation", "HQprecipitation"):
                    if var in ds:
                        return ds[var].squeeze()
            except Exception:
                continue
    log.debug("gpm_file_not_found", date=day)
    return None


def _build_feature(
    unit: pd.Series,
    result: dict[str, Any],
    evidence: CRMAEvidence,
    day: date,
) -> dict[str, Any]:
    return {
        "type": "Feature",
        "geometry": unit.geometry.__geo_interface__,
        "properties": {
            "admin1_pcode":      str(unit.get("admin1_pcode", "")),
            "admin1_name":       str(unit.get("admin1_name", "")),
            "country":           str(unit.get("adm0_name", "")),
            "date":              day.isoformat(),
            "risk_state":        result["risk_state"],
            "risk_label":        result["risk_label"],
            "p_green":           result["p_green"],
            "p_yellow":          result["p_yellow"],
            "p_orange":          result["p_orange"],
            "p_red":             result["p_red"],
            "exceedance_24h_5y": evidence.exceedance_prob_24h_5y,
            "exceedance_72h_5y": evidence.exceedance_prob_72h_5y,
            "api_mm":            evidence.api_mm,
            "spatial_coverage":  evidence.spatial_coverage_fraction,
        },
    }
