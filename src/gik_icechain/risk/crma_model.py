"""CRMA Bayesian Network for admin-1 daily flood risk.

DAG: Forecast_Hazard, Obs_Antecedent, Temporal_Persist, Spatial_Coverage,
Data_Confidence, API_State, Soil_Memory -> Compound_Risk -> Risk_State.

Two inference paths:
  infer()          - O(1) lookup table. Production path (risk_engine.py).
                     API_State is observed via DynamicBNState.
  infer_sequence() - 2-slice DBN, stochastic API_State transition.
                     Built lazily on first call. Used for EM-DAT
                     retrospective validation (ablation test, AUC-ROC).

load_cpts() invalidates the lazy DBN cache so refined CPTs propagate.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
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
    from pgmpy.inference import DBNInference
    from pgmpy.models import DiscreteBayesianNetwork, DynamicBayesianNetwork

    PGMPY_AVAILABLE = True
except ImportError:
    PGMPY_AVAILABLE = False
    DBNInference = None  # type: ignore[assignment,misc]
    log.warning("pgmpy_not_installed", msg="pip install pgmpy")


RISK_LEVELS = {0: "Green", 1: "Yellow", 2: "Orange", 3: "Red"}
N_RISK_LEVELS = len(RISK_LEVELS)

NODE_CARDS: dict[str, int] = {
    "Forecast_Hazard": 4,  # Low / Medium / High / Extreme
    "Obs_Antecedent": 3,  # Below normal / Normal / Above normal
    "Temporal_Persist": 2,  # No / Yes
    "Spatial_Coverage": 3,  # Local / Regional / Extensive
    "Data_Confidence": 3,  # Low / Medium / High
    "API_State": 3,  # Dry / Normal / Saturated
    "Soil_Memory": 2,  # Recent (<N days sat) / Prolonged (≥N days sat)
    "Compound_Risk": 4,  # None / Low / Moderate / High
    "Risk_State": 4,  # Green / Yellow / Orange / Red
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


def _norm_cdf(z: float) -> float:
    """Standard normal CDF Φ(z) without a SciPy dependency."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _gaussian_soft_bin(x: float, cutoffs: list[float], sigma: float) -> np.ndarray:
    """Probability vector over ``len(cutoffs)+1`` states for a continuous obs *x*.

    State ``k`` covers the interval ``(edge_k, edge_{k+1}]`` with ``edges =
    [-inf, *cutoffs, +inf]``. Treating *x* as a Gaussian-noisy observation of the
    true value (σ = measurement bandwidth), the membership of each state is

        ``P(state_k) = Φ((edge_{k+1} - x)/σ) - Φ((edge_k - x)/σ)``.

    With ``sigma <= 0`` (or a non-finite *x*) this returns the one-hot hard
    classification (``x`` in state = number of cutoffs ≤ ``x``), so the soft path
    reduces exactly to the legacy ``>=``-threshold discretization.
    """
    k_states = len(cutoffs) + 1
    if not math.isfinite(x):
        v = np.zeros(k_states)
        v[1 if k_states > 1 else 0] = 1.0  # neutral middle on missing value
        return v
    if sigma <= 0:
        v = np.zeros(k_states)
        v[int(np.searchsorted(cutoffs, x, side="right"))] = 1.0
        return v

    edges = [-math.inf, *cutoffs, math.inf]
    probs = np.empty(k_states)
    for k in range(k_states):
        e_lo, e_hi = edges[k], edges[k + 1]
        c_hi = 1.0 if e_hi == math.inf else _norm_cdf((e_hi - x) / sigma)
        c_lo = 0.0 if e_lo == -math.inf else _norm_cdf((e_lo - x) / sigma)
        probs[k] = c_hi - c_lo
    total = probs.sum()
    return probs / total if total > 0 else probs


