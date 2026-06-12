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

Per-return-period inference: each RP in ``rp_signal_options`` carries its own
DynamicBNState and its own Forecast_Hazard calibration
(``hazard_thresholds_by_rp``). Top-level output fields mirror the primary
``rp_signal``.

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

    rp_options = list(dict.fromkeys([rp_signal, *(rp_signal_options or [])]))

    # One state per (pcode, rp) so signal streaks don't leak across RPs.
    bn_states: dict[str, dict[int, DynamicBNState]] = {
        p: {rp: init_state(initial_api_mm) for rp in rp_options} for p in pcode_cluster
    }

    # Resume from checkpoint if available
    checkpoint_path = output_dir / _CHECKPOINT_FILE
    bn_states, resume_date = _load_checkpoint(checkpoint_path, bn_states, start, rp_options)

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
    bn_states: dict[str, dict[int, DynamicBNState]],
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

    # Per-RP admin-1 aggregation: hazard (24h/72h/7d) + signal coverage.
    agg: dict[int, dict[str, pd.Series]] = {}
    for rp in rp_options:
        try:
            grids = {w: exc_day["exceedance_prob"].sel(window=wh, return_period=rp)
                     for w, wh in (("24h", 24), ("72h", 72), ("7d", 168))}
        except KeyError as exc:
            log.warning("exceedance_window_missing", date=day, rp=rp, error=str(exc))
            continue
        agg[rp] = {w: aggregate_to_admin1(da, admin, stat=hazard_stat, min_coverage=min_coverage)
                   for w, da in grids.items()}
        agg[rp]["cov"] = coverage_fraction(grids["24h"], admin, signal_threshold)
    if rp_signal not in agg:
        log.warning("primary_rp_missing", date=day, rp=rp_signal)
        return None

    gpm_da = load_gpm_daily(gpm_dir, day)
    gpm_s = aggregate_to_admin1(gpm_da, admin) if gpm_da is not None else pd.Series(dtype=float)

    if "ensemble_confidence" in exc_day:
        conf_mean_s = aggregate_to_admin1(exc_day["ensemble_confidence"].astype(float), admin)
        conf_s: pd.Series = conf_mean_s.round().clip(0, 2).fillna(2.0).astype("int8")
    else:
        conf_s = pd.Series(dtype="int8")

    def _finite(series: pd.Series, pcode: str, default: float = 0.0) -> float:
        val = float(series.get(pcode, float("nan")))
        return val if math.isfinite(val) else default

    def _slim(result: dict) -> dict:
        return {k: result[k] for k in
                ("risk_state", "risk_label", "p_green", "p_yellow", "p_orange", "p_red")}

    scores: dict[str, dict] = {}
    for pcode, cluster in pcode_cluster.items():
        unit = unit_by_pcode[pcode]
        model = crma_models[cluster]
        gpm_24h = _finite(gpm_s, pcode)
        quality = int(conf_s.get(pcode, 2.0))

        risk_by_rp: dict[str, dict] = {}
        primary_result: dict | None = None
        primary_ev: CRMAEvidence | None = None

        for rp in rp_options:
            state = bn_states[pcode].setdefault(rp, init_state())
            if rp not in agg:
                risk_by_rp[str(rp)] = _NO_DATA_RESULT
                continue

            p24, p72, p7d = (
                float(agg[rp][w].get(pcode, float("nan"))) for w in ("24h", "72h", "7d"))
            if not all(math.isfinite(v) for v in (p24, p72, p7d)):
                bn_states[pcode][rp] = replace(state, api_mm=state.api_mm * api_decay)
                risk_by_rp[str(rp)] = _NO_DATA_RESULT
                continue

            ev = CRMAEvidence(
                exceedance_prob_24h=p24, exceedance_prob_72h=p72,
                exceedance_prob_7d=p7d, gpm_obs_24h=gpm_24h, api_mm=state.api_mm,
                spatial_coverage_fraction=_finite(agg[rp]["cov"], pcode),
                consecutive_signal_days=state.consecutive_days,
                sat_consecutive_days=state.sat_consecutive_days,
                gpm_quality=quality, rp_years=rp,
                thresholds=model.evidence_thresholds(rp),
            )
            result, bn_states[pcode][rp] = bn_step(
                state, ev, model, api_decay=api_decay,
                gpm_obs_mm=gpm_24h, signal_threshold=signal_threshold)
            risk_by_rp[str(rp)] = _slim(result)
            if rp == rp_signal:
                primary_result, primary_ev = result, ev

        if primary_ev is None:
            primary_ev = CRMAEvidence(
                exceedance_prob_24h=0.0, exceedance_prob_72h=0.0, exceedance_prob_7d=0.0,
                gpm_obs_24h=0.0, api_mm=bn_states[pcode][rp_signal].api_mm,
                spatial_coverage_fraction=0.0, consecutive_signal_days=0,
                sat_consecutive_days=bn_states[pcode][rp_signal].sat_consecutive_days,
                gpm_quality=quality, rp_years=rp_signal,
            )
        _, scores[pcode] = build_score(
            unit, primary_result or _NO_DATA_RESULT, primary_ev, risk_by_rp=risk_by_rp)

    out_path = write_risk_scores(day, scores, output_dir)
    log.info("risk_day_written", date=day, n_units=len(scores), rps=list(agg))
    return out_path


_CHECKPOINT_VERSION = 2


def _save_checkpoint(
    path: Path,
    bn_states: dict[str, dict[int, DynamicBNState]],
    next_date: date,
) -> None:
    """Serialise per-RP ``bn_states`` to JSON for crash recovery."""
    payload = {
        "version": _CHECKPOINT_VERSION,
        "next_date": next_date.isoformat(),
        "bn_states": {
            pcode: {str(rp): asdict(state) for rp, state in by_rp.items()}
            for pcode, by_rp in bn_states.items()
        },
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload))
    os.replace(tmp, path)  # atomic on POSIX and Windows NTFS
    log.debug("checkpoint_saved", path=str(path), next_date=next_date.isoformat())


def _state_from_dict(s: dict) -> DynamicBNState:
    return DynamicBNState(
        api_mm=float(s["api_mm"]),
        consecutive_days=int(s["consecutive_days"]),
        sat_consecutive_days=int(s["sat_consecutive_days"]),
        last_risk_state=int(s["last_risk_state"]),
    )


def _load_checkpoint(
    path: Path,
    default_states: dict[str, dict[int, DynamicBNState]],
    default_start: date,
    rp_options: list[int],
) -> tuple[dict[str, dict[int, DynamicBNState]], date]:
    """Load checkpoint if present and return (bn_states, resume_date).

    Version-1 checkpoints (one flat state per pcode, pre-dating per-RP states)
    are migrated by seeding every RP with the same state.
    """
    if not path.exists():
        return default_states, default_start

    try:
        data = json.loads(path.read_text())
        resume_date = date.fromisoformat(data["next_date"])
        if resume_date <= default_start:
            return default_states, default_start

        version = int(data.get("version", 1))
        states: dict[str, dict[int, DynamicBNState]] = {}
        for pcode, entry in data["bn_states"].items():
            if version >= 2:
                states[pcode] = {int(rp): _state_from_dict(s) for rp, s in entry.items()}
            else:
                shared = _state_from_dict(entry)
                states[pcode] = {rp: replace(shared) for rp in rp_options}
        if version < 2:
            log.warning("checkpoint_v1_migrated", msg="flat state seeded into every RP")

        for pcode, by_rp in default_states.items():
            states.setdefault(pcode, by_rp)
            for rp in rp_options:
                states[pcode].setdefault(rp, by_rp[rp])

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
