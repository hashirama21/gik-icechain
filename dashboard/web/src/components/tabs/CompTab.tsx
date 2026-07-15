"use client";

// AIFS vs IFS tab. The AIFS track is not yet a delivered pipeline output
// (aifs_track disabled), so this view is explicitly flagged as a PREVIEW
// rather than presenting fabricated divergence numbers as real.

import { Scale } from "lucide-react";

export default function CompTab({ date }: { date: string | null }) {
  return (
    <div className="p-3.5 flex flex-col gap-3" style={{ color: "var(--tp)" }}>
      <div className="flex items-start justify-between flex-wrap gap-2">
        <div>
          <div className="text-base font-bold">AIFS vs IFS Comparison</div>
          <div className="text-[11px] mt-0.5" style={{ color: "var(--ts)" }}>
            AI model (AIFS) vs physical model (IFS) · East Africa
          </div>
        </div>
        <div className="flex gap-1.5">
          <span className="font-mono text-[8px] font-bold px-2 py-[3px] rounded-[3px]"
            style={{ background: "rgba(59,130,246,.14)", border: "1px solid var(--tag-border)",
                     color: "var(--tag-ifs)" }}>IFS · v48r1</span>
          <span className="font-mono text-[8px] font-bold px-2 py-[3px] rounded-[3px]"
            style={{ background: "rgba(168,85,247,.14)", border: "1px solid var(--tag-border)",
                     color: "var(--tag-aifs)" }}>AIFS · preview</span>
        </div>
      </div>

      <div className="rounded-[10px] p-4 flex flex-col items-center justify-center text-center gap-2"
        style={{ background: "var(--sur)", border: "1px solid var(--brd)", minHeight: 240 }}>
        <Scale size={30} style={{ color: "var(--ts)" }} />
        <div className="text-sm font-semibold">AIFS track  preview</div>
        <div className="text-[11px] max-w-md leading-relaxed" style={{ color: "var(--ts)" }}>
          The AIFS ENS branch of the pipeline (<code>aifs_track</code>) is wired but
          not yet produced for {date ?? "this date"}. Once an AIFS run is committed,
          this tab will render the IFS↔AIFS split-map comparison on the same GEV
          thresholds (CMORPH)  the first systematic AI-NWP flood evaluation in
          East Africa.
        </div>
      </div>

      <div className="rounded-lg px-3 py-2.5 text-[10px] leading-relaxed"
        style={{ background: "rgba(168,85,247,.06)", border: "1px solid rgba(168,85,247,.2)", color: "var(--ts)" }}>
        <strong style={{ color: "var(--tag-aifs)" }}>Note:</strong> illustrative until the AIFS
        run + EM-DAT validation are delivered  no fabricated divergence metrics are shown.
      </div>
    </div>
  );
}