def _dist_of_max(p: np.ndarray, q: np.ndarray) -> np.ndarray:
    """Distribution of ``max(A, B)`` for independent categorical A~p, B~q.

    The soft generalization of ``max(frac_state, tail_state)``: it collapses to
    the hard max when *p* and *q* are one-hot.
    """
    p_cum = np.cumsum(p)
    q_cum = np.cumsum(q)
    out = np.empty(len(p))
    for k in range(len(p)):
        q_le = q_cum[k]
        p_lt = p_cum[k - 1] if k > 0 else 0.0
        out[k] = p[k] * q_le + q[k] * p_lt
    return out


def _cost_loss_state(
    probs: np.ndarray, tau_yellow: float, tau_orange: float, tau_red: float
) -> int:
    """Cost-loss decision label for a 4-level risk posterior.

    Assigns the *highest* tier whose cumulative (exceedance) posterior reaches
    that tier's cost-loss ratio C/L:

        Red    if P(≥Red)    = probs[3]                 ≥ tau_red
        Orange if P(≥Orange) = probs[2]+probs[3]        ≥ tau_orange
        Yellow if P(≥Yellow) = probs[1]+probs[2]+probs[3] ≥ tau_yellow
        else Green

    For anticipatory action C/L < 0.5, so triggers fire below the argmax's
    implicit ~0.5 — lifting recall / lead time at a controlled false-alarm
    cost (Murphy 1977; Coughlan de Perez et al. 2015). Checked most-severe
    first; with tau_yellow ≤ tau_orange ≤ tau_red the result is well-ordered.
    """
    p_red = float(probs[3])
    p_ge_orange = float(probs[2]) + p_red
    p_ge_yellow = float(probs[1]) + p_ge_orange
    if p_red >= tau_red:
        return 3
    if p_ge_orange >= tau_orange:
        return 2
    if p_ge_yellow >= tau_yellow:
        return 1
    return 0


@dataclass
class EvidenceThresholds:
    """Discretization thresholds for CRMAEvidence — instance fields (thread-safe).

    Defaults match configs/default.yaml (component3.crma_model).
    Use CRMAModel.make_evidence() to get instances pre-loaded from config.
    """

    gpm_normal_mmday: float = 5.0
    gpm_above_mmday: float = 25.0
    api_normal_mm: float = 30.0
    api_saturated_mm: float = 80.0
    spatial_regional: float = 0.25
    spatial_extensive: float = 0.75
    persist_threshold: int = 3
    soil_memory_days: int = 7
    hazard_medium_threshold: float = 0.15  # Low → Medium boundary for Forecast_Hazard
    hazard_high_threshold: float = 0.40  # Medium → High boundary for Forecast_Hazard
    hazard_extreme_threshold: float = 0.70  # High → Extreme boundary for Forecast_Hazard
    # Tail-aware Forecast_Hazard (possible-worlds): escalate on the ensemble tail
    # ratio even when the mean exceedance fraction is ~0. Disabled when
    # tail_aware_hazard is False (tail_*_ratio left at defaults but unused).
    tail_aware_hazard: bool = True
    tail_medium_ratio: float = 0.80
    tail_high_ratio: float = 1.00
    tail_extreme_ratio: float = 1.30
    # Gaussian soft-binning (virtual evidence). When False, evidence is hard.
    # Each sigma is a bandwidth in the node's native units; 0 → hard for that node.
    soft_evidence: bool = False
    sigma_forecast: float = 0.05
    sigma_tail: float = 0.07
    sigma_gpm: float = 5.0
    sigma_spatial: float = 0.08
    sigma_api: float = 10.0


