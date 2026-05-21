"""ICPAC CRMA Bayesian Network for admin-1 flood risk in East Africa.

Static DAG (8 nodes, 7 intra-slice edges):
  Forecast_Hazard  ──┐
  Obs_Antecedent   ──┤
  Temporal_Persist ──┼──► Compound_Risk ──► Risk_State
  Spatial_Coverage ──┤
  Data_Confidence  ──┤
  API_State        ──┘

Dynamic extension (C1-A): API_State carries over between days via
a 3×3 inter-slice transition edge in a DynamicBayesianNetwork used by
infer_sequence().  Single-step infer() uses VariableElimination on the
equivalent DiscreteBayesianNetwork for efficiency.

API_State extends the ICPAC CRMA prototype (EGU26-18323) with soil moisture
persistence via the Antecedent Precipitation Index (White et al., 2021).
CPTs are initialised from expert elicitation and optionally refined by
EM-DAT MLE (see cpt_refinement.py).

Risk_State levels: 0=Green, 1=Yellow, 2=Orange, 3=Red.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import numpy as np
import structlog

log = structlog.get_logger(__name__)


class EastAfricaCluster(StrEnum):
    """4 climate clusters for regionalized CPT weights (ICPAC E4DRR, 2024)."""

    EQUATORIAL_EAST = "equatorial_east"  # Kenya coast, Tanzania, Uganda
    HORN_ARID = "horn_arid"              # N. Somalia, Djibouti, Eritrea
    GREAT_RIFT = "great_rift"            # Ethiopian highlands, Kenya Rift
    NILE_BASIN = "nile_basin"            # South Sudan, Sudan, N. Uganda


# Per-cluster compound-risk score weights for _build_compound_risk_cpt.
# Higher forecast/api weights in arid zones to reduce false-alarm rate.
_CLUSTER_WEIGHTS: dict[str, dict[str, float]] = {
    EastAfricaCluster.EQUATORIAL_EAST: {"forecast": 2.0, "obs": 1.5, "api": 1.5},
    EastAfricaCluster.HORN_ARID:       {"forecast": 2.5, "obs": 1.0, "api": 2.0},
    EastAfricaCluster.GREAT_RIFT:      {"forecast": 2.0, "obs": 1.5, "api": 1.8},
    EastAfricaCluster.NILE_BASIN:      {"forecast": 1.8, "obs": 2.0, "api": 1.5},
}

# API_State inter-slice transition: P(API_t | API_{t-1}).
# Rows: API_t ∈ {Dry=0, Normal=1, Saturated=2}.
# Cols: API_{t-1} ∈ {Dry=0, Normal=1, Saturated=2}.
# Reflects exponential decay (k≈0.8) and the East Africa bimodal rain regime.
_API_TRANSITION = np.array([
    [0.70, 0.20, 0.05],  # P(Dry_t | prev)
    [0.25, 0.55, 0.35],  # P(Normal_t | prev)
    [0.05, 0.25, 0.60],  # P(Saturated_t | prev)
])

try:
    from pgmpy.factors.discrete import TabularCPD
    from pgmpy.inference import DBNInference, VariableElimination
    from pgmpy.models import DiscreteBayesianNetwork, DynamicBayesianNetwork

    PGMPY_AVAILABLE = True
except ImportError:
    PGMPY_AVAILABLE = False
    VariableElimination = None  # type: ignore[assignment,misc]
    DBNInference = None         # type: ignore[assignment,misc]
    log.warning("pgmpy_not_installed", msg="pip install pgmpy")


RISK_LEVELS = {0: "Green", 1: "Yellow", 2: "Orange", 3: "Red"}
N_RISK_LEVELS = len(RISK_LEVELS)

NODE_CARDS: dict[str, int] = {
    "Forecast_Hazard": 3,  # Low / Medium / High
    "Obs_Antecedent": 3,  # Below normal / Normal / Above normal
    "Temporal_Persist": 2,  # No / Yes
    "Spatial_Coverage": 3,  # Local / Regional / Extensive
    "Data_Confidence": 3,  # Low / Medium / High
    "API_State": 3,  # Dry / Normal / Saturated
    "Compound_Risk": 4,  # None / Low / Moderate / High
    "Risk_State": 4,  # Green / Yellow / Orange / Red
}

_INTRA_EDGES = [
    ("Forecast_Hazard", "Compound_Risk"),
    ("Obs_Antecedent", "Compound_Risk"),
    ("Temporal_Persist", "Compound_Risk"),
    ("Spatial_Coverage", "Compound_Risk"),
    ("Data_Confidence", "Compound_Risk"),
    ("API_State", "Compound_Risk"),
    ("Compound_Risk", "Risk_State"),
]


@dataclass
class CRMAEvidence:
    """Evidence observed on a given day for one admin-1 unit."""

    exceedance_prob_24h_5y: float
    exceedance_prob_72h_5y: float
    exceedance_prob_7d_5y: float
    gpm_obs_24h: float
    api_mm: float
    spatial_coverage_fraction: float
    consecutive_signal_days: int
    gpm_quality: int = 2

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

    def to_obs_dict(self) -> dict[str, int]:
        """Discretised evidence as a plain dict keyed by BN node name."""
        return {
            "Forecast_Hazard": self.forecast_hazard_state,
            "Obs_Antecedent": self.obs_antecedent_state,
            "Temporal_Persist": self.temporal_persistence_state,
            "Spatial_Coverage": self.spatial_coverage_state,
            "Data_Confidence": self.data_confidence_state,
            "API_State": self.api_state,
        }


class CRMAModel:
    """ICPAC CRMA Bayesian Network for East Africa flood risk.

    Single-step inference uses a DiscreteBayesianNetwork + VariableElimination.
    Multi-step sequence inference uses a DynamicBayesianNetwork + DBNInference
    with an inter-slice API_State transition capturing soil moisture persistence.

    CPT structure from expert elicitation described in:
    Kalladath, N. et al. (2026), "CRMA: Continuous Risk Monitoring and
    Assessment for East Africa", EGU General Assembly 2026, EGU26-18323.
    CPTs are optionally refined via EM-DAT MLE (see cpt_refinement.py).
    """

    def __init__(
        self,
        cluster: EastAfricaCluster = EastAfricaCluster.EQUATORIAL_EAST,
        cpt_path: Path | None = None,
    ) -> None:
        if not PGMPY_AVAILABLE:
            raise ImportError("pgmpy is required: pip install pgmpy")

        self.cluster = cluster
        self._model: Any = None        # DiscreteBayesianNetwork for infer()
        self._inference: Any = None    # VariableElimination on _model
        self._dbn: Any = None          # DynamicBayesianNetwork for infer_sequence()
        self._dbn_inference: Any = None
        self._cpt_path = cpt_path

    def build(self) -> None:
        """Construct both the static BN and the 2-slice DBN."""
        cpds = self._build_cpds()

        # ── Static DiscreteBayesianNetwork (single-step inference) ────────────
        self._model = DiscreteBayesianNetwork(_INTRA_EDGES)
        self._model.add_cpds(*cpds)
        if not self._model.check_model():
            raise ValueError("DiscreteBayesianNetwork failed validation")
        self._inference = VariableElimination(self._model)

        # ── DynamicBayesianNetwork (multi-step sequence inference) ────────────
        # Build all edges explicitly for both time slices to avoid calling
        # initialize_initial_state(), which has a pgmpy bug for nodes with
        # cardinality != 2 (hardcoded reshape to (2, -1)).
        dbn_all_edges = (
            [((u, 0), (v, 0)) for u, v in _INTRA_EDGES]   # slice-0 intra
            + [((u, 1), (v, 1)) for u, v in _INTRA_EDGES]  # slice-1 intra
            + [(("API_State", 0), ("API_State", 1))]         # inter-slice
        )
        self._dbn = DynamicBayesianNetwork(dbn_all_edges)

        # Slice-0 CPDs: same structure as the static BN.
        # Slice-1 CPDs for non-API nodes: same CPTs with slice-1 evidence variables.
        # API_State_1: 3×3 inter-slice transition matrix.
        api_transition_cpd = TabularCPD(
            ("API_State", 1), 3, _API_TRANSITION,
            evidence=[("API_State", 0)], evidence_card=[3],
        )
        dbn_cpds_0 = [self._to_dbn_cpd(cpd, time_slice=0) for cpd in cpds]
        dbn_cpds_1 = [
            self._to_dbn_cpd(cpd, time_slice=1)
            for cpd in cpds
            if cpd.variable != "API_State"  # API_State_1 uses transition CPD
        ]
        self._dbn.add_cpds(*dbn_cpds_0, *dbn_cpds_1, api_transition_cpd)
        if not self._dbn.check_model():
            raise ValueError("DynamicBayesianNetwork failed validation")
        self._dbn_inference = DBNInference(self._dbn)

        log.info("crma_model_built", cluster=self.cluster,
                 nodes=len(self._model.nodes()))

    def infer(self, evidence: CRMAEvidence) -> dict[str, Any]:
        """Single-step Bayesian inference for one admin-1 unit on one day.

        Uses VariableElimination on the static DiscreteBayesianNetwork.

        Returns a dict with risk_state (0–3), risk_label, per-state
        probabilities, and the discretised evidence passed to the BN.
        """
        if self._inference is None:
            raise RuntimeError("Model not built. Call build() first.")

        obs = evidence.to_obs_dict()
        risk_dist = self._inference.query(
            variables=["Risk_State"],
            evidence=obs,
            show_progress=False,
        )
        probs = risk_dist.values
        risk_state = int(np.argmax(probs))
        return self._format_result(risk_state, probs, obs)

    def infer_sequence(
        self,
        evidence_sequence: list[CRMAEvidence],
        initial_api_state: int = 0,
    ) -> list[dict[str, Any]]:
        """Multi-step inference over a temporal sequence via DBNInference.

        The API_State is treated as a latent variable propagated through the
        inter-slice transition matrix; all other evidence is fully observed at
        each time step.  The initial API_State at time 0 must be supplied.

        Args:
            evidence_sequence:  Ordered list of CRMAEvidence (one per day).
            initial_api_state:  Discretised API_State at day 0 (0=Dry, 1=Normal,
                                2=Saturated).

        Returns:
            List of result dicts (same format as infer()) for each day.
        """
        if self._dbn_inference is None:
            raise RuntimeError("Model not built. Call build() first.")

        n_steps = len(evidence_sequence)
        if n_steps == 0:
            return []

        # Build evidence spanning all time steps.
        # API_State is latent beyond t=0; all other nodes are fully observed.
        dbn_evidence: dict[tuple[str, int], int] = {
            ("API_State", 0): initial_api_state,
        }
        for t, ev in enumerate(evidence_sequence):
            obs = ev.to_obs_dict()
            for node in ("Forecast_Hazard", "Obs_Antecedent", "Temporal_Persist",
                         "Spatial_Coverage", "Data_Confidence"):
                dbn_evidence[(node, t)] = obs[node]

        query_vars = [("Risk_State", t) for t in range(n_steps)]
        results_raw = self._dbn_inference.forward_inference(query_vars, evidence=dbn_evidence)

        out: list[dict[str, Any]] = []
        for t, ev in enumerate(evidence_sequence):
            probs = results_raw[("Risk_State", t)].values
            risk_state = int(np.argmax(probs))
            out.append(self._format_result(risk_state, probs, ev.to_obs_dict()))
        return out

    # ── Private helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _to_dbn_cpd(cpd: Any, time_slice: int) -> Any:
        """Convert a static BN TabularCPD to a DBN CPD at *time_slice*."""
        parents = cpd.variables[1:]  # empty for root nodes
        return TabularCPD(
            variable=(cpd.variable, time_slice),
            variable_card=cpd.variable_card,
            values=cpd.get_values(),
            evidence=[(p, time_slice) for p in parents] if parents else None,
            evidence_card=list(cpd.cardinality[1:]) if parents else None,
        )

    def _format_result(
        self, risk_state: int, probs: np.ndarray, obs: dict[str, int]
    ) -> dict[str, Any]:
        return {
            "risk_state": risk_state,
            "risk_label": RISK_LEVELS[risk_state],
            "p_green": float(probs[0]),
            "p_yellow": float(probs[1]),
            "p_orange": float(probs[2]),
            "p_red": float(probs[3]),
            "evidence": obs,
        }

    def _build_cpds(self) -> list[Any]:
        """Return a list of TabularCPD objects for the static BN."""
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
        return [cpd_forecast, cpd_obs, cpd_persist, cpd_spatial,
                cpd_confidence, cpd_api, cpd_compound, cpd_risk]

    def _build_compound_risk_cpd(self) -> Any:
        """Build the Compound_Risk CPT from a rule-based risk score (0–10 scale).

        Each parent state contributes additively; Data_Confidence dampens the
        total score by [0.5, 0.8, 1.0] to reflect observational uncertainty.
        3×3×2×3×3×3 = 486 parent state combinations.
        """
        parent_cards = [
            NODE_CARDS["Forecast_Hazard"],  # 3
            NODE_CARDS["Obs_Antecedent"],  # 3
            NODE_CARDS["Temporal_Persist"],  # 2
            NODE_CARDS["Spatial_Coverage"],  # 3
            NODE_CARDS["Data_Confidence"],  # 3
            NODE_CARDS["API_State"],  # 3
        ]
        n_combinations = 1
        for c in parent_cards:
            n_combinations *= c

        w = _CLUSTER_WEIGHTS[self.cluster]
        cpt = np.zeros((4, n_combinations))
        for idx in range(n_combinations):
            f_hazard, obs_ant, t_persist, spatial, confidence, api = self._idx_to_states(
                idx, parent_cards
            )
            score = (
                f_hazard * w["forecast"]
                + obs_ant * w["obs"]
                + t_persist * 1.5    # temporal persistence — universal
                + spatial * 1.0      # spatial coverage — universal
                + api * w["api"]
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
                "Forecast_Hazard",
                "Obs_Antecedent",
                "Temporal_Persist",
                "Spatial_Coverage",
                "Data_Confidence",
                "API_State",
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
        self._inference = VariableElimination(self._model)  # type: ignore[operator]
        log.info("cpts_loaded", path=str(path))
