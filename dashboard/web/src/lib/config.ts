export const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH || "";

/** Data contract produced by data_pipeline/ and served from S3 (like COG_BASE).
 *  Defaults to the bundled `${BASE_PATH}/data` for local dev; in deploy set
 *  NEXT_PUBLIC_DATA_BASE to the public bucket URL so nothing is committed. */
export const DATA_BASE =
  process.env.NEXT_PUBLIC_DATA_BASE || `${BASE_PATH}/data`;

/** TiTiler endpoint serving COGs (local Docker by default; Lambda in prod). */
export const TITILER_BASE =
  process.env.NEXT_PUBLIC_TITILER_BASE || "http://localhost:8000";

/** Whether a real TiTiler endpoint is configured. When it is unset (or left at
 *  the localhost dev default), raster COG layers have no valid backend, so
 *  consumers render the basemap only instead of requesting error tiles. */
export const TITILER_ENABLED =
  !!process.env.NEXT_PUBLIC_TITILER_BASE &&
  !process.env.NEXT_PUBLIC_TITILER_BASE.includes("localhost");

/** Public bucket holding the COGs TiTiler reads (s3:// or https://). */
export const COG_BASE =
  process.env.NEXT_PUBLIC_COG_BASE || "https://gik-icechain-cogs.s3.eu-west-1.amazonaws.com/cogs";

/** Accumulation windows (component2.windows_h)  labelled for the UI. */
export const WINDOWS = ["3h", "6h", "12h", "24h", "48h", "72h", "7d"] as const;
export const WINDOW_HOURS: Record<string, number> = {
  "3h": 3, "6h": 6, "12h": 12, "24h": 24, "48h": 48, "72h": 72, "7d": 168,
};

/** Return periods in years (component2.return_periods). */
export const RETURN_PERIODS = [2, 5, 10, 20, 40, 100] as const;

/** Forecast lead days offered in Step 1 when the store carries the per-lead view.
 *  `"max"` = the default max-over-horizon view (worst step anywhere in the horizon). */
export const LEAD_MAX = "max" as const;
export type LeadChoice = number | typeof LEAD_MAX;

/** RPs the risk engine produces risk_state for (component3 rp_signal_options). */
export const RISK_RETURN_PERIODS = ["2", "5"] as const;

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
