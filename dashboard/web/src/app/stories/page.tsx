"use client";

// Storymap index: the 1 200+ event narratives grouped by year and month,
// with date search and alert-extent filtering.

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { getIndex, type CalendarEntry, type CalendarIndex } from "@/lib/api";
import { EXTENT_THRESHOLDS, RISK_COLOR, alertExtent, extentClass, type RiskState } from "@/lib/risk";

const MONTH_NAMES = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

type SeverityFilter = -1 | 0 | 1 | 2 | 3;

const FILTERS: { value: SeverityFilter; label: string; color?: string }[] = [
  { value: -1, label: "All days" },
  { value: 3, label: `≥${EXTENT_THRESHOLDS[2]} units`, color: RISK_COLOR[3] },
  { value: 2, label: `${EXTENT_THRESHOLDS[1]}-${EXTENT_THRESHOLDS[2] - 1}`, color: RISK_COLOR[2] },
  { value: 1, label: `${EXTENT_THRESHOLDS[0]}-${EXTENT_THRESHOLDS[1] - 1}`, color: RISK_COLOR[1] },
  { value: 0, label: `<${EXTENT_THRESHOLDS[0]}`, color: RISK_COLOR[0] },
];

interface DayItem {
  date: string;
  entry: CalendarEntry;
  cls: RiskState;
  extent: number | null;
}

function YearBar({ days }: { days: DayItem[] }) {
  const counts = [0, 0, 0, 0];
  for (const d of days) if (d.cls >= 0) counts[d.cls] += 1;
  const total = Math.max(1, days.length);
  return (
    <span className="inline-flex h-2 w-28 rounded-[2px] overflow-hidden shrink-0">
      {([0, 1, 2, 3] as RiskState[]).map((s) => (
        <span
          key={s}
          style={{ width: `${(100 * counts[s]) / total}%`, background: RISK_COLOR[s] }}
        />
      ))}
    </span>
  );
}

