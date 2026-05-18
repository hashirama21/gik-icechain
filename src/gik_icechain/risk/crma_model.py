"""ICPAC CRMA Bayesian Network for admin-1 flood risk in East Africa.

DAG (8 nodes, 7 edges):
  Forecast_Hazard  ──┐
  Obs_Antecedent   ──┤
  Temporal_Persist ──┼──► Compound_Risk ──► Risk_State
  Spatial_Coverage ──┤
  Data_Confidence  ──┘
  API_State        ──┘

API_State extends the ICPAC CRMA prototype (EGU26-18323) with soil moisture
persistence via the Antecedent Precipitation Index (White et al., 2021).
CPTs are initialised from expert elicitation and optionally refined by
EM-DAT MLE (see cpt_refinement.py).

Risk_State levels: 0=Green, 1=Yellow, 2=Orange, 3=Red.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import structlog

log = structlog.get_logger(__name__)

try:
    import pgmpy.models as pgm
    from pgmpy.factors.discrete import TabularCPD
    from pgmpy.inference import VariableElimination

    PGMPY_AVAILABLE = True
except ImportError:
    PGMPY_AVAILABLE = False
    log.warning("pgmpy_not_installed", msg="pip install pgmpy")


RISK_LEVELS = {0: "Green", 1: "Yellow", 2: "Orange", 3: "Red"}
N_RISK_LEVELS = len(RISK_LEVELS)

NODE_CARDS: dict[str, int] = {
    "Forecast_Hazard":  3,  # Low / Medium / High
    "Obs_Antecedent":   3,  # Below normal / Normal / Above normal
    "Temporal_Persist": 2,  # No / Yes
    "Spatial_Coverage": 3,  # Local / Regional / Extensive
    "Data_Confidence":  3,  # Low / Medium / High
    "API_State":        3,  # Dry / Normal / Saturated
    "Compound_Risk":    4,  # None / Low / Moderate / High
    "Risk_State":       4,  # Green / Yellow / Orange / Red
}


@dataclass
class CRMAEvidence:
    """Evidence observed on a given day for one admin-1 unit."""

    exceedance_prob_24h_5y:    float
    exceedance_prob_72h_5y:    float
    exceedance_prob_7d_5y:     float
    gpm_obs_24h:               float
    api_mm:                    float
    spatial_coverage_fraction: float
    consecutive_signal_days:   int
    gpm_quality:               int = 2

    @property
    def forecast_hazard_state(self) -> int:
        """0=Low, 1=Medium, 2=High."""
        p = max(self.exceedance_prob_24h_5y, self.exceedance_prob_72h_5y)
        if p >= 0.4:
            return 2
        if p >= 0.15:
            return 1
        return 0

    @property
    def obs_antecedent_state(self) -> int:
        """0=Below normal, 1=Normal, 2=Above normal."""
        if self.gpm_obs_24h >= 25.0:
            return 2
        if self.gpm_obs_24h >= 5.0:
            return 1
        return 0

    @property
    def temporal_persistence_state(self) -> int:
        """0=No, 1=Yes (≥3 consecutive days with signal)."""
        return int(self.consecutive_signal_days >= 3)

    @property
    def spatial_coverage_state(self) -> int:
        """0=Local (<25%), 1=Regional (25–75%), 2=Extensive (>75%)."""
        if self.spatial_coverage_fraction >= 0.75:
            return 2
        if self.spatial_coverage_fraction >= 0.25:
            return 1
        return 0

    @property
    def data_confidence_state(self) -> int:
        """0=Low, 1=Medium, 2=High."""
        return min(self.gpm_quality, 2)

    @property
    def api_state(self) -> int:
        """0=Dry (API<30mm), 1=Normal (30≤API<80mm), 2=Saturated (API≥80mm).

        Thresholds from White et al. (2021) for the Nzoia basin, East Africa.
        """
        if self.api_mm >= 80.0:
            return 2
        if self.api_mm >= 30.0:
            return 1
        return 0


class CRMAModel:
    """ICPAC CRMA Bayesian Network for East Africa flood risk."""

    def __init__(self, cpt_path: Path | None = None) -> None:
        if not PGMPY_AVAILABLE:
            raise ImportError("pgmpy is required: pip install pgmpy")

        self._model: pgm.BayesianNetwork | None = None
        self._inference: VariableElimination | None = None
        self._cpt_path = cpt_path

    def build(self) -> None:
        """Construct the Bayesian Network structure and CPTs."""
        edges = [
            ("Forecast_Hazard",  "Compound_Risk"),
            ("Obs_Antecedent",   "Compound_Risk"),
            ("Temporal_Persist", "Compound_Risk"),
            ("Spatial_Coverage", "Compound_Risk"),
            ("Data_Confidence",  "Compound_Risk"),
            ("API_State",        "Compound_Risk"),
            ("Compound_Risk",    "Risk_State"),
        ]

        self._model = pgm.BayesianNetwork(edges)

        cpd_forecast = TabularCPD(
            variable="Forecast_Hazard",
            variable_card=3,
            values=[[0.50], [0.30], [0.20]],
        )
        cpd_obs = TabularCPD(
            variable="Obs_Antecedent",
            variable_card=3,
            values=[[0.40], [0.35], [0.25]],
        )
        cpd_persist = TabularCPD(
            variable="Temporal_Persist",
            variable_card=2,
            values=[[0.75], [0.25]],
        )
        cpd_spatial = TabularCPD(
            variable="Spatial_Coverage",
            variable_card=3,
            values=[[0.50], [0.30], [0.20]],
        )
        cpd_confidence = TabularCPD(
            variable="Data_Confidence",
            variable_card=3,
            values=[[0.10], [0.30], [0.60]],
        )
        cpd_api = TabularCPD(
            variable="API_State",
            variable_card=3,
            values=[[0.45], [0.35], [0.20]],
        )
        cpd_compound = self._build_compound_risk_cpd()
        cpd_risk = TabularCPD(
            variable="Risk_State",
            variable_card=4,
            values=[
                [0.95, 0.50, 0.10, 0.02],  # P(Green  | Compound = None/Low/Mod/High)
                [0.04, 0.40, 0.40, 0.08],  # P(Yellow | ...)
                [0.01, 0.08, 0.40, 0.30],  # P(Orange | ...)
                [0.00, 0.02, 0.10, 0.60],  # P(Red    | ...)
            ],
            evidence=["Compound_Risk"],
            evidence_card=[4],
        )

        self._model.add_cpds(
            cpd_forecast, cpd_obs, cpd_persist, cpd_spatial,
            cpd_confidence, cpd_api, cpd_compound, cpd_risk,
        )

        if not self._model.check_model():
            raise ValueError("Bayesian Network model failed validation")

        self._inference = VariableElimination(self._model)
        log.info("crma_model_built", nodes=len(self._model.nodes()))

    def infer(self, evidence: CRMAEvidence) -> dict[str, Any]:
        """Run Bayesian inference for one admin-1 unit on one day.

        Returns a dict with risk_state (0–3), risk_label, per-state
        probabilities, and the discretised evidence passed to the BN.
        """
        if self._inference is None:
            raise RuntimeError("Model not built. Call build() first.")

        obs = {
            "Forecast_Hazard":  evidence.forecast_hazard_state,
            "Obs_Antecedent":   evidence.obs_antecedent_state,
            "Temporal_Persist": evidence.temporal_persistence_state,
            "Spatial_Coverage": evidence.spatial_coverage_state,
            "Data_Confidence":  evidence.data_confidence_state,
            "API_State":        evidence.api_state,
        }

        risk_dist = self._inference.query(
            variables=["Risk_State"],
            evidence=obs,
            show_progress=False,
        )
        probs = risk_dist.values
        risk_state = int(np.argmax(probs))

        return {
            "risk_state": risk_state,
            "risk_label": RISK_LEVELS[risk_state],
            "p_green":    float(probs[0]),
            "p_yellow":   float(probs[1]),
            "p_orange":   float(probs[2]),
            "p_red":      float(probs[3]),
            "evidence":   obs,
        }

    def _build_compound_risk_cpd(self) -> TabularCPD:
        """Build the Compound_Risk CPT from a rule-based risk score (0–10 scale).

        Each parent state contributes additively; Data_Confidence dampens the
        total score by [0.5, 0.8, 1.0] to reflect observational uncertainty.
        3×3×2×3×3×3 = 486 parent state combinations.
        """
        parent_cards = [
            NODE_CARDS["Forecast_Hazard"],   # 3
            NODE_CARDS["Obs_Antecedent"],     # 3
            NODE_CARDS["Temporal_Persist"],   # 2
            NODE_CARDS["Spatial_Coverage"],   # 3
            NODE_CARDS["Data_Confidence"],    # 3
            NODE_CARDS["API_State"],          # 3
        ]
        n_combinations = 1
        for c in parent_cards:
            n_combinations *= c

        cpt = np.zeros((4, n_combinations))
        for idx in range(n_combinations):
            f_hazard, obs_ant, t_persist, spatial, confidence, api = (
                self._idx_to_states(idx, parent_cards)
            )
            score = (
                f_hazard * 2.0
                + obs_ant * 1.5
                + t_persist * 1.5
                + spatial * 1.0
                + api * 1.5
            ) * [0.5, 0.8, 1.0][confidence]

            if score <= 1.5:
                cpt[:, idx] = [0.85, 0.12, 0.02, 0.01]
            elif score <= 4.0:
                cpt[:, idx] = [0.20, 0.60, 0.15, 0.05]
            elif score <= 7.0:
                cpt[:, idx] = [0.05, 0.20, 0.55, 0.20]
            else:
                cpt[:, idx] = [0.02, 0.08, 0.30, 0.60]

        return TabularCPD(
            variable="Compound_Risk",
            variable_card=4,
            values=cpt,
            evidence=[
                "Forecast_Hazard", "Obs_Antecedent", "Temporal_Persist",
                "Spatial_Coverage", "Data_Confidence", "API_State",
            ],
            evidence_card=parent_cards,
        )

    @staticmethod
    def _idx_to_states(idx: int, cards: list[int]) -> list[int]:
        """Convert a flat column index to parent state list (reverse mixed-radix)."""
        states = []
        for card in reversed(cards):
            states.append(idx % card)
            idx //= card
        return list(reversed(states))

    def save_cpts(self, path: Path) -> None:
        """Serialize CPTs to JSON for inspection and versioning."""
        if self._model is None:
            raise RuntimeError("Model not built.")
        cpts = {
            node: self._model.get_cpds(node).values.tolist()
            for node in self._model.nodes()
            if self._model.get_cpds(node) is not None
        }
        path.write_text(json.dumps(cpts, indent=2))
        log.info("cpts_saved", path=str(path))

    def load_cpts(self, path: Path) -> None:
        """Load CPTs from a JSON file produced by save_cpts()."""
        if self._model is None:
            raise RuntimeError("Model not built first.")
        cpts = json.loads(path.read_text())
        for node, values in cpts.items():
            cpd = self._model.get_cpds(node)
            if cpd is not None:
                cpd.values = np.array(values)
        self._inference = VariableElimination(self._model)
        log.info("cpts_loaded", path=str(path))
