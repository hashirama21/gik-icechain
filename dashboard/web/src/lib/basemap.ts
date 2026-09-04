// Shared basemap configuration. CARTO now requires an API key on its basemap
// tiles (an "API KEY REQUIRED" watermark is baked into keyless tiles). Provide
// the key via NEXT_PUBLIC_CARTO_KEY to use the dark CARTO cartography; when it
// is absent, fall back to keyless OpenStreetMap so no watermark is ever shown.

export const CARTO_KEY = process.env.NEXT_PUBLIC_CARTO_KEY || "";

const CARTO_QUERY = CARTO_KEY ? `?key=${CARTO_KEY}` : "";

/** Leaflet tile template (supports the {s} subdomain and {r} retina tokens). */
export const BASEMAP_LEAFLET_URL = CARTO_KEY
  ? `https://{s}.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}{r}.png${CARTO_QUERY}`
  : "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png";

/** MapLibre tile template (single host, no {s}/{r} tokens). */
export const BASEMAP_MAPLIBRE_URL = CARTO_KEY
  ? `https://a.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}.png${CARTO_QUERY}`
  : "https://a.tile.openstreetmap.org/{z}/{x}/{y}.png";

export const BASEMAP_SUBDOMAINS = CARTO_KEY ? "abcd" : "abc";

export const BASEMAP_ATTRIBUTION = CARTO_KEY
  ? "© CARTO · © OpenStreetMap · GADM/HDX"
  : "© OpenStreetMap · GADM/HDX";
