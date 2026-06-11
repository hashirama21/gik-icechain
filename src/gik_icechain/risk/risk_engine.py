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
import os
from dataclasses import asdict, replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
import structlog
import xarray as xr

from gik_icechain.risk.aggregator import aggregate_to_admin1, coverage_fraction
from gik_icechain.risk.crma_model import CRMAEvidence, CRMAModel, EastAfricaCluster
from gik_icechain.risk.dynamic_bn import DynamicBNState, init_state
from gik_icechain.risk.dynamic_bn import step as bn_step
from gik_icechain.risk.geojson_writer import build_score, write_boundaries, write_risk_scores
from gik_icechain.risk.gpm_loader import load_gpm_daily
from gik_icechain.shared.regions import COUNTRY_CLUSTER

log = structlog.get_logger(__name__)

_CHECKPOINT_FILE = "_checkpoint.json"
_DEFAULT_CHECKPOINT_INTERVAL = 7  # days


def _build_pcode_cluster_map(admin: gpd.GeoDataFrame) -> dict[str, EastAfricaCluster]:
    """Map each admin-1 pcode to its CRMA climate cluster via the shapeGroup ISO3 column."""
    result: dict[str, EastAfricaCluster] = {}
    for _, row in admin.iterrows():
        pcode = str(row["admin1_pcode"])
        iso3 = str(row.get("shapeGroup", ""))
        result[pcode] = EastAfricaCluster(COUNTRY_CLUSTER.get(iso3, "equatorial_east"))
    return result


