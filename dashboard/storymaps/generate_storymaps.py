"""Generate dashboard data and VEDA-UI storymap datasets from risk outputs.

Two modes:

1. **Calendar JSON** (daily workflow):
   Reads the risk GeoJSON for a single date, extracts per-admin-1 summary
   statistics, and writes a compact JSON file into the calendar_map/data/
   directory for the GitHub Pages dashboard.

2. **COG + STAC batch** (manual):
   Converts a date range of risk GeoJSONs to Cloud-Optimised GeoTIFFs and
   generates a STAC item catalog for TiTiler / VEDA-UI consumption.

Usage (daily — matches daily_update.yaml):
    python generate_storymaps.py \
        --risk-dir s3://bucket/admin1_risk/ \
        --output dashboard/calendar_map/data/ \
        --date 2025-04-15

Usage (batch COG):
    python generate_storymaps.py \
        --risk-dir results/admin1_risk/ \
        --output dashboard/storymaps/output/ \
        --start 2025-04-01 --end 2025-04-30 \
        --mode cog
"""

from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import structlog

log = structlog.get_logger(__name__)

RISK_LABELS = {0: "Green", 1: "Yellow", 2: "Orange", 3: "Red", -1: "No_Data"}



def generate_calendar_json(
    geojson_path: Path,
    output_dir: Path,
    forecast_date: date,
) -> Path | None:
    """Extract a compact daily summary JSON from a risk GeoJSON.

    The output file ``{date}.json`` is a GeoJSON FeatureCollection
    stripped to the fields needed by the calendar-map dashboard:
    admin1_pcode, admin1_name, risk_state, risk_label, p_red.

    A sibling ``index.json`` is maintained listing all available dates
    with country-level worst-case risk for the calendar heatmap.

    Returns:
        Path to the written JSON, or None if the source is empty/missing.
    """
    if not geojson_path.exists():
        log.debug("geojson_not_found", path=str(geojson_path))
        return None

    with open(geojson_path) as f:
        fc = json.load(f)

    features = fc.get("features", [])
    if not features:
        log.warning("empty_geojson", path=str(geojson_path))
        return None

    # Slim down to dashboard-relevant properties
    slim_features = []
    worst_risk = -1
    for feat in features:
        props = feat.get("properties", {})
        risk = props.get("risk_state", -1)
        if risk > worst_risk:
            worst_risk = risk
        slim_features.append({
            "type": "Feature",
            "geometry": feat["geometry"],
            "properties": {
                "admin1_pcode": props.get("admin1_pcode", ""),
                "admin1_name": props.get("admin1_name", ""),
                "risk_state": risk,
                "risk_label": props.get("risk_label", RISK_LABELS.get(risk, "N/A")),
                "p_red": round(props.get("p_red", 0.0), 4),
                "p_orange": round(props.get("p_orange", 0.0), 4),
            },
        })

    slim_fc = {"type": "FeatureCollection", "features": slim_features}

    output_dir.mkdir(parents=True, exist_ok=True)
    day_path = output_dir / f"{forecast_date.isoformat()}.json"
    day_path.write_text(json.dumps(slim_fc, separators=(",", ":")))
    log.info("calendar_json_written", path=str(day_path), n_units=len(slim_features))

    # Update index.json (append or update entry for this date)
    _update_calendar_index(output_dir, forecast_date, worst_risk, len(slim_features))

    return day_path


def _update_calendar_index(
    output_dir: Path,
    forecast_date: date,
    worst_risk: int,
    n_units: int,
) -> None:
    """Maintain an ``index.json`` listing all available dates and worst risk."""
    index_path = output_dir / "index.json"
    if index_path.exists():
        try:
            index = json.loads(index_path.read_text())
        except (json.JSONDecodeError, ValueError):
            index = {}
    else:
        index = {}

    index[forecast_date.isoformat()] = {
        "worst_risk": worst_risk,
        "risk_label": RISK_LABELS.get(worst_risk, "N/A"),
        "n_units": n_units,
    }

    # Keep sorted by date
    sorted_index = dict(sorted(index.items()))
    index_path.write_text(json.dumps(sorted_index, indent=1))



