/**
 * Backend contracts used by the frontend. Mirrors src/hydro_core/schemas.py (AnalysisResponse) and
 * ml/rl_irrigator/kowr_rl/api.py (POST /rl/recommend, branch feat/RL). Only the fields the UI reads.
 */
export interface CauseAttribution { climate_pct: number; soil_pct: number; runoff_pct: number; crop_pct: number; method: string; explanations: Record<string, string> }
export interface RetentionRecommendation { id: string; category: string; title: string; rationale: string; delta_taw_mm: number; runoff_reduction_pct: number; priority_rank: number; map_hint?: Record<string, unknown> | null }
export interface WhatIfScenarioResult { baseline_yield_loss_pct: number; scenario_yield_loss_pct: number; delta_reserve_days: number; saved_water_mm: number; reference_dry_year: number }
export interface RetentionPlan { recommendations: RetentionRecommendation[]; what_if_default: WhatIfScenarioResult }
export interface IrrigationStrategySummary { strategy_name: string; avg_yield_loss_pct: number; avg_water_mm: number; avg_deep_percolation_mm: number; water_use_efficiency: number }
export interface IrrigationAdvice { action_now: string; recommended_mm: number; rationale: string; forecast_mode: string; strategies_10y: IrrigationStrategySummary[] }
export interface SourceAuditEntry { category: string; source_name: string; endpoint_or_ref: string; mode: "live" | "pre_fetched" | "fallback"; retrieved_at: string; observation_date: string; spatial_resolution: string; license: string }
/** Map layers from the NMT 1 m pipeline: zones carry a water-state value −5…+5. */
export interface MapLayers { zones?: { type: "FeatureCollection"; features: { type: "Feature"; properties: { value: number; label?: string }; geometry: unknown }[] }; flows?: unknown }

export interface AnalysisRequest { parcel_id: string; crop: string; analysis_date: string }
export interface AnalysisResponse {
  assessment?: {
    water_risk?: { level: string; veto_triggered: boolean; veto_reasons: string[] };
    summary_text?: string;
    /** Final water state −5…+5 from the backend formula (FAO-56 balance, satellite/ML, soil, terrain). Replaces the browser estimate. */
    water_state?: number;
    /** Daily water state around the analysis date; `forecast` = after today. */
    water_state_timeline?: { date: string; value: number; forecast: boolean }[];
    confidence?: { level: "wysoka" | "srednia" | "ograniczona"; resolution_warnings?: string[]; missing_factors?: string[] };
  };
  causes?: CauseAttribution;
  retention?: RetentionPlan;
  irrigation?: IrrigationAdvice;
  map_layers?: MapLayers;
  audit?: { sources: SourceAuditEntry[]; execution_time_s: number };
}

export interface RlRecommendRequest { parcel_id?: string; lat?: number; lon?: number; crop: string; date: string; budget_mm?: number; budget_used_mm?: number }
export interface RlRecommendResponse {
  status: "ok" | "out_of_season" | "not_arable" | "outside_poland" | string;
  message: string;
  recommendation?: { action_now: string; dose_today_mm: number; total_7d_mm: number; plan_7d: { day: number; date: string; dose_mm: number; precip_assumed_mm: number; stress_expected: boolean }[] };
  policy?: { used: string; label: string };
  limitations?: string[];
}
