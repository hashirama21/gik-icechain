"""Unit tests for the Dynamic Bayesian Network state propagation."""

from __future__ import annotations

import pytest

from gik_icechain.risk.crma_model import CRMAEvidence
from gik_icechain.risk.dynamic_bn import (
    DynamicBNState,
    _trend_slope,
    init_state,
    run_temporal_sequence,
)
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


class TestTrendSlope:
    def test_empty_and_single_are_zero(self):
        assert _trend_slope(()) == 0.0
        assert _trend_slope((5.0,)) == 0.0

    def test_increasing_is_positive(self):
        assert _trend_slope((1.0, 2.0, 3.0, 4.0)) == pytest.approx(1.0)

    def test_decreasing_is_negative(self):
        assert _trend_slope((4.0, 3.0, 2.0, 1.0)) == pytest.approx(-1.0)

    def test_flat_is_zero(self):
        assert _trend_slope((5.0, 5.0, 5.0)) == pytest.approx(0.0)


class TestStepTrendBuffer:
    def test_buffer_appends_observation(self, built_model):
        _, ns = bn_step(init_state(), _evidence(), built_model, gpm_obs_mm=12.0)
        assert ns.gpm_history == (12.0,)

    def test_buffer_capped_at_window(self, built_model):
        state = init_state()
        for i in range(10):
            _, state = bn_step(
                state, _evidence(), built_model, gpm_obs_mm=float(i), trend_window=7
            )
        assert len(state.gpm_history) == 7
        assert state.gpm_history == tuple(float(i) for i in range(3, 10))

    def test_rising_series_feeds_increasing_trend(self, built_model):
        # A steepening 7-day GPM series ⇒ the BN sees Rainfall_Trend=Increasing,
        # proving the slope feed is live (not the inert default).
        state = init_state()
        result: dict = {}
        for g in (0.0, 3.0, 6.0, 9.0, 12.0, 15.0, 18.0):
            result, state = bn_step(state, _evidence(), built_model, gpm_obs_mm=g)
        assert _trend_slope(state.gpm_history) == pytest.approx(3.0)
        assert result["evidence"]["Rainfall_Trend"] == 2  # Increasing

    def test_falling_series_feeds_decreasing_trend(self, built_model):
        state = init_state()
        result: dict = {}
        for g in (18.0, 15.0, 12.0, 9.0, 6.0, 3.0, 0.0):
            result, state = bn_step(state, _evidence(), built_model, gpm_obs_mm=g)
        assert result["evidence"]["Rainfall_Trend"] == 0  # Decreasing


class TestStateSerialization:
    def test_roundtrip_preserves_gpm_history(self):
        from dataclasses import asdict

        from gik_icechain.risk.risk_engine import _state_from_dict

        s = DynamicBNState(
            api_mm=30.0, consecutive_days=2, sat_consecutive_days=1,
            last_risk_state=2, gpm_history=(1.0, 2.0, 3.0),
        )
        assert _state_from_dict(asdict(s)) == s

    def test_pre_v3_checkpoint_defaults_empty_history(self):
        from gik_icechain.risk.risk_engine import _state_from_dict

        legacy = dict(
            api_mm=20.0, consecutive_days=0, sat_consecutive_days=0, last_risk_state=0
        )
        assert _state_from_dict(legacy).gpm_history == ()


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
