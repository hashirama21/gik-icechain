"""Temporal propagation of the CRMA Bayesian Network (Dynamic BN slice).

The static BN treats API_State as an independent input.  This module adds the
temporal dimension:

  API(t) = observed(t) + decay * API(t-1)

so that soil moisture state carries forward between forecast days.  It also
tracks ``sat_consecutive_days`` — the number of consecutive days on which
API_State has been Saturated.  This drives the ``Soil_Memory`` BN node that
distinguishes 15-day saturated soil + 50mm new rainfall from dry soil + 50mm
(the key scientific distinction added in DBN Innovation 1a).

All numeric defaults match configs/default.yaml (component3.crma_model /
component3.api).  Pass explicit values to override.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from gik_icechain.risk.crma_model import CRMAEvidence, CRMAModel

log = structlog.get_logger(__name__)


@dataclass
class DynamicBNState:
    """Persistent state carried between consecutive forecast days."""

    api_mm: float
    consecutive_days: int       # consecutive days with rainfall signal (Temporal_Persist)
    sat_consecutive_days: int   # consecutive days with API_State=Saturated (Soil_Memory)
    last_risk_state: int


def init_state(
    initial_api_mm: float = 20.0,
    sat_consecutive_days: int = 0,
) -> DynamicBNState:
    """Create an initial :class:`DynamicBNState` before the first forecast day.

    Args:
        initial_api_mm:       Starting API value (mm). 20 mm represents
                              moderately wet antecedent soil conditions.
        sat_consecutive_days: Known consecutive saturated days before day 0.
    """
    return DynamicBNState(
        api_mm=initial_api_mm,
        consecutive_days=0,
        sat_consecutive_days=sat_consecutive_days,
        last_risk_state=0,
    )


def step(
    state: DynamicBNState,
    evidence: CRMAEvidence,
    model: CRMAModel,
    api_decay: float = 0.8,
    gpm_obs_mm: float = 0.0,
    signal_threshold: float = 0.15,
) -> tuple[dict[str, Any], DynamicBNState]:
    """Advance the Dynamic BN by one forecast day.

    Updates evidence with current state API, consecutive days, and soil memory,
    runs CRMA inference, then computes the new state for the next step.

    API is advanced via:  API(t+1) = gpm_obs_mm + api_decay * API(t)
    sat_consecutive_days increments when the resulting api_state == 2 (Saturated),
    resets to 0 otherwise — driving the Soil_Memory BN node.

    Args:
        state:            Current :class:`DynamicBNState`.
        evidence:         ``CRMAEvidence`` for this day (api_mm, consecutive_signal_days,
                          and sat_consecutive_days will be overridden from state).
        model:            Built :class:`CRMAModel` instance.
        api_decay:        Exponential decay factor (default from config: 0.8).
        gpm_obs_mm:       GPM IMERG observed precipitation for this day (mm/day).
        signal_threshold: Exceedance prob threshold for rainfall signal detection.

    Returns:
        Tuple of (result_dict, new_state) where result_dict is the output
        of ``CRMAModel.infer()``.
    """
    evidence_with_state = replace(
        evidence,
        api_mm=state.api_mm,
        consecutive_signal_days=state.consecutive_days,
        sat_consecutive_days=state.sat_consecutive_days,
    )

    result = model.infer(evidence_with_state)

    # Advance API via exponential decay + new rainfall
    new_api = gpm_obs_mm + api_decay * state.api_mm

    # Track consecutive rainfall-signal days (for Temporal_Persist node)
    has_signal = evidence.exceedance_prob_24h_5y >= signal_threshold
    new_consecutive = state.consecutive_days + 1 if has_signal else 0

    # Track consecutive saturated days (for Soil_Memory node)
    # Use the state-overridden evidence to get the correct api_state
    current_api_state = evidence_with_state.api_state
    new_sat_days = state.sat_consecutive_days + 1 if current_api_state == 2 else 0

    new_state = DynamicBNState(
        api_mm=new_api,
        consecutive_days=new_consecutive,
        sat_consecutive_days=new_sat_days,
        last_risk_state=int(result["risk_state"]),
    )

    log.debug(
        "dynamic_bn_step",
        api_in=f"{state.api_mm:.1f}",
        api_out=f"{new_api:.1f}",
        api_state=current_api_state,
        sat_days=new_sat_days,
        soil_memory=evidence_with_state.soil_memory_state,
        risk=result["risk_label"],
        consecutive=new_consecutive,
    )
    return result, new_state


def run_temporal_sequence(
    evidences: list[CRMAEvidence],
    model: CRMAModel,
    api_decay: float = 0.8,
    initial_api_mm: float = 20.0,
    gpm_obs_series: list[float] | None = None,
    signal_threshold: float = 0.15,
) -> list[dict[str, Any]]:
    """Apply :func:`step` over a sequence of daily evidence objects.

    Args:
        evidences:          Ordered list of ``CRMAEvidence`` (one per day).
        model:              Built ``CRMAModel`` instance.
        api_decay:          Exponential decay factor.
        initial_api_mm:     API value before the first day.
        gpm_obs_series:     Daily GPM observations (mm/day), aligned with
                            *evidences*. Defaults to zero each day.
        signal_threshold:   Exceedance prob threshold for signal detection.

    Returns:
        List of result dicts in the same order as *evidences*.
    """
    state = init_state(initial_api_mm)
    results: list[dict[str, Any]] = []
    obs_series = gpm_obs_series or [0.0] * len(evidences)

    for evidence, gpm_obs in zip(evidences, obs_series, strict=True):
        result, state = step(
            state,
            evidence,
            model,
            api_decay=api_decay,
            gpm_obs_mm=gpm_obs,
            signal_threshold=signal_threshold,
        )
        results.append(result)

    return results
