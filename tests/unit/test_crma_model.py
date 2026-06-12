"""
tests/unit/test_crma_model.py
Unit tests for the CRMA Bayesian Network model.
"""

import pytest

from gik_icechain.risk.crma_model import (
    _CLUSTER_WEIGHTS,
    RISK_LEVELS,
    CRMAModel,
    EastAfricaCluster,
    EvidenceThresholds,
)


class TestCRMAEvidenceDiscretisation:
    def test_low_forecast_hazard(self, make_evidence):
        e = make_evidence(exceedance_prob_24h=0.05)
        assert e.forecast_hazard_state == 0

    def test_medium_forecast_hazard(self, make_evidence):
        e = make_evidence(exceedance_prob_24h=0.20)
        assert e.forecast_hazard_state == 1

    def test_high_forecast_hazard(self, make_evidence):
        e = make_evidence(exceedance_prob_24h=0.50)
        assert e.forecast_hazard_state == 2

    def test_below_normal_obs(self, make_evidence):
        e = make_evidence(gpm_obs_24h=1.0)
        assert e.obs_antecedent_state == 0

    def test_normal_obs(self, make_evidence):
        e = make_evidence(gpm_obs_24h=10.0)
        assert e.obs_antecedent_state == 1

    def test_above_normal_obs(self, make_evidence):
        e = make_evidence(gpm_obs_24h=30.0)
        assert e.obs_antecedent_state == 2

    def test_no_persistence(self, make_evidence):
        e = make_evidence(consecutive_signal_days=2)
        assert e.temporal_persistence_state == 0

    def test_yes_persistence(self, make_evidence):
        e = make_evidence(consecutive_signal_days=3)
        assert e.temporal_persistence_state == 1

    def test_dry_api(self, make_evidence):
        e = make_evidence(api_mm=10.0)
        assert e.api_state == 0

    def test_normal_api(self, make_evidence):
        e = make_evidence(api_mm=50.0)
        assert e.api_state == 1

    def test_saturated_api(self, make_evidence):
        e = make_evidence(api_mm=100.0)
        assert e.api_state == 2

    def test_local_spatial(self, make_evidence):
        e = make_evidence(spatial_coverage_fraction=0.20)
        assert e.spatial_coverage_state == 0

    def test_regional_spatial(self, make_evidence):
        e = make_evidence(spatial_coverage_fraction=0.50)
        assert e.spatial_coverage_state == 1

    def test_extensive_spatial(self, make_evidence):
        e = make_evidence(spatial_coverage_fraction=0.80)
        assert e.spatial_coverage_state == 2

    def test_soil_memory_fresh(self, make_evidence):
        e = make_evidence(sat_consecutive_days=6)
        assert e.soil_memory_state == 0

    def test_soil_memory_prolonged(self, make_evidence):
        e = make_evidence(sat_consecutive_days=7)
        assert e.soil_memory_state == 1

    def test_evidence_thresholds_default(self, make_evidence):
        """EvidenceThresholds uses correct defaults."""
        e = make_evidence()
        assert isinstance(e.thresholds, EvidenceThresholds)
        assert e.thresholds.hazard_medium_threshold == 0.15
        assert e.thresholds.hazard_high_threshold == 0.40


@pytest.fixture(scope="module")
def built_model():
    """Build the CRMA model once for all tests (slow to construct)."""
    pytest.importorskip("pgmpy", reason="pgmpy not installed")
    model = CRMAModel(cluster=EastAfricaCluster.EQUATORIAL_EAST)
    model.build()
    return model


