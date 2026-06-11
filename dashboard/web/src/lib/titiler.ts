// TiTiler tile-URL builders. TiTiler turns a COG (on S3) into XYZ tiles on the
// fly; MapLibre consumes the returned {z}/{x}/{y} template. Only RASTER layers
// go through TiTiler — the admin-1 risk layer is vector (GeoJSON), no tiles.

import { COG_BASE, TITILER_BASE } from "./config";

function cogUrl(key: string): string {
  return `${COG_BASE}/${key}`;
}

interface CogTileOpts {
  colormap_name?: string;
  rescale?: string;
  colormap?: string; // JSON-encoded discrete colormap
}

/** Build a TiTiler XYZ raster-tile template for a COG key. */
export function cogTiles(key: string, opts: CogTileOpts = {}): string {
  const params = new URLSearchParams({ url: cogUrl(key) });
  if (opts.colormap_name) params.set("colormap_name", opts.colormap_name);
  if (opts.colormap) params.set("colormap", opts.colormap);
  if (opts.rescale) params.set("rescale", opts.rescale);
  return `${TITILER_BASE}/cog/tiles/{z}/{x}/{y}.png?${params.toString()}`;
}

/** Discrete risk colormap (0..3) — matches titiler_config.yaml risk_levels. */
const RISK_CMAP = JSON.stringify({
  "0": [76, 175, 80, 255], "1": [255, 235, 59, 255],
  "2": [255, 152, 0, 255], "3": [244, 67, 54, 255],
});

export function riskTiles(date: string): string {
  return cogTiles(`risk_${date}.tif`, { colormap: RISK_CMAP });
}

export function exceedanceTiles(date: string, windowH: number, rp: number): string {
  return cogTiles(`exceedance_${date}_${windowH}_${rp}.tif`, {
    colormap_name: "ylorrd", rescale: "0,1",
  });
}

export function gpmTiles(date: string): string {
  return cogTiles(`gpm_${date}.tif`, { colormap_name: "blues", rescale: "0,100" });
}
