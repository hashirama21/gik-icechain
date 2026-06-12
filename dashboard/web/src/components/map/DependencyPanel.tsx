"use client";

// Faithful Tailwind port of the v4 #dep-panel — the 5-step dependency chain,
// fed by REAL data ({date}/dependency.json + region_risks.json).

import { useState } from "react";
import Link from "next/link";
import { N_MEMBERS, RETURN_PERIODS, WINDOWS } from "@/lib/config";
import {
  DISPLAY_LABEL, DISPLAY_VAR, displayClass, riskForRp, type RiskState, type UnitRisk,
} from "@/lib/risk";
import type { UnitDependency } from "@/lib/api";

const SEV_LABEL = ["—", "Low", "Moderate", "High"];
const SEV_VAR = ["var(--c-normal)", "var(--c-low)", "var(--c-sig)", "var(--c-crit)"];

export default function DependencyPanel({
  date, unit, dep, rp,
}: { date: string; unit: UnitRisk; dep: UnitDependency | undefined; rp: string }) {
  const [win, setWin] = useState("24h");
  const gev = dep?.gev?.[win] ?? {};
  const risk = riskForRp(unit, rp);
  const cls = displayClass(risk);
  const topRp = RETURN_PERIODS.find((r) => (gev[String(r)] ?? 0) >= 0.15) ?? 5;
  const nExc = Math.round((gev[String(topRp)] ?? 0) * N_MEMBERS);

  return (
    <div className="p-3 flex flex-col gap-2.5 overflow-y-auto h-full" style={{ color: "var(--tp)" }}>
      {/* Hero */}
      <div className="rounded-lg p-2.5" style={{ background: "var(--ele)", border: "1px solid var(--brd)" }}>
        <div className="text-sm font-bold">{unit.name}</div>
        <div className="font-mono text-[10px]" style={{ color: DISPLAY_VAR[cls] }}>
          {DISPLAY_LABEL[cls]} · {risk.risk_label} · {rp}yr
        </div>
        <div className="font-mono text-[8px]" style={{ color: "var(--td)" }}>
          {unit.country} · {date} · IFS ENS 00z
        </div>
      </div>

      {/* Confidence */}
      <div className="rounded-md px-2.5 py-[7px] flex items-center gap-1.5"
        style={{ background: "var(--ele)", border: "1px solid var(--brd)" }}>
        <span>📊</span>
        <span className="font-mono text-[9px]" style={{ color: "var(--ts)" }}>
          {dep?.confidence?.m ?? 0}/{N_MEMBERS} members · <strong style={{ color: "var(--tp)" }}>
            {dep?.confidence?.label ?? "—"}</strong>
        </span>
      </div>

      {/* ③ Windows */}
      <Section title="③ Accumulation Windows — severity per window">
        <div className="grid grid-cols-4 gap-1">
          {WINDOWS.map((w) => {
            const sev = (dep?.win?.[w] ?? 0) as RiskState;
            const s = Math.max(0, sev);
            return (
              <button key={w} onClick={() => setWin(w)}
                className="rounded-md py-[7px] px-[3px] text-center transition-all"
                style={{ background: "var(--sur)",
                  border: `1px solid ${win === w ? "var(--blue)" : "var(--brd)"}` }}>
                <div className="font-mono text-[9px] font-bold" style={{ color: "var(--ts)" }}>{w}</div>
                <div className="inline-block font-mono text-[7px] font-bold px-1 py-0.5 rounded-[3px] mt-0.5"
                  style={{ background: `${SEV_VAR[s]}22`, color: SEV_VAR[s] }}>
                  {SEV_LABEL[s]}
                </div>
              </button>
            );
          })}
        </div>
      </Section>

      {/* ④ GEV */}
      <Section title={`④ GEV — ${win} window · 100y → 2y`}>
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
                <span className="w-7 text-right font-mono" style={{ color: met ? "var(--c-high)" : "var(--td)" }}>
                  {pct}%
                </span>
                <span className="w-3.5 text-[10px]">{met ? "✓" : "·"}</span>
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
              style={{ height: `${i < nExc ? 60 + (i % 5) * 8 : 18}%`,
                background: i < nExc ? "var(--c-high)" : "var(--bhi)",
                opacity: i < nExc ? 0.9 : 0.5 }} />
          ))}
        </div>
        <div className="flex justify-between font-mono text-[7px]" style={{ color: "var(--td)" }}>
          <span>M01</span><span>M26</span><span>M51</span>
        </div>
      </Section>

      <Link href={`/stories/${date}`}
        className="block text-center rounded-md py-2.5 text-[11px] font-medium"
        style={{ background: "rgba(59,130,246,.1)", color: "var(--blue)",
          border: "1px solid rgba(59,130,246,.28)" }}>
        📖 Open storymap
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