export default function StoriesIndex() {
  const [index, setIndex] = useState<CalendarIndex>({});
  const [query, setQuery] = useState("");
  const [severity, setSeverity] = useState<SeverityFilter>(-1);
  const [openYears, setOpenYears] = useState<Set<string> | null>(null);

  useEffect(() => {
    getIndex().then(setIndex).catch(() => setIndex({}));
  }, []);

  const items = useMemo<DayItem[]>(
    () =>
      Object.entries(index)
        .map(([date, entry]) => ({
          date,
          entry,
          cls: extentClass(entry),
          extent: alertExtent(entry),
        }))
        .sort((a, b) => (a.date < b.date ? 1 : -1)),
    [index],
  );

  const filtered = useMemo(() => {
    const q = query.trim();
    return items.filter(
      (d) => (severity === -1 || d.cls === severity) && (!q || d.date.includes(q)),
    );
  }, [items, query, severity]);

  const byYear = useMemo(() => {
    const years = new Map<string, Map<string, DayItem[]>>();
    for (const d of filtered) {
      const year = d.date.slice(0, 4);
      const month = d.date.slice(0, 7);
      if (!years.has(year)) years.set(year, new Map());
      const months = years.get(year)!;
      if (!months.has(month)) months.set(month, []);
      months.get(month)!.push(d);
    }
    return years;
  }, [filtered]);

  const yearKeys = [...byYear.keys()];
  const filtering = query.trim() !== "" || severity !== -1;
  const expanded =
    openYears ?? new Set(yearKeys.length > 0 ? [yearKeys[0]] : []);

  function toggleYear(y: string) {
    const next = new Set(expanded);
    if (next.has(y)) next.delete(y);
    else next.add(y);
    setOpenYears(next);
  }

  return (
    <div className="min-h-full pb-16" style={{ background: "var(--bg)" }}>
      <div
        className="sticky top-0 z-20 flex items-center gap-2.5 px-3.5"
        style={{ height: "var(--hdr)", background: "var(--sur)", borderBottom: "1px solid var(--brd)" }}
      >
        <Link href="/" className="font-mono text-[10px]" style={{ color: "var(--blue)" }}>
          ← Dashboard
        </Link>
        <span className="font-mono text-[10px] font-bold tracking-[1.5px]">
          GIK·ICECHAIN · STORYMAPS
        </span>
        <span
          className="ml-auto font-mono text-[9px] px-1.5 py-0.5 rounded-[3px]"
          style={{ background: "var(--ele)", border: "1px solid var(--brd)", color: "var(--teal)" }}
        >
          {items.length} days
        </span>
      </div>

      <div className="max-w-[880px] mx-auto px-5">
        <header className="pt-10 pb-5">
          <div
            className="font-mono text-[9px] font-bold tracking-[2px] mb-2"
            style={{ color: "var(--teal)" }}
          >
            EVENT ARCHIVE · 2023 → PRESENT
          </div>
          <h1 className="text-[clamp(22px,3.5vw,28px)] font-bold leading-tight tracking-[-0.02em]">
            Storymaps
          </h1>
          <p className="text-[13px] mt-1.5" style={{ color: "var(--ts)" }}>
            One flood-risk narrative per forecast day. Days are classed by alert extent:
            how many of the 238 admin-1 units reached Orange or Red.
          </p>
        </header>

        <div
          className="flex flex-wrap items-center gap-2 p-2.5 rounded-[var(--rl)] mb-6"
          style={{ background: "var(--sur)", border: "1px solid var(--brd)" }}
        >
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Filter by date, e.g. 2024-11"
            aria-label="Filter storymaps by date"
            className="font-mono text-[12px] px-3 py-1.5 rounded-[var(--r)] w-full sm:w-56 outline-none focus:ring-1"
            style={{
              background: "var(--ele)",
              border: "1px solid var(--brd)",
              color: "var(--tp)",
            }}
          />
          <div className="flex flex-wrap items-center gap-1.5">
            {FILTERS.map((f) => (
              <button
                key={f.value}
                onClick={() => setSeverity(severity === f.value ? -1 : f.value)}
                className="inline-flex items-center gap-1.5 font-mono text-[10px] px-2 py-1 rounded-[var(--r)] cursor-pointer"
                style={{
                  background: severity === f.value ? "var(--hov)" : "var(--ele)",
                  border: `1px solid ${severity === f.value ? "var(--bhi)" : "var(--brd)"}`,
                  color: severity === f.value ? "var(--tp)" : "var(--ts)",
                }}
              >
                {f.color && (
                  <span className="w-2 h-2 rounded-[2px]" style={{ background: f.color }} />
                )}
                {f.label}
              </button>
            ))}
          </div>
          {filtering && (
            <span className="font-mono text-[10px] ml-auto" style={{ color: "var(--td)" }}>
              {filtered.length} match{filtered.length === 1 ? "" : "es"}
            </span>
          )}
        </div>

        {items.length === 0 && (
          <p className="text-[13px]" style={{ color: "var(--ts)" }}>
            No storymaps yet. Run the data pipeline.
          </p>
        )}

        <div className="space-y-3">
          {yearKeys.map((year) => {
            const months = byYear.get(year)!;
            const days = [...months.values()].flat();
            const isOpen = filtering || expanded.has(year);
            return (
              <section
                key={year}
                className="rounded-[var(--rl)] overflow-hidden"
                style={{ background: "var(--sur)", border: "1px solid var(--brd)" }}
              >
                <button
                  onClick={() => toggleYear(year)}
                  className="w-full flex items-center gap-3 px-4 py-3 cursor-pointer text-left"
                  aria-expanded={isOpen}
                >
                  <span
                    className="font-mono text-[10px] w-3 shrink-0"
                    style={{ color: "var(--td)" }}
                  >
                    {isOpen ? "▾" : "▸"}
                  </span>
                  <span className="font-mono text-[15px] font-bold tracking-[1px]">{year}</span>
                  <YearBar days={days} />
                  <span className="font-mono text-[10px]" style={{ color: "var(--ts)" }}>
                    {days.length} days · {days.filter((d) => d.cls === 3).length} exceptional
                  </span>
                </button>

                {isOpen && (
                  <div className="px-4 pb-4 space-y-4">
                    {[...months.entries()].map(([month, mdays]) => (
                      <div key={month}>
                        <div
                          className="font-mono text-[9px] font-bold tracking-[1.5px] uppercase mb-2"
                          style={{ color: "var(--td)" }}
                        >
                          {MONTH_NAMES[Number(month.slice(5, 7)) - 1]} {year} · {mdays.length}
                        </div>
                        <div className="grid grid-cols-[repeat(auto-fill,minmax(96px,1fr))] gap-1.5">
                          {mdays.map((d) => (
                            <Link
                              key={d.date}
                              href={`/stories/${d.date}`}
                              className="rounded-[var(--r)] px-2 py-1.5 hover:opacity-85"
                              style={{
                                background: "var(--ele)",
                                border: "1px solid var(--brd)",
                                borderLeft: `3px solid ${RISK_COLOR[d.cls]}`,
                              }}
                            >
                              <div className="font-mono text-[11px] font-bold">
                                {d.date.slice(8)}
                                <span className="font-normal" style={{ color: "var(--td)" }}>
                                  {" "}· {d.date.slice(5, 7)}
                                </span>
                              </div>
                              <div
                                className="font-mono text-[9px] mt-0.5"
                                style={{ color: "var(--ts)" }}
                              >
                                {d.extent !== null ? `${d.extent} alerted` : d.entry.risk_label}
                              </div>
                            </Link>
                          ))}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </section>
            );
          })}
        </div>

        {filtering && filtered.length === 0 && items.length > 0 && (
          <p className="text-[13px] mt-6" style={{ color: "var(--ts)" }}>
            No day matches this filter. Clear the search or pick another severity.
          </p>
        )}
      </div>
    </div>
  );
}
