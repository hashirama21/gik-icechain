"""Unit tests for the continuous severity gradation (roadmap Item 2).

The discrete Risk_State CPT collapses continuous forecast magnitude into a few
``p_red`` values (empirically ~0.39 for every Red), erasing any hierarchy
between events. ``severity_index`` restores a monotone [0, 1] ordering, exposed
as ``severity_score`` alongside the (unchanged) risk label.
"""

import math

import pytest

from gik_icechain.risk.crma_model import (
    CRMAModel,
    EastAfricaCluster,
    severity_index,
)
from gik_icechain.shared.config import (
    CostLossConfig,
    CRMAModelConfig,
    SeverityGradationConfig,
)


class TestSeverityIndex:
    def test_dry_day_is_zero(self, make_evidence):
        cfg = CRMAModelConfig()
        dry = make_evidence(spatial_coverage_fraction=0.0, api_mm=0.0)
        assert severity_index(dry, cfg) == pytest.approx(0.0, abs=1e-9)

    def test_full_signal_is_one(self, make_evidence):
        cfg = CRMAModelConfig()
        ev = make_evidence(
            exceedance_prob_24h=1.0,
            exceedance_prob_72h=1.0,
            exceedance_prob_7d=1.0,
            forecast_tail_ratio=cfg.tail_extreme_ratio,
            spatial_coverage_fraction=1.0,
            api_mm=cfg.api_threshold_saturated_mm,
        )
        assert severity_index(ev, cfg) == pytest.approx(1.0, abs=1e-9)

    def test_bounded_unit_interval(self, make_evidence):
        cfg = CRMAModelConfig()
        # Over-range unconstrained drivers (tail ratio, API mm) still clip to 1.
        ev = make_evidence(
            exceedance_prob_24h=1.0,
            spatial_coverage_fraction=1.0,
            forecast_tail_ratio=10 * cfg.tail_extreme_ratio,
            api_mm=10 * cfg.api_threshold_saturated_mm,
        )
        assert severity_index(ev, cfg) == pytest.approx(1.0, abs=1e-9)

    def test_monotone_in_exceedance(self, make_evidence):
        cfg = CRMAModelConfig()
        low = severity_index(make_evidence(exceedance_prob_24h=0.2), cfg)
        high = severity_index(make_evidence(exceedance_prob_24h=0.9), cfg)
        assert high > low

    def test_nan_tail_contributes_zero(self, make_evidence):
        # forecast_tail_ratio is unconstrained; a NaN must not poison the score.
        cfg = CRMAModelConfig()
        ev = make_evidence(exceedance_prob_24h=0.5, forecast_tail_ratio=float("nan"))
        s = severity_index(ev, cfg)
        finite = severity_index(make_evidence(exceedance_prob_24h=0.5), cfg)
        assert math.isfinite(s) and s == pytest.approx(finite)

    def test_weights_are_normalised(self, make_evidence):
        # Scaling all weights by a constant leaves the score unchanged.
        ev = make_evidence(exceedance_prob_24h=0.6, spatial_coverage_fraction=0.4)
        base = severity_index(ev, CRMAModelConfig())
        scaled = severity_index(
            ev,
            CRMAModelConfig(
                severity=SeverityGradationConfig(
                    w_exceedance=0.90, w_tail=0.50, w_spatial=0.40, w_api=0.20
                )
            ),
        )
        assert scaled == pytest.approx(base, abs=1e-9)


class TestSeverityWiring:
    def _model(self, cfg: CRMAModelConfig) -> CRMAModel:
        m = CRMAModel(EastAfricaCluster.NILE_BASIN, crma_cfg=cfg)
        m.build()
        return m

    def test_infer_emits_field_when_enabled(self, make_evidence):
        m = self._model(CRMAModelConfig())
        r = m.infer(make_evidence(exceedance_prob_24h=0.5))
        assert "severity_score" in r and 0.0 <= r["severity_score"] <= 1.0

    def test_infer_omits_field_when_disabled(self, make_evidence):
        cfg = CRMAModelConfig(severity=SeverityGradationConfig(enabled=False))
        r = self._model(cfg).infer(make_evidence(exceedance_prob_24h=0.5))
        assert "severity_score" not in r

    def test_restores_hierarchy_between_saturated_reds(self, make_evidence):
        # Two high-signal events whose discrete posterior may be identical must
        # still be ordered by severity_score.
        m = self._model(CRMAModelConfig())
        marginal = m.infer(make_evidence(exceedance_prob_24h=0.45, spatial_coverage_fraction=0.1))
        extreme = m.infer(
            make_evidence(
                exceedance_prob_24h=0.99,
                exceedance_prob_72h=0.99,
                spatial_coverage_fraction=0.9,
                forecast_tail_ratio=1.30,
                api_mm=80.0,
            )
        )
        assert extreme["severity_score"] > marginal["severity_score"]

    def test_severity_does_not_change_label(self, make_evidence):
        # Enabling gradation must not alter the discrete risk_state / probs.
        ev = make_evidence(exceedance_prob_24h=0.5, spatial_coverage_fraction=0.3)
        on = self._model(CRMAModelConfig()).infer(ev)
        off = self._model(CRMAModelConfig(severity=SeverityGradationConfig(enabled=False))).infer(
            ev
        )
        assert on["risk_state"] == off["risk_state"]
        assert on["p_red"] == pytest.approx(off["p_red"])


class TestSeverityRedSplit:
    """Item 3: decouple Orange/Red via the continuous severity score."""

    def _model(self, split: float) -> CRMAModel:
        cfg = CRMAModelConfig(
            cost_loss=CostLossConfig(
                enabled=True,
                tau_yellow=0.02,
                tau_orange=0.03,
                tau_red=0.05,
                severity_red_split=split,
            )
        )
        m = CRMAModel(EastAfricaCluster.NILE_BASIN, crma_cfg=cfg)
        m.build()
        return m

    def test_low_magnitude_red_demoted_to_orange(self, make_evidence):
        marginal = make_evidence(
            exceedance_prob_24h=0.42, spatial_coverage_fraction=0.0, api_mm=0.0
        )
        # Without the split a saturated posterior labels it Red...
        assert self._model(split=0.0).infer(marginal)["risk_state"] == 3
        # ...with the split it drops to Orange because magnitude is low.
        r = self._model(split=0.5).infer(marginal)
        assert r["risk_state"] == 2
        assert r["severity_score"] < 0.5

    def test_high_magnitude_stays_red(self, make_evidence):
        extreme = make_evidence(
            exceedance_prob_24h=0.99,
            exceedance_prob_72h=0.99,
            spatial_coverage_fraction=0.9,
            forecast_tail_ratio=1.30,
            api_mm=80.0,
        )
        r = self._model(split=0.5).infer(extreme)
        assert r["risk_state"] == 3
        assert r["severity_score"] >= 0.5

    def test_split_zero_is_legacy(self, make_evidence):
        marginal = make_evidence(
            exceedance_prob_24h=0.42, spatial_coverage_fraction=0.0, api_mm=0.0
        )
        assert self._model(split=0.0).infer(marginal)["risk_state"] == 3
