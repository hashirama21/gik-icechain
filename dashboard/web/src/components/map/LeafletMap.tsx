"use client";

// Admin-1 risk map (vector GeoJSON, no tile server). Loads per-country
// boundaries from the data contract and colours each unit by its risk display
// class. Click a unit → onSelect(pcode).

import { useEffect, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { COUNTRIES, EA_BOUNDS } from "@/lib/config";
import { getGeoJson } from "@/lib/api";
import { DISPLAY_VAR, displayClass, type UnitRisk } from "@/lib/risk";

export interface LeafletMapProps {
  risks: Record<string, UnitRisk>;
  selected: string | null;
  onSelect: (pcode: string) => void;
}

export default function LeafletMap({ risks, selected, onSelect }: LeafletMapProps) {
  const ref = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const layerRef = useRef<L.GeoJSON | null>(null);

  // init once
  useEffect(() => {
    if (!ref.current || mapRef.current) return;
    const map = L.map(ref.current, {
      zoomControl: false, attributionControl: false, minZoom: 3, maxZoom: 12,
    });
    L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}{r}.png",
      { subdomains: "abcd", maxZoom: 19 }).addTo(map);
    L.control.zoom({ position: "topright" }).addTo(map);
    map.fitBounds(EA_BOUNDS as L.LatLngBoundsExpression);
    mapRef.current = map;
    return () => { map.remove(); mapRef.current = null; };
  }, []);

  // (re)draw admin-1 layer whenever risks change
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    let cancelled = false;

    (async () => {
      const features: GeoJSON.Feature[] = [];
      for (const c of COUNTRIES) {
        try {
          const fc = await getGeoJson(c.code);
          for (const f of fc.features) {
            const pcode = (f.properties?.pcode || f.properties?.admin1_pcode) as string;
            f.properties = { ...f.properties, pcode };
            features.push(f);
          }
        } catch { /* country not built yet */ }
      }
      if (cancelled) return;
      layerRef.current?.remove();
      const layer = L.geoJSON(
        { type: "FeatureCollection", features } as GeoJSON.FeatureCollection,
        {
          style: (feat) => styleFor(feat?.properties?.pcode),
          onEachFeature: (feat, lyr) => {
            const pcode = feat.properties?.pcode as string;
            const r = risks[pcode];
            lyr.on({
              click: () => onSelect(pcode),
              mouseover: (e) => {
                (e.target as L.Path).setStyle({ weight: 2, fillOpacity: 0.95 });
                L.popup({ closeButton: false })
                  .setLatLng((e as L.LeafletMouseEvent).latlng)
                  .setContent(
                    `<div style="font-family:monospace;font-size:11px;background:#121E38;color:#EDF2FF;padding:5px 9px;border-radius:4px;border:1px solid #1E2D4A"><strong>${r?.name ?? pcode}</strong><br>${r?.risk_label ?? "No data"}</div>`,
                  )
                  .openOn(map);
              },
              mouseout: (e) => {
                if (selected !== pcode) (e.target as L.Path).setStyle(styleFor(pcode));
                map.closePopup();
              },
            });
          },
        },
      ).addTo(map);
      layerRef.current = layer;
    })();

    function styleFor(pcode: string): L.PathOptions {
      const r = risks[pcode];
      const bg = r ? DISPLAY_VAR[displayClass(r)] : DISPLAY_VAR.no_data;
      return { color: "rgba(200,220,255,.38)", weight: 0.8, fillColor: bg, fillOpacity: 0.78 };
    }

    return () => { cancelled = true; };
  }, [risks, selected, onSelect]);

  return <div ref={ref} className="w-full h-full z-[1]" />;
}
