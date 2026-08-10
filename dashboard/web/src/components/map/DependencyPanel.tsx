"use client";

// Faithful Tailwind port of the v4 #dep-panel  the 5-step dependency chain,
// fed by REAL data ({date}/dependency.json + region_risks.json).

import { useState } from "react";
import Link from "next/link";
import { BarChart3, BookOpen, Check } from "lucide-react";
import { LEAD_MAX, N_MEMBERS, RETURN_PERIODS, WINDOWS, type LeadChoice } from "@/lib/config";
import {
  DISPLAY_BORDER_VAR, DISPLAY_LABEL, DISPLAY_TEXT_VAR, DISPLAY_VAR, displayClass,
  riskForRp, type DisplayClass, type RiskState, type UnitRisk,
} from "@/lib/risk";
import type { UnitDependency } from "@/lib/api";

const SEV_LABEL = ["", "Low", "Moderate", "High"];
const SEV_CLASS: DisplayClass[] = ["normal", "low", "moderate", "high"];

/** Client-side mirror of the pipeline's _sev(p) for the per-lead view. */
function sevFromGev(g: Record<string, number>): RiskState {
  const p = Math.max(0, ...Object.values(g), 0);
  return (p >= 0.5 ? 3 : p >= 0.3 ? 2 : p >= 0.15 ? 1 : 0) as RiskState;
}

