"""
tests/unit/test_crma_model.py
Unit tests for the CRMA Bayesian Network model.
"""

import numpy as np
import pytest

from gik_icechain.risk.crma_model import (
    _CLUSTER_WEIGHTS,
    RISK_LEVELS,
    CRMAModel,
    EastAfricaCluster,
    EvidenceThresholds,
    _cost_loss_state,
    _dist_of_max,
    _gaussian_soft_bin,
)
from gik_icechain.shared.config import (
    CostLossConfig,
    CRMAModelConfig,
    SoftEvidenceConfig,
)


class TestSoftBinningHelpers:
    def test_soft_bin_sums_to_one(self):
        v = _gaussian_soft_bin(0.16, [0.15, 0.40, 0.70], 0.05)
        assert v.sum() == pytest.approx(1.0)
        assert len(v) == 4

    def test_sigma_zero_is_onehot(self):
        v = _gaussian_soft_bin(0.16, [0.15, 0.40, 0.70], 0.0)
        assert list(v) == [0.0, 1.0, 0.0, 0.0]  # 0.16 >= 0.15 -> Medium

    def test_near_edge_splits_mass(self):
        # Just below the Medium cutoff -> mass shared between Low and Medium.
        v = _gaussian_soft_bin(0.14, [0.15, 0.40, 0.70], 0.05)
        assert v[0] > 0.0 and v[1] > 0.0
        assert v[2] == pytest.approx(0.0, abs=1e-6)

    def test_non_finite_is_neutral_middle(self):
        v = _gaussian_soft_bin(float("nan"), [0.15, 0.40, 0.70], 0.05)
        assert v[1] == 1.0

    def test_dist_of_max_onehot_equals_hard(self):
        m = _dist_of_max(np.array([0.0, 1.0, 0.0, 0.0]), np.array([0.0, 0.0, 1.0, 0.0]))
        assert list(m) == [0.0, 0.0, 1.0, 0.0]  # max(Medium, High) = High


class TestSoftEvidenceInference:
    @staticmethod
    def _model(**soft_kw):
        cfg = CRMAModelConfig(soft_evidence=SoftEvidenceConfig(**soft_kw))
        m = CRMAModel(EastAfricaCluster.EQUATORIAL_EAST, crma_cfg=cfg)
        m.build()
        return m

    def test_soft_sigma_zero_matches_hard(self, make_evidence):
        soft0 = self._model(enabled=True, sigma_forecast=0, sigma_tail=0, sigma_gpm=0,
                            sigma_spatial=0, sigma_api=0, sigma_trend=0)
        hard = self._model(enabled=False)
        rng = np.random.default_rng(0)
        for _ in range(50):
            kw = dict(
                exceedance_prob_24h=float(rng.random()),
                exceedance_prob_72h=float(rng.random()),
                exceedance_prob_7d=float(rng.random()),
                gpm_obs_24h=float(rng.uniform(0, 60)),
                api_mm=float(rng.uniform(0, 120)),
                spatial_coverage_fraction=float(rng.random()),
                consecutive_signal_days=int(rng.integers(0, 5)),
                sat_consecutive_days=int(rng.integers(0, 10)),
                forecast_tail_ratio=float(rng.uniform(0, 2.0)),
            )
            rs = soft0.infer(soft0.make_evidence(rp=5, **kw))
            rh = hard.infer(hard.make_evidence(rp=5, **kw))
            assert rs["risk_state"] == rh["risk_state"]
            for k in ("p_green", "p_yellow", "p_orange", "p_red"):
                assert rs[k] == pytest.approx(rh[k], abs=1e-9)

    def test_soft_posterior_normalised(self):
        m = self._model(enabled=True)
        ev = m.make_evidence(
            rp=5, exceedance_prob_24h=0.38, exceedance_prob_72h=0.0,
            exceedance_prob_7d=0.0, gpm_obs_24h=24.0, api_mm=79.0,
            spatial_coverage_fraction=0.26, consecutive_signal_days=0,
        )
        r = m.infer(ev)
        assert sum(r[k] for k in ("p_green", "p_yellow", "p_orange", "p_red")) == pytest.approx(1.0)

    def test_to_soft_obs_vectors_normalised(self):
        m = self._model(enabled=True)
        ev = m.make_evidence(
            rp=5, exceedance_prob_24h=0.2, exceedance_prob_72h=0.1,
            exceedance_prob_7d=0.0, gpm_obs_24h=10.0, api_mm=40.0,
            spatial_coverage_fraction=0.3, consecutive_signal_days=1,
            forecast_tail_ratio=0.9,
        )
        for node, vec in ev.to_soft_obs().items():
            assert vec.sum() == pytest.approx(1.0), node


