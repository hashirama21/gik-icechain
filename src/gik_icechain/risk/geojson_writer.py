"""Write per-day admin-1 risk results to GeoJSON and EAHW portal format."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger(__name__)

_EAHW_RISK_LABELS = {0: "Green", 1: "Yellow", 2: "Orange", 3: "Red"}
_EAHW_HAZARD_TYPE = "Flood"


def write_risk_geojson(
    day: date,
    features: list[dict[str, Any]],
    output_dir: Path,
) -> Path:
    """Write a GeoJSON FeatureCollection of risk features for *day*.

    Args:
        day:        Forecast date (used in the output filename).
        features:   Pre-built GeoJSON Feature dicts from :func:`build_feature`.
        output_dir: Directory to write ``YYYY-MM-DD_admin1_risk.geojson``.

    Returns:
        Path to the written file.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"{day.isoformat()}_admin1_risk.geojson"
    out_path.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    log.info("risk_geojson_written", date=day, n_features=len(features), path=str(out_path))
    return out_path


def export_eahw_format(
    geojson_path: Path,
    output_path: Path,
) -> None:
    """Convert a GIK-IceChain daily GeoJSON to East Africa Hazard Watch Portal format.

    The EAHW portal expects a specific property schema:
        - ``hazard_type``, ``issue_date``, ``valid_date``
        - ``admin1_pcode``, ``country_code``
        - ``risk_level`` (1–4 integer), ``risk_label``
        - ``probability`` (0–100 integer, dominant risk state)

    Args:
        geojson_path: Path to the source GIK-IceChain GeoJSON file.
        output_path:  Path for the output EAHW-formatted GeoJSON.
    """
    raw = json.loads(geojson_path.read_text())
    valid_date = geojson_path.stem[:10]  # YYYY-MM-DD from filename

    eahw_features: list[dict[str, Any]] = []
    for feat in raw.get("features", []):
        src = feat["properties"]
        risk = int(src.get("risk_state", 0))
        prob_key = ["p_green", "p_yellow", "p_orange", "p_red"][risk]
        probability = round(float(src.get(prob_key, 0.0)) * 100)

        eahw_features.append(
            {
                "type": "Feature",
                "geometry": feat["geometry"],
                "properties": {
                    "hazard_type": _EAHW_HAZARD_TYPE,
                    "issue_date": valid_date,
                    "valid_date": valid_date,
                    "admin1_pcode": src.get("admin1_pcode", ""),
                    "admin1_name": src.get("admin1_name", ""),
                    "country_code": src.get("country", ""),
                    "risk_level": risk + 1,
                    "risk_label": _EAHW_RISK_LABELS.get(risk, "Unknown"),
                    "probability": probability,
                    "exceedance_24h_5y": src.get("exceedance_24h_5y", 0.0),
                    "exceedance_72h_5y": src.get("exceedance_72h_5y", 0.0),
                    "api_mm": src.get("api_mm", 0.0),
                },
            }
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps({"type": "FeatureCollection", "features": eahw_features}))
    log.info("eahw_export_written", source=str(geojson_path), output=str(output_path))


def build_feature(
    unit: Any,
    result: dict[str, Any],
    evidence: Any,
    day: date,
    emdat_flood_match: bool = False,
) -> dict[str, Any]:
    """Build a single GeoJSON Feature dict from CRMA inference outputs.

    Args:
        unit:              A pandas Series row from the admin-1 GeoDataFrame.
        result:            Output of ``CRMAModel.infer()``.
        evidence:          ``CRMAEvidence`` instance used for inference.
        day:               Forecast date.
        emdat_flood_match: True when this day × unit matches an EM-DAT event
                           (used for retrospective validation overlays).

    Returns:
        GeoJSON Feature dict with geometry and flattened risk properties.
    """
    return {
        "type": "Feature",
        "geometry": unit.geometry.__geo_interface__,
        "properties": {
            "admin1_pcode": str(unit.get("admin1_pcode", "")),
            "admin1_name": str(unit.get("admin1_name", "")),
            "country": str(unit.get("adm0_name", "")),
            "date": day.isoformat(),
            "risk_state": result["risk_state"],
            "risk_label": result["risk_label"],
            "p_green": result["p_green"],
            "p_yellow": result["p_yellow"],
            "p_orange": result["p_orange"],
            "p_red": result["p_red"],
            "exceedance_24h_5y": evidence.exceedance_prob_24h_5y,
            "exceedance_72h_5y": evidence.exceedance_prob_72h_5y,
            "api_mm": evidence.api_mm,
            "spatial_coverage": evidence.spatial_coverage_fraction,
            "emdat_flood_match": emdat_flood_match,
        },
    }
