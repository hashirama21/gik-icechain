// Data-contract client. Fetches the static JSON/GeoJSON the data_pipeline
// exports from the pipeline's results/ outputs into web/public/data/.

import { DATA_BASE } from "./config";
import type { RiskState, UnitRisk } from "./risk";

const inFlight = new Map<string, Promise<unknown>>();

function fetchJson<T>(path: string, label: string): Promise<T> {
  const url = `${DATA_BASE}/${path}`;
  const hit = inFlight.get(url);
  if (hit) return hit as Promise<T>;

  const pending = fetch(url)
    .then((res) => {
      if (!res.ok) throw new Error(`${label} unavailable (${res.status})`);
      return res.json() as Promise<T>;
    })
    .catch((err) => {
      inFlight.delete(url);
      throw err;
    });

  inFlight.set(url, pending);
  return pending;
}

/** index.json  available dates and worst risk (drives the calendar). */
export interface CalendarIndex {
  [date: string]: { worst_risk: RiskState; risk_label: string; n_units: number };
}

export function getIndex(): Promise<CalendarIndex> {
  return fetchJson<CalendarIndex>("index.json", "index.json");
}

/** {date}/region_risks.json  keyed by admin1_pcode. */
export function getRegionRisks(date: string): Promise<Record<string, UnitRisk>> {
  return fetchJson(`${date}/region_risks.json`, `region_risks ${date}`);
}

/** Per-window severity + GEV exceedance + ensemble confidence, per unit. */
export interface UnitDependency {
  win: Record<string, RiskState>;          // severity class per window
  gev: Record<string, Record<string, number>>; // gev[window][rp] = P(exceed)
  confidence: { m: number; label: string };    // m/51 members converging
}

export function getDependency(date: string): Promise<Record<string, UnitDependency>> {
  return fetchJson(`${date}/dependency.json`, `dependency ${date}`);
}

/** Per-country admin-1 boundary FeatureCollection (properties.name). */
export function getGeoJson(countryCode: string): Promise<GeoJSON.FeatureCollection> {
  return fetchJson(`geojson/${countryCode}.json`, `geojson ${countryCode}`);
}

/** {date}/overlays/overlays.json - static exceedance PNGs with their bounds. */
export interface OverlayManifest {
  [file: string]: { bounds: [[number, number], [number, number]]; window: number; rp: number };
}

export function getOverlays(date: string): Promise<OverlayManifest> {
  return fetchJson(`${date}/overlays/overlays.json`, `overlays ${date}`);
}

export function overlayUrl(date: string, file: string): string {
  return `${DATA_BASE}/${date}/overlays/${file}`;
}