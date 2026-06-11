"use client";

// Faithful Tailwind port of GIK-IceChain-Dashboard-v4.html — same layout, tabs
// and look (via the ported design tokens), but fed with REAL pipeline data.

import { useEffect, useState } from "react";
import Header from "./ui/Header";
import BottomNav, { type TabId } from "./ui/BottomNav";
import MapTab from "./tabs/MapTab";
import ArchiveTab from "./tabs/ArchiveTab";
import CompTab from "./tabs/CompTab";
import DocsTab from "./tabs/DocsTab";
import { getDependency, getIndex, getRegionRisks, type UnitDependency } from "@/lib/api";
import type { UnitRisk } from "@/lib/risk";

export default function DashboardApp() {
  const [tab, setTab] = useState<TabId>("map");
  const [date, setDate] = useState<string | null>(null);
  const [risks, setRisks] = useState<Record<string, UnitRisk>>({});
  const [deps, setDeps] = useState<Record<string, UnitDependency>>({});

  useEffect(() => {
    getIndex()
      .then((idx) => {
        const dates = Object.keys(idx).sort();
        if (dates.length) setDate(dates[dates.length - 1]);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (!date) return;
    getRegionRisks(date).then(setRisks).catch(() => setRisks({}));
    getDependency(date).then(setDeps).catch(() => setDeps({}));
  }, [date]);

  return (
    <div className="fixed inset-0 flex flex-col">
      <Header tab={tab} date={date} onTab={setTab} />
      {/* disclaimer bar */}
      <div className="flex items-center gap-1.5 px-3 py-[3px] text-[9px] shrink-0"
        style={{ color: "var(--td)", background: "rgba(59,130,246,.05)",
                 borderBottom: "1px solid rgba(59,130,246,.1)" }}>
        ℹ️ Decision-support tool only — does not replace operational judgment of
        competent authorities · ECMWF Open Licence · EM-DAT CC-BY · NASA Open Data
      </div>

      <main className="flex-1 relative overflow-hidden">
        <div className={tab === "map" ? "absolute inset-0 flex" : "hidden"}>
          <MapTab date={date} risks={risks} deps={deps} />
        </div>
        <div className={tab === "archive" ? "absolute inset-0 overflow-y-auto" : "hidden"}>
          <ArchiveTab risks={risks} onPick={setDate} />
        </div>
        <div className={tab === "comp" ? "absolute inset-0 overflow-y-auto" : "hidden"}>
          <CompTab date={date} />
        </div>
        <div className={tab === "docs" ? "absolute inset-0 overflow-y-auto" : "hidden"}>
          <DocsTab />
        </div>
      </main>

      <BottomNav tab={tab} onTab={setTab} />
    </div>
  );
}