def run_risk_batch(
    exceedance_store_uri: str,
    gpm_dir: Path,
    admin_boundaries_path: Path,
    crma_models: dict[EastAfricaCluster, CRMAModel],
    output_dir: Path,
    start: date,
    end: date,
    api_decay: float = 0.8,
    initial_api_mm: float = 20.0,
    signal_threshold: float = 0.15,
    rp_signal: int = 5,
    rp_signal_options: list[int] | None = None,
    hazard_stat: str = "max",
    min_coverage: float = 0.5,
    checkpoint_interval: int = _DEFAULT_CHECKPOINT_INTERVAL,
    endpoint_url: str | None = None,
) -> list[Path]:
    """Run CRMA risk inference for all days in [start, end].

    Args:
        exceedance_store_uri:  URI of the Component-2 exceedance Zarr store.
        gpm_dir:               Directory containing GPM IMERG daily files.
        admin_boundaries_path: GeoPackage/Shapefile of East Africa admin-1 units.
        crma_models:           One built CRMAModel per EastAfricaCluster.
        output_dir:            Directory for per-day GeoJSON output.
        start:                 First forecast date (inclusive).
        end:                   Last forecast date (inclusive).
        api_decay:             Exponential decay factor for API carry-over.
        initial_api_mm:        Starting API for the first day (mm).
        signal_threshold:      Exceedance probability → rainfall-signal flag.
        rp_signal:             Return period (years) used for signal detection.
        hazard_stat:           Statistic used to aggregate the gridded exceedance
                               hazard to each admin-1 unit ("max", "mean", or a
                               percentile "pNN"). "max"/high-percentile avoids
                               diluting a localized flood peak over the polygon.
        min_coverage:          Minimum fraction of an admin-1 unit that must be
                               covered by valid grid cells; below it → No_Data.
        checkpoint_interval:   Save ``bn_states`` checkpoint every N days.

    Returns:
        Sorted list of written GeoJSON file paths.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    admin = gpd.read_file(admin_boundaries_path)
    write_boundaries(admin, output_dir)

    storage_options = {"endpoint_url": endpoint_url} if endpoint_url else None
    exc_ds = xr.open_zarr(exceedance_store_uri, consolidated=False, storage_options=storage_options)

    pcode_cluster = _build_pcode_cluster_map(admin)
    # Pre-build once — avoids iterrows() on each of the N forecast days.
    unit_by_pcode: dict[str, Any] = {str(row["admin1_pcode"]): row for _, row in admin.iterrows()}
    bn_states: dict[str, DynamicBNState] = {p: init_state(initial_api_mm) for p in pcode_cluster}

    # Resume from checkpoint if available
    checkpoint_path = output_dir / _CHECKPOINT_FILE
    bn_states, resume_date = _load_checkpoint(checkpoint_path, bn_states, start)

    # Risk is produced for each of these RPs (primary first); the dashboard
    # toggles between them. Always include the primary rp_signal.
    rp_options = list(dict.fromkeys([rp_signal, *(rp_signal_options or [])]))

    written: list[Path] = []
    current = resume_date
    day_count = 0
    while current <= end:
        path = _process_day(
            current,
            exc_ds,
            gpm_dir,
            admin,
            crma_models,
            pcode_cluster,
            unit_by_pcode,
            output_dir,
            bn_states,
            api_decay,
            signal_threshold,
            rp_signal,
            hazard_stat,
            min_coverage,
            rp_options,
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


_NO_DATA_RESULT = {
    "risk_state": -1, "risk_label": "No_Data",
    "p_green": 0.0, "p_yellow": 0.0, "p_orange": 0.0, "p_red": 0.0,
}


def _process_day(
    day: date,
    exc_ds: xr.Dataset,
    gpm_dir: Path,
    admin: gpd.GeoDataFrame,
    crma_models: dict[EastAfricaCluster, CRMAModel],
    pcode_cluster: dict[str, EastAfricaCluster],
    unit_by_pcode: dict[str, Any],
    output_dir: Path,
    bn_states: dict[str, DynamicBNState],
    api_decay: float,
    signal_threshold: float,
    rp_signal: int,
    hazard_stat: str = "max",
    min_coverage: float = 0.5,
    rp_options: list[int] | None = None,
) -> Path | None:
    rp_options = rp_options or [rp_signal]
    try:
        exc_day = exc_ds.sel(date=pd.Timestamp(day)).load()
    except KeyError:
        log.warning("exceedance_date_missing", date=day)
        return None

    # Aggregate hazard exceedance per RP (24h/72h/7d) so risk_state can be
    # produced for each return period (dashboard toggles 2yr↔5yr).
    agg: dict[int, dict[str, pd.Series]] = {}
    for rp in rp_options:
        try:
            e = {w: exc_day["exceedance_prob"].sel(window=wh, return_period=rp)
                 for w, wh in (("24h", 24), ("72h", 72), ("7d", 168))}
        except KeyError as exc:
            log.warning("exceedance_window_missing", date=day, rp=rp, error=str(exc))
            continue
        agg[rp] = {w: aggregate_to_admin1(da, admin, stat=hazard_stat, min_coverage=min_coverage)
                   for w, da in e.items()}
    if rp_signal not in agg:
        log.warning("primary_rp_missing", date=day, rp=rp_signal)
        return None

    cov_s = coverage_fraction(
        exc_day["exceedance_prob"].sel(window=24, return_period=rp_signal), admin, signal_threshold)
    gpm_da = load_gpm_daily(gpm_dir, day)
    gpm_s = aggregate_to_admin1(gpm_da, admin) if gpm_da is not None else pd.Series(dtype=float)

    if "ensemble_confidence" in exc_day:
        conf_mean_s = aggregate_to_admin1(exc_day["ensemble_confidence"].astype(float), admin)
        conf_s: pd.Series = conf_mean_s.round().clip(0, 2).fillna(2.0).astype("int8")
    else:
        conf_s = pd.Series(dtype="int8")

    def _exc(rp: int) -> tuple[float, float, float]:
        return tuple(float(agg[rp][w].get(pcode, float("nan"))) for w in ("24h", "72h", "7d"))

    def _evidence(exc: tuple[float, float, float], state: DynamicBNState,
                  gpm24: float, cov: float, quality: int) -> Any:
        return CRMAEvidence(
            exceedance_prob_24h_5y=exc[0], exceedance_prob_72h_5y=exc[1],
            exceedance_prob_7d_5y=exc[2], gpm_obs_24h=gpm24, api_mm=state.api_mm,
            spatial_coverage_fraction=cov, consecutive_signal_days=state.consecutive_days,
            sat_consecutive_days=state.sat_consecutive_days, gpm_quality=quality,
        )

    def _slim(result: dict) -> dict:
        return {k: result[k] for k in
                ("risk_state", "risk_label", "p_green", "p_yellow", "p_orange", "p_red")}

    scores: dict[str, dict] = {}
    for pcode, cluster in pcode_cluster.items():
        unit = unit_by_pcode[pcode]
        state = bn_states[pcode]
        exc_primary = _exc(rp_signal)

        if any(not math.isfinite(v) for v in exc_primary):
            bn_states[pcode] = replace(state, api_mm=state.api_mm * api_decay)
            nd_ev = _evidence((0.0, 0.0, 0.0), bn_states[pcode], 0.0, 0.0, 2)
            _, scores[pcode] = build_score(unit, _NO_DATA_RESULT, nd_ev,
                                           risk_by_rp={str(rp): _NO_DATA_RESULT for rp in agg})
            continue

        gpm_24h = gpm_s.get(pcode, float("nan"))
        gpm_24h = float(gpm_24h) if math.isfinite(gpm_24h) else 0.0
        cov_val = cov_s.get(pcode, float("nan"))
        cov_val = float(cov_val) if math.isfinite(cov_val) else 0.0
        quality = int(conf_s.get(pcode, 2.0))

        # Non-primary RPs: infer with the current (pre-advance) state.
        risk_by_rp: dict[str, dict] = {}
        for rp in agg:
            if rp == rp_signal:
                continue
            exc = _exc(rp)
            if any(not math.isfinite(v) for v in exc):
                risk_by_rp[str(rp)] = _NO_DATA_RESULT
            else:
                ev_rp = _evidence(exc, state, gpm_24h, cov_val, quality)
                risk_by_rp[str(rp)] = _slim(crma_models[cluster].infer(ev_rp))

        # Primary RP: bn_step advances the persistent state.
        ev = _evidence(exc_primary, state, gpm_24h, cov_val, quality)
        result, bn_states[pcode] = bn_step(
            state, ev, crma_models[cluster], api_decay=api_decay,
            gpm_obs_mm=gpm_24h, signal_threshold=signal_threshold)
        risk_by_rp[str(rp_signal)] = _slim(result)

        _, scores[pcode] = build_score(unit, result, ev, risk_by_rp=risk_by_rp)

    out_path = write_risk_scores(day, scores, output_dir)
    log.info("risk_day_written", date=day, n_units=len(scores), rps=list(agg))
    return out_path


def _save_checkpoint(
    path: Path,
    bn_states: dict[str, DynamicBNState],
    next_date: date,
) -> None:
    """Serialise ``bn_states`` to JSON for crash recovery."""
    payload = {
        "next_date": next_date.isoformat(),
        "bn_states": {pcode: asdict(state) for pcode, state in bn_states.items()},
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload))
    os.replace(tmp, path)  # atomic on POSIX and Windows NTFS
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
