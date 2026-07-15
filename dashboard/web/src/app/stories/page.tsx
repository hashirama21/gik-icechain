"use client";

// Storymap index  lists available event narratives (one per signal date).

import { useEffect, useState } from "react";
import Link from "next/link";
import { getIndex, type CalendarIndex } from "@/lib/api";
import { RISK_COLOR, type RiskState } from "@/lib/risk";

export default function StoriesIndex() {
  const [index, setIndex] = useState<CalendarIndex>({});
  useEffect(() => {
    getIndex().then(setIndex).catch(() => setIndex({}));
  }, []);
  const dates = Object.keys(index).sort().reverse();

  return (
    <div className="min-h-screen">
      <div className="flex items-center gap-2.5 px-3.5 shrink-0"
        style={{ height: "var(--hdr)", background: "var(--sur)", borderBottom: "1px solid var(--brd)" }}>
        <Link href="/" className="font-mono text-[10px]" style={{ color: "var(--blue)" }}>← Dashboard</Link>
        <span className="font-mono text-[10px] font-bold tracking-[1.5px]">GIK·ICECHAIN · STORYMAPS</span>
      </div>
      <div className="story">
        <h1>Storymaps</h1>
        <p>Per-event flood-risk narratives generated from the pipeline outputs.</p>
        <div className="space-y-2 mt-4">
          {dates.map((d) => (
            <Link key={d} href={`/stories/${d}`}
              className="flex items-center gap-3 panel rounded-lg p-3 hover:opacity-90">
              <span className="w-3 h-3 rounded-sm"
                style={{ background: RISK_COLOR[index[d].worst_risk as RiskState] }} />
              <span className="font-medium">{d}</span>
              <span className="mono text-[10px]" style={{ color: "var(--td)" }}>
                {index[d].risk_label} · {index[d].n_units} units
              </span>
            </Link>
          ))}
          {dates.length === 0 && <p>No storymaps yet  run the data pipeline.</p>}
        </div>
      </div>
    </div>
  );
}
