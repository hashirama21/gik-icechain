// Central configuration — mirrors configs/default.yaml (component2) and
// dashboard/storymaps/titiler_config.yaml. Single source of truth for the UI.

export const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH || "";

/** Static data contract (GitHub Pages) produced by data_pipeline/. */
export const DATA_BASE = `${BASE_PATH}/data`;

/** TiTiler endpoint serving COGs (local Docker by default; Lambda in prod). */
export const TITILER_BASE =
  process.env.NEXT_PUBLIC_TITILER_BASE || "http://localhost:8000";

/** Public bucket holding the COGs TiTiler reads (s3:// or https://). */
export const COG_BASE =
  process.env.NEXT_PUBLIC_COG_BASE || "https://gik-icechain-cogs.s3.eu-west-1.amazonaws.com/cogs";

/** Accumulation windows (component2.windows_h) — labelled for the UI. */
export const WINDOWS = ["3h", "6h", "12h", "24h", "48h", "72h", "7d"] as const;
export const WINDOW_HOURS: Record<string, number> = {
  "3h": 3, "6h": 6, "12h": 12, "24h": 24, "48h": 48, "72h": 72, "7d": 168,
};

/** Return periods in years (component2.return_periods). */
export const RETURN_PERIODS = [2, 5, 10, 20, 40, 100] as const;

/** Ensemble size (IFS ENS: 50 perturbed + 1 control). */
export const N_MEMBERS = 51;

/** East Africa map bounds [[south, west], [north, east]] (bbox -14.5..25 lat). */
export const EA_BOUNDS: [[number, number], [number, number]] = [
  [-14.5, 22], [25, 54],
];

/** 16 countries covered by the pipeline (238 admin-1 units). */
export interface Country { code: string; name: string; flag: string; }
export const COUNTRIES: Country[] = [
  { code: "ken", name: "Kenya", flag: "🇰🇪" },
  { code: "eth", name: "Ethiopia", flag: "🇪🇹" },
  { code: "uga", name: "Uganda", flag: "🇺🇬" },
  { code: "tza", name: "Tanzania", flag: "🇹🇿" },
  { code: "som", name: "Somalia", flag: "🇸🇴" },
  { code: "rwa", name: "Rwanda", flag: "🇷🇼" },
  { code: "bdi", name: "Burundi", flag: "🇧🇮" },
  { code: "ssd", name: "South Sudan", flag: "🇸🇸" },
  { code: "eri", name: "Eritrea", flag: "🇪🇷" },
  { code: "dji", name: "Djibouti", flag: "🇩🇯" },
  { code: "mdg", name: "Madagascar", flag: "🇲🇬" },
  { code: "sdn", name: "Sudan", flag: "🇸🇩" },
  { code: "com", name: "Comoros", flag: "🇰🇲" },
  { code: "syc", name: "Seychelles", flag: "🇸🇨" },
  { code: "mwi", name: "Malawi", flag: "🇲🇼" },
  { code: "zmb", name: "Zambia", flag: "🇿🇲" },
];

export const COUNTRY_BY_CODE: Record<string, Country> = Object.fromEntries(
  COUNTRIES.map((c) => [c.code, c]),
);
