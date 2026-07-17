"use client";

// Vector choropleth for storymaps, rendered from the static data contract
// (per-country boundary GeoJSON + per-date region_risks / dependency JSON).
// No raster infrastructure involved: every contract date has data.

import { useEffect, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { COUNTRIES, EA_BOUNDS, N_MEMBERS } from "@/lib/config";
import { getDependency, getGeoJson, getOverlays, getRegionRisks, overlayUrl } from "@/lib/api";
import { RISK_COLOR, type RiskState } from "@/lib/risk";

export type ChoroplethKind = "risk" | "exceedance" | "confidence";

export interface StoryChoroplethProps {
  kind: ChoroplethKind;
  date: string;
  windowH?: number;
  rp?: number;
  height?: number;
}

const NO_DATA = "#445577";

const RAMP_EXCEEDANCE = ["#2B0404", "#7A0D0D", "#B31515", "#E01F1F", "#FF6B6B", "#FFA3A3"];
const RAMP_CONFIDENCE = ["#0D1526", "#1E2D4A", "#2A3F6A", "#1D7A8C", "#14B8A6", "#6EE7B7"];

function rampColor(ramp: string[], t: number): string {
  const clamped = Math.max(0, Math.min(1, t));
  return ramp[Math.min(ramp.length - 1, Math.floor(clamped * ramp.length))];
}

interface LegendEntry {
  color: string;
  label: string;
}

const WINDOW_LABEL: Record<number, string> = {
  3: "3h", 6: "6h", 12: "12h", 24: "24h", 48: "48h", 72: "72h", 168: "7d",
};

async function valueMap(
  kind: ChoroplethKind,
  date: string,
  windowH: number,
  rp: number,
): Promise<{ fill: (pcode: string) => string; text: (pcode: string) => string; legend: LegendEntry[] }> {
  if (kind === "risk") {
    const risks = await getRegionRisks(date);
    return {
      fill: (p) => (risks[p] ? RISK_COLOR[risks[p].risk_state as RiskState] : NO_DATA),
      text: (p) => (risks[p] ? `${risks[p].name}: ${risks[p].risk_label}` : p),
      legend: [0, 1, 2, 3].map((s) => ({
        color: RISK_COLOR[s as RiskState],
        label: ["Green", "Yellow", "Orange", "Red"][s],
      })),
    };
  }
  const dep = await getDependency(date);
  const win = WINDOW_LABEL[windowH] ?? "24h";
  if (kind === "exceedance") {
    const value = (p: string) => dep[p]?.gev?.[win]?.[String(rp)] ?? 0;
    return {
      fill: (p) => (dep[p] ? rampColor(RAMP_EXCEEDANCE, value(p)) : NO_DATA),
      text: (p) => `${p.split("_").slice(1).join(" ")}: P=${value(p).toFixed(2)}`,
      legend: [0.1, 0.3, 0.5, 0.7, 0.9].map((v) => ({
        color: rampColor(RAMP_EXCEEDANCE, v),
        label: v.toFixed(1),
      })),
    };
  }
  const members = (p: string) => dep[p]?.confidence?.m ?? 0;
  return {
    fill: (p) => (dep[p] ? rampColor(RAMP_CONFIDENCE, members(p) / N_MEMBERS) : NO_DATA),
    text: (p) => `${p.split("_").slice(1).join(" ")}: ${members(p)}/${N_MEMBERS} members`,
    legend: [5, 15, 25, 35, 48].map((m) => ({
      color: rampColor(RAMP_CONFIDENCE, m / N_MEMBERS),
      label: `${m}`,
    })),
  };
}

export default function StoryChoropleth({
  kind, date, windowH = 24, rp = 5, height = 460,
}: StoryChoroplethProps) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!ref.current) return;
    const map = L.map(ref.current, {
      zoomControl: false, attributionControl: false,
      minZoom: 3, maxZoom: 10, scrollWheelZoom: false,
    });
    L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}{r}.png",
      { subdomains: "abcd", maxZoom: 19 }).addTo(map);
    L.control.attribution({ position: "bottomright", prefix: false })
      .addAttribution("© CARTO · © OpenStreetMap · GADM/HDX").addTo(map);
    L.control.zoom({ position: "topright" }).addTo(map);
    map.fitBounds(EA_BOUNDS as L.LatLngBoundsExpression);

    let cancelled = false;
    (async () => {
      // Exceedance: prefer the pre-rendered raster field (continuous pixels)
      // when the contract carries one; the choropleth then becomes a light
      // hover/outline layer on top. Missing manifest = vector-only fallback.
      let rasterOn = false;
      if (kind === "exceedance") {
        try {
          const manifest = await getOverlays(date);
          const file = `exceedance_${windowH}h_${rp}y.png`;
          const entry = manifest[file];
          if (entry && !cancelled) {
            L.imageOverlay(overlayUrl(date, file), entry.bounds as L.LatLngBoundsExpression, {
              opacity: 0.82,
            }).addTo(map);
            rasterOn = true;
          }
        } catch {
          rasterOn = false;
        }
      }

      const [style, settled] = await Promise.all([
        valueMap(kind, date, windowH, rp),
        Promise.allSettled(COUNTRIES.map((c) => getGeoJson(c.code))),
      ]);
      if (cancelled) return;

      const features: GeoJSON.Feature[] = [];
      for (const outcome of settled) {
        if (outcome.status !== "fulfilled") continue;
        for (const f of outcome.value.features) {
          const pcode = (f.properties?.pcode || f.properties?.admin1_pcode) as string;
          f.properties = { ...f.properties, pcode };
          features.push(f);
        }
      }
      L.geoJSON(
        { type: "FeatureCollection", features } as GeoJSON.FeatureCollection,
        {
          style: (feat) => ({
            color: "rgba(120,140,180,.45)",
            weight: 0.7,
            fillColor: style.fill(feat?.properties?.pcode),
            fillOpacity: rasterOn ? 0.08 : 0.88,
          }),
          onEachFeature: (feat, lyr) => {
            const pcode = feat.properties?.pcode as string;
            lyr.on({
              mouseover: (e) => {
                (e.target as L.Path).setStyle({ weight: 2 });
                L.popup({ closeButton: false })
                  .setLatLng((e as L.LeafletMouseEvent).latlng)
                  .setContent(style.text(pcode))
                  .openOn(map);
              },
              mouseout: (e) => {
                (e.target as L.Path).setStyle({ weight: 0.7 });
                map.closePopup();
              },
            });
          },
        },
      ).addTo(map);

      const legend = new L.Control({ position: "bottomleft" });
      legend.onAdd = () => {
        const div = L.DomUtil.create("div");
        div.style.cssText =
          "display:flex;gap:8px;padding:4px 8px;border-radius:4px;" +
          "background:rgba(8,13,21,.82);font:9px/1.6 'Space Mono',monospace;color:#B1BDD6";
        div.innerHTML = style.legend
          .map(
            (e) =>
              `<span style="display:inline-flex;align-items:center;gap:3px">` +
              `<span style="width:9px;height:9px;border-radius:2px;background:${e.color}"></span>` +
              `${e.label}</span>`,
          )
          .join("");
        return div;
      };
      legend.addTo(map);
    })();

    return () => {
      cancelled = true;
      map.remove();
    };
  }, [kind, date, windowH, rp]);

  return <div ref={ref} style={{ height, width: "100%" }} />;
}
