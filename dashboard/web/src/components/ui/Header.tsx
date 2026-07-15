"use client";

import ThemeToggle from "./ThemeToggle";
import type { TabId } from "./BottomNav";
import { BASE_PATH } from "@/lib/config";

const TABS: { id: TabId; label: string }[] = [
  { id: "map", label: "Map" },
  { id: "archive", label: "Archive" },
  { id: "comp", label: "AIFS vs IFS" },
  { id: "docs", label: "Docs" },
];

export default function Header({
  tab, date, onTab,
}: { tab: TabId; date: string | null; onTab: (t: TabId) => void }) {
  return (
    <header
      className="flex items-center gap-2.5 px-3.5 shrink-0 z-[1000]"
      style={{ height: "var(--hdr)", background: "var(--sur)", borderBottom: "1px solid var(--brd)" }}
    >
      {/* eslint-disable-next-line @next/next/no-img-element -- static export, no image optimizer */}
      <img src={`${BASE_PATH}/logo.jpg`} alt="GIK-IceChain"
        className="h-8 w-auto shrink-0 rounded-[5px]"
        style={{ border: "1px solid var(--brd)" }} />
      <div className="w-px h-[18px] shrink-0" style={{ background: "var(--brd)" }} />
      <div className="hidden sm:flex items-center gap-1.5 shrink-0">
        <span className="font-mono text-[8px] font-bold tracking-[1px] px-1.5 py-[3px] border rounded-[3px]"
          style={{ color: "var(--teal)", borderColor: "rgba(20,184,166,.3)" }}>ICPAC</span>
        <span className="font-mono text-[8px] font-bold tracking-[1px] px-1.5 py-[3px] border rounded-[3px]"
          style={{ color: "var(--td)", borderColor: "var(--brd)" }}>IGAD</span>
        <span className="font-mono text-[8px] font-bold tracking-[1px] px-1.5 py-[3px] border rounded-[3px]"
          style={{ color: "var(--td)", borderColor: "var(--brd)" }}>ECMWF</span>
      </div>

      <nav className="hidden md:flex gap-0.5 flex-1 mx-2">
        {TABS.map((t) => (
          <button key={t.id} onClick={() => onTab(t.id)}
            className="px-2.5 py-[5px] rounded-[5px] text-[11px] font-medium border transition-colors hover:bg-[var(--hov)]"
            style={tab === t.id
              ? { background: "var(--ele)", color: "var(--tp)", borderColor: "var(--brd)" }
              : { color: "var(--ts)", borderColor: "transparent" }}>
            {t.label}
          </button>
        ))}
      </nav>

      <div className="flex items-center gap-1 font-mono text-[8px] shrink-0" style={{ color: "var(--td)" }}>
        <span className="w-1.5 h-1.5 rounded-full anim-blink" style={{ background: "var(--r500)" }} />
        LIVE
      </div>
      {date && (
        <span className="hidden sm:inline font-mono text-[8px] shrink-0" style={{ color: "var(--td)" }}>
          {date} · IFS ENS 00z
        </span>
      )}
      <ThemeToggle />
    </header>
  );
}
