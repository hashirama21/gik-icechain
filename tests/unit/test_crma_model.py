"""
tests/unit/test_crma_model.py
Unit tests for the CRMA Bayesian Network model.
"""

import pytest

from gik_icechain.risk.crma_model import (
    RISK_LEVELS,
    CRMAEvidence,
    CRMAModel,
    EastAfricaCluster,
)


class TestCRMAEvidenceDiscretisation:
    def _make_evidence(self, **kwargs) -> CRMAEvidence:
        defaults = dict(
            exceedance_prob_24h_5y=0.0,
            exceedance_prob_72h_5y=0.0,
            exceedance_prob_7d_5y=0.0,
            gpm_obs_24h=0.0,
            api_mm=15.0,
            spatial_coverage_fraction=0.1,
            consecutive_signal_days=0,
        )
        defaults.update(kwargs)
        return CRMAEvidence(**defaults)

    def test_low_forecast_hazard(self):
        e = self._make_evidence(exceedance_prob_24h_5y=0.05)
        assert e.forecast_hazard_state == 0

    def test_medium_forecast_hazard(self):
        e = self._make_evidence(exceedance_prob_24h_5y=0.20)
        assert e.forecast_hazard_state == 1

    def test_high_forecast_hazard(self):
        e = self._make_evidence(exceedance_prob_24h_5y=0.50)
        assert e.forecast_hazard_state == 2

    def test_below_normal_obs(self):
        e = self._make_evidence(gpm_obs_24h=1.0)
        assert e.obs_antecedent_state == 0

    def test_normal_obs(self):
        e = self._make_evidence(gpm_obs_24h=10.0)
        assert e.obs_antecedent_state == 1

    def test_above_normal_obs(self):
        e = self._make_evidence(gpm_obs_24h=30.0)
        assert e.obs_antecedent_state == 2

    def test_no_persistence(self):
        e = self._make_evidence(consecutive_signal_days=2)
        assert e.temporal_persistence_state == 0

    def test_yes_persistence(self):
        e = self._make_evidence(consecutive_signal_days=3)
        assert e.temporal_persistence_state == 1

    def test_dry_api(self):
        e = self._make_evidence(api_mm=10.0)
        assert e.api_state == 0

    def test_normal_api(self):
        e = self._make_evidence(api_mm=50.0)
        assert e.api_state == 1

    def test_saturated_api(self):
        e = self._make_evidence(api_mm=100.0)
        assert e.api_state == 2

    def test_local_spatial(self):
        e = self._make_evidence(spatial_coverage_fraction=0.20)
        assert e.spatial_coverage_state == 0

    def test_regional_spatial(self):
        e = self._make_evidence(spatial_coverage_fraction=0.50)
        assert e.spatial_coverage_state == 1

    def test_extensive_spatial(self):
        e = self._make_evidence(spatial_coverage_fraction=0.80)
        assert e.spatial_coverage_state == 2



@pytest.fixture(scope="module")
def built_model():
    """Build the CRMA model once for all tests (slow to construct)."""
    pytest.importorskip("pgmpy", reason="pgmpy not installed")
    model = CRMAModel(cluster=EastAfricaCluster.EQUATORIAL_EAST)
    model.build()
    return model


