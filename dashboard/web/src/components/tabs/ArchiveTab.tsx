"use client";

// ARCHIVE tab — one continuous cal-heatmap (GitHub-style) of worst daily risk.

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { getIndex, type CalendarIndex } from "@/lib/api";
import { RISK_COLOR, RISK_LABEL, type RiskState, type UnitRisk } from "@/lib/risk";
import "cal-heatmap/cal-heatmap.css";

const MONTHS_SHOWN = 6;
const NOT_IN_ARCHIVE = -2;

type CalInstance = {
  paint: (options: object, plugins?: unknown[][]) => Promise<unknown>;
  previous: (n?: number) => Promise<unknown>;
  next: (n?: number) => Promise<unknown>;
  on: (event: string, cb: (...args: never[]) => void) => void;
  destroy: () => Promise<unknown>;
};

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function monthStart(ym: string): Date {
  const [y, m] = ym.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, 1));
}

export default function ArchiveTab({
  risks, onPick,
}: { risks: Record<string, UnitRisk>; onPick: (d: string) => void }) {
  const router = useRouter();
  const [index, setIndex] = useState<CalendarIndex>({});
  const calRef = useRef<CalInstance | null>(null);

  useEffect(() => { getIndex().then(setIndex).catch(() => setIndex({})); }, []);

  const dates = useMemo(() => Object.keys(index).sort(), [index]);

  const stats = useMemo(() => {
    const entries = Object.values(index);
    const signal = entries.filter((d) => d.worst_risk >= 1).length;
    return { days: entries.length, signal, units: Object.keys(risks).length };
  }, [index, risks]);

  useEffect(() => {
    if (dates.length === 0) return;
    let disposed = false;

    const paint = async () => {
      const [{ default: CalHeatmap }, { default: Tooltip }, { default: CalendarLabel }] =
        await Promise.all([
          import("cal-heatmap"),
          import("cal-heatmap/plugins/Tooltip"),
          import("cal-heatmap/plugins/CalendarLabel"),
        ]);
      if (disposed) return;

      const first = monthStart(dates[0].slice(0, 7));
      const last = monthStart(dates[dates.length - 1].slice(0, 7));
      // Initial view: the densest MONTHS_SHOWN-month window of the archive
      // (a stray recent date must not drag the view to an empty span).
      const perMonth = new Map<string, number>();
      for (const d of dates) perMonth.set(d.slice(0, 7), (perMonth.get(d.slice(0, 7)) ?? 0) + 1);
      let view = first;
      let best = -1;
      for (const c = new Date(first); c <= last; c.setUTCMonth(c.getUTCMonth() + 1)) {
        const w = new Date(c);
        let count = 0;
        for (let i = 0; i < MONTHS_SHOWN; i++) {
          count += perMonth.get(w.toISOString().slice(0, 7)) ?? 0;
          w.setUTCMonth(w.getUTCMonth() + 1);
        }
        if (count >= best) { best = count; view = new Date(c); }
      }

      // Every day of the paintable span gets a value, so every cell is colored
      // by the scale (no reliance on the library's empty-cell CSS).
      const source: { date: string; value: number }[] = [];
      const end = new Date(Date.UTC(last.getUTCFullYear(), last.getUTCMonth() + 1, 0));
      for (let d = new Date(first); d <= end; d.setUTCDate(d.getUTCDate() + 1)) {
        const ds = d.toISOString().slice(0, 10);
        source.push({ date: ds, value: index[ds]?.worst_risk ?? NOT_IN_ARCHIVE });
      }

      await calRef.current?.destroy();
      const cal = new CalHeatmap() as unknown as CalInstance;
      calRef.current = cal;

      await cal.paint(
        {
          itemSelector: "#risk-cal",
          data: { source, type: "json", x: "date", y: "value" },
          date: { start: view, min: first, max: end },
          range: MONTHS_SHOWN,
          scale: {
            color: {
              type: "threshold",
              domain: [-1, 0, 1, 2, 3],
              range: [
                cssVar("--c-nodata") || "#F0F0F0",
                RISK_COLOR[-1], RISK_COLOR[0], RISK_COLOR[1], RISK_COLOR[2], RISK_COLOR[3],
              ],
            },
          },
          domain: {
            type: "month",
            gutter: 6,
            label: { text: "MMM YYYY", textAlign: "start", position: "top" },
          },
          subDomain: { type: "ghDay", radius: 2, width: 12, height: 12, gutter: 3 },
        },
        [
          [
            Tooltip,
            {
              text: (_ts: number, value: number | null, dayjsDate: { format: (f: string) => string }) => {
                const day = dayjsDate.format("dddd, MMMM D, YYYY");
                if (value === null || value === NOT_IN_ARCHIVE) return `Not in archive · ${day}`;
                return `${RISK_LABEL[value as RiskState]} — worst unit risk · ${day}`;
              },
            },
          ],
          [
            CalendarLabel,
            {
              width: 26,
              textAlign: "start",
              text: () => ["", "Mon", "", "Wed", "", "Fri", ""],
              padding: [22, 0, 0, 0],
            },
          ],
        ],
      );

      cal.on("click", ((_e: unknown, ts: number, value: number | null) => {
        if (value === null || value === NOT_IN_ARCHIVE) return;
        const ds = new Date(ts).toISOString().slice(0, 10);
        onPick(ds);
        router.push(`/stories/${ds}`);
      }) as never);
    };

    paint();
    // Repaint on theme switch: the not-in-archive color comes from a CSS var
    // resolved at paint time.
    const observer = new MutationObserver(() => paint());
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });

    return () => {
      disposed = true;
      observer.disconnect();
      calRef.current?.destroy();
      calRef.current = null;
    };
  }, [dates, index, onPick, router]);

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

      <div className="grid grid-cols-2 sm:grid-cols-3 gap-1.5 mb-3.5">
        <Stat label="Days in archive" value={String(stats.days)} color="var(--teal)" />
        <Stat label="Active signal days" value={String(stats.signal)} color="var(--r500)" />
        <Stat label="Admin-1 units" value={String(stats.units)} color="var(--green)" />
      </div>

      <div className="rounded-[10px] p-3" style={{ background: "var(--sur)", border: "1px solid var(--brd)" }}>
        {dates.length === 0 ? (
          <p className="text-xs" style={{ color: "var(--ts)" }}>No dates yet — run the data pipeline.</p>
        ) : (
          <>
            <div className="overflow-x-auto pb-1">
              <div id="risk-cal" />
            </div>
            <div className="flex items-center justify-between mt-2 flex-wrap gap-2">
              <div className="flex gap-1.5">
                <NavButton onClick={() => calRef.current?.previous()} label="Previous">
                  <ChevronLeft size={12} /> Previous
                </NavButton>
                <NavButton onClick={() => calRef.current?.next()} label="Next">
                  Next <ChevronRight size={12} />
                </NavButton>
              </div>
              <div className="flex flex-wrap gap-3">
                {([3, 2, 1, 0, -1] as RiskState[]).map((s) => (
                  <span key={s} className="flex items-center gap-1 font-mono text-[9px]" style={{ color: "var(--ts)" }}>
                    <span className="w-3 h-3 rounded shrink-0" style={{ background: RISK_COLOR[s] }} />
                    {RISK_LABEL[s]}
                  </span>
                ))}
                <span className="flex items-center gap-1 font-mono text-[9px]" style={{ color: "var(--ts)" }}>
                  <span className="w-3 h-3 rounded shrink-0"
                    style={{ background: "var(--c-nodata)", border: "1px solid var(--brd)" }} />
                  Not in archive
                </span>
              </div>
            </div>
          </>
        )}
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

function NavButton({
  onClick, label, children,
}: { onClick: () => void; label: string; children: React.ReactNode }) {
  return (
    <button onClick={onClick} aria-label={label}
      className="flex items-center gap-1 font-mono text-[9px] px-2 py-1 rounded-[5px] cursor-pointer"
      style={{ background: "var(--ele)", border: "1px solid var(--brd)", color: "var(--ts)" }}>
      {children}
    </button>
  );
}
