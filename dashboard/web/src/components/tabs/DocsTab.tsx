"use client";

// DOCS tab — methodology, GEV thresholds, metrics, data sources (ported from v4).

const GEV_ROWS = [
  ["100 years", "≥ 5%", "≥ 3", "Critical", "var(--r500)"],
  ["50 years", "≥ 10%", "≥ 6", "Very High", "var(--r600)"],
  ["20 years", "≥ 20%", "≥ 11", "High", "var(--r700)"],
  ["10 years", "≥ 30%", "≥ 16", "Significant", "var(--r800)"],
  ["5 years", "≥ 40%", "≥ 21", "Moderate", "var(--r900)"],
  ["2 years", "≥ 50%", "≥ 26", "Low", "var(--r950)"],
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

      <Doc title="Dependency Order — 5-Step Reload Logic">
        <div className="font-mono text-[10px] leading-loose p-3 rounded-lg"
          style={{ background: "var(--ele)", border: "1px solid var(--brd)", color: "var(--ts)" }}>
          {[
            ["①", "Date selected", "IFS ENS forecast cycle (00z)"],
            ["②", "Country + Admin-1", "16 East African countries, 238 admin-1"],
            ["③", "Max severity per window", "7 windows (3h…7d)"],
            ["④", "GEV results for window", "100y → 2y hierarchical evaluation"],
            ["⑤", "Ensemble chart", "51 members, binary comparison"],
          ].map(([n, a, b]) => (
            <div key={n}><span style={{ color: "var(--teal)" }}>{n}</span> <strong>{a}</strong> — {b}</div>
          ))}
        </div>
      </Doc>

      <Doc title="GEV Thresholds — Minimum Operational Probabilities">
        <p className="text-[11px] mb-2" style={{ color: "var(--ts)" }}>
          Evaluation order: 100y first → 2y last. First condition met = window severity.
        </p>
        <table className="w-full border-collapse text-[10px]">
          <thead>
            <tr>{["Return Period", "Min P%", "Min members / 51", "Severity"].map((h) => (
              <th key={h} className="font-mono text-[8px] tracking-[1px] text-left p-2"
                style={{ color: "var(--td)", borderBottom: "1px solid var(--brd)" }}>{h}</th>
            ))}</tr>
          </thead>
          <tbody>
            {GEV_ROWS.map((r) => (
              <tr key={r[0]}>
                <td className="font-mono p-2" style={{ borderBottom: "1px solid rgba(30,45,74,.25)" }}>{r[0]}</td>
                <td className="font-mono p-2" style={{ borderBottom: "1px solid rgba(30,45,74,.25)" }}>{r[1]}</td>
                <td className="font-mono p-2" style={{ borderBottom: "1px solid rgba(30,45,74,.25)" }}>{r[2]}</td>
                <td className="font-mono p-2" style={{ color: r[4], borderBottom: "1px solid rgba(30,45,74,.25)" }}>{r[3]}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Doc>

      <Doc title="Core Risk Metrics">
        {[
          ["Exceedance Probability", "% of 51 ensemble members exceeding a GEV return-period threshold for a window. Source: IFS ENS · ECMWF."],
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
        decision-support tool. Risk scores are probability estimates — they do not replace the
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
