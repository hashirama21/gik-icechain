"use client";

// MAP tab  country/region sidebar · admin-1 risk map · 5-step dependency chain.

import { useEffect, useMemo, useState } from "react";
import dynamic from "next/dynamic";
import { Globe } from "lucide-react";
import { COUNTRIES, COUNTRY_BY_CODE, RISK_RETURN_PERIODS } from "@/lib/config";
import {
  DISPLAY_BORDER_VAR, DISPLAY_LABEL, DISPLAY_TEXT_VAR, DISPLAY_VAR, displayClass,
  DISPLAY_ORDER, riskForRp, type DisplayClass, type UnitRisk,
} from "@/lib/risk";
import { getDependency, type UnitDependency } from "@/lib/api";
import DependencyPanel from "../map/DependencyPanel";

// Leaflet must be client-only (no SSR).
const LeafletMap = dynamic(() => import("../map/LeafletMap"), { ssr: false });

export interface MapTabProps {
  date: string | null;
  risks: Record<string, UnitRisk>;
  rp: string;
  onRp: (rp: string) => void;
}

export default function MapTab({ date, risks, rp, onRp }: MapTabProps) {
  const [country, setCountry] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [deps, setDeps] = useState<Record<string, UnitDependency>>({});

  // dependency.json only feeds the panel, so pay for it when a unit is picked
  // rather than on every date change. The api layer dedupes and caches it.
  useEffect(() => {
    if (!date || !selected) return;
    let cancelled = false;
    getDependency(date)
      .then((d) => { if (!cancelled) setDeps(d); })
      .catch(() => { if (!cancelled) setDeps({}); });
    return () => { cancelled = true; };
  }, [date, selected]);

  // worst display class per country (for the sidebar chips), at the chosen RP
  const countryWorst = useMemo(() => {
    const m: Record<string, DisplayClass> = {};
    for (const u of Object.values(risks)) {
      const code = u.country.toLowerCase();
      const cls = displayClass(riskForRp(u, rp));
      if (!m[code] || DISPLAY_ORDER.indexOf(cls) < DISPLAY_ORDER.indexOf(m[code])) m[code] = cls;
    }
    return m;
  }, [risks, rp]);

  const regionList = useMemo(() => {
    if (!country) return [];
    return Object.values(risks)
      .filter((u) => u.country.toLowerCase() === country)
      .sort((a, b) =>
        DISPLAY_ORDER.indexOf(displayClass(riskForRp(a, rp)))
        - DISPLAY_ORDER.indexOf(displayClass(riskForRp(b, rp))));
  }, [country, risks, rp]);

  const selUnit = selected ? risks[selected] : null;

  return (
    <div className="flex flex-row w-full h-full">
      {/* Sidebar */}
      <aside className="hidden md:flex w-[220px] flex-col overflow-y-auto shrink-0"
        style={{ background: "var(--sur)", borderRight: "1px solid var(--brd)" }}>
        {!country ? (
          <div className="p-3" style={{ background: "var(--ele)" }}>
            <div className="font-mono text-[8px] tracking-[1.5px] uppercase mb-2" style={{ color: "var(--td)" }}>
              Countries
            </div>
            {COUNTRIES.map((c) => {
              const cls = countryWorst[c.code] ?? "no_data";
              return (
                <button key={c.code} onClick={() => setCountry(c.code)}
                  className="w-full flex items-center gap-2 px-2 py-[7px] rounded-[5px] mb-0.5 text-left hover:bg-[var(--hov)]">
                  <span className="text-[14px]">{c.flag}</span>
                  <span className="text-[12px] font-medium flex-1" style={{ color: "var(--tp)" }}>{c.name}</span>
                  <span className="font-mono text-[8px] font-bold px-1.5 py-0.5 rounded-[3px]"
                    style={{ background: DISPLAY_VAR[cls], color: DISPLAY_TEXT_VAR[cls],
                             border: `1px solid ${DISPLAY_BORDER_VAR[cls]}` }}>
                    {DISPLAY_LABEL[cls]}
                  </span>
                </button>
              );
            })}
          </div>
        ) : (
          <div className="p-3">
            <button onClick={() => { setCountry(null); setSelected(null); }}
              className="font-mono text-[10px] mb-1.5 block" style={{ color: "var(--blue)" }}>← Back</button>
            <div className="font-mono text-[8px] tracking-[1.5px] uppercase mb-2" style={{ color: "var(--td)" }}>
              {COUNTRY_BY_CODE[country]?.flag} {COUNTRY_BY_CODE[country]?.name}  Admin-1
            </div>
            {regionList.map((u) => {
              const cls = displayClass(riskForRp(u, rp));
              return (
                <button key={u.pcode} onClick={() => setSelected(u.pcode)}
                  className="w-full flex items-center gap-1.5 px-1.5 py-1.5 rounded-[5px] mb-0.5 text-left hover:bg-[var(--hov)]"
                  style={{ background: selected === u.pcode ? "var(--ele)" : undefined }}>
                  <span className="w-[7px] h-[7px] rounded-sm shrink-0" style={{ background: DISPLAY_VAR[cls] }} />
                  <span className="text-[11px] flex-1" style={{ color: "var(--tp)" }}>{u.name}</span>
                  <span className="font-mono text-[8px] font-bold px-1.5 py-0.5 rounded-[3px]"
                    style={{ background: DISPLAY_VAR[cls], color: DISPLAY_TEXT_VAR[cls],
                             border: `1px solid ${DISPLAY_BORDER_VAR[cls]}` }}>
                    {DISPLAY_LABEL[cls]}
                  </span>
                </button>
              );
            })}
          </div>
        )}
      </aside>

      {/* Map */}
      <div className="flex-1 flex flex-col overflow-hidden">
        <div className="flex items-center gap-1.5 px-2.5 py-1.5 flex-wrap shrink-0"
          style={{ background: "var(--sur)", borderBottom: "1px solid var(--brd)" }}>
          <span className="font-mono text-[9px] flex-1 truncate" style={{ color: "var(--td)" }}>
            {country
              ? `${COUNTRY_BY_CODE[country]?.name} · admin-1`
              : `East Africa Overview · ${COUNTRIES.length} countries · 238 admin-1`}
          </span>
          <div className="flex rounded p-[2px] gap-px" style={{ background: "var(--bg)", border: "1px solid var(--brd)" }}>
            {RISK_RETURN_PERIODS.map((opt) => (
              <button key={opt} onClick={() => onRp(opt)}
                className="px-2 py-0.5 rounded text-[10px] font-mono transition-colors"
                style={rp === opt
                  ? { background: "var(--blue)", color: "#fff" }
                  : { color: "var(--ts)" }}>
                {opt}yr
              </button>
            ))}
          </div>
        </div>
        <div className="flex-1 relative">
          <LeafletMap risks={risks} rp={rp} selected={selected} onSelect={setSelected} />
          <div className="absolute bottom-7 left-2 z-[400] flex flex-col gap-1 pointer-events-none">
            <div className="flex flex-wrap gap-x-2 gap-y-1 font-mono text-[8px] px-2 py-1.5 rounded max-w-[190px]"
              style={{ background: "color-mix(in srgb, var(--sur) 88%, transparent)",
                       border: "1px solid var(--brd)", color: "var(--ts)" }}>
              {DISPLAY_ORDER.map((cls) => (
                <span key={cls} className="flex items-center gap-1">
                  <span className="w-2 h-2 rounded-[2px] shrink-0"
                    style={{ background: DISPLAY_VAR[cls], border: "1px solid var(--brd)" }} />
                  {DISPLAY_LABEL[cls]}
                </span>
              ))}
            </div>
            <div className="font-mono text-[8px] px-2 py-1 rounded max-w-[190px] leading-tight"
              style={{ background: "color-mix(in srgb, var(--sur) 88%, transparent)",
                       border: "1px solid var(--brd)", color: "var(--td)" }}>
              Boundaries: GADM/HDX · No political value
            </div>
          </div>
        </div>
      </div>

      {/* Dependency panel */}
      <aside className="hidden md:block w-[290px] overflow-y-auto shrink-0"
        style={{ background: "var(--sur)", borderLeft: "1px solid var(--brd)" }}>
        {selUnit && date ? (
          <DependencyPanel date={date} unit={selUnit} dep={deps[selUnit.pcode]} rp={rp} />
        ) : (
          <div className="text-center px-3 py-7">
            <Globe size={28} className="mx-auto mb-2.5" style={{ color: "var(--ts)" }} />
            <div className="text-[13px] font-semibold mb-1.5" style={{ color: "var(--tp)" }}>Select a region</div>
            <div className="text-[10px] leading-relaxed" style={{ color: "var(--ts)" }}>
              Click a country, then an admin-1 region, to explore the 5-step flood-risk dependency chain.
            </div>
          </div>
        )}
      </aside>
    </div>
  );
}