class TestCRMAModelInference:
    def _make_evidence(self, **kwargs) -> CRMAEvidence:
        defaults = dict(
            exceedance_prob_24h_5y=0.0,
            exceedance_prob_72h_5y=0.0,
            exceedance_prob_7d_5y=0.0,
            gpm_obs_24h=0.0,
            api_mm=15.0,
            spatial_coverage_fraction=0.1,
            consecutive_signal_days=0,
        )
        defaults.update(kwargs)
        return CRMAEvidence(**defaults)

    def test_low_risk_scenario(self, built_model):
        """No signal at all → should return Green or Yellow."""
        evidence = self._make_evidence()
        result = built_model.infer(evidence)
        assert result["risk_state"] in (0, 1), f"Expected low risk, got {result['risk_label']}"

    def test_high_risk_scenario(self, built_model):
        """Severe signal across all nodes → should return Orange or Red."""
        evidence = self._make_evidence(
            exceedance_prob_24h_5y=0.80,
            exceedance_prob_72h_5y=0.75,
            gpm_obs_24h=50.0,
            api_mm=120.0,  # saturated
            spatial_coverage_fraction=0.90,
            consecutive_signal_days=5,
        )
        result = built_model.infer(evidence)
        assert result["risk_state"] in (2, 3), f"Expected high risk, got {result['risk_label']}"

    def test_probabilities_sum_to_one(self, built_model):
        evidence = self._make_evidence(exceedance_prob_24h_5y=0.3, gpm_obs_24h=15.0)
        result = built_model.infer(evidence)
        total = result["p_green"] + result["p_yellow"] + result["p_orange"] + result["p_red"]
        assert abs(total - 1.0) < 1e-6, f"Probabilities don't sum to 1: {total}"

    def test_risk_monotonic_with_hazard(self, built_model):
        """Higher forecast hazard → should produce higher expected risk state."""
        low = self._make_evidence(exceedance_prob_24h_5y=0.05)
        high = self._make_evidence(exceedance_prob_24h_5y=0.80)

        result_low = built_model.infer(low)
        result_high = built_model.infer(high)

        expected_low = result_low["p_orange"] + result_low["p_red"]
        expected_high = result_high["p_orange"] + result_high["p_red"]

        assert expected_high > expected_low, (
            "Higher hazard should produce higher Orange+Red probability"
        )

    def test_api_increases_risk(self, built_model):
        """Saturated soil (API=120) should increase risk vs dry soil (API=10)."""
        dry = self._make_evidence(exceedance_prob_24h_5y=0.25, api_mm=10.0)
        sat = self._make_evidence(exceedance_prob_24h_5y=0.25, api_mm=120.0)

        result_dry = built_model.infer(dry)
        result_sat = built_model.infer(sat)

        risk_dry = result_dry["p_orange"] + result_dry["p_red"]
        risk_sat = result_sat["p_orange"] + result_sat["p_red"]

        assert risk_sat >= risk_dry, "Saturated soil should not reduce flood risk"

    def test_result_keys(self, built_model):
        evidence = self._make_evidence()
        result = built_model.infer(evidence)
        expected_keys = {
            "risk_state",
            "risk_label",
            "p_green",
            "p_yellow",
            "p_orange",
            "p_red",
            "evidence",
        }
        assert expected_keys.issubset(result.keys())

    def test_risk_label_matches_state(self, built_model):
        evidence = self._make_evidence()
        result = built_model.infer(evidence)
        assert RISK_LEVELS[result["risk_state"]] == result["risk_label"]

    def test_saturated_api_increases_risk_vs_dry(self, built_model):
        """API_State=Saturated must not produce lower risk than API_State=Dry."""
        from gik_icechain.risk.dynamic_bn import init_state
        from gik_icechain.risk.dynamic_bn import step as bn_step

        shared_kwargs = dict(
            exceedance_prob_24h_5y=0.25,
            exceedance_prob_72h_5y=0.20,
            exceedance_prob_7d_5y=0.15,
            gpm_obs_24h=15.0,
            spatial_coverage_fraction=0.5,
            consecutive_signal_days=1,
        )
        ev_dry = self._make_evidence(api_mm=5.0, **shared_kwargs)
        ev_sat = self._make_evidence(api_mm=120.0, **shared_kwargs)

        state_dry = init_state(5.0)
        state_sat = init_state(120.0)

        result_dry, _ = bn_step(state_dry, ev_dry, built_model)
        result_sat, _ = bn_step(state_sat, ev_sat, built_model)

        risk_dry = result_dry["p_orange"] + result_dry["p_red"]
        risk_sat = result_sat["p_orange"] + result_sat["p_red"]
        assert risk_sat >= risk_dry, "Saturated soil must not reduce flood risk"

    def test_horn_arid_cluster_higher_api_weight(self):
        """Horn-arid cluster must weight API more heavily than equatorial."""
        from gik_icechain.risk.crma_model import _CLUSTER_WEIGHTS, EastAfricaCluster

        eq = _CLUSTER_WEIGHTS[EastAfricaCluster.EQUATORIAL_EAST]
        horn = _CLUSTER_WEIGHTS[EastAfricaCluster.HORN_ARID]
        assert horn["api"] > eq["api"]
        assert horn["forecast"] >= eq["forecast"]
