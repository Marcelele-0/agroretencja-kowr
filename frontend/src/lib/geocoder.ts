import { isAbort, requestViaProxy, sourceFailure } from "./http";

export const UUG_URL = "https://services.gugik.gov.pl/uug/";

export interface PlaceHit {
  label: string;
  sub: string;
  lon: number;
  lat: number;
  kind: string;
}

function str(v: unknown): string {
  if (typeof v === "string") return v.trim();
  if (typeof v === "number" && Number.isFinite(v)) return String(v);
  return "";
}

export function parseUug(text: string): PlaceHit[] {
  let json: unknown;
  try {
    json = JSON.parse(text);
  } catch {
    return [];
  }
  if (!json || typeof json !== "object") return [];
  const obj = json as Record<string, unknown>;
  if (!obj.results || typeof obj.results !== "object") return [];

  const kind = typeof obj.type === "string" ? obj.type : "place";
  const seen = new Set<string>();
  const hits: PlaceHit[] = [];

  for (const entry of Object.values(obj.results as Record<string, unknown>)) {
    if (!entry || typeof entry !== "object") continue;
    const r = entry as Record<string, unknown>;
    if (r.x == null || r.x === "" || r.y == null || r.y === "") continue;
    const lon = Number(r.x);
    const lat = Number(r.y);
    if (!Number.isFinite(lon) || !Number.isFinite(lat)) continue;
    if (lon < 13 || lon > 25 || lat < 48 || lat > 56) continue;

    const city = str(r.city);
    const street = str(r.street);
    const number = str(r.number);
    const commune = str(r.commune);
    const county = str(r.county).replace(/^powiat\s+/i, "");
    const voivodeship = str(r.voivodeship);
    const simc = str(r.simc);

    let label: string;
    if (street && number) {
      label = city ? `${city}, ${street} ${number}` : `${street} ${number}`;
    } else if (street) {
      label = city ? `${city}, ${street}` : street;
    } else {
      label = city || commune || simc || "Lokalizacja";
    }

    const sub = Array.from(
      new Set(
        [
          commune && commune !== label ? `gm. ${commune}` : "",
          county ? `pow. ${county}` : "",
          voivodeship,
        ].filter(Boolean),
      ),
    ).join(", ");

    const key = `${label}|${lon.toFixed(4)}|${lat.toFixed(4)}`;
    if (seen.has(key)) continue;
    seen.add(key);

    hits.push({ label, sub, lon, lat, kind });
    if (hits.length >= 5) break;
  }

  return hits;
}

export async function searchPlace(query: string): Promise<PlaceHit[]> {
  const trimmed = query.trim();
  if (!trimmed) return [];
  const qs = `request=GetAddress&address=${encodeURIComponent(trimmed)}&srid=4326`;
  let res;
  try {
    res = await requestViaProxy("/uug", `/uug?${qs}`, `${UUG_URL}?${qs}`, 10000);
  } catch (e) {
    if (isAbort(e)) throw e;
    throw sourceFailure("GUGiK UUG", e);
  }
  if (!res.ok) throw sourceFailure("GUGiK UUG", null, res.status);
  return parseUug(res.text);
}