class TestTailAwareHazard:
    """Possible-worlds tail signal escalates Forecast_Hazard even when the
    ensemble-mean exceedance fraction is ~0 (the convective wet-tail case)."""

    def test_tail_alone_escalates_hazard(self, make_evidence):
        # Mean fraction 0 everywhere, but the p95 member reaches the return level.
        e = make_evidence(exceedance_prob_24h=0.0, forecast_tail_ratio=1.05)
        assert e.forecast_hazard_state == 2  # High via tail (default tail_high_ratio=1.0)

    def test_extreme_tail_gives_extreme_hazard(self, make_evidence):
        e = make_evidence(exceedance_prob_24h=0.0, forecast_tail_ratio=1.4)
        assert e.forecast_hazard_state == 3

    def test_medium_tail_band(self, make_evidence):
        e = make_evidence(exceedance_prob_24h=0.0, forecast_tail_ratio=0.85)
        assert e.forecast_hazard_state == 1

    def test_no_tail_signal_keeps_fraction_state(self, make_evidence):
        e = make_evidence(exceedance_prob_24h=0.0, forecast_tail_ratio=0.5)
        assert e.forecast_hazard_state == 0

    def test_hazard_is_max_of_fraction_and_tail(self, make_evidence):
        # Strong fraction (High=2) but only a weak tail → fraction wins.
        e = make_evidence(exceedance_prob_24h=0.50, forecast_tail_ratio=0.0)
        assert e.forecast_hazard_state == 2

    def test_tail_disabled_ignores_ratio(self, make_evidence):
        thr = EvidenceThresholds(tail_aware_hazard=False)
        e = make_evidence(
            exceedance_prob_24h=0.0, forecast_tail_ratio=1.4, thresholds=thr
        )
        assert e.forecast_hazard_state == 0


class TestCostLossDecision:
    """Cost-loss trigger: highest tier whose cumulative posterior ≥ its C/L."""

    def test_pure_rule_escalates_below_argmax(self):
        # argmax is Green, but the cumulative Yellow mass clears tau_yellow.
        probs = np.array([0.60, 0.20, 0.12, 0.08])
        assert int(np.argmax(probs)) == 0
        # defaults: tau_yellow=0.15, tau_orange=0.25, tau_red=0.35
        assert _cost_loss_state(probs, 0.15, 0.25, 0.35) == 1  # Yellow

    def test_pure_rule_most_severe_first(self):
        probs = np.array([0.10, 0.20, 0.30, 0.40])
        assert _cost_loss_state(probs, 0.15, 0.25, 0.35) == 3  # P(≥Red)=0.40

    def test_pure_rule_orange_band(self):
        probs = np.array([0.30, 0.40, 0.20, 0.10])
        # P(≥Red)=0.10<0.35; P(≥Orange)=0.30≥0.25 → Orange
        assert _cost_loss_state(probs, 0.15, 0.25, 0.35) == 2

    def test_pure_rule_stays_green(self):
        probs = np.array([0.95, 0.03, 0.015, 0.005])
        assert _cost_loss_state(probs, 0.15, 0.25, 0.35) == 0

    def test_boundary_and_conservative_high_taus(self):
        probs = np.array([0.05, 0.05, 0.10, 0.80])
        # tau exactly at the red mass fires Red (>= boundary, inclusive).
        assert _cost_loss_state(probs, 0.80, 0.80, 0.80) == 3
        # taus above every cumulative mass → nothing fires → Green (conservative).
        assert _cost_loss_state(probs, 0.99, 0.99, 0.99) == 0

    @staticmethod
    def _model(cost_loss: CostLossConfig | None = None) -> CRMAModel:
        cfg = CRMAModelConfig(cost_loss=cost_loss or CostLossConfig())
        m = CRMAModel(EastAfricaCluster.EQUATORIAL_EAST, crma_cfg=cfg)
        m.build()
        return m

    def test_default_disabled_is_argmax(self, make_evidence):
        m = self._model()  # cost_loss default OFF
        assert m._cfg.cost_loss.enabled is False
        rng = np.random.default_rng(1)
        for _ in range(40):
            ev = m.make_evidence(
                rp=5,
                exceedance_prob_24h=float(rng.random()),
                exceedance_prob_72h=float(rng.random()),
                exceedance_prob_7d=float(rng.random()),
                gpm_obs_24h=float(rng.uniform(0, 60)),
                api_mm=float(rng.uniform(0, 120)),
                spatial_coverage_fraction=float(rng.random()),
                consecutive_signal_days=int(rng.integers(0, 5)),
            )
            r = m.infer(ev)
            p = np.array([r["p_green"], r["p_yellow"], r["p_orange"], r["p_red"]])
            assert r["risk_state"] == int(np.argmax(p))

    def test_enabled_label_matches_rule(self, make_evidence):
        cl = CostLossConfig(enabled=True)
        m = self._model(cl)
        rng = np.random.default_rng(2)
        for _ in range(40):
            ev = m.make_evidence(
                rp=5,
                exceedance_prob_24h=float(rng.random()),
                exceedance_prob_72h=float(rng.random()),
                exceedance_prob_7d=float(rng.random()),
                gpm_obs_24h=float(rng.uniform(0, 60)),
                api_mm=float(rng.uniform(0, 120)),
                spatial_coverage_fraction=float(rng.random()),
                consecutive_signal_days=int(rng.integers(0, 5)),
            )
            r = m.infer(ev)
            p = np.array([r["p_green"], r["p_yellow"], r["p_orange"], r["p_red"]])
            expected = _cost_loss_state(p, cl.tau_yellow, cl.tau_orange, cl.tau_red)
            assert r["risk_state"] == expected


