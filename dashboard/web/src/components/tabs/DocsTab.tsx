"use client";

// DOCS tab  methodology, GEV thresholds, metrics, data sources (ported from v4).

import { COUNTRIES, N_MEMBERS, WINDOWS } from "@/lib/config";
import {
  DISPLAY_BORDER_VAR, DISPLAY_LABEL, DISPLAY_TEXT_VAR, DISPLAY_VAR, type DisplayClass,
} from "@/lib/risk";

const GEV_ROWS: [string, string, string, DisplayClass][] = [
  ["100 years", "≥ 5%", "≥ 3", "critical"],
  ["40 years", "≥ 10%", "≥ 6", "very_high"],
  ["20 years", "≥ 20%", "≥ 11", "high"],
  ["10 years", "≥ 30%", "≥ 16", "significant"],
  ["5 years", "≥ 40%", "≥ 21", "moderate"],
  ["2 years", "≥ 50%", "≥ 26", "low"],
];

const CHIPS = [
  "ECMWF IFS ENS v48r1", "AIFS v1.2", "GPM IMERG v07B", "EM-DAT CC-BY",
  "CMORPH (GEV calibration)", "Zarr v3 · IceChunk", "VirtualiZarr",
  "TiTiler · AWS Lambda", "NASA VEDA-UI", "GADM Admin-1",
];