def rasterise_risk_geojson(
    geojson_path: Path,
    output_path: Path,
    resolution: float = 0.25,
    bounds: tuple[float, float, float, float] = (21.0, -12.0, 52.0, 16.0),
) -> Path | None:
    """Convert an admin-1 risk GeoJSON to a Cloud-Optimised GeoTIFF.

    Args:
        geojson_path: Path to the daily risk GeoJSON FeatureCollection.
        output_path:  Path for the output COG file.
        resolution:   Grid resolution in degrees.
        bounds:       (west, south, east, north) bounding box.

    Returns:
        Path to the written COG, or None if the input is empty.
    """
    try:
        import rasterio
        from rasterio.features import rasterize
        from rasterio.transform import from_bounds
        from shapely.geometry import shape
    except ImportError:
        log.error("rasterise_dependencies_missing", msg="pip install rasterio shapely")
        return None

    with open(geojson_path) as f:
        fc = json.load(f)

    features = fc.get("features", [])
    if not features:
        log.warning("empty_geojson", path=str(geojson_path))
        return None

    west, south, east, north = bounds
    width = int((east - west) / resolution)
    height = int((north - south) / resolution)
    transform = from_bounds(west, south, east, north, width, height)

    shapes = []
    for feat in features:
        geom = shape(feat["geometry"])
        risk_state = feat["properties"].get("risk_state", -1)
        shapes.append((geom, risk_state))

    raster = rasterize(
        shapes,
        out_shape=(height, width),
        transform=transform,
        fill=-1,
        dtype=np.int8,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        output_path,
        "w",
        driver="GTiff",
        height=height,
        width=width,
        count=1,
        dtype=np.int8,
        crs="EPSG:4326",
        transform=transform,
        compress="deflate",
    ) as dst:
        dst.write(raster, 1)
        dst.update_tags(ns="rio_overview", resampling="nearest")

    log.info("cog_written", path=str(output_path))
    return output_path


def generate_stac_item(
    cog_path: Path,
    forecast_date: date,
    collection_id: str = "gik-icechain-risk",
) -> dict:
    """Generate a minimal STAC Item for a daily risk COG."""
    return {
        "type": "Feature",
        "stac_version": "1.0.0",
        "id": f"{collection_id}-{forecast_date.isoformat()}",
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [[21.0, -12.0], [52.0, -12.0], [52.0, 16.0], [21.0, 16.0], [21.0, -12.0]]
            ],
        },
        "bbox": [21.0, -12.0, 52.0, 16.0],
        "properties": {
            "datetime": f"{forecast_date.isoformat()}T00:00:00Z",
            "collection": collection_id,
        },
        "assets": {
            "data": {
                "href": str(cog_path),
                "type": "image/tiff; application=geotiff; profile=cloud-optimized",
                "roles": ["data"],
            }
        },
        "links": [],
    }



def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate dashboard calendar JSON or storymap COGs from risk GeoJSONs."
    )
    parser.add_argument(
        "--risk-dir", type=Path, required=True,
        help="Directory (or S3 prefix) containing daily risk GeoJSONs.",
    )
    parser.add_argument(
        "--output", type=Path, required=True,
        help="Output directory for calendar JSONs or COGs.",
    )
    parser.add_argument(
        "--date", type=date.fromisoformat, default=None,
        help="Single date to process (YYYY-MM-DD). Used by daily workflow.",
    )
    parser.add_argument(
        "--start", type=date.fromisoformat, default=None,
        help="Start date for batch mode (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--end", type=date.fromisoformat, default=None,
        help="End date for batch mode (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--exceedance-store", default=None,
        help="Exceedance store URI (reserved for future use).",
    )
    parser.add_argument(
        "--mode", choices=["calendar", "cog"], default="calendar",
        help="Output mode: 'calendar' (JSON summaries) or 'cog' (GeoTIFFs + STAC).",
    )
    args = parser.parse_args()

    # Resolve date range
    if args.date:
        start, end = args.date, args.date
    elif args.start and args.end:
        start, end = args.start, args.end
    else:
        parser.error("Provide either --date or both --start and --end.")
        return  # unreachable

    args.output.mkdir(parents=True, exist_ok=True)
    stac_items: list[dict] = []

    current = start
    while current <= end:
        geojson_path = args.risk_dir / f"risk_{current.isoformat()}.geojson"

        if args.mode == "calendar":
            generate_calendar_json(geojson_path, args.output, current)
        else:
            cog_path = args.output / f"risk_{current.isoformat()}.tif"
            result = rasterise_risk_geojson(geojson_path, cog_path)
            if result is not None:
                stac_items.append(generate_stac_item(cog_path, current))

        current += timedelta(days=1)

    if args.mode == "cog" and stac_items:
        catalog_path = args.output / "stac_items.json"
        catalog_path.write_text(json.dumps(stac_items, indent=2, default=str))
        log.info("stac_catalog_written", path=str(catalog_path), n_items=len(stac_items))


if __name__ == "__main__":
    main()
