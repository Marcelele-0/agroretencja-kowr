import { areaWgs84, bboxOf, centroid, parseWkt, toGeometry, type AreaGeometry, type BBox, type Position } from "./geo";
import { isAbort, request, sourceFailure } from "./http";

/** Usługa Lokalizacji Działek Katastralnych (GUGiK). CORS: *. Geometry requested in EPSG:4326 (lon lat). */
export const ULDK_URL = "https://uldk.gugik.gov.pl/";
const RESULT = "geom_wkt,teryt,voivodeship,county,commune,region,parcel";

export interface Parcel {
  id: string;
  voivodeship: string;
  county: string;
  commune: string;
  region: string;
  number: string;
  /** Area of the ULDK boundary. ULDK has no registered (EGiB) area. */
  areaHa: number;
  geometry: AreaGeometry;
  centroid: Position;
  bbox: BBox;
  retrievedAt: Date;
}

export class ParcelNotFound extends Error {}

/** Parses the ULDK text answer: line 1 is the status ("0" = OK, "-1 ..." = no result), line 2 the "|"-separated fields. */
export function parseUldk(text: string, retrievedAt = new Date()): Parcel {
  const lines = text.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
  if (lines[0] !== "0" || !lines[1]) throw new ParcelNotFound(lines[0]?.replace(/^-1\s*/, "") || "brak wyników");
  const [wkt, id, voivodeship, county, commune, region, number] = lines[1].split("|");
  if (!wkt || !id) throw new ParcelNotFound("niepełna odpowiedź ULDK");
  const polys = parseWkt(wkt);
  const geometry = toGeometry(polys);
  return {
    id, voivodeship: voivodeship ?? "", county: (county ?? "").replace(/^powiat\s+/i, ""), commune: commune ?? "", region: region ?? "", number: number ?? "",
    areaHa: areaWgs84(polys) / 10_000, geometry, centroid: centroid(polys), bbox: bboxOf(geometry), retrievedAt,
  };
}

const cache = new Map<string, Promise<Parcel>>();

async function query(params: string, signal?: AbortSignal): Promise<Parcel> {
  let res;
  try {
    res = await request(`${ULDK_URL}?${params}&result=${RESULT}&srid=4326`, 8000, signal);
  } catch (e) {
    if (isAbort(e)) throw e;
    throw sourceFailure("ULDK", e);
  }
  if (!res.ok) throw sourceFailure("ULDK", null, res.status);
  return parseUldk(res.text);
}

/** Normalises user input: trims and removes spaces. */
export function normaliseParcelId(raw: string): string {
  return raw.trim().replace(/\s+/g, "");
}
export function looksLikeParcelId(raw: string): boolean {
  return /^\d{6}_\d\.\d{4}(\.[\w-]+)*\.[\w/-]+$/.test(normaliseParcelId(raw));
}

/** Cached per id; the shared request has no caller signal, so one caller's abort never breaks another. */
export function parcelById(id: string): Promise<Parcel> {
  const key = normaliseParcelId(id);
  const hit = cache.get(key);
  if (hit) return hit;
  const p = query(`request=GetParcelById&id=${encodeURIComponent(key)}`);
  cache.set(key, p);
  p.catch(() => cache.delete(key));
  return p;
}

/** Point in WGS84 (lon, lat) — ULDK expects this order with srid 4326. */
export async function parcelByPoint(lon: number, lat: number, signal?: AbortSignal): Promise<Parcel> {
  const p = await query(`request=GetParcelByXY&xy=${lon.toFixed(7)},${lat.toFixed(7)},4326`, signal);
  if (!cache.has(p.id)) cache.set(p.id, Promise.resolve(p));
  return p;
}
