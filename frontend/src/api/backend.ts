import { request } from "../lib/http";
import type { AnalysisRequest, AnalysisResponse, RlCompareResponse, RlRecommendRequest, RlRecommendResponse, TerrainStatus, WhatIfRequest, WhatIfScenarioResult, ZonesRequest, ZonesResponse } from "./contract";

/**
 * Hooks to the FastAPI backend. VITE_API_URL not set = "/" (same-origin proxy of `pnpm dev` / `pnpm preview` to
 * 127.0.0.1:8000), so the documented start gives the full analysis. Explicitly empty VITE_API_URL= = hooks off:
 * the UI shows the browser-only IUNG estimate and hides the steps that need the server.
 * Endpoints: POST /api/analyze, POST /api/zones, POST /api/what-if, GET /api/report.pdf, POST /rl/recommend, POST /rl/compare.
 */
const RAW_API_URL: string = (import.meta.env.VITE_API_URL ?? "/").trim();
export const backendEnabled = RAW_API_URL !== "";
export const API_URL: string = RAW_API_URL === "/" ? "" : RAW_API_URL.replace(/\/$/, "");

export class BackendError extends Error {
  constructor(
    public readonly status: number,
    message: string,
    public readonly path: string,
    public readonly code?: string,
  ) {
    super(message);
    this.name = "BackendError";
  }
}

export function parseBackendError(path: string, status: number, text: string): BackendError {
  let body: { message?: unknown; detail?: unknown; status?: unknown } | null = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = null;
  }
  const code = typeof body?.status === "string" ? body.status : undefined;
  let msg = "";
  if (typeof body?.message === "string" && body.message.trim()) {
    msg = body.message.trim();
  } else if (typeof body?.detail === "string" && body.detail.trim()) {
    msg = body.detail.trim();
  } else if (Array.isArray(body?.detail)) {
    msg = body.detail
      .map((d) => (d && typeof d === "object" && "msg" in d && typeof d.msg === "string" ? d.msg.trim() : ""))
      .filter(Boolean)
      .join("; ");
  }
  return new BackendError(status, msg || `${path}: HTTP ${status}`, path, code);
}

async function post<T>(path: string, body: unknown, ms: number, signal?: AbortSignal): Promise<T> {
  const res = await request(`${API_URL}${path}`, ms, signal, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!res.ok) throw parseBackendError(path, res.status, res.text);
  return JSON.parse(res.text) as T;
}

export function postAnalyze(req: AnalysisRequest, signal?: AbortSignal): Promise<AnalysisResponse> {
  return post<AnalysisResponse>("/api/analyze", req, 45000, signal);
}
/** Joint intra-parcel zones for several parcels on one grid. The UI shares one request per key through zonesCache. */
export function postZones(req: ZonesRequest, signal?: AbortSignal): Promise<ZonesResponse> {
  return post<ZonesResponse>("/api/zones", req, 30000, signal);
}
export function postWhatIf(req: WhatIfRequest, signal?: AbortSignal): Promise<WhatIfScenarioResult> {
  return post<WhatIfScenarioResult>("/api/what-if", req, 25000, signal);
}
export function postRlRecommend(req: RlRecommendRequest, signal?: AbortSignal): Promise<RlRecommendResponse> {
  return post<RlRecommendResponse>("/rl/recommend", req, 35000, signal);
}
export function postRlCompare(req: { parcel_id: string; crop: string; budget_mm?: number }, signal?: AbortSignal): Promise<RlCompareResponse> {
  return post<RlCompareResponse>("/rl/compare", req, 45000, signal);
}
/** Is the NMT 1 m of the parcel ready yet (asked while an analysis reports terrain_upgrade_pending). */
export async function getTerrainStatus(parcelId: string, signal?: AbortSignal): Promise<TerrainStatus> {
  const path = "/api/terrain/status";
  const res = await request(`${API_URL}${path}?${new URLSearchParams({ parcel_id: parcelId })}`, 10000, signal);
  if (!res.ok) throw parseBackendError(path, res.status, res.text);
  return JSON.parse(res.text) as TerrainStatus;
}
export function reportPdfUrl(req: AnalysisRequest): string | null {
  if (!backendEnabled) return null;
  const q = new URLSearchParams({ parcel_id: req.parcel_id, crop: req.crop, analysis_date: req.analysis_date });
  if (req.tile_drainage !== undefined) q.set("tile_drainage", String(req.tile_drainage));
  if (req.gauge_rain_7d_mm !== undefined) q.set("gauge_rain_7d_mm", String(req.gauge_rain_7d_mm));
  return `${API_URL}/api/report.pdf?${q}`;
}
