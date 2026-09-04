"use client";

// Generic raster map: renders a basemap + one TiTiler-served COG layer.
// Used inside MDX storymaps for exceedance / GPM / risk rasters.

import { useEffect, useRef } from "react";
import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { TITILER_ENABLED } from "@/lib/config";

export interface MapWidgetProps {
  /** TiTiler XYZ tile template (see lib/titiler.ts). */
  tiles: string;
  center: [number, number]; // [lng, lat]
  zoom?: number;
  height?: number;
  opacity?: number;
}

export default function MapWidget({
  tiles, center, zoom = 5, height = 420, opacity = 0.8,
}: MapWidgetProps) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!ref.current) return;
    const sources: maplibregl.StyleSpecification["sources"] = {
      osm: {
        type: "raster",
        tiles: ["https://a.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}.png"],
        tileSize: 256,
        attribution: "© OpenStreetMap, © CARTO",
      },
    };
    const layers: maplibregl.LayerSpecification[] = [
      { id: "basemap", type: "raster", source: "osm" },
    ];
    // Only add the COG raster layer when a real TiTiler backend is configured;
    // otherwise the tile endpoint returns error tiles ("API KEY REQUIRED").
    if (TITILER_ENABLED) {
      sources.raster = { type: "raster", tiles: [tiles], tileSize: 256 };
      layers.push({
        id: "cog", type: "raster", source: "raster",
        paint: { "raster-opacity": opacity },
      });
    }
    const map = new maplibregl.Map({
      container: ref.current,
      style: { version: 8, sources, layers },
      center, zoom,
    });
    map.addControl(new maplibregl.NavigationControl({}), "top-right");
    return () => map.remove();
  }, [tiles, center, zoom, opacity]);

  return (
    <div style={{ position: "relative", width: "100%", height }}>
      <div ref={ref} style={{ width: "100%", height }} className="rounded-lg overflow-hidden" />
      {!TITILER_ENABLED && (
        <div
          style={{
            position: "absolute", bottom: 8, left: 8, padding: "4px 8px",
            background: "rgba(8,13,21,.85)", borderRadius: 6, fontSize: 11, color: "#9fb0c8",
          }}
        >
          Raster layer unavailable (tile server not configured)
        </div>
      )}
    </div>
  );
}
