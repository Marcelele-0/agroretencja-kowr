import { useSyncExternalStore } from "react";
import type { AnalysisRequest, FarmerFields } from "../api/contract";
import type { Iso } from "../lib/dates";

/**
 * What the farmer knows about a parcel and no public map has: tile drainage, and the rain gauge total
 * of the 7 days ending on a date. Kept in this browser only (per parcel); sent with /api/analyze.
 */
export interface FarmerData {
  /** null = "nie wiem" */
  drained: boolean | null;
  /** rain gauge total [mm] of the 7 days ending on the date */
  rain7d: Record<Iso, number>;
}

export const GAUGE_MAX_MM = 500;
const STORAGE_KEY = "rusalka.farmer.v1";
const EMPTY: FarmerData = Object.freeze({ drained: null, rain7d: Object.freeze({}) as Record<Iso, number> });

function load(): Record<string, FarmerData> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : null;
    if (!parsed || typeof parsed !== "object") return {};
    const out: Record<string, FarmerData> = {};
    for (const [id, v] of Object.entries(parsed as Record<string, Partial<FarmerData>>)) {
      const drained = typeof v?.drained === "boolean" ? v.drained : null;
      const rain7d: Record<Iso, number> = {};
      for (const [d, mm] of Object.entries(v?.rain7d ?? {})) if (typeof mm === "number" && mm >= 0 && mm <= GAUGE_MAX_MM) rain7d[d as Iso] = mm;
      out[id] = { drained, rain7d };
    }
    return out;
  } catch {
    return {};
  }
}

let state: Record<string, FarmerData> = load();
let version = 0;
const listeners = new Set<() => void>();

function save(next: Record<string, FarmerData>): void {
  state = next;
  version++;
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  } catch {
    // private window or blocked storage: the data still works for this visit
  }
  listeners.forEach((l) => l());
}

function update(parcelId: string, fn: (d: FarmerData) => FarmerData): void {
  const d = fn(state[parcelId] ?? EMPTY);
  const next = { ...state };
  if (d.drained === null && !Object.keys(d.rain7d).length) delete next[parcelId];
  else next[parcelId] = d;
  save(next);
}

export function farmerData(parcelId: string): FarmerData {
  return state[parcelId] ?? EMPTY;
}

export function setDrained(parcelId: string, drained: boolean | null): void {
  update(parcelId, (d) => ({ ...d, drained }));
}

/** null removes the gauge total for that date. */
export function setGaugeRain(parcelId: string, date: Iso, mm: number | null): void {
  update(parcelId, (d) => {
    const rain7d = { ...d.rain7d };
    if (mm === null) delete rain7d[date];
    else rain7d[date] = Math.round(Math.min(GAUGE_MAX_MM, Math.max(0, mm)) * 10) / 10;
    return { ...d, rain7d };
  });
}

/** Request fields for /api/analyze, /api/what-if and the PDF (none when the farmer gave nothing). */
export function farmerFields(parcelId: string, date: Iso): FarmerFields {
  const d = farmerData(parcelId);
  const out: FarmerFields = {};
  if (d.drained !== null) out.tile_drainage = d.drained;
  const rain = d.rain7d[date];
  if (rain !== undefined) out.gauge_rain_7d_mm = rain;
  return out;
}

export function withFarmer<T extends Omit<AnalysisRequest, keyof FarmerFields>>(req: T): T & FarmerFields {
  return { ...req, ...farmerFields(req.parcel_id, req.analysis_date as Iso) };
}

/** Decimal comma or dot; null when not a number in 0…GAUGE_MAX_MM. */
export function parseMm(text: string): number | null {
  const t = text.trim().replace(",", ".");
  if (!/^\d+(\.\d+)?$/.test(t)) return null;
  const v = Number(t);
  return v >= 0 && v <= GAUGE_MAX_MM ? v : null;
}

function subscribe(l: () => void): () => void {
  listeners.add(l);
  return () => listeners.delete(l);
}

/** Re-renders on every change; returns a counter to use in effect dependencies. */
export function useFarmerVersion(): number {
  return useSyncExternalStore(subscribe, () => version, () => version);
}

/** For tests. */
export function resetFarmerData(): void {
  save({});
}