class TestRainfallTrend:
    """Rainfall_Trend node: the 7-day IMERG slope escalates/dampens compound
    risk, with Stable as the neutral (legacy-preserving) state."""

    def test_default_slope_is_stable(self, make_evidence):
        assert make_evidence().rainfall_trend_state == 1

    def test_increasing_and_decreasing_bins(self, make_evidence):
        assert make_evidence(rainfall_trend_slope=2.5).rainfall_trend_state == 2
        assert make_evidence(rainfall_trend_slope=-2.5).rainfall_trend_state == 0
        assert make_evidence(rainfall_trend_slope=1.0).rainfall_trend_state == 1

    def test_threshold_is_inclusive(self, make_evidence):
        thr = EvidenceThresholds(trend_threshold=2.0)
        assert make_evidence(rainfall_trend_slope=2.0, thresholds=thr).rainfall_trend_state == 2
        assert make_evidence(rainfall_trend_slope=-2.0, thresholds=thr).rainfall_trend_state == 0

    @staticmethod
    def _model() -> CRMAModel:
        m = CRMAModel(EastAfricaCluster.EQUATORIAL_EAST)
        m.build()
        return m

    @staticmethod
    def _expected_state(r: dict) -> float:
        p = [r["p_green"], r["p_yellow"], r["p_orange"], r["p_red"]]
        return sum(k * pk for k, pk in enumerate(p))

    @staticmethod
    def _base(exc: float) -> dict:
        return dict(
            rp=5, exceedance_prob_24h=exc, exceedance_prob_72h=0.0,
            exceedance_prob_7d=0.0, gpm_obs_24h=24.0, api_mm=85.0,
            spatial_coverage_fraction=0.30, consecutive_signal_days=0,
        )

    def test_trend_monotonic_in_severity(self):
        m = self._model()
        b = self._base(0.30)
        vals = [
            self._expected_state(m.infer(m.make_evidence(**b, rainfall_trend_slope=s)))
            for s in (-5.0, -1.0, 0.0, 1.0, 5.0)
        ]
        # Centred (state-1)*weight ⇒ expected severity is monotonic non-decreasing.
        assert vals == sorted(vals)

    def test_trend_can_change_the_label(self):
        m = self._model()
        # Across a forecast-strength sweep, some base score sits within one
        # weight of a Compound_Risk bucket edge, so the trend flips the label.
        flipped = False
        for exc in np.linspace(0.05, 0.95, 19):
            b = self._base(float(exc))
            dec = m.infer(m.make_evidence(**b, rainfall_trend_slope=-3.0))["risk_state"]
            inc = m.infer(m.make_evidence(**b, rainfall_trend_slope=3.0))["risk_state"]
            if dec != inc:
                assert inc > dec  # intensifying trend never lowers the label
                flipped = True
                break
        assert flipped, "rainfall trend never altered the risk label across the sweep"

    def test_soft_dist_normalised_and_onehot_when_hard(self, make_evidence):
        ev = make_evidence(rainfall_trend_slope=1.5)
        assert list(ev.rainfall_trend_dist()) == [0.0, 1.0, 0.0]  # hard → one-hot Stable
        thr = EvidenceThresholds(soft_evidence=True, sigma_trend=1.0, trend_threshold=2.0)
        d = make_evidence(rainfall_trend_slope=1.8, thresholds=thr).rainfall_trend_dist()
        assert d.sum() == pytest.approx(1.0)
        assert d[1] > 0 and d[2] > 0  # near +threshold splits Stable/Increasing


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

    def test_extreme_forecast_hazard(self, make_evidence):
        e = make_evidence(exceedance_prob_24h=0.80)
        assert e.forecast_hazard_state == 3

    def test_extreme_threshold_boundary(self, make_evidence):
        # Exactly at 0.70 → Extreme; just below → High.
        assert make_evidence(exceedance_prob_24h=0.70).forecast_hazard_state == 3
        assert make_evidence(exceedance_prob_24h=0.699).forecast_hazard_state == 2

    def test_below_normal_obs(self, make_evidence):
        e = make_evidence(gpm_obs_24h=1.0)
        assert e.obs_antecedent_state == 0

    def test_normal_obs(self, make_evidence):
        e = make_evidence(gpm_obs_24h=10.0)
        assert e.obs_antecedent_state == 1

    def test_above_normal_obs(self, make_evidence):
        e = make_evidence(gpm_obs_24h=30.0)
        assert e.obs_antecedent_state == 2

    def test_missing_obs_is_neutral_not_dry(self, make_evidence):
        # A missing observation must not read as "Below normal" (a dry day).
        e = make_evidence(gpm_obs_24h=0.0, gpm_missing=True)
        assert e.obs_antecedent_state == 1

    def test_present_zero_obs_is_dry(self, make_evidence):
        # An actually-observed 0 mm still discretises to Below normal.
        e = make_evidence(gpm_obs_24h=0.0, gpm_missing=False)
        assert e.obs_antecedent_state == 0

    def test_missing_obs_forces_low_confidence(self, make_evidence):
        e = make_evidence(gpm_quality=2, gpm_missing=True)
        assert e.data_confidence_state == 0

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
        assert e.thresholds.hazard_extreme_threshold == 0.70


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


