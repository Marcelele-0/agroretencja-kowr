import { BackendError, postZones } from "../api/backend";
import type { MapLayers, ZonesRequest, ZonesResponse } from "../api/contract";
import type { MapZone } from "../map/ParcelMap";

/** Base states are sent and keyed on a 0.25 grid: smaller changes never start a new /api/zones request. */
export const STATE_STEP = 0.25;
/** Parcels per /api/zones request (backend limit). */
export const MAX_JOINT_PARCELS = 12;

export interface JointZoneInput {
  id: string;
  /** Base water state −5…+5: the backend water_state when present, else the local stage. */
  state: number | null;
  /** The row can still get a base state (parcel, land or weather still loading). It joins the request once it has one. */
  pending: boolean;
  /** Frontend crop code; neighbours blend only when their crops match. */
  crop?: string;
}
export interface ZoneBin { step: number; pct: number; ha: number }

export function roundState(v: number): number {
  const r = Math.round(Math.max(-5, Math.min(5, v)) / STATE_STEP) * STATE_STEP;
  return r === 0 ? 0 : r;
}

/**
 * Request body and cache key for the rows that already have a base state, or null when none has one.
 * Rows still loading do not hold the request back: the key changes when they arrive and a new request follows.
 * Key = sorted ids + crops + states rounded to 0.25; at most 12 parcels (the rest keep their analyze zones).
 */
export function jointRequestOf(rows: JointZoneInput[]): { key: string; req: ZonesRequest } | null {
  const parcels = rows
    .filter((r): r is JointZoneInput & { state: number } => r.state !== null && Number.isFinite(r.state))
    .map((r) => ({ parcel_id: r.id, water_state: roundState(r.state), ...(r.crop ? { crop: r.crop } : {}) }))
    .sort((a, b) => (a.parcel_id < b.parcel_id ? -1 : a.parcel_id > b.parcel_id ? 1 : 0));
  const unique = parcels.filter((p, i) => i === 0 || parcels[i - 1]!.parcel_id !== p.parcel_id).slice(0, MAX_JOINT_PARCELS);
  if (!unique.length) return null;
  return { key: unique.map((p) => `${p.parcel_id}~${p.crop ?? ""}=${p.water_state.toFixed(2)}`).join(","), req: { parcels: unique } };
}

/** A zone the map can fill: finite value and a polygon with at least one ring (narrow strips can come back empty). */
export function drawableZone(geometry: unknown, value: unknown): boolean {
  if (typeof value !== "number" || !Number.isFinite(value)) return false;
  const g = geometry as { type?: string; coordinates?: unknown } | null;
  if (!g || (g.type !== "Polygon" && g.type !== "MultiPolygon") || !Array.isArray(g.coordinates)) return false;
  const rings = g.type === "Polygon" ? [g.coordinates] : g.coordinates;
  return rings.some((poly) => Array.isArray(poly) && poly.some((ring) => Array.isArray(ring) && ring.length >= 4));
}

/** Drawable zones of a collection; null when none is left, so the parcel falls back to the next source or the flat stage fill. */
export function zonesFromFeatures(fc: MapLayers["zones"] | null | undefined): MapZone[] | null {
  const z = (fc?.features ?? [])
    .filter((f) => drawableZone(f.geometry, f.properties?.value))
    .map((f) => ({ geometry: f.geometry, value: f.properties.value, areaPct: f.properties.area_pct, areaHa: f.properties.area_ha }));
  return z.length ? z : null;
}

/** Joint zones per parcel id; parcels with an error or an empty collection are left out (they fall back to the analyze zones). */
export function zonesByParcel(resp: ZonesResponse | null | undefined): Record<string, MapZone[]> {
  const out: Record<string, MapZone[]> = {};
  if (!resp?.zones) return out;
  for (const [id, fc] of Object.entries(resp.zones)) {
    if (resp.errors?.[id]) continue;
    const z = zonesFromFeatures(fc);
    if (z?.length) out[id] = z;
  }
  return out;
}

function step(v: number | null | undefined): number | null {
  if (v === null || v === undefined || !Number.isFinite(v)) return null;
  return Math.max(-5, Math.min(5, Math.round(v)));
}

