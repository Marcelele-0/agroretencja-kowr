import progi from "../data/iung-progi.json";
import { requestViaProxy, sourceFailure } from "./http";

/** IUNG-PIB GeoServer, layer Susza:katgl_landuse2024: soil drought-susceptibility category and land use. CORS: *. */
export const IUNG_WMS = "https://sgeo.iung.pl/geoserver/Susza/wms";
export const IUNG_PROGI = progi;

export type SoilCategory = "I" | "II" | "III" | "IV";
export type Threshold = number | "x" | "-";

export type LandUse =
  | { kind: "mineral"; code: number; category: SoilCategory; label: string }
  | { kind: "organic"; code: number; label: string }
  | { kind: "nonAgri"; code: number; label: string }
  | { kind: "noData"; code: number | null; label: string };

const CATS: SoilCategory[] = ["I", "II", "III", "IV"];
const CAT_LABEL: Record<SoilCategory, string> = {
  I: "gleba bardzo podatna na suszę", II: "gleba podatna na suszę", III: "gleba średnio podatna na suszę", IV: "gleba mało podatna na suszę",
};
/** Water available to plants (WOD) per category, from the IUNG category definitions. */
export const CAT_WOD: Record<SoilCategory, string> = { I: "poniżej 127,5 mm", II: "127,5–169,9 mm", III: "170–202,5 mm", IV: "powyżej 202,5 mm" };

export function classifyIung(code: number | null): LandUse {
  if (code === null) return { kind: "noData", code, label: "Brak danych glebowych w tym punkcie" };
  if (code >= 1 && code <= 4) { const category = CATS[code - 1]!; return { kind: "mineral", code, category, label: `kat. ${category}: ${CAT_LABEL[category]}` }; }
  if (code === 5) return { kind: "organic", code, label: "Gleba organiczna: progi IUNG dotyczą gleb mineralnych" };
  const non: Record<number, string> = { 11: "Las", 12: "Woda", 13: "Zabudowa", 14: "Droga lub nieużytek" };
  if (non[code]) return { kind: "nonAgri", code, label: non[code]! };
  return { kind: "noData", code, label: "Teren bez kategorii gleby w warstwie IUNG" };
}

const landCache = new Map<string, Promise<LandUse>>();
/** Cached per point (about 10 m), shared by the map popover and the analysis page. */
export function landUseAt(lon: number, lat: number): Promise<LandUse> {
  const key = `${lon.toFixed(4)},${lat.toFixed(4)}`;
  const hit = landCache.get(key);
  if (hit) return hit;
  const p = fetchLandUse(lon, lat);
  landCache.set(key, p);
  p.catch(() => landCache.delete(key));
  return p;
}

async function fetchLandUse(lon: number, lat: number): Promise<LandUse> {
  const h = 0.0005;
  const q = new URLSearchParams({
    service: "WMS", version: "1.1.1", request: "GetFeatureInfo", layers: "Susza:katgl_landuse2024", query_layers: "Susza:katgl_landuse2024",
    info_format: "application/json", srs: "EPSG:4326", bbox: `${lon - h},${lat - h},${lon + h},${lat + h}`, width: "101", height: "101", x: "50", y: "50", styles: "",
  });
  let res;
  try {
    res = await requestViaProxy("/iung", `/iung?${q}`, `${IUNG_WMS}?${q}`, 10000);
  } catch (e) {
    throw sourceFailure("Warstwa gleb IUNG", e);
  }
  if (!res.ok) throw sourceFailure("Warstwa gleb IUNG", null, res.status);
  const json = JSON.parse(res.text) as { features?: { properties?: { GRAY_INDEX?: number } }[] };
  const v = json.features?.[0]?.properties?.GRAY_INDEX;
  return classifyIung(v === undefined || v === null ? null : Math.round(Number(v)));
}

/** 14 thresholds (one per SMSR window) for a crop row and soil category; null if the table has no row. */
export function thresholdsFor(iungCrop: string | null, cat: SoilCategory | null): Threshold[] | null {
  if (!iungCrop || !cat) return null;
  const row = (progi.crops as Record<string, Record<string, Threshold[]>>)[iungCrop]?.[cat];
  return row && row.length === 14 ? row : null;
}
