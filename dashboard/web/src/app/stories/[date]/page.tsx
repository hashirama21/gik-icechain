// Generic per-date storymap. One static page per date in the data contract
// (public/data/index.json at build time); dates with a hand-written MDX
// narrative keep their own route.

import fs from "node:fs";
import path from "node:path";
import Link from "next/link";
import { Block, Prose, Figure, Caption } from "@/components/story/Blocks";
import StoryMap from "@/components/story/StoryMap";

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
  if (m >= 3 && m <= 5) return "MAM (long rains)";
  if (m >= 10 && m <= 12) return "OND (short rains)";
  if (m >= 6 && m <= 9) return "JJAS";
  return "JF (dry season)";
}

export default async function StoryPage({ params }: { params: Promise<{ date: string }> }) {
  const { date } = await params;
  const entry = readIndex()[date];

  return (
    <div className="min-h-screen">
      <div
        className="flex items-center gap-2.5 px-3.5 shrink-0"
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
        <span className="font-mono text-[10px] font-bold tracking-[1.5px]">{date}</span>
      </div>

      <Block>
        <Prose>
          <h1>East Africa Flood Risk — {date}</h1>
          <p>
            The <strong>GIK-IceChain</strong> pipeline assessed flood risk for all{" "}
            {entry ? entry.n_units : 238} admin-1 units across 16 East African countries on this{" "}
            {season(date)} date, combining the 51-member ECMWF IFS ensemble, observed
            precipitation, and the ICPAC CRMA Bayesian Network.
            {entry && (
              <>
                {" "}
                Peak signal: <strong>{entry.risk_label}</strong>.
              </>
            )}
          </p>
          <ul>
            <li>
              <strong>Forecast hazard</strong> — ensemble exceedance probability vs adaptive GEV
              thresholds
            </li>
            <li>
              <strong>Observed antecedent</strong> — daily precipitation observations
            </li>
            <li>
              <strong>Soil memory</strong> — Antecedent Precipitation Index (API), exponential
              decay
            </li>
            <li>
              <strong>Spatial coverage</strong> — fraction of grid cells with an active signal
            </li>
          </ul>
        </Prose>
      </Block>

      <Block>
        <Figure>
          <StoryMap layer="risk" date={date} center={[38, 2]} zoom={5} />
          <Caption>
            CRMA admin-1 risk state. Green/Yellow/Orange/Red traffic-light from the Bayesian
            Network.
          </Caption>
        </Figure>
      </Block>

      <Block>
        <Prose>
          <h2>Exceedance probability (24 h, 5-year return period)</h2>
          <p>
            Fraction of the 51 ensemble members whose worst-case 24-hour accumulation exceeds the
            5-year return-period GEV threshold. Values above 0.4 map to a <strong>High</strong>{" "}
            forecast-hazard state.
          </p>
        </Prose>
        <Figure>
          <StoryMap layer="exceedance" date={date} windowH={24} rp={5} center={[38, 2]} zoom={5} />
          <Caption>Ensemble exceedance probability — 24 h window, 5-year return period.</Caption>
        </Figure>
      </Block>

      <Block>
        <Prose>
          <h2>Observed rainfall</h2>
          <p>
            Observed daily precipitation provides the observational counterpart used by the CRMA
            model&apos;s antecedent-precipitation evidence node.
          </p>
        </Prose>
        <Figure>
          <StoryMap layer="gpm" date={date} center={[38, 2]} zoom={5} />
          <Caption>Observed daily rainfall (mm/day).</Caption>
        </Figure>
      </Block>
    </div>
  );
}