class TestCRMAModelInference:
    def test_low_risk_scenario(self, built_model, make_evidence):
        """No signal at all → should return Green or Yellow."""
        evidence = make_evidence()
        result = built_model.infer(evidence)
        assert result["risk_state"] in (0, 1), f"Expected low risk, got {result['risk_label']}"

    def test_high_risk_scenario(self, built_model, make_evidence):
        """Severe signal across all nodes → should return Orange or Red."""
        evidence = make_evidence(
            exceedance_prob_24h=0.80,
            exceedance_prob_72h=0.75,
            gpm_obs_24h=50.0,
            api_mm=120.0,
            spatial_coverage_fraction=0.90,
            consecutive_signal_days=5,
            sat_consecutive_days=10,
        )
        result = built_model.infer(evidence)
        assert result["risk_state"] in (2, 3), f"Expected high risk, got {result['risk_label']}"

    def test_medium_confidence_does_not_veto_strong_signal(self, built_model, make_evidence):
        """Regression: a strong forecast signal at Medium data-confidence (the
        precip-ensemble norm) must NOT collapse to Green. The old hardcoded
        [0.5, 0.8, 1.0] damping vetoed it, producing a structural all-Green run.
        """
        evidence = make_evidence(
            exceedance_prob_24h=1.0,
            exceedance_prob_72h=1.0,
            spatial_coverage_fraction=0.36,  # Regional
            gpm_quality=1,                    # Medium confidence
        )
        assert evidence.data_confidence_state == 1
        result = built_model.infer(evidence)
        assert result["risk_state"] >= 1, (
            f"Strong signal vetoed by Medium confidence → {result['risk_label']}"
        )

    def test_probabilities_sum_to_one(self, built_model, make_evidence):
        evidence = make_evidence(exceedance_prob_24h=0.3, gpm_obs_24h=15.0)
        result = built_model.infer(evidence)
        total = result["p_green"] + result["p_yellow"] + result["p_orange"] + result["p_red"]
        assert abs(total - 1.0) < 1e-6, f"Probabilities don't sum to 1: {total}"

    def test_risk_monotonic_with_hazard(self, built_model, make_evidence):
        """Higher forecast hazard → higher expected risk state."""
        low = make_evidence(exceedance_prob_24h=0.05)
        high = make_evidence(exceedance_prob_24h=0.80)

        result_low = built_model.infer(low)
        result_high = built_model.infer(high)

        expected_low = result_low["p_orange"] + result_low["p_red"]
        expected_high = result_high["p_orange"] + result_high["p_red"]

        assert expected_high > expected_low

    def test_api_increases_risk(self, built_model, make_evidence):
        """Saturated soil (API=120) should increase risk vs dry soil (API=10)."""
        dry = make_evidence(exceedance_prob_24h=0.25, api_mm=10.0)
        sat = make_evidence(exceedance_prob_24h=0.25, api_mm=120.0)

        result_dry = built_model.infer(dry)
        result_sat = built_model.infer(sat)

        risk_dry = result_dry["p_orange"] + result_dry["p_red"]
        risk_sat = result_sat["p_orange"] + result_sat["p_red"]

        assert risk_sat >= risk_dry, "Saturated soil should not reduce flood risk"

    def test_result_keys(self, built_model, make_evidence):
        evidence = make_evidence()
        result = built_model.infer(evidence)
        expected_keys = {
            "risk_state", "risk_label",
            "p_green", "p_yellow", "p_orange", "p_red", "evidence",
        }
        assert expected_keys.issubset(result.keys())

    def test_risk_label_matches_state(self, built_model, make_evidence):
        evidence = make_evidence()
        result = built_model.infer(evidence)
        assert RISK_LEVELS[result["risk_state"]] == result["risk_label"]

    def test_saturated_api_increases_risk_vs_dry(self, built_model, make_evidence):
        """API_State=Saturated must not produce lower risk than API_State=Dry."""
        from gik_icechain.risk.dynamic_bn import init_state
        from gik_icechain.risk.dynamic_bn import step as bn_step

        shared_kwargs = dict(
            exceedance_prob_24h=0.25,
            exceedance_prob_72h=0.20,
            exceedance_prob_7d=0.15,
            gpm_obs_24h=15.0,
            spatial_coverage_fraction=0.5,
            consecutive_signal_days=1,
            sat_consecutive_days=0,
        )
        ev_dry = make_evidence(api_mm=5.0, **shared_kwargs)
        ev_sat = make_evidence(api_mm=120.0, **shared_kwargs)

        state_dry = init_state(5.0)
        state_sat = init_state(120.0)

        result_dry, _ = bn_step(state_dry, ev_dry, built_model)
        result_sat, _ = bn_step(state_sat, ev_sat, built_model)

        risk_dry = result_dry["p_orange"] + result_dry["p_red"]
        risk_sat = result_sat["p_orange"] + result_sat["p_red"]
        assert risk_sat >= risk_dry, "Saturated soil must not reduce flood risk"

    def test_soil_memory_amplifies_risk(self, built_model, make_evidence):
        """Key DBN scientific test: 15-day saturated soil + 50mm MUST produce
        higher P(Red) than dry soil + 50mm (same forecast hazard)."""

        shared = dict(
            exceedance_prob_24h=0.30,
            exceedance_prob_72h=0.25,
            exceedance_prob_7d=0.20,
            gpm_obs_24h=50.0,
            spatial_coverage_fraction=0.5,
            consecutive_signal_days=1,
        )
        # Case A: soil saturated for 15 days
        ev_prolonged = make_evidence(
            api_mm=120.0, sat_consecutive_days=15, **shared
        )
        # Case B: dry soil, no prior saturation
        ev_dry = make_evidence(
            api_mm=10.0, sat_consecutive_days=0, **shared
        )

        result_prolonged = built_model.infer(ev_prolonged)
        result_dry = built_model.infer(ev_dry)

        p_red_prolonged = result_prolonged["p_red"]
        p_red_dry = result_dry["p_red"]

        assert p_red_prolonged > p_red_dry, (
            f"15-day saturated soil + 50mm should produce higher P(Red) than dry + 50mm. "
            f"Got P(Red|prolonged)={p_red_prolonged:.3f} vs P(Red|dry)={p_red_dry:.3f}"
        )

    def test_dynamic_bn_sat_days_accumulate(self, built_model, make_evidence):
        """sat_consecutive_days in DynamicBNState must increment each saturated day."""
        from gik_icechain.risk.dynamic_bn import init_state
        from gik_icechain.risk.dynamic_bn import step as bn_step

        state = init_state(initial_api_mm=120.0)  # start saturated
        ev = make_evidence(
            exceedance_prob_24h=0.20, gpm_obs_24h=10.0, api_mm=120.0
        )

        for day in range(1, 9):
            _, state = bn_step(state, ev, built_model, gpm_obs_mm=30.0)
            assert state.sat_consecutive_days == day, (
                f"sat_consecutive_days should be {day}, got {state.sat_consecutive_days}"
            )

    def test_horn_arid_cluster_higher_api_weight(self):
        """Horn-arid cluster must weight API more heavily than equatorial."""
        eq = _CLUSTER_WEIGHTS[EastAfricaCluster.EQUATORIAL_EAST]
        horn = _CLUSTER_WEIGHTS[EastAfricaCluster.HORN_ARID]
        assert horn["api"] > eq["api"]
        assert horn["forecast"] >= eq["forecast"]

    def test_get_pgmpy_model(self, built_model):
        """get_pgmpy_model() returns the underlying pgmpy model."""
        model = built_model.get_pgmpy_model()
        assert model is not None
        assert hasattr(model, "check_model")
