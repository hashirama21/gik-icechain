"use client";

// ARCHIVE tab — calendar heatmap of worst daily risk, from index.json.

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { getIndex, type CalendarIndex } from "@/lib/api";
import { RISK_COLOR, RISK_LABEL, type RiskState, type UnitRisk } from "@/lib/risk";

const MNAMES = ["January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December"];
const DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export default function ArchiveTab({
  risks, onPick,
}: { risks: Record<string, UnitRisk>; onPick: (d: string) => void }) {
  const [index, setIndex] = useState<CalendarIndex>({});
  useEffect(() => { getIndex().then(setIndex).catch(() => setIndex({})); }, []);

  const months = useMemo(() => {
    const set = new Set<string>();
    for (const d of Object.keys(index)) set.add(d.slice(0, 7)); // YYYY-MM
    return Array.from(set).sort();
  }, [index]);

  const stats = useMemo(() => {
    const dates = Object.values(index);
    const signal = dates.filter((d) => d.worst_risk >= 1).length;
    return { days: dates.length, signal, units: Object.keys(risks).length };
  }, [index, risks]);

  return (
    <div className="p-3.5" style={{ color: "var(--tp)" }}>
      <div className="flex items-center justify-between mb-1">
        <div className="text-base font-bold">Risk Archive</div>
        <span className="font-mono text-[8px] px-1.5 py-0.5 rounded-[3px]"
          style={{ background: "var(--ele)", border: "1px solid var(--brd)", color: "var(--teal)" }}>
          {stats.days} days
        </span>
      </div>
      <div className="text-[11px] mb-3" style={{ color: "var(--ts)" }}>
        Max daily flood-risk per day · East Africa · click a day → storymap
      </div>

      {/* Stats */}
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-1.5 mb-3.5">
        <Stat label="Days in archive" value={String(stats.days)} color="var(--teal)" />
        <Stat label="Active signal days" value={String(stats.signal)} color="var(--r500)" />
        <Stat label="Admin-1 units" value={String(stats.units)} color="var(--green)" />
      </div>

      {/* Months */}
      <div className="grid gap-2.5" style={{ gridTemplateColumns: "repeat(auto-fill,minmax(220px,1fr))" }}>
        {months.map((ym) => <MonthBlock key={ym} ym={ym} index={index} onPick={onPick} />)}
        {months.length === 0 && (
          <p className="text-xs" style={{ color: "var(--ts)" }}>No dates yet — run the data pipeline.</p>
        )}
      </div>

      {/* Legend */}
      <div className="flex flex-wrap gap-3 mt-3 p-2.5 rounded-[10px]"
        style={{ background: "var(--sur)", border: "1px solid var(--brd)" }}>
        {([3, 2, 1, 0, -1] as RiskState[]).map((s) => (
          <span key={s} className="flex items-center gap-1 font-mono text-[9px]" style={{ color: "var(--ts)" }}>
            <span className="w-3 h-3 rounded shrink-0" style={{ background: RISK_COLOR[s] }} />
            {RISK_LABEL[s]}
          </span>
        ))}
      </div>
    </div>
  );
}

function Stat({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div className="rounded-[10px] p-2.5" style={{ background: "var(--sur)", border: "1px solid var(--brd)" }}>
      <div className="font-mono text-[8px] tracking-[1px] uppercase mb-1" style={{ color: "var(--td)" }}>{label}</div>
      <div className="font-mono text-[20px] font-bold leading-none" style={{ color }}>{value}</div>
    </div>
  );
}

function MonthBlock({
  ym, index, onPick,
}: { ym: string; index: CalendarIndex; onPick: (d: string) => void }) {
  const [yr, mo] = ym.split("-").map(Number);
  const first = new Date(yr, mo - 1, 1);
  const days = new Date(yr, mo, 0).getDate();
  const startDow = (first.getDay() + 6) % 7;
  const cells: (string | null)[] = [];
  for (let i = 0; i < startDow; i++) cells.push(null);
  for (let d = 1; d <= days; d++) cells.push(`${ym}-${String(d).padStart(2, "0")}`);

  return (
    <div className="rounded-[10px] p-2.5" style={{ background: "var(--sur)", border: "1px solid var(--brd)" }}>
      <div className="font-mono text-[8px] tracking-[2px] uppercase mb-2 text-center font-bold" style={{ color: "var(--ts)" }}>
        {MNAMES[mo - 1]} {yr}
      </div>
      <div className="grid grid-cols-7 gap-0.5 mb-1">
        {DOW.map((d) => (
          <div key={d} className="font-mono text-[7px] text-center" style={{ color: "var(--td)" }}>{d}</div>
        ))}
      </div>
      <div className="grid grid-cols-7 gap-0.5">
        {cells.map((ds, i) => {
          if (!ds) return <div key={i} className="aspect-square" />;
          const entry = index[ds];
          const has = !!entry;
          const cell = (
            <div
              title={has ? `${ds} · ${entry.risk_label}` : ds}
              className="aspect-square rounded-[6px] flex items-center justify-center font-mono text-[9px] cursor-pointer transition-transform hover:scale-110"
              style={{
                background: has ? RISK_COLOR[entry.worst_risk as RiskState] : "var(--c-nodata)",
                color: has && entry.worst_risk >= 2 ? "#fff" : "var(--c-txt-n)",
                opacity: has ? 1 : 0.4,
              }}
            >
              {Number(ds.slice(-2))}
            </div>
          );
          return has ? (
            <Link key={i} href={`/stories/${ds}`} onClick={() => onPick(ds)}>{cell}</Link>
          ) : <div key={i}>{cell}</div>;
        })}
      </div>
    </div>
  );
}
