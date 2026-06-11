"use client";

// VEDA-style <StoryMap> block for MDX. Resolves a TiTiler raster layer
// (risk / exceedance / gpm) and renders it over a basemap. Used inline in MDX.

import MapWidget from "@/components/map/MapWidget";
import { exceedanceTiles, gpmTiles, riskTiles } from "@/lib/titiler";

export type LayerKind = "risk" | "exceedance" | "gpm";

export interface StoryMapProps {
  layer: LayerKind;
  date: string;
  windowH?: number; // exceedance only
  rp?: number;      // exceedance only
  center?: [number, number]; // [lng, lat]
  zoom?: number;
  height?: number;
}

export default function StoryMap({
  layer, date, windowH = 24, rp = 5, center = [38, 2], zoom = 5, height = 460,
}: StoryMapProps) {
  const tiles =
    layer === "risk" ? riskTiles(date)
    : layer === "gpm" ? gpmTiles(date)
    : exceedanceTiles(date, windowH, rp);
  return <MapWidget tiles={tiles} center={center} zoom={zoom} height={height} />;
}
