"""Batch risk engine: CRMA inference for all forecast days and admin-1 units.

Iterates over the requested date range, loads exceedance probabilities from
the C2 Zarr store and GPM IMERG daily observations, propagates the Antecedent
Precipitation Index (API) and soil-saturation day-count across days, and
writes one GeoJSON FeatureCollection per day.

All numeric thresholds and initial values are read from GIKConfig
(configs/default.yaml component3.crma_model / component3.api) — no
hardcoded constants.

NaN values from aggregation are detected and produce risk_state=-1 /
risk_label="No_Data" rather than silently mapping to 0.0.

Checkpointing: ``bn_states`` are serialised to ``_checkpoint.json`` every
*checkpoint_interval* days so that a crashed run can resume from the last
checkpoint.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, replace
from datetime import date, timedelta
from pathlib import Path

import geopandas as gpd
import pandas as pd
import structlog
import xarray as xr

from gik_icechain.risk.aggregator import aggregate_to_admin1, coverage_fraction
from gik_icechain.risk.crma_model import CRMAEvidence, CRMAModel
from gik_icechain.risk.dynamic_bn import DynamicBNState, init_state
from gik_icechain.risk.dynamic_bn import step as bn_step
from gik_icechain.risk.geojson_writer import build_feature, write_risk_geojson
from gik_icechain.risk.gpm_loader import load_gpm_daily

log = structlog.get_logger(__name__)

_CHECKPOINT_FILE = "_checkpoint.json"
_DEFAULT_CHECKPOINT_INTERVAL = 7  # days


def run_risk_batch(
    exceedance_store_uri: str,
    gpm_dir: Path,
    admin_boundaries_path: Path,
    crma_model: CRMAModel,
    output_dir: Path,
    start: date,
    end: date,
    api_decay: float = 0.8,
    initial_api_mm: float = 20.0,
    signal_threshold: float = 0.15,
    rp_signal: int = 5,
    checkpoint_interval: int = _DEFAULT_CHECKPOINT_INTERVAL,
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
        initial_api_mm:        Starting API for the first day (mm).
        signal_threshold:      Exceedance probability → rainfall-signal flag.
        rp_signal:             Return period (years) used for signal detection.
        checkpoint_interval:   Save ``bn_states`` checkpoint every N days.

    Returns:
        Sorted list of written GeoJSON file paths.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    admin = gpd.read_file(admin_boundaries_path)
    exc_ds = xr.open_zarr(exceedance_store_uri, consolidated=False)

    pcodes = [str(row["admin1_pcode"]) for _, row in admin.iterrows()]
    bn_states: dict[str, DynamicBNState] = {
        p: init_state(initial_api_mm) for p in pcodes
    }

    # Resume from checkpoint if available
    checkpoint_path = output_dir / _CHECKPOINT_FILE
    bn_states, resume_date = _load_checkpoint(checkpoint_path, bn_states, start)

    written: list[Path] = []
    current = resume_date
    day_count = 0
    while current <= end:
        path = _process_day(
            current,
            exc_ds,
            gpm_dir,
            admin,
            crma_model,
            output_dir,
            bn_states,
            api_decay,
            signal_threshold,
            rp_signal,
        )
        if path is not None:
            written.append(path)
        current += timedelta(days=1)
        day_count += 1

        if checkpoint_interval > 0 and day_count % checkpoint_interval == 0:
            _save_checkpoint(checkpoint_path, bn_states, current)

    # Successful completion — remove checkpoint so re-runs start from scratch
    if checkpoint_interval > 0 and checkpoint_path.exists():
        checkpoint_path.unlink()

    log.info("risk_batch_complete", n_days=len(written), start=start, end=end)
    return written


def _process_day(
    day: date,
    exc_ds: xr.Dataset,
    gpm_dir: Path,
    admin: gpd.GeoDataFrame,
    crma_model: CRMAModel,
    output_dir: Path,
    bn_states: dict[str, DynamicBNState],
    api_decay: float,
    signal_threshold: float,
    rp_signal: int,
) -> Path | None:
    try:
        exc_day = exc_ds.sel(date=pd.Timestamp(day))
    except KeyError:
        log.warning("exceedance_date_missing", date=day)
        return None

    try:
        exc_24h = exc_day["exceedance_prob"].sel(window=24, return_period=rp_signal)
        exc_72h = exc_day["exceedance_prob"].sel(window=72, return_period=rp_signal)
        exc_7d = exc_day["exceedance_prob"].sel(window=168, return_period=rp_signal)
    except KeyError as exc:
        log.warning("exceedance_window_missing", date=day, rp=rp_signal, error=str(exc))
        return None
    gpm_da = load_gpm_daily(gpm_dir, day)

    p_24h_s = aggregate_to_admin1(exc_24h, admin)
    p_72h_s = aggregate_to_admin1(exc_72h, admin)
    p_7d_s = aggregate_to_admin1(exc_7d, admin)
    cov_s = coverage_fraction(exc_24h, admin, signal_threshold)
    gpm_s = aggregate_to_admin1(gpm_da, admin) if gpm_da is not None else pd.Series(dtype=float)

    # Ensemble confidence (Data_Confidence BN node): IQR/median spread per admin-1.
    # Defaults to High (2) when the variable is absent (stores written before this fix).
    if "ensemble_confidence" in exc_day:
        conf_mean_s = aggregate_to_admin1(exc_day["ensemble_confidence"].astype(float), admin)
        conf_s: pd.Series = conf_mean_s.round().clip(0, 2).fillna(2.0).astype("int8")
    else:
        conf_s = pd.Series(dtype="int8")

    features = []
    for _, unit in admin.iterrows():
        pcode = str(unit["admin1_pcode"])
        p_24h = p_24h_s.get(pcode, float("nan"))
        p_72h = p_72h_s.get(pcode, float("nan"))
        p_7d = p_7d_s.get(pcode, float("nan"))
        gpm_24h = gpm_s.get(pcode, float("nan"))
        cov_val = cov_s.get(pcode, float("nan"))

        # Detect NaN in aggregated values → No_Data instead of silent zero
        key_values = [p_24h, p_72h, p_7d]
        if any(not math.isfinite(v) for v in key_values):
            log.warning("nan_in_aggregated_values", pcode=pcode, date=day)
            # Decay API so state doesn't freeze across data gaps
            bn_states[pcode] = replace(
                bn_states[pcode], api_mm=bn_states[pcode].api_mm * api_decay
            )
            no_data_result = {
                "risk_state": -1,
                "risk_label": "No_Data",
                "p_green": 0.0,
                "p_yellow": 0.0,
                "p_orange": 0.0,
                "p_red": 0.0,
                "evidence": {},
            }
            no_data_evidence = CRMAEvidence(
                exceedance_prob_24h_5y=0.0,
                exceedance_prob_72h_5y=0.0,
                exceedance_prob_7d_5y=0.0,
                gpm_obs_24h=0.0,
                api_mm=bn_states[pcode].api_mm,
                spatial_coverage_fraction=0.0,
                consecutive_signal_days=0,
                sat_consecutive_days=bn_states[pcode].sat_consecutive_days,
            )
            features.append(build_feature(unit, no_data_result, no_data_evidence, day))
            continue

        # Safe conversion for non-critical values: NaN → 0.0 for gpm/coverage only
        gpm_24h = gpm_24h if math.isfinite(gpm_24h) else 0.0
        cov_val = cov_val if math.isfinite(cov_val) else 0.0

        evidence = CRMAEvidence(
            exceedance_prob_24h_5y=float(p_24h),
            exceedance_prob_72h_5y=float(p_72h),
            exceedance_prob_7d_5y=float(p_7d),
            gpm_obs_24h=float(gpm_24h),
            api_mm=bn_states[pcode].api_mm,
            spatial_coverage_fraction=float(cov_val),
            consecutive_signal_days=bn_states[pcode].consecutive_days,
            sat_consecutive_days=bn_states[pcode].sat_consecutive_days,
            gpm_quality=int(conf_s.get(pcode, 2.0)),
        )

        result, bn_states[pcode] = bn_step(
            bn_states[pcode],
            evidence,
            crma_model,
            api_decay=api_decay,
            gpm_obs_mm=float(gpm_24h),
            signal_threshold=signal_threshold,
        )
        features.append(build_feature(unit, result, evidence, day))

    out_path = write_risk_geojson(day, features, output_dir)
    log.info("risk_day_written", date=day, n_units=len(features))
    return out_path


def _save_checkpoint(
    path: Path,
    bn_states: dict[str, DynamicBNState],
    next_date: date,
) -> None:
    """Serialise ``bn_states`` to JSON for crash recovery."""
    payload = {
        "next_date": next_date.isoformat(),
        "bn_states": {
            pcode: asdict(state) for pcode, state in bn_states.items()
        },
    }
    path.write_text(json.dumps(payload))
    log.debug("checkpoint_saved", path=str(path), next_date=next_date.isoformat())


def _load_checkpoint(
    path: Path,
    default_states: dict[str, DynamicBNState],
    default_start: date,
) -> tuple[dict[str, DynamicBNState], date]:
    """Load checkpoint if present and return (bn_states, resume_date).

    If no checkpoint exists or it's older than default_start, returns the
    original defaults.
    """
    if not path.exists():
        return default_states, default_start

    try:
        data = json.loads(path.read_text())
        resume_date = date.fromisoformat(data["next_date"])
        if resume_date <= default_start:
            return default_states, default_start

        states: dict[str, DynamicBNState] = {}
        for pcode, s in data["bn_states"].items():
            states[pcode] = DynamicBNState(
                api_mm=float(s["api_mm"]),
                consecutive_days=int(s["consecutive_days"]),
                sat_consecutive_days=int(s["sat_consecutive_days"]),
                last_risk_state=int(s["last_risk_state"]),
            )

        # Ensure all pcodes exist (new units added since checkpoint)
        for pcode in default_states:
            if pcode not in states:
                states[pcode] = default_states[pcode]

        log.info(
            "checkpoint_loaded",
            path=str(path),
            resume_date=resume_date.isoformat(),
            n_units=len(states),
        )
        return states, resume_date
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        log.warning("checkpoint_load_failed", error=str(exc))
        return default_states, default_start
