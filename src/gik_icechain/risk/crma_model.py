"""ICPAC CRMA Bayesian Network for admin-1 flood risk in East Africa.

Static DAG (9 nodes, 8 intra-slice edges):
  Forecast_Hazard  ──┐
  Obs_Antecedent   ──┤
  Temporal_Persist ──┼──► Compound_Risk ──► Risk_State
  Spatial_Coverage ──┤
  Data_Confidence  ──┤
  API_State        ──┤
  Soil_Memory      ──┘   (new — DBN Innovation 1a)

Dynamic extension (DBN): API_State carries over between days via a 3×3
inter-slice transition edge.  Single-step infer() uses a pre-computed
lookup table (3×3×2×3×3×3×2×4 ndarray) for O(1) inference.

Soil_Memory captures the key scientific distinction missed by a static BN:
saturated soil for 15 days + 50mm additional rainfall is now distinguishable
from dry soil with the same 50mm event (SoilMemory_State=1 lowers the CPT
bucket thresholds and shifts probability mass toward Red).

All CPT parameters are driven by configs/default.yaml (component3.crma_model)

CPT structure from expert elicitation described in:
Kalladath, N. et al. (2026), "CRMA: Continuous Risk Monitoring and
Assessment for East Africa", EGU General Assembly 2026, EGU26-18323.
CPTs are optionally refined via EM-DAT MLE (see cpt_refinement.py).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import structlog

if TYPE_CHECKING:
    from gik_icechain.shared.config import CRMAModelConfig

log = structlog.get_logger(__name__)


class EastAfricaCluster(StrEnum):
    """4 climate clusters for regionalized CPT weights (ICPAC E4DRR, 2024)."""

    EQUATORIAL_EAST = "equatorial_east"  # Kenya coast, Tanzania, Uganda
    HORN_ARID = "horn_arid"  # N. Somalia, Djibouti, Eritrea
    GREAT_RIFT = "great_rift"  # Ethiopian highlands, Kenya Rift
    NILE_BASIN = "nile_basin"  # South Sudan, Sudan, N. Uganda


# Module-level default weights — kept for backward-compat imports in tests.
# CRMAModel overrides these with values from CRMAModelConfig at build() time.
_CLUSTER_WEIGHTS: dict[str, dict[str, float]] = {
    EastAfricaCluster.EQUATORIAL_EAST: {"forecast": 2.0, "obs": 1.5, "api": 1.5},
    EastAfricaCluster.HORN_ARID: {"forecast": 2.5, "obs": 1.0, "api": 2.0},
    EastAfricaCluster.GREAT_RIFT: {"forecast": 2.0, "obs": 1.5, "api": 1.8},
    EastAfricaCluster.NILE_BASIN: {"forecast": 1.8, "obs": 2.0, "api": 1.5},
}

try:
    from pgmpy.factors.discrete import TabularCPD
    from pgmpy.inference import DBNInference, VariableElimination
    from pgmpy.models import DiscreteBayesianNetwork, DynamicBayesianNetwork

    PGMPY_AVAILABLE = True
except ImportError:
    PGMPY_AVAILABLE = False
    VariableElimination = None  # type: ignore[assignment,misc]
    DBNInference = None  # type: ignore[assignment,misc]
    log.warning("pgmpy_not_installed", msg="pip install pgmpy")


RISK_LEVELS = {0: "Green", 1: "Yellow", 2: "Orange", 3: "Red"}
N_RISK_LEVELS = len(RISK_LEVELS)

NODE_CARDS: dict[str, int] = {
    "Forecast_Hazard": 3,   # Low / Medium / High
    "Obs_Antecedent": 3,    # Below normal / Normal / Above normal
    "Temporal_Persist": 2,  # No / Yes
    "Spatial_Coverage": 3,  # Local / Regional / Extensive
    "Data_Confidence": 3,   # Low / Medium / High
    "API_State": 3,         # Dry / Normal / Saturated
    "Soil_Memory": 2,       # Recent (<N days sat) / Prolonged (≥N days sat)
    "Compound_Risk": 4,     # None / Low / Moderate / High
    "Risk_State": 4,        # Green / Yellow / Orange / Red
}

# Ordered parent dimensions of Compound_Risk — defines the lookup table axis order
_COMPOUND_PARENTS = [
    "Forecast_Hazard",
    "Obs_Antecedent",
    "Temporal_Persist",
    "Spatial_Coverage",
    "Data_Confidence",
    "API_State",
    "Soil_Memory",
]
_PARENT_CARDS = [NODE_CARDS[p] for p in _COMPOUND_PARENTS]

_INTRA_EDGES = [
    ("Forecast_Hazard", "Compound_Risk"),
    ("Obs_Antecedent", "Compound_Risk"),
    ("Temporal_Persist", "Compound_Risk"),
    ("Spatial_Coverage", "Compound_Risk"),
    ("Data_Confidence", "Compound_Risk"),
    ("API_State", "Compound_Risk"),
    ("Soil_Memory", "Compound_Risk"),
    ("Compound_Risk", "Risk_State"),
]


@dataclass
class CRMAEvidence:
    """Evidence observed on a given day for one admin-1 unit.

    All discretization thresholds are set from configs/default.yaml
    (component3.crma_model) — defaults here match the YAML values.
    """

    exceedance_prob_24h_5y: float
    exceedance_prob_72h_5y: float
    exceedance_prob_7d_5y: float
    gpm_obs_24h: float
    api_mm: float
    spatial_coverage_fraction: float
    consecutive_signal_days: int
    sat_consecutive_days: int = 0
    gpm_quality: int = 2
    # Discretization thresholds — instance fields (thread-safe).
    # Defaults match configs/default.yaml (component3.crma_model).
    # Use CRMAModel.make_evidence() to get instances pre-loaded from config.
    gpm_normal_mmday: float = 5.0
    gpm_above_mmday: float = 25.0
    api_normal_mm: float = 30.0
    api_saturated_mm: float = 80.0
    spatial_regional: float = 0.25
    spatial_extensive: float = 0.75
    persist_threshold: int = 3
    soil_memory_days: int = 7
    hazard_medium_threshold: float = 0.15  # Low → Medium boundary for Forecast_Hazard
    hazard_high_threshold: float = 0.40    # Medium → High boundary for Forecast_Hazard

    def __post_init__(self) -> None:
        for field, val in (
            ("exceedance_prob_24h_5y", self.exceedance_prob_24h_5y),
            ("exceedance_prob_72h_5y", self.exceedance_prob_72h_5y),
            ("exceedance_prob_7d_5y", self.exceedance_prob_7d_5y),
        ):
            if not (0.0 <= val <= 1.0):
                raise ValueError(f"{field}={val!r} must be in [0, 1]")
        if self.api_mm < 0:
            raise ValueError(f"api_mm={self.api_mm!r} must be >= 0")
        if not (0.0 <= self.spatial_coverage_fraction <= 1.0):
            raise ValueError(
                f"spatial_coverage_fraction={self.spatial_coverage_fraction!r} must be in [0, 1]"
            )

    @property
    def forecast_hazard_state(self) -> int:
        """0=Low, 1=Medium, 2=High."""
        p = max(self.exceedance_prob_24h_5y, self.exceedance_prob_72h_5y)
        if p >= self.hazard_high_threshold:
            return 2
        if p >= self.hazard_medium_threshold:
            return 1
        return 0

    @property
    def obs_antecedent_state(self) -> int:
        """0=Below normal, 1=Normal, 2=Above normal."""
        if self.gpm_obs_24h >= self.gpm_above_mmday:
            return 2
        if self.gpm_obs_24h >= self.gpm_normal_mmday:
            return 1
        return 0

    @property
    def temporal_persistence_state(self) -> int:
        """0=No, 1=Yes (≥ persist_threshold days with signal)."""
        return int(self.consecutive_signal_days >= self.persist_threshold)

    @property
    def spatial_coverage_state(self) -> int:
        """0=Local, 1=Regional, 2=Extensive."""
        if self.spatial_coverage_fraction >= self.spatial_extensive:
            return 2
        if self.spatial_coverage_fraction >= self.spatial_regional:
            return 1
        return 0

    @property
    def data_confidence_state(self) -> int:
        """0=Low, 1=Medium, 2=High."""
        return min(self.gpm_quality, 2)

    @property
    def api_state(self) -> int:
        """0=Dry, 1=Normal, 2=Saturated."""
        if self.api_mm >= self.api_saturated_mm:
            return 2
        if self.api_mm >= self.api_normal_mm:
            return 1
        return 0

    @property
    def soil_memory_state(self) -> int:
        """0=Recent, 1=Prolonged (≥ soil_memory_days of consecutive saturation)."""
        return int(self.sat_consecutive_days >= self.soil_memory_days)

    def to_obs_dict(self) -> dict[str, int]:
        """Discretised evidence as a plain dict keyed by BN node name."""
        return {
            "Forecast_Hazard": self.forecast_hazard_state,
            "Obs_Antecedent": self.obs_antecedent_state,
            "Temporal_Persist": self.temporal_persistence_state,
            "Spatial_Coverage": self.spatial_coverage_state,
            "Data_Confidence": self.data_confidence_state,
            "API_State": self.api_state,
            "Soil_Memory": self.soil_memory_state,
        }


class CRMAModel:
    """ICPAC CRMA Bayesian Network for East Africa flood risk.

    Single-step inference uses a pre-computed lookup table (O(1) per call).
    Shape: (3, 3, 2, 3, 3, 3, 2, 4) → 972 parent combos × 4 risk states.

    Multi-step sequence inference uses a DynamicBayesianNetwork + DBNInference
    with an inter-slice API_State transition capturing soil moisture persistence.

    All CPT parameters are driven by CRMAModelConfig (no hardcoded numerics).
    """

    def __init__(
        self,
        cluster: EastAfricaCluster = EastAfricaCluster.EQUATORIAL_EAST,
        cpt_path: Path | None = None,
        crma_cfg: CRMAModelConfig | None = None,
    ) -> None:
        if not PGMPY_AVAILABLE:
            raise ImportError("pgmpy is required: pip install pgmpy")

        self.cluster = cluster
        self._model: Any = None   # DiscreteBayesianNetwork
        self._dbn: Any = None     # DynamicBayesianNetwork
        self._dbn_inference: Any = None
        # Shape: (3, 3, 2, 3, 3, 3, 2, 4) — O(1) single-step inference
        self._lookup_table: np.ndarray | None = None
        self._cpt_path = cpt_path
        self._cfg = crma_cfg or self._default_cfg()

    @staticmethod
    def _default_cfg() -> CRMAModelConfig:
        from gik_icechain.shared.config import CRMAModelConfig
        return CRMAModelConfig()

    def make_evidence(self, **kwargs: object) -> CRMAEvidence:
        """Create a CRMAEvidence pre-loaded with this model's config thresholds.

        Keyword arguments are forwarded directly to CRMAEvidence, which still
        requires the mandatory positional fields.  Use this factory in production
        code to guarantee that threshold values always match the live config.
        """
        cfg = self._cfg
        return CRMAEvidence(
            gpm_normal_mmday=cfg.gpm_obs_normal_mmday,
            gpm_above_mmday=cfg.gpm_obs_above_mmday,
            api_normal_mm=cfg.api_threshold_normal_mm,
            api_saturated_mm=cfg.api_threshold_saturated_mm,
            spatial_regional=cfg.spatial_threshold_regional,
            spatial_extensive=cfg.spatial_threshold_extensive,
            persist_threshold=cfg.consecutive_signal_threshold,
            soil_memory_days=cfg.soil_memory_days,
            hazard_medium_threshold=cfg.hazard_medium_threshold,
            hazard_high_threshold=cfg.hazard_high_threshold,
            **kwargs,  # type: ignore[arg-type]
        )

    def build(self) -> None:
        """Construct the static BN, the 2-slice DBN, and the inference lookup table."""
        cpds = self._build_cpds()
        cpd_compound = next(c for c in cpds if c.variable == "Compound_Risk")
        cpd_risk = next(c for c in cpds if c.variable == "Risk_State")

        self._model = DiscreteBayesianNetwork(_INTRA_EDGES)
        self._model.add_cpds(*cpds)
        if not self._model.check_model():
            raise ValueError("DiscreteBayesianNetwork failed validation")

        # Pre-compute lookup table: 972 parent combinations → Risk_State probs
        self._lookup_table = self._build_lookup_table(cpd_compound, cpd_risk)

        # Build DBN with explicit intra + inter slice edges to avoid pgmpy
        # initialize_initial_state() bug (hardcoded reshape to (2, -1)).
        dbn_all_edges = (
            [((u, 0), (v, 0)) for u, v in _INTRA_EDGES]
            + [((u, 1), (v, 1)) for u, v in _INTRA_EDGES]
            + [(("API_State", 0), ("API_State", 1))]
        )
        self._dbn = DynamicBayesianNetwork(dbn_all_edges)

        api_trans = np.array(self._cfg.api_transition)
        api_transition_cpd = TabularCPD(
            ("API_State", 1),
            3,
            api_trans,
            evidence=[("API_State", 0)],
            evidence_card=[3],
        )
        dbn_cpds_0 = [self._to_dbn_cpd(cpd, time_slice=0) for cpd in cpds]
        dbn_cpds_1 = [
            self._to_dbn_cpd(cpd, time_slice=1)
            for cpd in cpds
            if cpd.variable != "API_State"
        ]
        self._dbn.add_cpds(*dbn_cpds_0, *dbn_cpds_1, api_transition_cpd)
        if not self._dbn.check_model():
            raise ValueError("DynamicBayesianNetwork failed validation")
        self._dbn_inference = DBNInference(self._dbn)

        log.info("crma_model_built", cluster=self.cluster, nodes=len(self._model.nodes()))

    def infer(self, evidence: CRMAEvidence) -> dict[str, Any]:
        """Single-step flood risk inference via O(1) lookup table."""
        if self._lookup_table is None:
            raise RuntimeError("Model not built. Call build() first.")

        obs = evidence.to_obs_dict()
        probs = self._lookup_table[
            obs["Forecast_Hazard"],
            obs["Obs_Antecedent"],
            obs["Temporal_Persist"],
            obs["Spatial_Coverage"],
            obs["Data_Confidence"],
            obs["API_State"],
            obs["Soil_Memory"],
        ]
        risk_state = int(np.argmax(probs))
        return self._format_result(risk_state, probs, obs)

    def infer_sequence(
        self,
        evidence_sequence: list[CRMAEvidence],
        initial_api_state: int = 0,
    ) -> list[dict[str, Any]]:
        """Multi-step inference over a temporal sequence via DBNInference.

        API_State is treated as a latent variable propagated through the
        inter-slice transition matrix; all other evidence is fully observed.
        Soil_Memory is derived from evidence.sat_consecutive_days at each step
        and is passed as observed evidence (not latent).

        Args:
            evidence_sequence:  Ordered list of CRMAEvidence (one per day).
            initial_api_state:  Discretised API_State at day 0 (0/1/2).
        """
        if self._dbn_inference is None:
            raise RuntimeError("Model not built. Call build() first.")

        n_steps = len(evidence_sequence)
        if n_steps == 0:
            return []

        dbn_evidence: dict[tuple[str, int], int] = {
            ("API_State", 0): initial_api_state,
        }
        for t, ev in enumerate(evidence_sequence):
            obs = ev.to_obs_dict()
            for node in (
                "Forecast_Hazard",
                "Obs_Antecedent",
                "Temporal_Persist",
                "Spatial_Coverage",
                "Data_Confidence",
                "Soil_Memory",
            ):
                dbn_evidence[(node, t)] = obs[node]

        query_vars = [("Risk_State", t) for t in range(n_steps)]
        results_raw = self._dbn_inference.forward_inference(query_vars, evidence=dbn_evidence)

        out: list[dict[str, Any]] = []
        for t, ev in enumerate(evidence_sequence):
            probs = results_raw[("Risk_State", t)].values
            risk_state = int(np.argmax(probs))
            out.append(self._format_result(risk_state, probs, ev.to_obs_dict()))
        return out

    def _build_lookup_table(self, cpd_compound: Any, cpd_risk: Any) -> np.ndarray:
        """Pre-compute Risk_State probabilities for all 972 parent combinations.

        Table shape: (3, 3, 2, 3, 3, 3, 2, 4) indexed by
        [forecast_hazard, obs_antecedent, temporal_persist,
         spatial_coverage, data_confidence, api_state, soil_memory] → risk_probs.
        """
        compound_cpt = cpd_compound.get_values()  # shape (4, 972)
        risk_cpt = cpd_risk.get_values()           # shape (4, 4)

        table = np.zeros((3, 3, 2, 3, 3, 3, 2, 4))
        for idx in range(compound_cpt.shape[1]):
            compound_probs = compound_cpt[:, idx]
            risk_probs = risk_cpt @ compound_probs
            states = self._idx_to_states(idx, _PARENT_CARDS)
            table[tuple(states)] = risk_probs

        return table

    @staticmethod
    def _to_dbn_cpd(cpd: Any, time_slice: int) -> Any:
        parents = cpd.variables[1:]
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
        """Return TabularCPD objects for the static BN, all values from config."""
        cfg = self._cfg

        cpd_forecast = TabularCPD("Forecast_Hazard", 3, [[0.50], [0.30], [0.20]])
        cpd_obs = TabularCPD("Obs_Antecedent", 3, [[0.40], [0.35], [0.25]])
        cpd_persist = TabularCPD("Temporal_Persist", 2, [[0.75], [0.25]])
        cpd_spatial = TabularCPD("Spatial_Coverage", 3, [[0.50], [0.30], [0.20]])
        cpd_confidence = TabularCPD("Data_Confidence", 3, [[0.10], [0.30], [0.60]])
        cpd_api = TabularCPD("API_State", 3, [[0.45], [0.35], [0.20]])
        cpd_soil_memory = TabularCPD("Soil_Memory", 2, [[0.80], [0.20]])

        cpd_compound = self._build_compound_risk_cpd(cfg)
        cpd_risk = TabularCPD(
            variable="Risk_State",
            variable_card=4,
            values=[
                [0.95, 0.50, 0.10, 0.02],
                [0.04, 0.40, 0.40, 0.08],
                [0.01, 0.08, 0.40, 0.30],
                [0.00, 0.02, 0.10, 0.60],
            ],
            evidence=["Compound_Risk"],
            evidence_card=[4],
        )
        return [
            cpd_forecast, cpd_obs, cpd_persist, cpd_spatial,
            cpd_confidence, cpd_api, cpd_soil_memory, cpd_compound, cpd_risk,
        ]

    def _build_compound_risk_cpd(self, cfg: CRMAModelConfig) -> Any:
        """Build the Compound_Risk CPT from a config-driven rule-based risk score.

        Each parent state contributes additively (0–10 scale).
        Data_Confidence dampens by [0.5, 0.8, 1.0] to reflect uncertainty.
        Soil_Memory selects between two CPT bucket sets:
          - fresh (soil_memory=0): standard thresholds
          - prolonged (soil_memory=1): lower thresholds → higher sensitivity,
            capturing the "15-day saturated soil + 50mm" scenario.
        3×3×2×3×3×3×2 = 972 parent state combinations.
        """
        n_combinations = 1
        for c in _PARENT_CARDS:
            n_combinations *= c

        w_map = {k: {"forecast": v.forecast, "obs": v.obs, "api": v.api}
                 for k, v in cfg.cluster_weights.items()}
        w = w_map.get(self.cluster, {"forecast": 2.0, "obs": 1.5, "api": 1.5})

        fresh_thr = cfg.compound_score_thresholds.fresh    # [low, mid, high]
        prol_thr = cfg.compound_score_thresholds.prolonged

        fresh_b = cfg.compound_cpt_buckets.fresh
        prol_b = cfg.compound_cpt_buckets.prolonged

        cpt = np.zeros((4, n_combinations))
        for idx in range(n_combinations):
            f_haz, obs_ant, t_persist, spatial, confidence, api, soil_mem = (
                self._idx_to_states(idx, _PARENT_CARDS)
            )

            score = (
                f_haz * w["forecast"]
                + obs_ant * w["obs"]
                + t_persist * 1.5
                + spatial * 1.0
                + api * w["api"]
            ) * [0.5, 0.8, 1.0][confidence]

            if soil_mem == 0:
                lo, mi, hi = fresh_thr
                if score <= lo:
                    cpt[:, idx] = fresh_b.low
                elif score <= mi:
                    cpt[:, idx] = fresh_b.mid
                elif score <= hi:
                    cpt[:, idx] = fresh_b.mod
                else:
                    cpt[:, idx] = fresh_b.high
            else:
                lo, mi, hi = prol_thr
                if score <= lo:
                    cpt[:, idx] = prol_b.low
                elif score <= mi:
                    cpt[:, idx] = prol_b.mid
                elif score <= hi:
                    cpt[:, idx] = prol_b.mod
                else:
                    cpt[:, idx] = prol_b.high

        return TabularCPD(
            variable="Compound_Risk",
            variable_card=4,
            values=cpt,
            evidence=_COMPOUND_PARENTS,
            evidence_card=_PARENT_CARDS,
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

    def load_cpts(self, path_or_dict: Path | dict) -> None:
        """Load CPTs from a JSON file (or pre-parsed dict) produced by save_cpts()."""
        if self._model is None:
            raise RuntimeError("Model not built first.")
        if isinstance(path_or_dict, dict):
            cpts = path_or_dict
        else:
            cpts = json.loads(path_or_dict.read_text())
        for node, values in cpts.items():
            cpd = self._model.get_cpds(node)
            if cpd is not None:
                cpd.values = np.array(values)
        cpd_compound = self._model.get_cpds("Compound_Risk")
        cpd_risk = self._model.get_cpds("Risk_State")
        self._lookup_table = self._build_lookup_table(cpd_compound, cpd_risk)
        log.info("cpts_loaded")
