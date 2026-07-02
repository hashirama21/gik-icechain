"""REV-based calibration of the cost-loss decision thresholds (tau).

The live cost-loss trigger (``CRMAModel._decide_risk_state`` /
``_cost_loss_state``) labels a unit at the highest tier ``T`` whose cumulative
posterior ``P(>=T)`` reaches that tier's threshold ``tau_T``. The default taus
are *guessed* from forecast-based-financing (FbF) cost-loss ratios.

This module **learns** ``tau_T`` from EM-DAT by maximising the **Relative
Economic Value** (REV; Richardson 2000; Wilks) of the tier-``T`` trigger at that
tier's anticipatory-action cost-loss ratio ``alpha_T``. REV measures the
fraction of the perfect-forecast economic value a trigger captures for a
decision-maker with cost-loss ratio ``alpha = C/L`` (act cost C, miss loss L):

    REV = (E_clim - E_forecast) / (E_clim - E_perfect)

    E_forecast = alpha*H*s + alpha*F*(1-s) + (1-H)*s
    E_clim     = min(alpha, s)        # cheaper of always-act / never-act
    E_perfect  = alpha*s

with base rate ``s`` (climatological event frequency), hit rate ``H`` and
false-alarm rate ``F`` of the trigger. REV=1 is a perfect forecast, REV<=0 means
no better than climatology. This is the metric of the Challenge 41
"missed opportunities" theme, and only our multi-year C1 corpus can fit it.

Only the calibration lives here — the live inference path is untouched. The
output is a :class:`CostLossConfig` the operator can drop into config
(``enabled=True``) once satisfied.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import structlog

from gik_icechain.shared.config import CostLossConfig

if TYPE_CHECKING:
    import pandas as pd

    from gik_icechain.risk.cpt_refinement import EMDATFloodRecord

log = structlog.get_logger(__name__)

# FbF anticipatory-action cost-loss ratios per tier (acting at T vs missing).
# Cheaper actions trigger at a lower C/L: cash transfers (~0.1) < pre-positioned
# stockpiles (~0.2) < heavy mobilisation (~0.3). See Coughlan de Perez 2015.
_DEFAULT_ALPHAS: dict[str, float] = {"yellow": 0.10, "orange": 0.20, "red": 0.30}

# Tier → the posterior columns whose sum is the cumulative trigger P(>=tier).
_TIER_COLS: dict[str, tuple[str, ...]] = {
    "yellow": ("p_yellow", "p_orange", "p_red"),
    "orange": ("p_orange", "p_red"),
    "red": ("p_red",),
}


def relative_economic_value(
    hit_rate: float,
    false_alarm_rate: float,
    base_rate: float,
    cost_loss_ratio: float,
) -> float:
    """Relative Economic Value of a binary trigger (Richardson 2000).

    Returns 1.0 for a perfect trigger (H=1, F=0) and 0.0 for no skill (matching
    the climatological always-act / never-act decision). Can be negative when
    the trigger is worse than climatology. Degenerate base rates / ratios
    (0 or 1) return 0.0.
    """
    s, a, h, f = base_rate, cost_loss_ratio, hit_rate, false_alarm_rate
    if not (0.0 < s < 1.0) or not (0.0 < a < 1.0):
        return 0.0
    e_forecast = a * h * s + a * f * (1.0 - s) + (1.0 - h) * s
    e_clim = min(a, s)
    e_perfect = a * s
    denom = e_clim - e_perfect
    return float((e_clim - e_forecast) / denom) if denom != 0.0 else 0.0


def _rates_at_tau(
    score: np.ndarray, label: np.ndarray, tau: float
) -> tuple[float, float]:
    """Hit rate and false-alarm rate when triggering at ``score >= tau``."""
    pred = score >= tau
    pos = label == 1
    neg = ~pos
    tp = int(np.count_nonzero(pred & pos))
    fn = int(np.count_nonzero(~pred & pos))
    fp = int(np.count_nonzero(pred & neg))
    tn = int(np.count_nonzero(~pred & neg))
    hit = tp / (tp + fn) if (tp + fn) else 0.0
    far = fp / (fp + tn) if (fp + tn) else 0.0
    return hit, far


@dataclass
class TierCalibration:
    """REV-optimal trigger for one tier."""

    tier: str
    alpha: float
    tau: float
    rev: float
    hit_rate: float
    false_alarm_rate: float


def _default_grid() -> np.ndarray:
    return np.round(np.arange(0.01, 1.0, 0.01), 4)


def calibrate_tier(
    score: np.ndarray,
    label: np.ndarray,
    alpha: float,
    tau_grid: np.ndarray | None = None,
) -> TierCalibration:
    """Sweep ``tau`` and return the trigger maximising REV at cost-loss ``alpha``.

    Args:
        score:    Cumulative trigger posterior ``P(>=tier)`` per unit-day.
        label:    Binary EM-DAT flood label (1=flood) per unit-day.
        alpha:    Cost-loss ratio of the anticipatory action for this tier.
        tau_grid: Candidate thresholds (default 0.01…0.99 step 0.01).
    """
    score = np.asarray(score, dtype=float)
    label = np.asarray(label, dtype=int)
    base_rate = float(label.mean()) if label.size else 0.0
    grid = _default_grid() if tau_grid is None else np.asarray(tau_grid, dtype=float)

    best: TierCalibration | None = None
    for tau in grid:
        hit, far = _rates_at_tau(score, label, float(tau))
        rev = relative_economic_value(hit, far, base_rate, alpha)
        if best is None or rev > best.rev:
            best = TierCalibration("", alpha, float(tau), rev, hit, far)
    assert best is not None  # grid is non-empty
    return best


def calibrate_cost_loss(
    df: pd.DataFrame,
    alphas: dict[str, float] | None = None,
    tau_grid: np.ndarray | None = None,
) -> tuple[CostLossConfig, dict]:
    """Calibrate all three tier thresholds from a labelled posterior frame.

    Args:
        df:       Rows = unit-days; columns ``label`` (0/1) and the posteriors
                  ``p_yellow``, ``p_orange``, ``p_red``.
        alphas:   Per-tier cost-loss ratios (defaults to FbF ratios).
        tau_grid: Candidate thresholds.

    Returns:
        ``(CostLossConfig, report)``. The taus are clamped to be non-decreasing
        (``tau_yellow <= tau_orange <= tau_red``, the model's ordering
        constraint); the report records the raw per-tier optima and base rate.
    """
    alphas = alphas or _DEFAULT_ALPHAS
    label = df["label"].to_numpy(dtype=int)

    cals: dict[str, TierCalibration] = {}
    for tier, cols in _TIER_COLS.items():
        score = df.loc[:, list(cols)].sum(axis=1).to_numpy(dtype=float)
        cal = calibrate_tier(score, label, alphas[tier], tau_grid)
        cal.tier = tier
        cals[tier] = cal

    # Enforce the CostLossConfig ordering (a more expensive action gets a higher
    # bar): non-decreasing taus via a running max. Clamp into (0, 1] for the
    # validator (the grid already excludes 0).
    ty = min(max(cals["yellow"].tau, 1e-3), 1.0)
    to = min(max(cals["orange"].tau, ty), 1.0)
    tr = min(max(cals["red"].tau, to), 1.0)
    cfg = CostLossConfig(enabled=True, tau_yellow=ty, tau_orange=to, tau_red=tr)

    report = {
        "base_rate": float(label.mean()) if label.size else 0.0,
        "n_unit_days": int(label.size),
        "n_positives": int(label.sum()),
        "tiers": {t: asdict(c) for t, c in cals.items()},
        "ordering_clamped": (ty, to, tr)
        != (cals["yellow"].tau, cals["orange"].tau, cals["red"].tau),
    }
    log.info(
        "cost_loss_calibrated",
        tau_yellow=ty,
        tau_orange=to,
        tau_red=tr,
        base_rate=report["base_rate"],
        n=report["n_unit_days"],
    )
    return cfg, report


def calibrate_from_risk_dir(
    risk_dir: Path,
    emdat_records: list[EMDATFloodRecord],
    start: str | None = None,
    end: str | None = None,
    alphas: dict[str, float] | None = None,
) -> tuple[CostLossConfig, dict]:
    """Load per-day risk score files + EM-DAT labels, then calibrate.

    Reads every ``{date}_risk_scores.json`` in *risk_dir* (optionally date
    filtered), joins EM-DAT flood days as labels, and runs
    :func:`calibrate_cost_loss`.
    """
    import json

    import pandas as pd

    from gik_icechain.risk.cpt_refinement import emdat_flood_days

    flood_days = emdat_flood_days(emdat_records)
    rows: list[dict] = []
    for scores_path in sorted(Path(risk_dir).glob("*_risk_scores.json")):
        data = json.loads(scores_path.read_text())
        date_str = data.get("date", scores_path.stem[:10])
        if (start and date_str < start) or (end and date_str > end):
            continue
        for pcode, score in data.get("units", {}).items():
            rows.append(
                {
                    "date": date_str,
                    "admin1_pcode": pcode,
                    "label": int((date_str, pcode) in flood_days),
                    "p_yellow": float(score.get("p_yellow", 0.0)),
                    "p_orange": float(score.get("p_orange", 0.0)),
                    "p_red": float(score.get("p_red", 0.0)),
                }
            )
    if not rows:
        raise ValueError(f"No *_risk_scores.json files found in {risk_dir}")
    return calibrate_cost_loss(pd.DataFrame(rows), alphas=alphas)
