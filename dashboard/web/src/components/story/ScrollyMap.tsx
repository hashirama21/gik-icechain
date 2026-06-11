"use client";

// Scrollytelling: a single sticky map whose layer/date updates as the reader
// scrolls through steps (react-scrollama). VEDA-style narrative driving.

import { useState } from "react";
import { Scrollama, Step } from "react-scrollama";
import MapWidget from "@/components/map/MapWidget";
import { exceedanceTiles, gpmTiles, riskTiles } from "@/lib/titiler";
import type { LayerKind } from "./StoryMap";

export interface ScrollyStep {
  layer: LayerKind;
  date: string;
  windowH?: number;
  rp?: number;
  text: React.ReactNode;
}

function tilesFor(s: ScrollyStep): string {
  if (s.layer === "risk") return riskTiles(s.date);
  if (s.layer === "gpm") return gpmTiles(s.date);
  return exceedanceTiles(s.date, s.windowH ?? 24, s.rp ?? 5);
}

export default function ScrollyMap({
  steps, center = [38, 2], zoom = 5,
}: { steps: ScrollyStep[]; center?: [number, number]; zoom?: number }) {
  const [active, setActive] = useState(0);
  const cur = steps[active] ?? steps[0];

  return (
    <div className="relative">
      <div className="scrolly-sticky">
        <MapWidget tiles={tilesFor(cur)} center={center} zoom={zoom} height={0} />
      </div>
      <div className="relative -mt-[100vh]">
        <Scrollama offset={0.5} onStepEnter={({ data }: { data: number }) => setActive(data)}>
          {steps.map((s, i) => (
            <Step data={i} key={i}>
              <div className="min-h-screen flex items-center">
                <div className="story panel rounded-lg p-5 backdrop-blur"
                  style={{ background: "rgba(8,13,21,.85)" }}>
                  {s.text}
                </div>
              </div>
            </Step>
          ))}
        </Scrollama>
      </div>
    </div>
  );
}