@dataclass
class CRMAEvidence:
    """Evidence observed on a given day for one admin-1 unit.

    Exceedance probabilities are RP-agnostic: they refer to whatever return
    period the caller selected (``rp_years``). Discretization into the
    Forecast_Hazard node is calibrated per return period via
    ``thresholds.hazard_medium_threshold`` / ``hazard_high_threshold``
    (see CRMAModelConfig.hazard_thresholds_by_rp) — a 2yr exceedance is
    structurally higher than a 5yr one, so each RP gets its own boundaries.

    All discretization thresholds are grouped in ``thresholds``
    (:class:`EvidenceThresholds`).  Defaults match configs/default.yaml
    (component3.crma_model).
    """

    exceedance_prob_24h: float
    exceedance_prob_72h: float
    exceedance_prob_7d: float
    gpm_obs_24h: float
    api_mm: float
    spatial_coverage_fraction: float
    consecutive_signal_days: int
    sat_consecutive_days: int = 0
    # Possible-worlds tail signal: p95 member accumulation / GEV return level
    # (max over the 24h/72h windows). 0.0 = no tail signal / tail-risk disabled.
    forecast_tail_ratio: float = 0.0
    gpm_quality: int = 2
    gpm_missing: bool = False
    rp_years: int = 5
    thresholds: EvidenceThresholds = field(default_factory=EvidenceThresholds)

    def __post_init__(self) -> None:
        for fld, val in (
            ("exceedance_prob_24h", self.exceedance_prob_24h),
            ("exceedance_prob_72h", self.exceedance_prob_72h),
            ("exceedance_prob_7d", self.exceedance_prob_7d),
        ):
            if not (0.0 <= val <= 1.0):
                raise ValueError(f"{fld}={val!r} must be in [0, 1]")
        if self.api_mm < 0:
            raise ValueError(f"api_mm={self.api_mm!r} must be >= 0")
        if not (0.0 <= self.spatial_coverage_fraction <= 1.0):
            raise ValueError(
                f"spatial_coverage_fraction={self.spatial_coverage_fraction!r} must be in [0, 1]"
            )

    @property
    def forecast_hazard_state(self) -> int:
        """0=Low, 1=Medium, 2=High, 3=Extreme.

        The state is the max of two complementary views of the forecast:

        - **Fraction (expected-value)**: the share of ensemble members above the
          GEV threshold (``exceedance_prob_*``). Strong when the ensemble agrees.
        - **Tail (possible-worlds)**: the high-quantile member accumulation as a
          ratio to the return level (``forecast_tail_ratio``). Strong when a
          narrow but extreme tail exists even if the mean fraction is ~0 — the
          convective wet-tail an ensemble-mean trigger is blind to.

        Taking the max means either an agreeing ensemble *or* a credible extreme
        member escalates the hazard.
        """
        t = self.thresholds
        p = max(self.exceedance_prob_24h, self.exceedance_prob_72h)
        if p >= t.hazard_extreme_threshold:
            frac_state = 3
        elif p >= t.hazard_high_threshold:
            frac_state = 2
        elif p >= t.hazard_medium_threshold:
            frac_state = 1
        else:
            frac_state = 0

        if not t.tail_aware_hazard:
            return frac_state

        r = self.forecast_tail_ratio
        if r >= t.tail_extreme_ratio:
            tail_state = 3
        elif r >= t.tail_high_ratio:
            tail_state = 2
        elif r >= t.tail_medium_ratio:
            tail_state = 1
        else:
            tail_state = 0

        return max(frac_state, tail_state)

    @property
    def obs_antecedent_state(self) -> int:
        """0=Below normal, 1=Normal, 2=Above normal.

        When the GPM observation is missing, return Normal (neutral) instead of
        deriving "Below normal" from a placeholder 0 mm — an absent observation
        is not evidence of a dry day.
        """
        if self.gpm_missing:
            return 1
        if self.gpm_obs_24h >= self.thresholds.gpm_above_mmday:
            return 2
        if self.gpm_obs_24h >= self.thresholds.gpm_normal_mmday:
            return 1
        return 0

    @property
    def temporal_persistence_state(self) -> int:
        """0=No, 1=Yes (≥ persist_threshold days with signal)."""
        return int(self.consecutive_signal_days >= self.thresholds.persist_threshold)

    @property
    def spatial_coverage_state(self) -> int:
        """0=Local, 1=Regional, 2=Extensive."""
        if self.spatial_coverage_fraction >= self.thresholds.spatial_extensive:
            return 2
        if self.spatial_coverage_fraction >= self.thresholds.spatial_regional:
            return 1
        return 0

    @property
    def data_confidence_state(self) -> int:
        """0=Low, 1=Medium, 2=High.

        A missing GPM observation forces Low confidence so the compound score is
        dampened to reflect the absent evidence.
        """
        if self.gpm_missing:
            return 0
        return min(self.gpm_quality, 2)

    @property
    def api_state(self) -> int:
        """0=Dry, 1=Normal, 2=Saturated."""
        if self.api_mm >= self.thresholds.api_saturated_mm:
            return 2
        if self.api_mm >= self.thresholds.api_normal_mm:
            return 1
        return 0

    @property
    def soil_memory_state(self) -> int:
        """0=Recent, 1=Prolonged (≥ soil_memory_days of consecutive saturation)."""
        return int(self.sat_consecutive_days >= self.thresholds.soil_memory_days)

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

    # --- Soft (virtual) evidence: probability vector per node ---------------
    # Continuous-derived nodes are Gaussian soft-binned; count/quality-derived
    # nodes stay one-hot. With soft_evidence disabled (or all sigma = 0) every
    # vector is one-hot and ``to_soft_obs`` reproduces ``to_obs_dict`` exactly.

    @staticmethod
    def _onehot(state: int, k_states: int) -> np.ndarray:
        v = np.zeros(k_states)
        v[state] = 1.0
        return v

    def forecast_hazard_dist(self) -> np.ndarray:
        """Soft distribution over the 4 Forecast_Hazard states.

        Soft generalization of ``forecast_hazard_state``: the distribution of
        ``max(fraction_state, tail_state)`` where each is Gaussian soft-binned.
        """
        t = self.thresholds
        if not t.soft_evidence:
            return self._onehot(self.forecast_hazard_state, 4)
        p = max(self.exceedance_prob_24h, self.exceedance_prob_72h)
        frac = _gaussian_soft_bin(
            p,
            [t.hazard_medium_threshold, t.hazard_high_threshold, t.hazard_extreme_threshold],
            t.sigma_forecast,
        )
        if not t.tail_aware_hazard:
            return frac
        tail = _gaussian_soft_bin(
            self.forecast_tail_ratio,
            [t.tail_medium_ratio, t.tail_high_ratio, t.tail_extreme_ratio],
            t.sigma_tail,
        )
        return _dist_of_max(frac, tail)

    def obs_antecedent_dist(self) -> np.ndarray:
        t = self.thresholds
        if self.gpm_missing:
            return np.array([0.0, 1.0, 0.0])  # neutral Normal on missing obs
        if not t.soft_evidence:
            return self._onehot(self.obs_antecedent_state, 3)
        return _gaussian_soft_bin(
            self.gpm_obs_24h, [t.gpm_normal_mmday, t.gpm_above_mmday], t.sigma_gpm
        )

    def spatial_coverage_dist(self) -> np.ndarray:
        t = self.thresholds
        if not t.soft_evidence:
            return self._onehot(self.spatial_coverage_state, 3)
        return _gaussian_soft_bin(
            self.spatial_coverage_fraction,
            [t.spatial_regional, t.spatial_extensive],
            t.sigma_spatial,
        )

    def api_state_dist(self) -> np.ndarray:
        t = self.thresholds
        if not t.soft_evidence:
            return self._onehot(self.api_state, 3)
        return _gaussian_soft_bin(
            self.api_mm, [t.api_normal_mm, t.api_saturated_mm], t.sigma_api
        )

    def to_soft_obs(self) -> dict[str, np.ndarray]:
        """Per-node probability vectors for soft-evidence marginalization."""
        return {
            "Forecast_Hazard": self.forecast_hazard_dist(),
            "Obs_Antecedent": self.obs_antecedent_dist(),
            "Temporal_Persist": self._onehot(self.temporal_persistence_state, 2),
            "Spatial_Coverage": self.spatial_coverage_dist(),
            "Data_Confidence": self._onehot(self.data_confidence_state, 3),
            "API_State": self.api_state_dist(),
            "Soil_Memory": self._onehot(self.soil_memory_state, 2),
        }


