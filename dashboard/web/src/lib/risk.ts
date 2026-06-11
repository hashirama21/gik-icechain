// Risk model. The pipeline emits 4 canonical classes (risk_state 0-3 + No_Data)
// matching titiler_config.yaml `risk_levels` and template.mdx `rescale "0,3"`.
// The richer 7-tone display of the v4 template is *derived* from the continuous
// probabilities (p_red/p_orange/...), keeping the COG/storymap truth at 4.

export type RiskState = -1 | 0 | 1 | 2 | 3;

export const RISK_LABEL: Record<RiskState, string> = {
  [-1]: "No data", 0: "Green", 1: "Yellow", 2: "Orange", 3: "Red",
};

/** Canonical 4-class colours (== titiler_config risk_levels). */
export const RISK_COLOR: Record<RiskState, string> = {
  [-1]: "#445577", 0: "#10B981", 1: "#F59E0B", 2: "#FF9800", 3: "#FF2626",
};

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

/** 7-tone display class (template v4 look) derived from severity. */
export type DisplayClass =
  | "normal" | "low" | "moderate" | "significant" | "high" | "very_high" | "critical";

const DISPLAY_BANDS: [number, DisplayClass][] = [
  [0.85, "critical"], [0.7, "very_high"], [0.55, "high"],
  [0.4, "significant"], [0.25, "moderate"], [0.12, "low"], [0, "normal"],
];

export function displayClass(u: RiskFields): DisplayClass {
  if (u.risk_state === -1) return "normal";
  const s = severity(u);
  for (const [thr, cls] of DISPLAY_BANDS) if (s >= thr) return cls;
  return "normal";
}

export const DISPLAY_COLOR: Record<DisplayClass, string> = {
  normal: "#2A3F6A", low: "#4D0808", moderate: "#7A0D0D", significant: "#B31515",
  high: "#E01F1F", very_high: "#FF2626", critical: "#FF6B6B",
};

// 7-class display labels + CSS-var backgrounds (== v4 RLBL / .cal-cell.*).
export const DISPLAY_LABEL: Record<DisplayClass, string> = {
  normal: "Normal", low: "Low", moderate: "Moderate", significant: "Significant",
  high: "High", very_high: "Very High", critical: "Critical",
};

export const DISPLAY_ORDER: DisplayClass[] = [
  "critical", "very_high", "high", "significant", "moderate", "low", "normal",
];

/** CSS variable for a display class background (matches globals.css tokens). */
export const DISPLAY_VAR: Record<DisplayClass | "no_data", string> = {
  critical: "var(--c-crit)", very_high: "var(--c-vhi)", high: "var(--c-high)",
  significant: "var(--c-sig)", moderate: "var(--c-mod)", low: "var(--c-low)",
  normal: "var(--c-normal)", no_data: "var(--c-nodata)",
};

/** Pulse animation class for high-severity calendar cells. */
export function cellPulse(cls: DisplayClass): string {
  return cls === "critical" ? "cell-crit"
    : cls === "very_high" ? "cell-vhi"
    : cls === "high" ? "cell-high" : "";
}
