// Generic per-date storymap. One static page per date in the data contract
// (public/data/index.json at build time); dates with a hand-written MDX
// narrative keep their own route.

import fs from "node:fs";
import path from "node:path";
import Link from "next/link";
import StoryMap from "@/components/story/StoryMap";
import { RISK_COLOR, type RiskState } from "@/lib/risk";

export const dynamicParams = false;

type IndexEntry = { worst_risk: number; risk_label: string; n_units: number };

function readIndex(): Record<string, IndexEntry> {
  try {
    const p = path.join(process.cwd(), "public", "data", "index.json");
    return JSON.parse(fs.readFileSync(p, "utf-8"));
  } catch {
    return {};
  }
}

function mdxDates(): Set<string> {
  try {
    const dir = path.join(process.cwd(), "src", "app", "stories");
    return new Set(
      fs
        .readdirSync(dir, { withFileTypes: true })
        .filter((e) => e.isDirectory() && fs.existsSync(path.join(dir, e.name, "page.mdx")))
        .map((e) => e.name),
    );
  } catch {
    return new Set();
  }
}

export function generateStaticParams() {
  const handWritten = mdxDates();
  return Object.keys(readIndex())
    .filter((d) => !handWritten.has(d))
    .map((date) => ({ date }));
}

function season(date: string): string {
  const m = Number(date.slice(5, 7));
  if (m >= 3 && m <= 5) return "MAM · long rains";
  if (m >= 10 && m <= 12) return "OND · short rains";
  if (m >= 6 && m <= 9) return "JJAS";
  return "JF · dry season";
}

function longDate(date: string): string {
  return new Date(`${date}T00:00:00Z`).toLocaleDateString("en-GB", {
    day: "numeric",
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  });
}

const EVIDENCE = [
  {
    label: "FORECAST HAZARD",
    text: "Ensemble exceedance probability against adaptive GEV thresholds",
  },
  { label: "OBSERVED ANTECEDENT", text: "Daily precipitation observations over the unit" },
  { label: "SOIL MEMORY", text: "Antecedent Precipitation Index with exponential decay" },
  { label: "SPATIAL COVERAGE", text: "Fraction of grid cells carrying an active signal" },
];

function Eyebrow({ children }: { children: React.ReactNode }) {
  return (
    <div
      className="font-mono text-[9px] font-bold tracking-[2px] mb-2"
      style={{ color: "var(--teal)" }}
    >
      {children}
    </div>
  );
}

function MapPanel({
  eyebrow,
  caption,
  children,
}: {
  eyebrow: string;
  caption: string;
  children: React.ReactNode;
}) {
  return (
    <figure
      className="rounded-[var(--rl)] overflow-hidden"
      style={{ background: "var(--sur)", border: "1px solid var(--brd)" }}
    >
      <div className="px-4 pt-3">
        <Eyebrow>{eyebrow}</Eyebrow>
      </div>
      {children}
      <figcaption
        className="font-mono text-[10px] leading-relaxed px-4 py-3"
        style={{ color: "var(--td)", borderTop: "1px solid var(--brd)" }}
      >
        {caption}
      </figcaption>
    </figure>
  );
}

