"use client";

// ARCHIVE tab  one continuous cal-heatmap (GitHub-style) of daily alert extent.

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { getIndex, type CalendarIndex } from "@/lib/api";
import {
  EXTENT_THRESHOLDS,
  RISK_COLOR,
  alertExtent,
  extentClass,
  type RiskState,
  type UnitRisk,
} from "@/lib/risk";
import "cal-heatmap/cal-heatmap.css";

const NOT_IN_ARCHIVE = -2;
const ARCHIVE_START = Date.UTC(2023, 0, 1);
const LABEL_W = 26;
const DOMAIN_GUTTER = 6;
const CELL_GUTTER = 3;

type CalInstance = {
  paint: (options: object, plugins?: unknown[][]) => Promise<unknown>;
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
  const wrapRef = useRef<HTMLDivElement>(null);

  useEffect(() => { getIndex().then(setIndex).catch(() => setIndex({})); }, []);

  const dates = useMemo(() => Object.keys(index).sort(), [index]);

  const stats = useMemo(() => {
    const entries = Object.values(index);
    const signal = entries.filter((d) => extentClass(d) >= 2).length;
    return { days: entries.length, signal, units: Object.keys(risks).length };
  }, [index, risks]);

  useEffect(() => {
    if (dates.length === 0) return;
    let disposed = false;
    let scrolledOnce = false;

    const paint = async () => {
      const prevScroll = wrapRef.current?.scrollLeft ?? 0;
      const [{ default: CalHeatmap }, { default: Tooltip }, { default: CalendarLabel }] =
        await Promise.all([
          import("cal-heatmap"),
          import("cal-heatmap/plugins/Tooltip"),
          import("cal-heatmap/plugins/CalendarLabel"),
        ]);
      if (disposed) return;

      const first = new Date(ARCHIVE_START);
      const now = new Date();
      let last = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1));
      const lastData = monthStart(dates[dates.length - 1].slice(0, 7));
      if (lastData > last) last = lastData;
      const end = new Date(Date.UTC(last.getUTCFullYear(), last.getUTCMonth() + 1, 0));

      const months: Date[] = [];
      for (const c = new Date(first); c <= last; c.setUTCMonth(c.getUTCMonth() + 1)) {
        months.push(new Date(c));
      }
      // Week columns per month (ghDay): partial first week + full weeks.
      const columns = months.reduce((n, m) => {
        const daysInMonth = new Date(Date.UTC(m.getUTCFullYear(), m.getUTCMonth() + 1, 0)).getUTCDate();
        return n + Math.ceil((m.getUTCDay() + daysInMonth) / 7);
      }, 0);
      const avail = wrapRef.current?.clientWidth ?? 960;
      const cell = Math.min(17, Math.max(10,
        Math.floor((avail - LABEL_W - months.length * DOMAIN_GUTTER) / columns) - CELL_GUTTER));

      // Every day of the paintable span gets a value, so every cell is colored
      // by the scale (no reliance on the library's empty-cell CSS).
      const source: { date: string; value: number }[] = [];
      for (let d = new Date(first); d <= end; d.setUTCDate(d.getUTCDate() + 1)) {
        const ds = d.toISOString().slice(0, 10);
        source.push({ date: ds, value: index[ds] ? extentClass(index[ds]) : NOT_IN_ARCHIVE });
      }

      await calRef.current?.destroy();
      const cal = new CalHeatmap() as unknown as CalInstance;
      calRef.current = cal;

      await cal.paint(
        {
          itemSelector: "#risk-cal",
          data: { source, type: "json", x: "date", y: "value" },
          date: { start: first, min: first, max: end },
          range: months.length,
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
          subDomain: { type: "ghDay", radius: 2, width: cell, height: cell, gutter: CELL_GUTTER },
        },
        [
          [
            Tooltip,
            {
              text: (ts: number, value: number | null, dayjsDate: { format: (f: string) => string }) => {
                const day = dayjsDate.format("dddd, MMMM D, YYYY");
                if (value === null || value === NOT_IN_ARCHIVE) return `Not in archive · ${day}`;
                const entry = index[new Date(ts).toISOString().slice(0, 10)];
                const n = entry ? alertExtent(entry) : null;
                const extent = n === null ? "" : `${n} Orange+ units · `;
                const worst = entry ? `worst ${entry.risk_label}` : "";
                return `${extent}${worst} · ${day}`;
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

      const el = wrapRef.current;
      if (el) {
        let todayLeft: number | null = null;
        for (const r of el.querySelectorAll<SVGRectElement>(".ch-subdomain-bg")) {
          const d = (r as unknown as { __data__?: { t?: number } }).__data__;
          if (d?.t == null) continue;
          const dt = new Date(d.t);
          if (dt.getFullYear() === now.getFullYear() && dt.getMonth() === now.getMonth()
            && dt.getDate() === now.getDate()) {
            r.classList.add("today-cell");
            todayLeft = r.getBoundingClientRect().left - el.getBoundingClientRect().left + el.scrollLeft;
            break;
          }
        }
        el.scrollLeft = scrolledOnce ? prevScroll
          : todayLeft != null ? Math.max(0, todayLeft - el.clientWidth / 2) : el.scrollWidth;
        scrolledOnce = true;
      }
    };

    paint();
    // Repaint on theme switch: the not-in-archive color comes from a CSS var
    // resolved at paint time.
    const observer = new MutationObserver(() => paint());
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });

    let lastW = wrapRef.current?.clientWidth ?? 0;
    const resizeObserver = new ResizeObserver(() => {
      const w = wrapRef.current?.clientWidth ?? 0;
      if (Math.abs(w - lastW) < 8) return;
      lastW = w;
      paint();
    });
    if (wrapRef.current) resizeObserver.observe(wrapRef.current);

    return () => {
      disposed = true;
      observer.disconnect();
      resizeObserver.disconnect();
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
        Daily alert extent (admin-1 units at Orange or Red) · East Africa · click a day → storymap
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-3 gap-1.5 mb-3.5">
        <Stat label="Days in archive" value={String(stats.days)} color="var(--teal)" />
        <Stat label="Widespread-alert days" value={String(stats.signal)} color="var(--r500)" />
        <Stat label="Admin-1 units" value={String(stats.units)} color="var(--green)" />
      </div>

      <div className="rounded-[10px] p-3" style={{ background: "var(--sur)", border: "1px solid var(--brd)" }}>
        {dates.length === 0 ? (
          <p className="text-xs" style={{ color: "var(--ts)" }}>No dates yet  run the data pipeline.</p>
        ) : (
          <>
            <div className="overflow-x-auto pb-1" ref={wrapRef}>
              <div id="risk-cal" />
            </div>
            <div className="flex items-center justify-end mt-2 flex-wrap gap-3">
              {(
                [
                  [3, `≥${EXTENT_THRESHOLDS[2]} units`],
                  [2, `${EXTENT_THRESHOLDS[1]}-${EXTENT_THRESHOLDS[2] - 1}`],
                  [1, `${EXTENT_THRESHOLDS[0]}-${EXTENT_THRESHOLDS[1] - 1}`],
                  [0, `<${EXTENT_THRESHOLDS[0]}`],
                ] as [RiskState, string][]
              ).map(([s, label]) => (
                <span key={s} className="flex items-center gap-1 font-mono text-[9px]" style={{ color: "var(--ts)" }}>
                  <span className="w-3 h-3 rounded shrink-0" style={{ background: RISK_COLOR[s] }} />
                  {label}
                </span>
              ))}
              <span className="flex items-center gap-1 font-mono text-[9px]" style={{ color: "var(--ts)" }}>
                <span className="w-3 h-3 rounded shrink-0"
                  style={{ background: "var(--c-nodata)", border: "1px solid var(--brd)" }} />
                Not in archive
              </span>
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
      <div className="flex items-center gap-1 font-mono text-[8px] tracking-[1px] uppercase mb-1" style={{ color: "var(--td)" }}>
        <span className="w-1.5 h-1.5 rounded-full shrink-0" style={{ background: color }} />
        {label}
      </div>
      <div className="font-mono text-[20px] font-bold leading-none" style={{ color: "var(--tp)" }}>{value}</div>
    </div>
  );
}