/** Area share of each water-state class (dry→wet). Without zones: one bin of the fallback stage covering the whole parcel. */
export function zoneBins(zones: MapZone[] | null | undefined, fallbackStage: number | null, areaHa?: number | null): ZoneBin[] {
  const byStep = new Map<number, ZoneBin>();
  if (zones?.length) {
    const hasExplicitPct = zones.some((z) => typeof z.areaPct === "number" && Number.isFinite(z.areaPct));
    const evenPct = 100 / zones.length;
    for (const z of zones) {
      const s = step(z.value ?? fallbackStage);
      if (s === null) continue;
      const prev = byStep.get(s) ?? { step: s, pct: 0, ha: 0 };
      prev.pct += hasExplicitPct && typeof z.areaPct === "number" && Number.isFinite(z.areaPct) ? z.areaPct : evenPct;
      if (typeof z.areaHa === "number" && Number.isFinite(z.areaHa)) prev.ha += z.areaHa;
      byStep.set(s, prev);
    }
  } else {
    const s = step(fallbackStage);
    if (s !== null) byStep.set(s, { step: s, pct: 100, ha: areaHa ?? 0 });
  }
  const bins = [...byStep.values()].sort((a, b) => a.step - b.step);
  const total = bins.reduce((acc, b) => acc + b.pct, 0);
  return bins.map((b) => ({ ...b, pct: total > 0 ? Math.max(0, Math.min(100, (100 * b.pct) / total)) : 0 }));
}

/** Classes present (area > 0) in any of the parcels, ordered dry→wet (−5…+5). Every row histogram uses this order. */
export function zoneClassOrder(rows: { zones: MapZone[] | null | undefined; fallbackStage: number | null }[]): number[] {
  const present = new Set<number>();
  for (const r of rows) for (const b of zoneBins(r.zones, r.fallbackStage)) if (b.pct > 0) present.add(b.step);
  return [...present].sort((a, b) => a - b);
}

/** Bins laid out in the shared class order: absent classes get 0 %, classes missing from the order are merged in by step. */
export function alignBins(bins: ZoneBin[], order: number[] | null | undefined): ZoneBin[] {
  if (!order?.length) return bins;
  const by = new Map(bins.map((b) => [b.step, b]));
  const steps = new Set([...order, ...bins.map((b) => b.step)]);
  return [...steps].sort((a, b) => a - b).map((s) => by.get(s) ?? { step: s, pct: 0, ha: 0 });
}

export type ZonesFetcher = (req: ZonesRequest) => Promise<ZonesResponse>;
export interface ZonesCache {
  /** Shared request per key: callers never abort it; resolved results stay in a small LRU, failures are dropped. */
  get(key: string, req: ZonesRequest): Promise<ZonesResponse>;
  peek(key: string): ZonesResponse | undefined;
  /** False for a while after the endpoint answered 404/405 (or a non-JSON page): callers use the analyze zones. */
  available(): boolean;
}

export function createZonesCache(fetcher: ZonesFetcher, opts: { maxEntries?: number; retryAfterMs?: number; now?: () => number } = {}): ZonesCache {
  const maxEntries = Math.max(1, opts.maxEntries ?? 12);
  const retryAfterMs = opts.retryAfterMs ?? 60_000;
  const now = opts.now ?? Date.now;
  const entries = new Map<string, { promise: Promise<ZonesResponse>; data?: ZonesResponse }>();
  let offUntil = 0;

  const evict = () => {
    for (const [k, e] of entries) {
      if (entries.size <= maxEntries) break;
      if (e.data !== undefined) entries.delete(k);
    }
  };

  return {
    get(key, req) {
      const hit = entries.get(key);
      if (hit) {
        entries.delete(key);
        entries.set(key, hit);
        return hit.promise;
      }
      const entry: { promise: Promise<ZonesResponse>; data?: ZonesResponse } = { promise: Promise.resolve(null as never) };
      entry.promise = Promise.resolve()
        .then(() => fetcher(req))
        .then(
          (data) => {
            if (entries.get(key) === entry) { entry.data = data; evict(); }
            return data;
          },
          (e: unknown) => {
            if (entries.get(key) === entry) entries.delete(key);
            if ((e instanceof BackendError && (e.status === 404 || e.status === 405)) || e instanceof SyntaxError) offUntil = now() + retryAfterMs;
            throw e;
          },
        );
      entry.promise.catch(() => undefined);
      entries.set(key, entry);
      evict();
      return entry.promise;
    },
    peek(key) {
      return entries.get(key)?.data;
    },
    available() {
      return now() >= offUntil;
    },
  };
}

/** App-wide cache: the parcel rows, the detail panel and the map read the same joint result. */
export const zonesCache = createZonesCache((req) => postZones(req));
