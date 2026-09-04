"use client";

// Generic raster map: renders a basemap + one TiTiler-served COG layer.
// Used inside MDX storymaps for exceedance / GPM / risk rasters.

import { useEffect, useRef } from "react";
import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { BASEMAP_ATTRIBUTION, BASEMAP_MAPLIBRE_URL } from "@/lib/basemap";

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
    const map = new maplibregl.Map({
      container: ref.current,
      style: {
        version: 8,
        sources: {
          osm: {
            type: "raster",
            tiles: [BASEMAP_MAPLIBRE_URL],
            tileSize: 256,
            attribution: BASEMAP_ATTRIBUTION,
          },
          raster: { type: "raster", tiles: [tiles], tileSize: 256 },
        },
        layers: [
          { id: "basemap", type: "raster", source: "osm" },
          { id: "cog", type: "raster", source: "raster", paint: { "raster-opacity": opacity } },
        ],
      },
      center, zoom,
    });
    map.addControl(new maplibregl.NavigationControl({}), "top-right");
    return () => map.remove();
  }, [tiles, center, zoom, opacity]);

  return <div ref={ref} style={{ width: "100%", height }} className="rounded-lg overflow-hidden" />;
}
