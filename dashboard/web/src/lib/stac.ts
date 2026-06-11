// Minimal STAC client. The data_pipeline `pipeline.py stac` writes a static catalog
// (collections gik-icechain-risk / gik-icechain-exceedance) so the storymap can
// resolve a COG asset href by (collection, date) instead of hard-coding URLs.

import { DATA_BASE } from "./config";

export interface StacItem {
  id: string;
  bbox: number[];
  properties: { datetime: string; collection: string };
  assets: Record<string, { href: string; type: string; roles: string[] }>;
}

export interface StacCatalog {
  collections: Record<string, { items: StacItem[] }>;
}

let _cache: StacCatalog | null = null;

export async function loadCatalog(): Promise<StacCatalog> {
  if (_cache) return _cache;
  const res = await fetch(`${DATA_BASE}/../stac/catalog.json`);
  if (!res.ok) throw new Error(`STAC catalog unavailable (${res.status})`);
  _cache = (await res.json()) as StacCatalog;
  return _cache;
}

export async function findItem(
  collection: string,
  date: string,
): Promise<StacItem | undefined> {
  const cat = await loadCatalog();
  return cat.collections[collection]?.items.find((it) =>
    it.properties.datetime.startsWith(date),
  );
}
