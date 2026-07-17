// Risk model. The pipeline emits 4 canonical classes (risk_state 0-3 + No_Data)
// matching titiler_config.yaml `risk_levels` and template.mdx `rescale "0,3"`.
// The richer 7-tone display of the v4 template is *derived* from the continuous
// probabilities (p_red/p_orange/...), keeping the COG/storymap truth at 4.

export type RiskState = -1 | 0 | 1 | 2 | 3;

export const RISK_LABEL: Record<RiskState, string> = {
  [-1]: "No data", 0: "Green", 1: "Yellow", 2: "Orange", 3: "Red",
};

/** Canonical 4-class colours. Orange is #C2410C (not titiler's #FF9800):
 *  #FF9800 vs #F59E0B is dE 1.2 under protanopia - indistinguishable. */
export const RISK_COLOR: Record<RiskState, string> = {
  [-1]: "#445577", 0: "#10B981", 1: "#F59E0B", 2: "#C2410C", 3: "#FF2626",
};

/** Alert-extent scale for calendar-level severity. worst_risk (max over 238
 *  units) saturates at archive scale: with a chronic riverine background of
 *  ~25 Orange+ units/day, some unit is Red almost every day. Class by how
 *  MANY units alert instead. Thresholds are quartile-anchored on the
 *  1 270-day archive (median 25, p95 51): <15 background, 15-29 elevated,
 *  30-49 widespread, >=50 exceptional. */
export const EXTENT_THRESHOLDS = [15, 30, 50] as const;

export function alertExtent(entry: { n_orange?: number; n_red?: number }): number | null {
  if (entry.n_orange === undefined || entry.n_red === undefined) return null;
  return entry.n_orange + entry.n_red;
}

export function extentClass(entry: {
  worst_risk: RiskState;
  n_orange?: number;
  n_red?: number;
}): RiskState {
  const n = alertExtent(entry);
  if (n === null) return entry.worst_risk;
  if (n >= EXTENT_THRESHOLDS[2]) return 3;
  if (n >= EXTENT_THRESHOLDS[1]) return 2;
  if (n >= EXTENT_THRESHOLDS[0]) return 1;
  return 0;
}

/** Risk fields for one return period. */
export interface RiskFields {
  risk_state: RiskState;
  risk_label: string;
  p_green: number;
  p_yellow: number;
  p_orange: number;
  p_red: number;
}

/** Per-admin-1 risk record from {date}/region_risks.json. */
export interface UnitRisk extends RiskFields {
  pcode: string;
  name: string;
  country: string;
  /** Per-return-period risk so the UI can switch 2yr↔5yr. */
  risk_by_rp?: Record<string, RiskFields>;
}

/** Resolve the risk fields for a chosen return period (falls back to top-level). */
export function riskForRp(unit: UnitRisk, rp: string): RiskFields {
  return unit.risk_by_rp?.[rp] ?? unit;
}

/**
 * Derive a continuous 0..1 severity from the probability vector, so the v4
 * template's 7-tone palette can be reproduced without inventing data.
 * severity = p_red + 0.6*p_orange + 0.3*p_yellow.
 */
export function severity(u: Pick<UnitRisk, "p_red" | "p_orange" | "p_yellow">): number {
  return Math.min(1, u.p_red + 0.6 * u.p_orange + 0.3 * u.p_yellow);
}

/** 7-tone display class (template v4 look) derived from severity, plus
 *  "no_data" so missing data is never presented as Normal. */
export type DisplayClass =
  | "normal" | "low" | "moderate" | "significant" | "high" | "very_high" | "critical"
  | "no_data";

const DISPLAY_BANDS: [number, DisplayClass][] = [
  [0.85, "critical"], [0.7, "very_high"], [0.55, "high"],
  [0.4, "significant"], [0.25, "moderate"], [0.12, "low"], [0, "normal"],
];

export function displayClass(u: RiskFields): DisplayClass {
  if (u.risk_state === -1) return "no_data";
  const s = severity(u);
  for (const [thr, cls] of DISPLAY_BANDS) if (s >= thr) return cls;
  return "normal";
}

// 7-class display labels + CSS-var backgrounds (== v4 RLBL / .cal-cell.*).
export const DISPLAY_LABEL: Record<DisplayClass, string> = {
  normal: "Normal", low: "Low", moderate: "Moderate", significant: "Significant",
  high: "High", very_high: "Very High", critical: "Critical", no_data: "No data",
};

/** Most severe first; no_data sorts after normal (least alarming). */
export const DISPLAY_ORDER: DisplayClass[] = [
  "critical", "very_high", "high", "significant", "moderate", "low", "normal", "no_data",
];

/** CSS variable for a display class background (matches globals.css tokens). */
export const DISPLAY_VAR: Record<DisplayClass, string> = {
  critical: "var(--c-crit)", very_high: "var(--c-vhi)", high: "var(--c-high)",
  significant: "var(--c-sig)", moderate: "var(--c-mod)", low: "var(--c-low)",
  normal: "var(--c-normal)", no_data: "var(--c-nodata)",
};

/** Text colour readable ON the matching DISPLAY_VAR fill (globals.css --c-txt-*).
 *  Fill colours must never be used as text: the low end of the ramp is
 *  near-black in dark mode and near-white in light mode. */
export const DISPLAY_TEXT_VAR: Record<DisplayClass, string> = {
  critical: "var(--c-txt-c)", very_high: "var(--c-txt-v)", high: "var(--c-txt-h)",
  significant: "var(--c-txt-s)", moderate: "var(--c-txt-m)", low: "var(--c-txt-l)",
  normal: "var(--c-txt-n)", no_data: "var(--c-txt-n)",
};

/** Chip/cell border: risk classes get --c-brd-r so the dark low tones don't
 *  melt into dark surfaces; normal/no_data stay on the neutral --c-brd. */
export const DISPLAY_BORDER_VAR: Record<DisplayClass, string> = {
  critical: "var(--c-brd-r)", very_high: "var(--c-brd-r)", high: "var(--c-brd-r)",
  significant: "var(--c-brd-r)", moderate: "var(--c-brd-r)", low: "var(--c-brd-r)",
  normal: "var(--c-brd)", no_data: "var(--c-brd)",
};

/** Pulse animation class for high-severity calendar cells. */
export function cellPulse(cls: DisplayClass): string {
  return cls === "critical" ? "cell-crit"
    : cls === "very_high" ? "cell-vhi"
    : cls === "high" ? "cell-high" : "";
}
