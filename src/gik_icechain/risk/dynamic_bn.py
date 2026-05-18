"""Temporal propagation of the CRMA Bayesian Network (Dynamic BN slice).

The static BN in ``crma_model.py`` treats API_State as an independent input.
This module adds the temporal dimension: ``API(t) = obs(t) + decay * API(t-1)``
so that soil moisture state carries forward between forecast days.

Each call to :func:`step` advances the state by one day and returns the CRMA
inference result alongside the updated :class:`DynamicBNState`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from gik_icechain.risk.crma_model import CRMAEvidence, CRMAModel

log = structlog.get_logger(__name__)

_DEFAULT_API_DECAY   = 0.8
_DEFAULT_INITIAL_API = 20.0
_SIGNAL_THRESHOLD    = 0.15


@dataclass
class DynamicBNState:
    """Persistent state carried between consecutive forecast days.

    Attributes:
        api_mm:           Current API value in mm.
        consecutive_days: Number of consecutive days with an exceedance signal
                          (p_24h >= _SIGNAL_THRESHOLD).
        last_risk_state:  Risk state integer (0–3) from the previous step.
    """

    api_mm:           float
    consecutive_days: int
    last_risk_state:  int


def init_state(initial_api_mm: float = _DEFAULT_INITIAL_API) -> DynamicBNState:
    """Create an initial :class:`DynamicBNState` before the first forecast day.

    Args:
        initial_api_mm: Starting API value (mm). 20 mm represents moderately
                        wet antecedent soil conditions.
    """
    return DynamicBNState(
        api_mm=initial_api_mm,
        consecutive_days=0,
        last_risk_state=0,
    )


def step(
    state: DynamicBNState,
    evidence: "CRMAEvidence",
    model: "CRMAModel",
    api_decay: float = _DEFAULT_API_DECAY,
    gpm_obs_mm: float = 0.0,
) -> tuple[dict[str, Any], DynamicBNState]:
    """Advance the Dynamic BN by one forecast day.

    Updates *evidence.api_mm* with the current state's API value, runs CRMA
    inference, then computes the new API for the next step.

    Args:
        state:      Current :class:`DynamicBNState`.
        evidence:   ``CRMAEvidence`` for this day (api_mm will be overridden
                    with the state value; consecutive_signal_days similarly).
        model:      Built :class:`CRMAModel` instance.
        api_decay:  Exponential decay factor.
        gpm_obs_mm: GPM IMERG observed precipitation for this day (mm/day),
                    used to advance the API.

    Returns:
        Tuple of (result_dict, new_state) where result_dict is the output
        of ``CRMAModel.infer()``.
    """
    from dataclasses import replace

    evidence_with_state = replace(
        evidence,
        api_mm=state.api_mm,
        consecutive_signal_days=state.consecutive_days,
    )

    result = model.infer(evidence_with_state)

    new_api = gpm_obs_mm + api_decay * state.api_mm
    has_signal = evidence.exceedance_prob_24h_5y >= _SIGNAL_THRESHOLD
    new_consecutive = state.consecutive_days + 1 if has_signal else 0
    new_risk_state  = int(result["risk_state"])

    new_state = DynamicBNState(
        api_mm=new_api,
        consecutive_days=new_consecutive,
        last_risk_state=new_risk_state,
    )

    log.debug(
        "dynamic_bn_step",
        api_in=f"{state.api_mm:.1f}",
        api_out=f"{new_api:.1f}",
        risk=result["risk_label"],
        consecutive=new_consecutive,
    )
    return result, new_state


def run_temporal_sequence(
    evidences: "list[CRMAEvidence]",
    model: "CRMAModel",
    api_decay: float = _DEFAULT_API_DECAY,
    initial_api_mm: float = _DEFAULT_INITIAL_API,
    gpm_obs_series: list[float] | None = None,
) -> list[dict[str, Any]]:
    """Apply :func:`step` over a sequence of daily evidence objects.

    Args:
        evidences:        Ordered list of ``CRMAEvidence`` (one per day).
        model:            Built ``CRMAModel`` instance.
        api_decay:        Exponential decay factor.
        initial_api_mm:   API value before the first day.
        gpm_obs_series:   Optional list of daily GPM observations (mm/day),
                          aligned with *evidences*. Defaults to zero each day.

    Returns:
        List of result dicts in the same order as *evidences*.
    """
    state = init_state(initial_api_mm)
    results: list[dict[str, Any]] = []

    obs_series = gpm_obs_series or [0.0] * len(evidences)

    for evidence, gpm_obs in zip(evidences, obs_series):
        result, state = step(
            state, evidence, model, api_decay=api_decay, gpm_obs_mm=gpm_obs
        )
        results.append(result)

    return results
