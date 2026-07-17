"use client";

// VEDA-style <StoryMap> block for MDX and the generic per-date storymap.
// Renders vector choropleths straight from the static data contract, so
// every date in the calendar has data (no raster/TiTiler infrastructure).
// The legacy "gpm" layer maps to ensemble confidence: the contract carries
// no per-unit observed-rainfall field.

import dynamic from "next/dynamic";
import type { ChoroplethKind } from "@/components/story/StoryChoropleth";

const StoryChoropleth = dynamic(() => import("@/components/story/StoryChoropleth"), {
  ssr: false,
});

export type LayerKind = "risk" | "exceedance" | "confidence" | "gpm";

export interface StoryMapProps {
  layer: LayerKind;
  date: string;
  windowH?: number;
  rp?: number;
  center?: [number, number];
  zoom?: number;
  height?: number;
}

export default function StoryMap({
  layer, date, windowH = 24, rp = 5, height = 460,
}: StoryMapProps) {
  const kind: ChoroplethKind = layer === "gpm" ? "confidence" : layer;
  return <StoryChoropleth kind={kind} date={date} windowH={windowH} rp={rp} height={height} />;
}
