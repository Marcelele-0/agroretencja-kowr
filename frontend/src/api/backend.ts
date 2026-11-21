import { request } from "../lib/http";
import type { AnalysisRequest, AnalysisResponse, RlRecommendRequest, RlRecommendResponse } from "./contract";

/**
 * Loose hooks to the FastAPI backend. Empty VITE_API_URL = hooks off: the UI hides the steps that need the server.
 * Endpoints: POST /api/analyze, GET /api/report.pdf, POST /rl/recommend.
 */
export const API_URL: string = (import.meta.env.VITE_API_URL ?? "").replace(/\/$/, "");
export const backendEnabled = API_URL !== "";

async function post<T>(path: string, body: unknown, ms: number, signal?: AbortSignal): Promise<T> {
  const res = await request(`${API_URL}${path}`, ms, signal, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`);
  return JSON.parse(res.text) as T;
}

export function postAnalyze(req: AnalysisRequest, signal?: AbortSignal): Promise<AnalysisResponse> {
  return post<AnalysisResponse>("/api/analyze", req, 30000, signal);
}
export function postRlRecommend(req: RlRecommendRequest, signal?: AbortSignal): Promise<RlRecommendResponse> {
  return post<RlRecommendResponse>("/rl/recommend", req, 20000, signal);
}
export function reportPdfUrl(req: AnalysisRequest): string | null {
  if (!backendEnabled) return null;
  return `${API_URL}/api/report.pdf?${new URLSearchParams({ parcel_id: req.parcel_id, crop: req.crop, analysis_date: req.analysis_date })}`;
}
