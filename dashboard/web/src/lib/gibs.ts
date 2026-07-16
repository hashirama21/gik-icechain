// NASA GIBS / Worldview Snapshots imagery (public, no auth). Used for the
// storymap hero: the true-color satellite view of East Africa on the story's
// own date. VIIRS SNPP covers 2012 to present daily, so every archive date
// resolves.

const WVS = "https://wvs.earthdata.nasa.gov/api/v1/snapshot";
const LAYER = "VIIRS_SNPP_CorrectedReflectance_TrueColor";

// Wide 2:1 crop over East Africa (lat -2..14, lon 21..53) so the hero band
// stays sharp at banner aspect ratios.
const HERO_BBOX = "-2,21,14,53";

export function gibsHeroUrl(date: string, width = 1600, height = 800): string {
  const params = new URLSearchParams({
    REQUEST: "GetSnapshot",
    TIME: date,
    BBOX: HERO_BBOX,
    CRS: "EPSG:4326",
    LAYERS: LAYER,
    WRAP: "day",
    FORMAT: "image/jpeg",
    WIDTH: String(width),
    HEIGHT: String(height),
  });
  return `${WVS}?${params.toString()}`;
}

export const GIBS_ATTRIBUTION = "NASA Worldview · VIIRS SNPP true color";
