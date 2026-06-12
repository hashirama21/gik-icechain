"""EM-DAT CPT refinement for the CRMA Bayesian Network.

Refines the Risk_State leaf CPT using historical EM-DAT flood events as
labeled ground truth via Maximum Likelihood Estimation with Laplace smoothing.

Positive samples: EM-DAT flood event days → Risk_State = Red (3).
Negative samples: randomly drawn non-event days, labeled with the alert level
implied by the forecast hazard (0..2) — a high-hazard day without a flood is a
legitimate Orange (probabilistic warning that did not verify), so the Orange
boundary stays learnable.
Root-node CPTs (expert priors) are left unchanged.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import structlog

from gik_icechain.risk.crma_model import CRMAEvidence, CRMAModel, EvidenceThresholds
from gik_icechain.shared.regions import EAST_AFRICA_COUNTRIES_ISO3

log = structlog.get_logger(__name__)

try:
    from pgmpy.estimators import MaximumLikelihoodEstimator
    from pgmpy.inference import VariableElimination

    PGMPY_AVAILABLE = True
except ImportError:
    MaximumLikelihoodEstimator = None  # type: ignore[assignment,misc]
    VariableElimination = None  # type: ignore[assignment,misc]
    PGMPY_AVAILABLE = False

_EVIDENCE_COLS = [
    "Forecast_Hazard",
    "Obs_Antecedent",
    "Temporal_Persist",
    "Spatial_Coverage",
    "Data_Confidence",
    "API_State",
    "Soil_Memory",
    "Risk_State",
]

_STATE_NAMES: dict[str, list[int]] = {
    "Forecast_Hazard": [0, 1, 2],
    "Obs_Antecedent": [0, 1, 2],
    "Temporal_Persist": [0, 1],
    "Spatial_Coverage": [0, 1, 2],
    "Data_Confidence": [0, 1, 2],
    "API_State": [0, 1, 2],
    "Soil_Memory": [0, 1],
    "Compound_Risk": [0, 1, 2, 3],
    "Risk_State": [0, 1, 2, 3],
}


@dataclass
class EMDATFloodRecord:
    """A single EM-DAT flood event relevant to East Africa."""

    event_id: str
    country: str
    admin1_name: str
    admin1_pcode: str
    start_date: pd.Timestamp
    end_date: pd.Timestamp
    deaths: int | None
    affected: int | None
    disaster_type: str = "Flood"


def load_emdat_east_africa(csv_path: Path) -> list[EMDATFloodRecord]:
    """Load and filter EM-DAT flood records for East Africa (from May 2023).

    Expects a CSV export from emdat.be filtered for Disaster Type=Flood,
    Continent=Africa. Returns records with admin-1 attribution.
    """
    df = pd.read_csv(csv_path, parse_dates=["Start Date", "End Date"])
    df = df[
        (df["Disaster Type"] == "Flood")
        & (df["ISO"].isin(EAST_AFRICA_COUNTRIES_ISO3))
        & (df["Start Date"] >= "2023-05-01")
    ].copy()

    records = [
        EMDATFloodRecord(
            event_id=str(row.get("DisNo.", "")),
            country=str(row.get("Country", "")),
            admin1_name=str(row.get("Admin1", "")),
            admin1_pcode=str(row.get("Admin1 Code", "")),
            start_date=pd.Timestamp(row["Start Date"]),
            end_date=pd.Timestamp(row.get("End Date", row["Start Date"])),
            deaths=int(row["Total Deaths"]) if pd.notna(row.get("Total Deaths")) else None,
            affected=int(row["No. Affected"]) if pd.notna(row.get("No. Affected")) else None,
        )
        for _, row in df.iterrows()
    ]

    log.info(
        "emdat_loaded",
        n_events=len(records),
        date_range=f"{df['Start Date'].min().date()} to {df['Start Date'].max().date()}",
        countries=df["Country"].value_counts().to_dict(),
    )
    return records


def _col_or_default(df: pd.DataFrame, col: str, default: float = 0.0) -> float:
    if df.empty or col not in df.columns:
        return default
    return float(df[col].iloc[0])


def _evidence_row(evidence: CRMAEvidence, risk_state: int, **extra: object) -> dict:
    """One labeled training row from discretised evidence (shared pos/neg)."""
    return {
        "Forecast_Hazard": evidence.forecast_hazard_state,
        "Obs_Antecedent": evidence.obs_antecedent_state,
        "Temporal_Persist": evidence.temporal_persistence_state,
        "Spatial_Coverage": evidence.spatial_coverage_state,
        "Data_Confidence": evidence.data_confidence_state,
        "API_State": evidence.api_state,
        "Soil_Memory": evidence.soil_memory_state,
        "Risk_State": risk_state,
        **extra,
    }


def build_training_dataset(
    emdat_records: list[EMDATFloodRecord],
    exceedance_df: pd.DataFrame,
    gpm_df: pd.DataFrame,
    api_df: pd.DataFrame,
    negative_sample_ratio: float = 3.0,
    thresholds: EvidenceThresholds | None = None,
) -> pd.DataFrame:
    """Build a labeled training dataset for CPT refinement.

    Args:
        emdat_records:         EM-DAT flood events.
        exceedance_df:         Columns: date, admin1_pcode, exceedance_prob_24h,
                               exceedance_prob_72h, spatial_coverage_fraction,
                               consecutive_signal_days.
        gpm_df:                Columns: date, admin1_pcode, gpm_obs_24h.
        api_df:                Columns: date, admin1_pcode, api_mm.
        negative_sample_ratio: Ratio of negative to positive samples.

    Returns:
        DataFrame with discretised evidence columns and a Risk_State label.
    """
    rows: list[dict] = []
    n_dropped_no_pcode = 0
    n_dropped_no_match = 0

    for record in emdat_records:
        if not record.admin1_pcode:
            n_dropped_no_pcode += 1
            log.warning("emdat_record_no_pcode", event_id=record.event_id,
                        country=record.country, admin1=record.admin1_name)
            continue
        for offset in range(3):
            event_date = record.start_date + pd.Timedelta(days=offset)
            pcode = record.admin1_pcode

            exc_row = exceedance_df[
                (exceedance_df["date"] == event_date.date())
                & (exceedance_df["admin1_pcode"] == pcode)
            ]
            gpm_row = gpm_df[
                (gpm_df["date"] == event_date.date()) & (gpm_df["admin1_pcode"] == pcode)
            ]
            api_row = api_df[
                (api_df["date"] == event_date.date()) & (api_df["admin1_pcode"] == pcode)
            ]

            if exc_row.empty or gpm_row.empty:
                n_dropped_no_match += 1
                continue

            evidence = CRMAEvidence(
                exceedance_prob_24h=float(exc_row["exceedance_prob_24h"].iloc[0]),
                exceedance_prob_72h=_col_or_default(exc_row, "exceedance_prob_72h"),
                exceedance_prob_7d=_col_or_default(exc_row, "exceedance_prob_7d"),
                gpm_obs_24h=float(gpm_row["gpm_obs_24h"].iloc[0]),
                api_mm=float(api_row["api_mm"].iloc[0]) if not api_row.empty else 20.0,
                spatial_coverage_fraction=_col_or_default(
                    exc_row, "spatial_coverage_fraction", 0.5
                ),
                consecutive_signal_days=int(
                    _col_or_default(exc_row, "consecutive_signal_days", 1.0)
                ),
                sat_consecutive_days=int(
                    _col_or_default(api_row, "sat_consecutive_days", 0.0)
                ),
                thresholds=thresholds or EvidenceThresholds(),
            )
            rows.append(_evidence_row(
                evidence, 3,
                source="emdat_positive", event_id=record.event_id,
                date=event_date.date(), admin1_pcode=pcode,
            ))

    n_positive = len(rows)
    log.info(
        "positive_examples_built",
        n=n_positive,
        dropped_no_pcode=n_dropped_no_pcode,
        dropped_no_data_match=n_dropped_no_match,
    )

    n_negative = int(n_positive * negative_sample_ratio)
    flood_day_pcodes = {(r.start_date.date(), r.admin1_pcode) for r in emdat_records}

    neg_pool = exceedance_df[
        ~exceedance_df.apply(
            lambda row: (row["date"], row["admin1_pcode"]) in flood_day_pcodes,
            axis=1,
        )
    ]
    neg_sample = neg_pool.sample(min(n_negative, len(neg_pool)), random_state=42)

    for _, row in neg_sample.iterrows():
        gpm_row = gpm_df[
            (gpm_df["date"] == row["date"]) & (gpm_df["admin1_pcode"] == row["admin1_pcode"])
        ]
        api_row = api_df[
            (api_df["date"] == row["date"]) & (api_df["admin1_pcode"] == row["admin1_pcode"])
        ]
        if gpm_row.empty:
            continue

        evidence = CRMAEvidence(
            exceedance_prob_24h=float(row.get("exceedance_prob_24h", 0.0)),
            exceedance_prob_72h=float(row.get("exceedance_prob_72h", 0.0)),
            exceedance_prob_7d=float(row.get("exceedance_prob_7d", 0.0)),
            gpm_obs_24h=float(gpm_row["gpm_obs_24h"].iloc[0]),
            api_mm=float(api_row["api_mm"].iloc[0]) if not api_row.empty else 15.0,
            spatial_coverage_fraction=float(row.get("spatial_coverage_fraction", 0.1)),
            consecutive_signal_days=int(row.get("consecutive_signal_days", 0)),
            sat_consecutive_days=int(
                _col_or_default(api_row, "sat_consecutive_days", 0.0)
            ),
            thresholds=thresholds or EvidenceThresholds(),
        )
        # Label = alert level implied by the hazard (0..2, never Red): a
        # high-hazard non-event day is a legitimate Orange, keeping the
        # Orange boundary learnable (was min(.,1) → Orange never seen).
        rows.append(_evidence_row(
            evidence, evidence.forecast_hazard_state,
            source="negative_sample", event_id=None,
            date=row["date"], admin1_pcode=row["admin1_pcode"],
        ))

    df = pd.DataFrame(rows)
    dist = df["Risk_State"].value_counts().to_dict() if not df.empty else {}
    log.info(
        "training_dataset_built",
        n_positive=n_positive,
        n_negative=len(df) - n_positive,
        risk_state_distribution=dist,
    )
    return df


def refine_cpts_with_emdat(
    crma: CRMAModel,
    training_df: pd.DataFrame,
    laplace_alpha: float = 1.0,
    output_path: Path | None = None,
) -> None:
    """Refine the Risk_State CPT in-place using MLE on the EM-DAT training dataset.

    Only the Risk_State leaf node is updated — root node priors are kept
    as the expert elicitation values. Rebuilds the inference engine after
    updating the CPD so the wrapper is immediately usable.

    Args:
        crma:          Built CRMAModel instance (call build() before this).
        training_df:   Labeled data from build_training_dataset().
        laplace_alpha: Laplace smoothing parameter (1.0 = add-one smoothing).
        output_path:   If given, saves all CPTs to this JSON path.
    """
    if not PGMPY_AVAILABLE:
        raise ImportError("pgmpy is required: pip install pgmpy")
    model = crma.get_pgmpy_model()  # public API; raises RuntimeError if not built
    estimator = MaximumLikelihoodEstimator(  # type: ignore[operator]
        model,
        training_df[_EVIDENCE_COLS].copy(),
        state_names=_STATE_NAMES,
    )

    refined_cpd = estimator.estimate_cpd(
        "Risk_State",
        prior_type="dirichlet",
        pseudo_counts=laplace_alpha,
    )
    model.remove_cpds(model.get_cpds("Risk_State"))
    model.add_cpds(refined_cpd)

    if not model.check_model():
        raise ValueError("Refined model failed validation")

    # Rebuild the O(1) lookup table to pick up the updated CPD
    crma.load_cpts({
        node: model.get_cpds(node).values.tolist()
        for node in model.nodes()
        if model.get_cpds(node) is not None
    })

    if output_path is not None:
        cpts = {
            node: model.get_cpds(node).values.tolist()
            for node in model.nodes()
            if model.get_cpds(node) is not None
        }
        output_path.write_text(json.dumps(cpts, indent=2))
        log.info("refined_cpts_saved", path=str(output_path))

    log.info(
        "cpts_refined_with_emdat",
        n_training_samples=len(training_df),
        laplace_alpha=laplace_alpha,
    )


def run_validation(
    risk_results_df: pd.DataFrame,
    emdat_records: list[EMDATFloodRecord],
    output_path: Path,
    risk_threshold: int = 2,
) -> dict[str, float]:
    """Compute precision, recall, F1, and AUC-ROC against EM-DAT flood events.

    Treats any day × admin-1 where ``risk_state >= risk_threshold`` as a
    positive prediction. EM-DAT event days are positive ground-truth labels.

    Args:
        risk_results_df:  DataFrame with columns: date, admin1_pcode, risk_state, p_red.
        emdat_records:    EM-DAT flood records from :func:`load_emdat_east_africa`.
        output_path:      CSV path for the per-event hit/miss table.
        risk_threshold:   Minimum risk_state to count as a predicted flood (default 2=Orange).

    Returns:
        Dict with keys: precision, recall, f1, auc_roc, hit_rate, false_alarm_rate.
    """
    try:
        from sklearn.metrics import roc_auc_score
    except ImportError:
        raise ImportError("scikit-learn is required: pip install scikit-learn") from None

    from datetime import timedelta

    flood_days: set[tuple[str, str]] = set()
    for rec in emdat_records:
        current = rec.start_date.date()
        end = rec.end_date.date()
        while current <= end:
            flood_days.add((str(current), rec.admin1_pcode))
            current += timedelta(days=1)

    df = risk_results_df.copy()
    df["date"] = df["date"].astype(str)
    df["label"] = df.apply(
        lambda r: 1 if (r["date"], r["admin1_pcode"]) in flood_days else 0, axis=1
    )
    df["predicted"] = (df["risk_state"] >= risk_threshold).astype(int)
    df["score"] = df["p_red"].fillna(0.0)

    tp = int(((df["predicted"] == 1) & (df["label"] == 1)).sum())
    fp = int(((df["predicted"] == 1) & (df["label"] == 0)).sum())
    fn = int(((df["predicted"] == 0) & (df["label"] == 1)).sum())
    tn = int(((df["predicted"] == 0) & (df["label"] == 0)).sum())

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    far = fp / (fp + tn) if (fp + tn) > 0 else 0.0

    try:
        roc_auc = float(roc_auc_score(df["label"], df["score"]))
    except Exception:
        roc_auc = float("nan")

    per_event: list[dict] = []
    for rec in emdat_records:
        day_str = str(rec.start_date.date())
        hit_row = df[(df["date"] == day_str) & (df["admin1_pcode"] == rec.admin1_pcode)]
        predicted_state = int(hit_row["risk_state"].iloc[0]) if not hit_row.empty else -1
        per_event.append(
            {
                "event_id": rec.event_id,
                "date": day_str,
                "admin1_pcode": rec.admin1_pcode,
                "predicted_risk_state": predicted_state,
                "hit": int(predicted_state >= risk_threshold),
                "p_red": float(hit_row["p_red"].iloc[0]) if not hit_row.empty else 0.0,
            }
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(per_event).to_csv(output_path, index=False)

    metrics = {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "auc_roc": round(roc_auc, 4),
        "hit_rate": round(recall, 4),
        "false_alarm_rate": round(far, 4),
    }
    log.info("emdat_validation_complete", **metrics, n_events=len(emdat_records))
    return metrics