class CRMAModel:
    """ICPAC CRMA Bayesian Network for East Africa flood risk.

    Single-step inference uses a pre-computed lookup table (O(1) per call).
    Shape: (3, 3, 2, 3, 3, 3, 2, 4) → 972 parent combos × 4 risk states.

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
        self._model: Any = None  # DiscreteBayesianNetwork
        self._dbn: Any = None  # DynamicBayesianNetwork — built lazily
        self._dbn_inference: Any = None  # DBNInference — built lazily
        # Shape: (3, 3, 2, 3, 3, 3, 2, 4) — O(1) single-step inference
        self._lookup_table: np.ndarray | None = None
        self._cpt_path = cpt_path
        self._cfg = crma_cfg or self._default_cfg()

    @staticmethod
    def _default_cfg() -> CRMAModelConfig:
        from gik_icechain.shared.config import CRMAModelConfig

        return CRMAModelConfig()

    def evidence_thresholds(self, rp: int | None = None) -> EvidenceThresholds:
        """Build EvidenceThresholds from the live config, calibrated for *rp*.

        The Forecast_Hazard boundaries come from ``hazard_thresholds_by_rp``
        when the return period is listed there; otherwise the global
        ``hazard_medium_threshold`` / ``hazard_high_threshold`` apply. This is
        what makes the 2yr risk view calibrated rather than a re-use of the
        5yr boundaries on structurally higher 2yr exceedances.
        """
        cfg = self._cfg
        medium, high = cfg.hazard_medium_threshold, cfg.hazard_high_threshold
        if rp is not None and rp in cfg.hazard_thresholds_by_rp:
            medium, high = cfg.hazard_thresholds_by_rp[rp]
        extreme = cfg.hazard_extreme_threshold
        if rp is not None:
            extreme = cfg.hazard_extreme_by_rp.get(rp, extreme)
        return EvidenceThresholds(
            gpm_normal_mmday=cfg.gpm_obs_normal_mmday,
            gpm_above_mmday=cfg.gpm_obs_above_mmday,
            api_normal_mm=cfg.api_threshold_normal_mm,
            api_saturated_mm=cfg.api_threshold_saturated_mm,
            spatial_regional=cfg.spatial_threshold_regional,
            spatial_extensive=cfg.spatial_threshold_extensive,
            persist_threshold=cfg.consecutive_signal_threshold,
            soil_memory_days=cfg.soil_memory_days,
            hazard_medium_threshold=medium,
            hazard_high_threshold=high,
            hazard_extreme_threshold=extreme,
            tail_aware_hazard=cfg.tail_aware_hazard,
            tail_medium_ratio=cfg.tail_medium_ratio,
            tail_high_ratio=cfg.tail_high_ratio,
            tail_extreme_ratio=cfg.tail_extreme_ratio,
            soft_evidence=cfg.soft_evidence.enabled,
            sigma_forecast=cfg.soft_evidence.sigma_forecast,
            sigma_tail=cfg.soft_evidence.sigma_tail,
            sigma_gpm=cfg.soft_evidence.sigma_gpm,
            sigma_spatial=cfg.soft_evidence.sigma_spatial,
            sigma_api=cfg.soft_evidence.sigma_api,
        )

    def make_evidence(self, rp: int | None = None, **kwargs: object) -> CRMAEvidence:
        """Create a CRMAEvidence pre-loaded with this model's config thresholds.

        Keyword arguments are forwarded directly to CRMAEvidence, which still
        requires the mandatory positional fields.  Use this factory in production
        code to guarantee that threshold values always match the live config.
        """
        if rp is not None:
            kwargs.setdefault("rp_years", rp)
        return CRMAEvidence(
            thresholds=self.evidence_thresholds(rp),
            **kwargs,  # type: ignore[arg-type]
        )

    def get_pgmpy_model(self) -> Any:
        """Return the underlying pgmpy DiscreteBayesianNetwork (public API).

        Raises RuntimeError if the model has not been built yet.
        """
        if self._model is None:
            raise RuntimeError("Model not built. Call build() first.")
        return self._model

    def build(self) -> None:
        """Build the static BN and pre-compute the O(1) lookup table.

        The DBN for infer_sequence() is NOT built here — it is constructed
        lazily on first call via _ensure_dbn(), since infer() (lookup table)
        is the only path used in production batch processing.
        """
        cpds = self._build_cpds()
        self._model = DiscreteBayesianNetwork(_INTRA_EDGES)
        self._model.add_cpds(*cpds)
        if not self._model.check_model():
            raise ValueError("CRMA BayesianNetwork failed validation")

        cpd_compound = self._model.get_cpds("Compound_Risk")
        cpd_risk = self._model.get_cpds("Risk_State")
        self._lookup_table = self._build_lookup_table(cpd_compound, cpd_risk)
        self._dbn = None
        self._dbn_inference = None

        log.info("crma_model_built", cluster=self.cluster, nodes=len(self._model.nodes()))

    def _ensure_dbn(self) -> None:
        """Lazily build the DynamicBayesianNetwork for infer_sequence().

        Builds once and caches. Invalidated by load_cpts() so refined CPTs
        propagate to subsequent infer_sequence() calls.
        """
        if self._dbn_inference is not None:
            return
        if self._model is None:
            raise RuntimeError("Model not built. Call build() first.")

        cpds = self._model.get_cpds()

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
            self._to_dbn_cpd(cpd, time_slice=1) for cpd in cpds if cpd.variable != "API_State"
        ]
        self._dbn.add_cpds(*dbn_cpds_0, *dbn_cpds_1, api_transition_cpd)

        if not self._dbn.check_model():
            raise ValueError("DynamicBayesianNetwork failed validation")

        self._dbn_inference = DBNInference(self._dbn)
        log.debug("dbn_built_lazily", cluster=self.cluster)

    def infer(self, evidence: CRMAEvidence) -> dict[str, Any]:
        """Single-step flood risk inference.

        Dispatches to the soft-evidence marginalization when
        ``soft_evidence.enabled`` (config), otherwise the O(1) hard lookup.
        Both share the same pre-computed lookup table.
        """
        if self._lookup_table is None:
            raise RuntimeError("Model not built. Call build() first.")

        if self._cfg.soft_evidence.enabled:
            return self._infer_soft(evidence)

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
        risk_state = self._decide_risk_state(probs)
        return self._format_result(risk_state, probs, obs)

    def _infer_soft(self, evidence: CRMAEvidence) -> dict[str, Any]:
        """Soft-evidence inference: weighted marginal of the lookup table.

        ``P(risk) = Σ_states Π_k p_k(state) · P(risk | states)`` — contract each
        parent axis of the lookup table with that parent's soft probability
        vector. Under one-hot vectors this equals the hard lookup exactly.
        """
        soft = evidence.to_soft_obs()
        assert self._lookup_table is not None  # guarded by infer()
        res: np.ndarray = self._lookup_table
        for node in _COMPOUND_PARENTS:
            res = np.tensordot(soft[node], res, axes=([0], [0]))
        probs = res  # shape (N_RISK_LEVELS,)
        risk_state = self._decide_risk_state(probs)
        return self._format_result(risk_state, probs, evidence.to_obs_dict())

    def infer_sequence(
        self,
        evidence_sequence: list[CRMAEvidence],
        initial_api_state: int = 0,
    ) -> list[dict[str, Any]]:
        """Multi-step inference via DBNInference. Used for retrospective validation.

        API_State is latent and propagated via the api_transition matrix.
        The DBN is built lazily on first call and cached.
        """
        self._ensure_dbn()

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
            risk_state = self._decide_risk_state(probs)
            out.append(self._format_result(risk_state, probs, ev.to_obs_dict()))
        return out

    def _build_lookup_table(self, cpd_compound: Any, cpd_risk: Any) -> np.ndarray:
        """Pre-compute Risk_State probabilities for all 972 parent combinations.

        Table shape: (3, 3, 2, 3, 3, 3, 2, 4) indexed by
        [forecast_hazard, obs_antecedent, temporal_persist,
         spatial_coverage, data_confidence, api_state, soil_memory] → risk_probs.
        """
        compound_cpt = cpd_compound.get_values()  # shape (4, 972)
        risk_cpt = cpd_risk.get_values()  # shape (4, 4)

        table = np.zeros((*_PARENT_CARDS, N_RISK_LEVELS))
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

    def _decide_risk_state(self, probs: np.ndarray) -> int:
        """Map a risk posterior to a label.

        Cost-loss tiering when ``cost_loss.enabled`` (highest tier whose
        cumulative posterior reaches its C/L ratio), otherwise argmax. Under
        the default (disabled) config this is exactly ``argmax(probs)``.
        """
        cl = self._cfg.cost_loss
        if cl.enabled:
            return _cost_loss_state(probs, cl.tau_yellow, cl.tau_orange, cl.tau_red)
        return int(np.argmax(probs))

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

        cpd_forecast = TabularCPD("Forecast_Hazard", 4, [[0.50], [0.28], [0.15], [0.07]])
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
            cpd_forecast,
            cpd_obs,
            cpd_persist,
            cpd_spatial,
            cpd_confidence,
            cpd_api,
            cpd_soil_memory,
            cpd_compound,
            cpd_risk,
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

        w_map = {
            k: {"forecast": v.forecast, "obs": v.obs, "api": v.api}
            for k, v in cfg.cluster_weights.items()
        }
        w = w_map.get(self.cluster, {"forecast": 2.0, "obs": 1.5, "api": 1.5})

        fresh_thr = cfg.compound_score_thresholds.fresh  # [low, mid, high]
        prol_thr = cfg.compound_score_thresholds.prolonged
        # Data_Confidence dampening [Low, Medium, High] — config-driven so the
        # common Medium case for precip ensembles does not veto strong signals.
        damping = list(cfg.confidence_damping)

        fresh_b = cfg.compound_cpt_buckets.fresh
        prol_b = cfg.compound_cpt_buckets.prolonged

        cpt = np.zeros((4, n_combinations))
        for idx in range(n_combinations):
            f_haz, obs_ant, t_persist, spatial, confidence, api, soil_mem = self._idx_to_states(
                idx, _PARENT_CARDS
            )

            # Data_Confidence dampens only the observation branch, not forecast.
            score_forecast = f_haz * w["forecast"]
            score_obs = (
                obs_ant * w["obs"]
                + t_persist * cfg.weight_temporal_persist
                + spatial * cfg.weight_spatial_coverage
                + api * w["api"]
            )
            if self._cfg.confidence_damps_forecast:
                score = (score_forecast + score_obs) * damping[confidence]
            else:
                score = score_forecast + score_obs * damping[confidence]

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
        """Load CPTs from a JSON file (or pre-parsed dict) produced by save_cpts().

        Rebuilds the lookup table and invalidates the DBN cache so the next
        call to infer_sequence() picks up the updated CPDs.
        """
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
        self._dbn = None
        self._dbn_inference = None
        log.info("cpts_loaded")
