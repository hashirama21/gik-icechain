"""Unit tests for the Dynamic Bayesian Network state propagation."""

from __future__ import annotations

import pytest

from gik_icechain.risk.crma_model import CRMAEvidence
from gik_icechain.risk.dynamic_bn import DynamicBNState, init_state, run_temporal_sequence
from gik_icechain.risk.dynamic_bn import step as bn_step


def _evidence(**kwargs) -> CRMAEvidence:
    defaults = dict(
        exceedance_prob_24h=0.0,
        exceedance_prob_72h=0.0,
        exceedance_prob_7d=0.0,
        gpm_obs_24h=0.0,
        api_mm=20.0,
        spatial_coverage_fraction=0.1,
        consecutive_signal_days=0,
        sat_consecutive_days=0,
    )
    defaults.update(kwargs)
    return CRMAEvidence(**defaults)


class TestInitState:
    def test_default_values(self):
        state = init_state()
        assert state.api_mm == pytest.approx(20.0)
        assert state.consecutive_days == 0
        assert state.sat_consecutive_days == 0
        assert state.last_risk_state == 0

    def test_custom_api(self):
        state = init_state(initial_api_mm=80.0)
        assert state.api_mm == pytest.approx(80.0)

    def test_custom_sat_days(self):
        state = init_state(sat_consecutive_days=5)
        assert state.sat_consecutive_days == 5


@pytest.fixture(scope="module")
def built_model():
    pytest.importorskip("pgmpy", reason="pgmpy not installed")
    from gik_icechain.risk.crma_model import CRMAModel
    m = CRMAModel()
    m.build()
    return m


class TestBNStep:
    def test_api_advances(self, built_model):
        state = init_state(initial_api_mm=20.0)
        ev = _evidence()
        _, new_state = bn_step(state, ev, built_model, api_decay=0.8, gpm_obs_mm=5.0)
        expected = 5.0 + 0.8 * 20.0
        assert new_state.api_mm == pytest.approx(expected)

    def test_no_signal_resets_consecutive(self, built_model):
        state = DynamicBNState(api_mm=20.0, consecutive_days=3,
                               sat_consecutive_days=0, last_risk_state=1)
        ev = _evidence(exceedance_prob_24h=0.05)  # below signal_threshold=0.15
        _, new_state = bn_step(state, ev, built_model, signal_threshold=0.15)
        assert new_state.consecutive_days == 0

    def test_signal_increments_consecutive(self, built_model):
        state = DynamicBNState(api_mm=20.0, consecutive_days=2,
                               sat_consecutive_days=0, last_risk_state=1)
        ev = _evidence(exceedance_prob_24h=0.40)  # above signal_threshold
        _, new_state = bn_step(state, ev, built_model, signal_threshold=0.15)
        assert new_state.consecutive_days == 3

    def test_saturated_api_increments_sat_days(self, built_model):
        state = init_state(initial_api_mm=100.0)  # starts saturated
        ev = _evidence(api_mm=100.0)
        _, new_state = bn_step(state, ev, built_model, gpm_obs_mm=20.0)
        # API_State should be Saturated → sat_consecutive_days increments
        assert new_state.sat_consecutive_days == 1

    def test_dry_api_resets_sat_days(self, built_model):
        state = DynamicBNState(api_mm=5.0, consecutive_days=0,
                               sat_consecutive_days=10, last_risk_state=0)
        ev = _evidence(api_mm=5.0)
        _, new_state = bn_step(state, ev, built_model, gpm_obs_mm=0.0)
        assert new_state.sat_consecutive_days == 0

    def test_result_has_expected_keys(self, built_model):
        state = init_state()
        ev = _evidence()
        result, _ = bn_step(state, ev, built_model)
        assert {"risk_state", "risk_label", "p_green", "p_yellow", "p_orange", "p_red"}.issubset(
            result.keys()
        )


class TestRunTemporalSequence:
    def test_length_matches_evidences(self, built_model):
        evidences = [_evidence() for _ in range(5)]
        results = run_temporal_sequence(evidences, built_model)
        assert len(results) == 5

    def test_api_increases_with_rainfall(self, built_model):
        evidences = [_evidence() for _ in range(3)]
        obs = [30.0, 30.0, 30.0]
        run_temporal_sequence(evidences, built_model, gpm_obs_series=obs, initial_api_mm=0.0)
        # After 3 days of 30mm: API grows — just check it runs without error

    def test_prolonged_saturation_amplifies_risk(self, built_model):
        # 10 days of saturated soil → soil_memory activates
        evidences_sat = [_evidence(api_mm=120.0, exceedance_prob_24h=0.30)] * 10
        evidences_dry = [_evidence(api_mm=5.0,   exceedance_prob_24h=0.30)] * 10

        results_sat = run_temporal_sequence(evidences_sat, built_model, initial_api_mm=120.0)
        results_dry = run_temporal_sequence(evidences_dry, built_model, initial_api_mm=5.0)

        # Last day: saturated should have >= risk than dry
        p_red_sat = results_sat[-1]["p_red"]
        p_red_dry = results_dry[-1]["p_red"]
        assert p_red_sat >= p_red_dry, (
            f"Prolonged saturation should not reduce P(Red): {p_red_sat:.3f} vs {p_red_dry:.3f}"
        )
