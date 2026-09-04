"use client";

import { useEffect, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { COUNTRIES, EA_BOUNDS } from "@/lib/config";
import { BASEMAP_ATTRIBUTION, BASEMAP_LEAFLET_URL, BASEMAP_SUBDOMAINS } from "@/lib/basemap";
import { getGeoJson } from "@/lib/api";
import { DISPLAY_VAR, displayClass, riskForRp, type UnitRisk } from "@/lib/risk";

export interface LeafletMapProps {
  risks: Record<string, UnitRisk>;
  rp: string;
  selected: string | null;
  onSelect: (pcode: string) => void;
}

export default function LeafletMap({ risks, rp, selected, onSelect }: LeafletMapProps) {
  const ref = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const layerRef = useRef<L.GeoJSON | null>(null);
  // Latest props, readable from stable Leaflet event handlers.
  const stateRef = useRef({ risks, rp, selected, onSelect });
  stateRef.current = { risks, rp, selected, onSelect };

  function styleFor(pcode: string): L.PathOptions {
    const { risks, rp } = stateRef.current;
    const r = risks[pcode];
    const bg = r ? DISPLAY_VAR[displayClass(riskForRp(r, rp))] : DISPLAY_VAR.no_data;
    return { color: "rgba(120,140,180,.55)", weight: 0.8, fillColor: bg, fillOpacity: 0.92 };
  }

  // init once
  useEffect(() => {
    if (!ref.current || mapRef.current) return;
    const map = L.map(ref.current, {
      zoomControl: false, attributionControl: false, minZoom: 3, maxZoom: 12,
    });
    L.tileLayer(BASEMAP_LEAFLET_URL,
      { subdomains: BASEMAP_SUBDOMAINS, maxZoom: 19 }).addTo(map);
    L.control.attribution({ position: "bottomright", prefix: false })
      .addAttribution(BASEMAP_ATTRIBUTION).addTo(map);
    L.control.zoom({ position: "topright" }).addTo(map);
    map.fitBounds(EA_BOUNDS as L.LatLngBoundsExpression);
    mapRef.current = map;
    return () => { map.remove(); mapRef.current = null; };
  }, []);

  // fetch boundaries + build layer once
  useEffect(() => {
    const map = mapRef.current;
    if (!map || layerRef.current) return;
    let cancelled = false;

    (async () => {
      // One request per country, all in flight at once: sequential awaits made
      // the map wait on 16 round trips before its first paint.
      const settled = await Promise.allSettled(COUNTRIES.map((c) => getGeoJson(c.code)));
      const features: GeoJSON.Feature[] = [];
      for (const outcome of settled) {
        if (outcome.status !== "fulfilled") continue; // country not built yet
        for (const f of outcome.value.features) {
          const pcode = (f.properties?.pcode || f.properties?.admin1_pcode) as string;
          f.properties = { ...f.properties, pcode };
          features.push(f);
        }
      }
      if (cancelled) return;
      const layer = L.geoJSON(
        { type: "FeatureCollection", features } as GeoJSON.FeatureCollection,
        {
          style: (feat) => styleFor(feat?.properties?.pcode),
          onEachFeature: (feat, lyr) => {
            const pcode = feat.properties?.pcode as string;
            lyr.on({
              click: () => stateRef.current.onSelect(pcode),
              mouseover: (e) => {
                const { risks, rp } = stateRef.current;
                const r = risks[pcode];
                const label = r ? riskForRp(r, rp).risk_label : "No data";
                (e.target as L.Path).setStyle({ weight: 2, fillOpacity: 0.95 });
                L.popup({ closeButton: false })
                  .setLatLng((e as L.LeafletMouseEvent).latlng)
                  .setContent(`<strong>${r?.name ?? pcode}</strong><br>${label} · ${rp}yr`)
                  .openOn(map);
              },
              mouseout: (e) => {
                if (stateRef.current.selected !== pcode) (e.target as L.Path).setStyle(styleFor(pcode));
                map.closePopup();
              },
            });
          },
        },
      ).addTo(map);
      layerRef.current = layer;
    })();

    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // restyle on risk/RP/selection change  no refetch
  useEffect(() => {
    layerRef.current?.eachLayer((lyr) => {
      const pcode = (lyr as L.Path & { feature?: GeoJSON.Feature }).feature?.properties?.pcode;
      if (pcode) (lyr as L.Path).setStyle(styleFor(pcode));
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [risks, rp, selected]);

  return <div ref={ref} className="w-full h-full z-[1]" />;
}