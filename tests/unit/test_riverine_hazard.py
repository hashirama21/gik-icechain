"""Unit tests for riverine-aware Forecast_Hazard (roadmap Item 1).

An upstream/riverine ratio escalates Forecast_Hazard even when local exceedance
is ~0  the catchment-routed floods (Shabelle/Juba, Sudd) a local-rainfall
trigger misses. Mirrors the existing tail-aware escalation.
"""

import pytest

from gik_icechain.risk.crma_model import (
    CRMAModel,
    EastAfricaCluster,
    EvidenceThresholds,
    severity_index,
)
from gik_icechain.shared.config import CRMAModelConfig


def _thr(**kw) -> EvidenceThresholds:
    return EvidenceThresholds(riverine_aware_hazard=True, **kw)


class TestRiverineHazardState:
    def test_disabled_ignores_riverine(self, make_evidence):
        ev = make_evidence(
            exceedance_prob_24h=0.0,
            riverine_ratio=1.40,
            thresholds=EvidenceThresholds(riverine_aware_hazard=False),
        )
        assert ev.forecast_hazard_state == 0

    def test_riverine_escalates_from_zero_local(self, make_evidence):
        # Local exceedance 0, strong upstream signal → Extreme hazard.
        ev = make_evidence(exceedance_prob_24h=0.0, riverine_ratio=1.40, thresholds=_thr())
        assert ev.forecast_hazard_state == 3

    def test_riverine_bands(self, make_evidence):
        for ratio, expected in [(0.5, 0), (0.85, 1), (1.05, 2), (1.35, 3)]:
            ev = make_evidence(exceedance_prob_24h=0.0, riverine_ratio=ratio, thresholds=_thr())
            assert ev.forecast_hazard_state == expected

    def test_takes_max_of_local_and_riverine(self, make_evidence):
        # Strong local, weak riverine → local wins (no downgrade).
        ev = make_evidence(exceedance_prob_24h=0.99, riverine_ratio=0.0, thresholds=_thr())
        assert ev.forecast_hazard_state == 3

    def test_soft_dist_escalates(self, make_evidence):
        ev = make_evidence(
            exceedance_prob_24h=0.0,
            riverine_ratio=1.40,
            thresholds=_thr(soft_evidence=True),
        )
        dist = ev.forecast_hazard_dist()
        assert dist.argmax() == 3 and dist.sum() == pytest.approx(1.0)


class TestRiverineInModel:
    def _model(self, enabled: bool) -> CRMAModel:
        m = CRMAModel(
            EastAfricaCluster.NILE_BASIN,
            crma_cfg=CRMAModelConfig(riverine_aware_hazard=enabled),
        )
        m.build()
        return m

    def test_riverine_flood_lifts_risk_above_green(self, make_evidence):
        # Same evidence is Green without riverine awareness, an alert with it.
        off = self._model(enabled=False)
        on = self._model(enabled=True)
        kw = dict(exceedance_prob_24h=0.0, riverine_ratio=1.40, spatial_coverage_fraction=0.6)
        assert (
            off.infer(make_evidence(thresholds=off.evidence_thresholds(5), **kw))["risk_state"] == 0
        )
        assert (
            on.infer(make_evidence(thresholds=on.evidence_thresholds(5), **kw))["risk_state"] >= 1
        )

    def test_riverine_reaches_orange_under_cost_loss(self, make_evidence):
        from gik_icechain.shared.config import CostLossConfig

        cfg = CRMAModelConfig(
            riverine_aware_hazard=True,
            cost_loss=CostLossConfig(enabled=True, tau_yellow=0.05, tau_orange=0.10, tau_red=0.20),
        )
        m = CRMAModel(EastAfricaCluster.NILE_BASIN, crma_cfg=cfg)
        m.build()
        ev = make_evidence(
            exceedance_prob_24h=0.0,
            riverine_ratio=1.40,
            spatial_coverage_fraction=0.8,
            thresholds=m.evidence_thresholds(5),
        )
        assert m.infer(ev)["risk_state"] >= 2

    def test_backward_compatible_when_disabled(self, make_evidence):
        m = self._model(enabled=False)
        ev = make_evidence(
            exceedance_prob_24h=0.0,
            riverine_ratio=1.40,
            thresholds=m.evidence_thresholds(5),
        )
        assert m.infer(ev)["risk_state"] == 0

    def test_severity_reflects_riverine(self):
        cfg = CRMAModelConfig(riverine_aware_hazard=True)
        dry = _EvidenceStub(riverine_ratio=0.0)
        wet = _EvidenceStub(riverine_ratio=cfg.riverine_extreme_ratio)
        assert severity_index(wet, cfg) > severity_index(dry, cfg)


class _EvidenceStub:
    """Minimal duck-typed evidence for severity_index."""

    def __init__(self, riverine_ratio: float) -> None:
        self.exceedance_prob_24h = 0.0
        self.exceedance_prob_72h = 0.0
        self.exceedance_prob_7d = 0.0
        self.forecast_tail_ratio = 0.0
        self.spatial_coverage_fraction = 0.0
        self.api_mm = 0.0
        self.riverine_ratio = riverine_ratio


class TestRiverineConfigValidation:
    def test_unordered_ratios_rejected(self):
        with pytest.raises(ValueError, match="riverine ratios"):
            CRMAModelConfig(riverine_medium_ratio=1.0, riverine_high_ratio=0.5)
