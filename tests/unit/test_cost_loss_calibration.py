"""Unit tests for REV-based cost-loss threshold calibration."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from gik_icechain.risk.cost_loss_calibration import (
    calibrate_cost_loss,
    calibrate_tier,
    relative_economic_value,
)
from gik_icechain.shared.config import CostLossConfig


class TestRelativeEconomicValue:
    def test_perfect_trigger_is_one(self):
        # H=1, F=0 captures the full perfect-forecast value, any alpha/base rate.
        assert relative_economic_value(1.0, 0.0, 0.1, 0.1) == pytest.approx(1.0)
        assert relative_economic_value(1.0, 0.0, 0.2, 0.3) == pytest.approx(1.0)

    def test_always_act_no_skill_is_zero(self):
        # alpha < s ⇒ climatology is always-act; an always-act trigger (H=F=1)
        # matches it exactly → REV 0.
        assert relative_economic_value(1.0, 1.0, 0.1, 0.05) == pytest.approx(0.0)

    def test_never_act_no_skill_is_zero(self):
        # alpha > s ⇒ climatology is never-act; a never-act trigger (H=F=0) → 0.
        assert relative_economic_value(0.0, 0.0, 0.1, 0.3) == pytest.approx(0.0)

    def test_degenerate_inputs_return_zero(self):
        assert relative_economic_value(1.0, 0.0, 0.0, 0.2) == 0.0
        assert relative_economic_value(1.0, 0.0, 1.0, 0.2) == 0.0
        assert relative_economic_value(1.0, 0.0, 0.2, 0.0) == 0.0

    def test_worse_than_climatology_is_negative(self):
        # A trigger that fires only on non-events (H=0, F=1) is value-destroying.
        assert relative_economic_value(0.0, 1.0, 0.2, 0.2) < 0.0


class TestCalibrateTier:
    def test_separable_scores_recover_perfect_trigger(self):
        rng = np.random.default_rng(0)
        n = 200
        label = (rng.random(n) < 0.2).astype(int)
        score = np.where(label == 1, 0.9, 0.05)
        cal = calibrate_tier(score, label, alpha=0.2)
        assert cal.rev == pytest.approx(1.0)
        assert cal.hit_rate == 1.0
        assert cal.false_alarm_rate == 0.0
        assert 0.05 < cal.tau <= 0.9

    def test_tau_within_grid(self):
        rng = np.random.default_rng(1)
        label = (rng.random(100) < 0.3).astype(int)
        score = rng.random(100)
        cal = calibrate_tier(score, label, alpha=0.2)
        assert 0.0 < cal.tau < 1.0


def _frame(n=300, seed=0):
    """Separable posterior frame: floods carry red mass, non-floods green mass."""
    rng = np.random.default_rng(seed)
    label = (rng.random(n) < 0.2).astype(int)
    rows = []
    for y in label:
        if y == 1:
            rows.append({"label": 1, "p_yellow": 0.0, "p_orange": 0.1, "p_red": 0.9})
        else:
            rows.append({"label": 0, "p_yellow": 0.1, "p_orange": 0.0, "p_red": 0.0})
    return pd.DataFrame(rows)


class TestCalibrateCostLoss:
    def test_returns_valid_ordered_config(self):
        cfg, _ = calibrate_cost_loss(_frame())
        assert isinstance(cfg, CostLossConfig)
        assert cfg.enabled is True
        # The model's ordering constraint must hold (and CostLossConfig validates).
        assert 0.0 < cfg.tau_yellow <= cfg.tau_orange <= cfg.tau_red <= 1.0

    def test_report_fields(self):
        df = _frame()
        _, report = calibrate_cost_loss(df)
        assert report["n_unit_days"] == len(df)
        assert report["n_positives"] == int(df["label"].sum())
        assert report["base_rate"] == pytest.approx(df["label"].mean())
        assert set(report["tiers"]) == {"yellow", "orange", "red"}
        # Separable data ⇒ every tier recovers full economic value.
        assert report["tiers"]["red"]["rev"] == pytest.approx(1.0)

    def test_ordering_invariant_on_random_data(self):
        rng = np.random.default_rng(7)
        n = 250
        label = (rng.random(n) < 0.25).astype(int)
        g = rng.random(n)
        y = rng.random(n)
        o = rng.random(n)
        r = rng.random(n)
        tot = g + y + o + r
        df = pd.DataFrame(
            {
                "label": label,
                "p_yellow": y / tot,
                "p_orange": o / tot,
                "p_red": r / tot,
            }
        )
        cfg, _ = calibrate_cost_loss(df)
        # Whatever the raw per-tier optima, the output is always well-ordered.
        assert cfg.tau_yellow <= cfg.tau_orange <= cfg.tau_red

    def test_custom_alphas_passed_through(self):
        alphas = {"yellow": 0.05, "orange": 0.1, "red": 0.15}
        _, report = calibrate_cost_loss(_frame(), alphas=alphas)
        assert report["tiers"]["yellow"]["alpha"] == 0.05
        assert report["tiers"]["red"]["alpha"] == 0.15
