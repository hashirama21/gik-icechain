"""Replayable EM-DAT validation of production risk_scores.json output (C3).

Scores the risk files written by ``gik-icechain risk`` against the EM-DAT
ground truth already joined into each unit (``emdat_flood_match``), so it
validates the real exceedance->risk path rather than a training set. Reports
AUC-ROC on a continuous score plus precision/recall/FAR at the Yellow/Orange/
Red decision thresholds, and writes a per-unit-day hit/miss CSV.

Using the pre-joined ``emdat_flood_match`` (not raw EM-DAT pcodes) avoids the
pcode-namespace mismatch between the risk output keys and emdat.be Admin1 codes.

Usage:
    python scripts/validate_emdat.py --risk-dir results/admin1_risk
    python scripts/validate_emdat.py --risk-dir results/admin1_risk \
        --start 2024-04-22 --end 2024-04-28 --output results/validation
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

# risk_state thresholds for each decision level.
_LEVELS = {"Yellow": 1, "Orange": 2, "Red": 3}


def _load_rows(risk_dir: Path, start: str | None, end: str | None) -> list[dict]:
    """Flatten every evaluable unit-day from the risk_scores.json files."""
    rows: list[dict] = []
    for path in sorted(risk_dir.glob("*_risk_scores.json")):
        doc = json.loads(path.read_text())
        date = str(doc.get("date", path.name[:10]))
        if (start and date < start) or (end and date > end):
            continue
        for pcode, u in doc["units"].items():
            state = int(u.get("risk_state", -1))
            if state < 0 or u.get("risk_label") == "No_Data":
                continue  # no prediction issued — not evaluable
            rows.append(
                {
                    "date": date,
                    "pcode": pcode,
                    "risk_state": state,
                    "risk_label": u.get("risk_label", ""),
                    "p_red": float(u.get("p_red", 0.0) or 0.0),
                    "exceedance_72h": float(u.get("exceedance_72h", 0.0) or 0.0),
                    "label": 1 if u.get("emdat_flood_match") else 0,
                }
            )
    return rows


def _confusion(rows: list[dict], threshold: int) -> dict[str, float]:
    tp = fp = fn = tn = 0
    for r in rows:
        predicted = r["risk_state"] >= threshold
        positive = r["label"] == 1
        if predicted and positive:
            tp += 1
        elif predicted and not positive:
            fp += 1
        elif not predicted and positive:
            fn += 1
        else:
            tn += 1
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    far = fp / (fp + tn) if (fp + tn) else 0.0
    return {
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "false_alarm_rate": round(far, 4),
    }


def _auc(rows: list[dict], score_field: str) -> float:
    labels = [r["label"] for r in rows]
    if len(set(labels)) < 2:
        return float("nan")  # AUC undefined without both classes
    try:
        from sklearn.metrics import roc_auc_score
    except ImportError as exc:
        raise ImportError("scikit-learn is required: pip install scikit-learn") from exc
    return round(float(roc_auc_score(labels, [r[score_field] for r in rows])), 4)


def validate(
    risk_dir: Path,
    output_dir: Path,
    start: str | None = None,
    end: str | None = None,
    score_field: str = "p_red",
) -> dict:
    rows = _load_rows(risk_dir, start, end)
    if not rows:
        raise SystemExit(f"No evaluable unit-days found in {risk_dir} for the given range.")

    n_pos = sum(r["label"] for r in rows)
    n_neg = len(rows) - n_pos
    per_level = {name: _confusion(rows, thr) for name, thr in _LEVELS.items()}
    metrics = {
        "n_unit_days": len(rows),
        "n_positives": n_pos,
        "n_negatives": n_neg,
        "auc_roc": _auc(rows, score_field) if n_pos else float("nan"),
        "score_field": score_field,
        "recall_at_yellow": per_level["Yellow"]["recall"],
        "recall_at_orange": per_level["Orange"]["recall"],
        "recall_at_red": per_level["Red"]["recall"],
        "by_threshold": per_level,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "emdat_validation_metrics.json").write_text(json.dumps(metrics, indent=2))
    with (output_dir / "emdat_validation_events.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "pcode", "risk_label", "risk_state", "p_red", "exceedance_72h", "hit_red"])
        for r in (x for x in rows if x["label"] == 1):
            w.writerow([
                r["date"], r["pcode"], r["risk_label"], r["risk_state"],
                r["p_red"], r["exceedance_72h"], int(r["risk_state"] >= _LEVELS["Red"]),
            ])
    return metrics


def _print_report(m: dict) -> None:
    auc = m["auc_roc"]
    print(f"\nEM-DAT validation — {m['n_unit_days']} unit-days "
          f"({m['n_positives']} EM-DAT positives / {m['n_negatives']} negatives)")
    print(f"AUC-ROC ({m['score_field']}): "
          f"{'n/a (need both classes)' if isinstance(auc, float) and math.isnan(auc) else auc}")
    print(f"\n{'level':8} {'recall':>8} {'precis.':>8} {'FAR':>8} {'TP':>5} {'FP':>6} {'FN':>5}")
    for name in _LEVELS:
        b = m["by_threshold"][name]
        print(f"{name:8} {b['recall']:>8.3f} {b['precision']:>8.3f} {b['false_alarm_rate']:>8.4f} "
              f"{b['tp']:>5} {b['fp']:>6} {b['fn']:>5}")
    if m["n_positives"] == 0:
        print("\nWARNING: 0 EM-DAT positives in range — ground truth empty "
              "(check date range / pcode join).")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--risk-dir", type=Path, required=True, help="Directory of *_risk_scores.json.")
    p.add_argument("--output", type=Path, default=Path("results/validation"), help="Output dir.")
    p.add_argument("--start", help="First date YYYY-MM-DD (inclusive).")
    p.add_argument("--end", help="Last date YYYY-MM-DD (inclusive).")
    p.add_argument("--score-field", default="p_red", help="Continuous field for AUC (default p_red).")
    args = p.parse_args()
    metrics = validate(args.risk_dir, args.output, args.start, args.end, args.score_field)
    _print_report(metrics)
    print(f"\nWrote {args.output / 'emdat_validation_metrics.json'} "
          f"and {args.output / 'emdat_validation_events.csv'}")


if __name__ == "__main__":
    main()
