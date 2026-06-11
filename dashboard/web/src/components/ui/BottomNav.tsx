"use client";

export type TabId = "map" | "archive" | "comp" | "docs";

const ITEMS: { id: TabId; label: string }[] = [
  { id: "map", label: "MAP" },
  { id: "archive", label: "ARCHIVE" },
  { id: "comp", label: "AI·IFS" },
  { id: "docs", label: "DOCS" },
];

export default function BottomNav({ tab, onTab }: { tab: TabId; onTab: (t: TabId) => void }) {
  return (
    <nav className="md:hidden flex items-stretch shrink-0"
      style={{ height: "var(--nav)", background: "var(--sur)", borderTop: "1px solid var(--brd)" }}>
      {ITEMS.map((it) => (
        <button key={it.id} onClick={() => onTab(it.id)}
          className="flex-1 flex flex-col items-center justify-center gap-0.5 font-mono text-[8px] tracking-[.5px]"
          style={{ color: tab === it.id ? "var(--blue)" : "var(--td)" }}>
          {it.label}
        </button>
      ))}
    </nav>
  );
}