export default function DependencyPanel({
  date, unit, dep, rp,
}: { date: string; unit: UnitRisk; dep: UnitDependency | undefined; rp: string }) {
  const [win, setWin] = useState("24h");
  const [lead, setLead] = useState<LeadChoice>(LEAD_MAX);
  const byLead = dep?.gev_by_lead;
  const leadKeys = byLead ? Object.keys(byLead).map(Number).sort((a, b) => a - b) : [];

  // GEV source for a window: the chosen lead day, or the max-over-horizon view.
  const gevForWin = (w: string): Record<string, number> =>
    lead === LEAD_MAX ? (dep?.gev?.[w] ?? {}) : (byLead?.[String(lead)]?.[w] ?? {});

  const gev = gevForWin(win);
  const risk = riskForRp(unit, rp);
  const cls = displayClass(risk);
  const topRp = RETURN_PERIODS.find((r) => (gev[String(r)] ?? 0) >= 0.15) ?? 5;
  const nExc = Math.round((gev[String(topRp)] ?? 0) * N_MEMBERS);

  return (
    <div className="p-3 flex flex-col gap-2.5 overflow-y-auto h-full" style={{ color: "var(--tp)" }}>
      {/* Hero */}
      <div className="rounded-lg p-2.5" style={{ background: "var(--ele)", border: "1px solid var(--brd)" }}>
        <div className="text-sm font-bold">{unit.name}</div>
        <div className="font-mono text-[10px] mt-0.5">
          <span className="font-bold px-1.5 py-0.5 rounded-[3px]"
            style={{ background: DISPLAY_VAR[cls], color: DISPLAY_TEXT_VAR[cls],
              border: `1px solid ${DISPLAY_BORDER_VAR[cls]}` }}>
            {DISPLAY_LABEL[cls]}
          </span>
          <span style={{ color: "var(--ts)" }}> · {risk.risk_label} · {rp}yr</span>
        </div>
        <div className="font-mono text-[8px]" style={{ color: "var(--td)" }}>
          {unit.country} · init {date} · IFS ENS 00z
        </div>
        <div className="font-mono text-[8px] mt-0.5 leading-tight" style={{ color: "var(--td)" }}>
          Forecast initialisation date. Severity = worst case anywhere in the
          forecast horizon (max over lead time).
        </div>
      </div>

      {/* Confidence */}
      <div className="rounded-md px-2.5 py-[7px] flex items-center gap-1.5"
        style={{ background: "var(--ele)", border: "1px solid var(--brd)" }}>
        <BarChart3 size={12} style={{ color: "var(--ts)", flexShrink: 0 }} />
        <span className="font-mono text-[9px]" style={{ color: "var(--ts)" }}>
          {dep?.confidence?.m ?? 0}/{N_MEMBERS} members · <strong style={{ color: "var(--tp)" }}>
            {dep?.confidence?.label ?? ""}</strong>
        </span>
      </div>

      {/* Lead-time sub-selection (only when the store carries the per-lead view) */}
      {leadKeys.length > 0 && (
        <Section title="① Forecast lead time  which valid day">
          <div className="flex flex-wrap gap-1">
            {[LEAD_MAX as LeadChoice, ...leadKeys].map((l) => {
              const active = lead === l;
              const label = l === LEAD_MAX ? "Max" : `L${l}`;
              return (
                <button key={String(l)} onClick={() => setLead(l)}
                  className="font-mono text-[9px] font-bold px-2 py-1 rounded-[4px] transition-colors"
                  style={active
                    ? { background: "var(--blue)", color: "#fff" }
                    : { background: "var(--sur)", color: "var(--ts)", border: "1px solid var(--brd)" }}>
                  {label}
                </button>
              );
            })}
          </div>
          <div className="font-mono text-[8px] mt-1.5 leading-tight" style={{ color: "var(--td)" }}>
            {lead === LEAD_MAX
              ? "Worst step anywhere in the forecast horizon."
              : `Valid day ${lead} (${(lead as number) * 24}–${((lead as number) + 1) * 24} h after init).`}
          </div>
        </Section>
      )}

      {/* ③ Windows */}
      <Section title="③ Accumulation Windows  worst over forecast horizon">
        <div className="grid grid-cols-4 gap-1">
          {WINDOWS.map((w) => {
            const s = lead === LEAD_MAX
              ? Math.max(0, dep?.win?.[w] ?? 0)
              : sevFromGev(gevForWin(w));
            return (
              <button key={w} onClick={() => setWin(w)}
                className="rounded-md py-[7px] px-[3px] text-center transition-all"
                style={{ background: "var(--sur)",
                  border: `1px solid ${win === w ? "var(--blue)" : "var(--brd)"}` }}>
                <div className="font-mono text-[9px] font-bold" style={{ color: "var(--ts)" }}>{w}</div>
                <div className="inline-block font-mono text-[7px] font-bold px-1 py-0.5 rounded-[3px] mt-0.5"
                  style={{ background: DISPLAY_VAR[SEV_CLASS[s]], color: DISPLAY_TEXT_VAR[SEV_CLASS[s]],
                    border: `1px solid ${DISPLAY_BORDER_VAR[SEV_CLASS[s]]}` }}>
                  {SEV_LABEL[s]}
                </div>
              </button>
            );
          })}
        </div>
      </Section>

      {/* ④ GEV */}
      <Section title={`④ GEV  ${win} window · 100y → 2y`}>
        <div className="space-y-1.5">
          {RETURN_PERIODS.slice().reverse().map((rp) => {
            const p = gev[String(rp)] ?? 0;
            const pct = Math.round(p * 100);
            const met = p >= 0.15;
            return (
              <div key={rp} className="flex items-center gap-1.5 text-[11px]">
                <span className="w-8 font-mono" style={{ color: "var(--ts)" }}>{rp}y</span>
                <div className="flex-1 h-[7px] rounded overflow-hidden"
                  style={{ background: "var(--bg)", border: "1px solid var(--brd)" }}>
                  <div className="h-full rounded transition-[width] duration-500"
                    style={{ width: `${pct}%`, background: met ? "var(--c-high)" : "var(--bhi)" }} />
                </div>
                <span className="w-7 text-right font-mono" style={{ color: met ? "var(--c-txt-h)" : "var(--td)" }}>
                  {pct}%
                </span>
                <span className="w-3.5 inline-flex justify-center text-[10px]">
                  {met ? <Check size={10} style={{ color: "var(--c-txt-h)" }} /> : "·"}
                </span>
              </div>
            );
          })}
        </div>
      </Section>

      {/* ⑤ Ensemble */}
      <Section title={`⑤ Ensemble · ${topRp}y · ${nExc}/${N_MEMBERS} exceeding`}>
        <div className="flex items-end gap-px h-9 my-1">
          {Array.from({ length: N_MEMBERS }, (_, i) => (
            <div key={i} className="flex-1 rounded-t-sm min-w-[2px]"
              style={{ height: i < nExc ? "72%" : "18%",
                background: i < nExc ? "var(--c-high)" : "var(--bhi)",
                opacity: i < nExc ? 0.9 : 0.5 }} />
          ))}
        </div>
        <div className="flex justify-between font-mono text-[7px]" style={{ color: "var(--td)" }}>
          <span>M01</span><span>M26</span><span>M51</span>
        </div>
      </Section>

      <Link href={`/stories/${date}`}
        className="flex items-center justify-center gap-1.5 rounded-md py-2.5 text-[11px] font-medium"
        style={{ background: "rgba(59,130,246,.1)", color: "var(--blue)",
          border: "1px solid rgba(59,130,246,.28)" }}>
        <BookOpen size={12} /> Open storymap
      </Link>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-lg p-2.5" style={{ background: "var(--ele)", border: "1px solid var(--brd)" }}>
      <div className="font-mono text-[8px] tracking-[1.5px] uppercase mb-2" style={{ color: "var(--td)" }}>
        {title}
      </div>
      {children}
    </div>
  );
}