export default async function StoryPage({ params }: { params: Promise<{ date: string }> }) {
  const { date } = await params;
  const entry = readIndex()[date];
  const riskColor = entry ? RISK_COLOR[entry.worst_risk as RiskState] : "var(--td)";

  return (
    <div className="min-h-full pb-16" style={{ background: "var(--bg)" }}>
      <div
        className="sticky top-0 z-20 flex items-center gap-2.5 px-3.5"
        style={{
          height: "var(--hdr)",
          background: "var(--sur)",
          borderBottom: "1px solid var(--brd)",
        }}
      >
        <Link href="/" className="font-mono text-[10px]" style={{ color: "var(--blue)" }}>
          ← Dashboard
        </Link>
        <Link href="/stories" className="font-mono text-[10px]" style={{ color: "var(--blue)" }}>
          Storymaps
        </Link>
        <span className="font-mono text-[10px] font-bold tracking-[1.5px] ml-auto">{date}</span>
        <span className="w-2.5 h-2.5 rounded-[2px]" style={{ background: riskColor }} />
      </div>

      <header className="max-w-[68ch] mx-auto px-5 pt-12 pb-2">
        <Eyebrow>GIK·ICECHAIN · EVENT BULLETIN</Eyebrow>
        <h1
          className="text-[clamp(26px,4.5vw,34px)] font-bold leading-[1.15] tracking-[-0.02em]"
          style={{ textWrap: "balance" }}
        >
          East Africa flood risk
          <span className="block" style={{ color: "var(--ts)" }}>
            {longDate(date)}
          </span>
        </h1>

        <div className="flex flex-wrap items-center gap-2 mt-5">
          {entry && (
            <span
              className="inline-flex items-center gap-2 font-mono text-[10px] font-bold tracking-[1px] px-2.5 py-1 rounded-[var(--r)]"
              style={{
                color: riskColor,
                background: "color-mix(in srgb, currentColor 12%, transparent)",
                border: "1px solid color-mix(in srgb, currentColor 35%, transparent)",
              }}
            >
              <span className="w-2 h-2 rounded-full" style={{ background: riskColor }} />
              PEAK {entry.risk_label.toUpperCase()}
            </span>
          )}
          <span
            className="font-mono text-[10px] px-2.5 py-1 rounded-[var(--r)]"
            style={{ color: "var(--ts)", border: "1px solid var(--brd)", background: "var(--sur)" }}
          >
            {entry ? entry.n_units : 238} admin-1 units
          </span>
          <span
            className="font-mono text-[10px] px-2.5 py-1 rounded-[var(--r)]"
            style={{ color: "var(--ts)", border: "1px solid var(--brd)", background: "var(--sur)" }}
          >
            {season(date)}
          </span>
          <span
            className="font-mono text-[10px] px-2.5 py-1 rounded-[var(--r)]"
            style={{ color: "var(--ts)", border: "1px solid var(--brd)", background: "var(--sur)" }}
          >
            IFS ENS · 51 members
          </span>
        </div>
      </header>

      <section className="story mt-8">
        <p>
          The <strong>GIK-IceChain</strong> pipeline assessed flood risk for every admin-1 unit
          across 16 East African countries on this date, combining the 51-member ECMWF IFS
          ensemble, observed precipitation, and the ICPAC CRMA Bayesian Network. Four evidence
          streams feed each unit&apos;s daily verdict:
        </p>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 mt-4">
          {EVIDENCE.map((e) => (
            <div
              key={e.label}
              className="rounded-[var(--r)] p-3"
              style={{ background: "var(--sur)", border: "1px solid var(--brd)" }}
            >
              <div
                className="font-mono text-[9px] font-bold tracking-[1.5px] mb-1.5"
                style={{ color: "var(--teal)" }}
              >
                {e.label}
              </div>
              <div className="text-[12.5px] leading-snug" style={{ color: "var(--ts)" }}>
                {e.text}
              </div>
            </div>
          ))}
        </div>
      </section>

      <div className="max-w-[840px] mx-auto px-5 mt-10 space-y-10">
        <MapPanel
          eyebrow="DECISION LAYER · CRMA ADMIN-1 RISK"
          caption="Traffic-light risk state per admin-1 unit from the CRMA Bayesian Network: Green, Yellow, Orange, Red."
        >
          <StoryMap layer="risk" date={date} center={[38, 2]} zoom={5} />
        </MapPanel>

        <section className="story">
          <h2>Forecast signal: exceedance probability</h2>
          <p>
            Fraction of the 51 ensemble members whose worst-case 24-hour accumulation exceeds the
            5-year return-period GEV threshold. Values above 0.4 map to a{" "}
            <strong>High</strong> forecast-hazard state in the Bayesian Network.
          </p>
        </section>
        <MapPanel
          eyebrow="FORECAST LAYER · 24 H WINDOW · 5-YEAR RETURN PERIOD"
          caption="Ensemble exceedance probability, 24 h accumulation against the 5-year GEV threshold."
        >
          <StoryMap layer="exceedance" date={date} windowH={24} rp={5} center={[38, 2]} zoom={5} />
        </MapPanel>

        <section className="story">
          <h2>Observed rainfall</h2>
          <p>
            Observed daily precipitation is the observational counterpart used by the CRMA
            model&apos;s antecedent-precipitation evidence node.
          </p>
        </section>
        <MapPanel
          eyebrow="OBSERVATION LAYER · DAILY RAINFALL"
          caption="Observed daily rainfall (mm/day)."
        >
          <StoryMap layer="gpm" date={date} center={[38, 2]} zoom={5} />
        </MapPanel>
      </div>

      <footer className="max-w-[68ch] mx-auto px-5 mt-12">
        <Link
          href="/stories"
          className="font-mono text-[10px] tracking-[1px]"
          style={{ color: "var(--blue)" }}
        >
          ← All storymaps
        </Link>
      </footer>
    </div>
  );
}