class TestEscalationLevers:
    """Lever #1 (Extreme hazard state) + #3 (confidence/forecast decoupling)."""

    def test_lookup_table_shape_4states(self, built_model):
        """Forecast_Hazard axis of the O(1) lookup table is 4 states (Extreme).

        Axes: Forecast_Hazard, Obs_Antecedent, Temporal_Persist, Spatial_Coverage,
        Data_Confidence, API_State, Soil_Memory, Rainfall_Trend, Risk_State.
        """
        assert built_model._lookup_table.shape[0] == 4
        assert built_model._lookup_table.shape == (4, 3, 2, 3, 3, 3, 2, 3, 4)

    def test_decoupling_boundary_diverges(self, make_evidence):
        """At a bucket boundary, a strong forecast under missing GPM (Low
        confidence) must survive when decoupled but is damped down under the
        legacy whole-score coupling - the only case where #3 is observable."""
        from gik_icechain.shared.config import CRMAModelConfig

        kwargs = dict(
            exceedance_prob_72h=0.71,           # Extreme hazard
            gpm_missing=True,                   # → Data_Confidence Low
            api_mm=6.0,                         # dry soil
            spatial_coverage_fraction=0.10,     # Local
        )

        def infer(damps_forecast: bool):
            model = CRMAModel(
                cluster=EastAfricaCluster.HORN_ARID,
                crma_cfg=CRMAModelConfig(confidence_damps_forecast=damps_forecast),
            )
            model.build()
            ev = make_evidence(thresholds=model.evidence_thresholds(5), **kwargs)
            return model.infer(ev)

        legacy = infer(True)
        decoupled = infer(False)
        assert decoupled["risk_state"] > legacy["risk_state"], (
            f"Decoupling must preserve the forecast: legacy={legacy['risk_label']} "
            f"decoupled={decoupled['risk_label']}"
        )