export default function DocsTab() {
  return (
    <div className="p-3.5 max-w-3xl" style={{ color: "var(--tp)" }}>
      <div className="text-base font-bold mb-1">Documentation</div>
      <div className="text-[11px] mb-4" style={{ color: "var(--ts)" }}>
        GIK-IceChain methodology, KPI definitions, GEV thresholds, data sources.
      </div>

      <Doc title="Dependency Order  5-Step Reload Logic">
        <div className="font-mono text-[10px] leading-loose p-3 rounded-lg"
          style={{ background: "var(--ele)", border: "1px solid var(--brd)", color: "var(--ts)" }}>
          {[
            ["①", "Initialisation date", "IFS ENS base date / forecast cycle (00z)"],
            ["②", "Country + Admin-1", `${COUNTRIES.length} East African countries, 238 admin-1`],
            ["③", "Severity per window", `${WINDOWS.length} windows (${WINDOWS[0]}…${WINDOWS[WINDOWS.length - 1]}), worst over horizon`],
            ["④", "GEV results for window", "100y → 2y hierarchical evaluation"],
            ["⑤", "Ensemble chart", `${N_MEMBERS} members, binary comparison`],
          ].map(([n, a, b]) => (
            <div key={n}><span style={{ color: "var(--teal)" }}>{n}</span> <strong>{a}</strong>  {b}</div>
          ))}
        </div>
      </Doc>

      <Doc title="Forecast Time Axes  Initialisation Date vs Lead Time">
        <p className="text-[11px] mb-2" style={{ color: "var(--ts)" }}>
          A forecast has two time dimensions. The <strong>initialisation date</strong> (base date,
          Step ①) is when the IFS ENS run was issued. The <strong>valid date / lead time</strong> is
          the future period the forecast describes: one run issued on 1 July carries ~15 days of lead
          time, so several runs cover the same valid day at different leads.
        </p>
        <p className="text-[11px] mb-2" style={{ color: "var(--ts)" }}>
          The current severity and return-period assessment aggregates <strong>over the whole
          forecast horizon</strong>: for each window it takes the <strong>maximum accumulation found
          anywhere in the horizon</strong> (max over lead time), not a single valid day nor the first
          period after initialisation. The exceedance probability is therefore the fraction of the
          {" "}{N_MEMBERS} members whose worst window accumulation over the horizon exceeds the GEV
          threshold.
        </p>
        <p className="text-[11px]" style={{ color: "var(--ts)" }}>
          The storyboards additionally report, from the versioned as-of-date record, <strong>how many
          days ahead</strong> an event was already flagged — an explicit per-event lead time.
        </p>
      </Doc>

      <Doc title="GEV Thresholds  Minimum Operational Probabilities">
        <p className="text-[11px] mb-2" style={{ color: "var(--ts)" }}>
          Evaluation order: 100y first → 2y last. First condition met = window severity.
        </p>
        <div className="overflow-x-auto">
        <table className="w-full border-collapse text-[10px]">
          <thead>
            <tr>{["Return Period", "Min P%", `Min members / ${N_MEMBERS}`, "Severity"].map((h) => (
              <th key={h} className="font-mono text-[8px] tracking-[1px] text-left p-2"
                style={{ color: "var(--td)", borderBottom: "1px solid var(--brd)" }}>{h}</th>
            ))}</tr>
          </thead>
          <tbody>
            {GEV_ROWS.map(([period, minP, minM, cls]) => {
              const border = "1px solid color-mix(in srgb, var(--brd) 55%, transparent)";
              return (
                <tr key={period}>
                  <td className="font-mono p-2" style={{ borderBottom: border }}>{period}</td>
                  <td className="font-mono p-2" style={{ borderBottom: border }}>{minP}</td>
                  <td className="font-mono p-2" style={{ borderBottom: border }}>{minM}</td>
                  <td className="p-2" style={{ borderBottom: border }}>
                    <span className="font-mono text-[9px] font-bold px-1.5 py-0.5 rounded-[3px]"
                      style={{ background: DISPLAY_VAR[cls], color: DISPLAY_TEXT_VAR[cls],
                               border: `1px solid ${DISPLAY_BORDER_VAR[cls]}` }}>
                      {DISPLAY_LABEL[cls]}
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        </div>
      </Doc>

      <Doc title="Core Risk Metrics">
        {[
          ["Exceedance Probability", `% of ${N_MEMBERS} ensemble members exceeding a GEV return-period threshold for a window. Source: IFS ENS · ECMWF.`],
          ["Return Period Triggered", "Highest GEV return period meeting its minimum probability (100y → 2y). Source: CMORPH/GPM GEV calibration."],
          ["Admin-1 Risk Level", "CRMA Bayesian Network output (Green/Yellow/Orange/Red) integrating exceedance, GPM IMERG, soil saturation. Source: Component 3."],
        ].map(([n, d]) => (
          <div key={n} className="rounded-md p-2.5 mb-1.5" style={{ background: "var(--ele)", border: "1px solid var(--brd)" }}>
            <div className="font-mono text-[10px] font-bold mb-0.5">{n}</div>
            <div className="text-[10px] leading-snug" style={{ color: "var(--ts)" }}>{d}</div>
          </div>
        ))}
      </Doc>

      <Doc title="Data Sources">
        <div className="flex flex-wrap gap-1">
          {CHIPS.map((c) => (
            <span key={c} className="font-mono text-[9px] px-2 py-[3px] rounded-[3px]"
              style={{ background: "var(--ele)", border: "1px solid var(--brd)", color: "var(--td)" }}>{c}</span>
          ))}
        </div>
      </Doc>

      <div className="rounded-lg p-3 text-[10px] leading-relaxed mt-1"
        style={{ background: "rgba(59,130,246,.06)", border: "1px solid rgba(59,130,246,.18)", color: "var(--ts)" }}>
        <strong style={{ color: "var(--blue)" }}>Responsibility notice:</strong> a probabilistic
        decision-support tool. Risk scores are probability estimates  they do not replace the
        operational judgment of competent authorities (NMAs, ICPAC/IGAD). Boundaries from GADM/HDX,
        no political implication.
      </div>
    </div>
  );
}

function Doc({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mb-5">
      <h2 className="text-[13px] font-semibold mb-2 pb-1.5" style={{ borderBottom: "1px solid var(--brd)" }}>{title}</h2>
      {children}
    </div>
  );
}
