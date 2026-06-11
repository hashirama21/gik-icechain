// Data-contract client. Fetches the static JSON/GeoJSON the data_pipeline
// exports from the pipeline's results/ outputs into web/public/data/.

import { DATA_BASE } from "./config";
import type { RiskState, UnitRisk } from "./risk";

/** index.json — available dates and worst risk (drives the calendar). */
export interface CalendarIndex {
  [date: string]: { worst_risk: RiskState; risk_label: string; n_units: number };
}

export async function getIndex(): Promise<CalendarIndex> {
  const res = await fetch(`${DATA_BASE}/index.json`);
  if (!res.ok) throw new Error(`index.json unavailable (${res.status})`);
  return res.json();
}

/** {date}/region_risks.json — keyed by admin1_pcode. */
export async function getRegionRisks(date: string): Promise<Record<string, UnitRisk>> {
  const res = await fetch(`${DATA_BASE}/${date}/region_risks.json`);
  if (!res.ok) throw new Error(`region_risks ${date} unavailable (${res.status})`);
  return res.json();
}

/** Per-window severity + GEV exceedance + ensemble confidence, per unit. */
export interface UnitDependency {
  win: Record<string, RiskState>;          // severity class per window
  gev: Record<string, Record<string, number>>; // gev[window][rp] = P(exceed)
  confidence: { m: number; label: string };    // m/51 members converging
}

export async function getDependency(date: string): Promise<Record<string, UnitDependency>> {
  const res = await fetch(`${DATA_BASE}/${date}/dependency.json`);
  if (!res.ok) throw new Error(`dependency ${date} unavailable (${res.status})`);
  return res.json();
}

/** Per-country admin-1 boundary FeatureCollection (properties.name). */
export async function getGeoJson(countryCode: string): Promise<GeoJSON.FeatureCollection> {
  const res = await fetch(`${DATA_BASE}/geojson/${countryCode}.json`);
  if (!res.ok) throw new Error(`geojson ${countryCode} unavailable (${res.status})`);
  return res.json();
}
