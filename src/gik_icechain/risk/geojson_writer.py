"""Write per-day admin-1 risk results to lightweight score files and EAHW portal format.

Output layout::

    output_dir/
        admin1_boundaries.geojson          # written once - 16 MB geometries
        2025-01-01_risk_scores.json        # written daily - ~44 KB scores only
        2025-01-02_risk_scores.json
        ...
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import structlog

from gik_icechain.shared.storage import join_uri, path_exists, write_text

log = structlog.get_logger(__name__)

_EAHW_RISK_LABELS = {0: "Green", 1: "Yellow", 2: "Orange", 3: "Red"}
_EAHW_HAZARD_TYPE = "Flood"


def write_boundaries(admin: Any, output_dir: str, storage_options: dict | None = None) -> str:
    """Write admin-1 boundary GeoJSON once under *output_dir* (local path or URI).

    Skips if the file already exists so subsequent daily runs are idempotent.

    Args:
        admin:           GeoDataFrame from the admin-1 boundaries file.
        output_dir:      Directory or S3 URI that holds all C3 outputs.
        storage_options: fsspec options (e.g. ``{"endpoint_url": ...}``) for S3.

    Returns:
        URI of the written (or existing) boundaries file.
    """
    out_uri = join_uri(output_dir, "admin1_boundaries.geojson")
    if path_exists(out_uri, storage_options):
        return out_uri

    features = [
        {
            "type": "Feature",
            "geometry": row.geometry.__geo_interface__,
            "properties": {
                "admin1_pcode": str(row.get("admin1_pcode", "")),
                "admin1_name": str(row.get("shapeName", "")),
                "country": str(row.get("shapeGroup", "")),
            },
        }
        for _, row in admin.iterrows()
    ]
    write_text(
        out_uri, json.dumps({"type": "FeatureCollection", "features": features}), storage_options
    )
    log.info("boundaries_written", path=out_uri, n_units=len(features))
    return out_uri


def write_risk_scores(
    day: date,
    scores: dict[str, dict],
    output_dir: str,
    meta: dict | None = None,
    storage_options: dict | None = None,
) -> str:
    """Write lightweight per-day risk scores (no geometry) under *output_dir*.

    Args:
        day:             Forecast date.
        scores:          Mapping pcode → score dict from :func:`build_score`.
        output_dir:      Output directory or S3 URI.
        meta:            Optional pipeline metadata (version, config hash, etc.).
        storage_options: fsspec options for S3.

    Returns:
        URI of the written scores file.
    """
    out_uri = join_uri(output_dir, f"{day.isoformat()}_risk_scores.json")
    payload: dict = {"date": day.isoformat(), "units": scores}
    if meta:
        payload["meta"] = meta
    write_text(out_uri, json.dumps(payload), storage_options)
    log.info("risk_scores_written", date=day, n_units=len(scores), path=out_uri)
    return out_uri


def build_score(
    unit: Any,
    result: dict[str, Any],
    evidence: Any,
    emdat_flood_match: bool = False,
    emdat_match_level: str | None = None,
    risk_by_rp: dict[str, dict] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Build a (pcode, score_dict) pair from CRMA inference outputs - no geometry.

    Args:
        unit:              pandas Series row from the admin-1 GeoDataFrame.
        result:            Output of ``CRMAModel.infer()``.
        evidence:          ``CRMAEvidence`` instance used for inference.
        emdat_flood_match: True when this day × unit matches an EM-DAT event
                           attributed (or alias-resolved) to this admin-1 unit.
        emdat_match_level: ``"admin1"`` for an attributed match, ``"country"``
                           when only a national-level EM-DAT event covers the
                           unit's country that day, ``None`` otherwise.

    Returns:
        Tuple of (admin1_pcode, score_dict).
    """
    pcode = str(unit.get("admin1_pcode", ""))
    score: dict[str, Any] = {
        "risk_state": result["risk_state"],
        "risk_label": result["risk_label"],
        "p_green": round(result["p_green"], 4),
        "p_yellow": round(result["p_yellow"], 4),
        "p_orange": round(result["p_orange"], 4),
        "p_red": round(result["p_red"], 4),
        "exceedance_24h": round(evidence.exceedance_prob_24h, 4),
        "exceedance_72h": round(evidence.exceedance_prob_72h, 4),
        "rp_years": getattr(evidence, "rp_years", 5),
        "api_mm": round(evidence.api_mm, 2),
        "spatial_coverage": round(evidence.spatial_coverage_fraction, 4),
        "emdat_flood_match": emdat_flood_match,
        "emdat_match_level": emdat_match_level,
    }
    if risk_by_rp:
        score["risk_by_rp"] = {
            rp: {
                k: (round(v[k], 4) if k.startswith("p_") else v[k])
                for k in ("risk_state", "risk_label", "p_green", "p_yellow", "p_orange", "p_red")
            }
            for rp, v in risk_by_rp.items()
        }
    return pcode, score


def export_eahw_format(
    scores_path: Path,
    boundaries_path: Path,
    output_path: Path,
) -> None:
    """Combine daily scores + shared boundaries into EAHW portal GeoJSON.

    Args:
        scores_path:     Path to a ``{date}_risk_scores.json`` file.
        boundaries_path: Path to the shared ``admin1_boundaries.geojson`` file.
        output_path:     Destination path for the EAHW-formatted output.
    """
    scores_data = json.loads(scores_path.read_text())
    boundaries_data = json.loads(boundaries_path.read_text())
    valid_date = scores_data["date"]
    units_by_pcode = scores_data["units"]

    eahw_features: list[dict[str, Any]] = []
    for feat in boundaries_data["features"]:
        pcode = feat["properties"]["admin1_pcode"]
        src = units_by_pcode.get(pcode, {})
        risk = int(src.get("risk_state", 0))
        prob_key = ["p_green", "p_yellow", "p_orange", "p_red"][max(0, risk)]
        probability = round(float(src.get(prob_key, 0.0)) * 100)

        eahw_features.append(
            {
                "type": "Feature",
                "geometry": feat["geometry"],
                "properties": {
                    "hazard_type": _EAHW_HAZARD_TYPE,
                    "issue_date": valid_date,
                    "valid_date": valid_date,
                    "admin1_pcode": pcode,
                    "admin1_name": feat["properties"].get("admin1_name", ""),
                    "country_code": feat["properties"].get("country", ""),
                    "risk_level": risk + 1,
                    "risk_label": _EAHW_RISK_LABELS.get(risk, "Unknown"),
                    "probability": probability,
                    "exceedance_24h": src.get("exceedance_24h", src.get("exceedance_24h_5y", 0.0)),
                    "exceedance_72h": src.get("exceedance_72h", src.get("exceedance_72h_5y", 0.0)),
                    "api_mm": src.get("api_mm", 0.0),
                },
            }
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fc: dict[str, Any] = {"type": "FeatureCollection", "features": eahw_features}
    if scores_data.get("meta"):
        fc["meta"] = scores_data["meta"]
    output_path.write_text(json.dumps(fc))
    log.info("eahw_export_written", source=str(scores_path), output=str(output_path))
