import type { AnalysisResponse, RlCompareResponse, RlRecommendResponse, RlSource, WhatIfScenarioResult } from "../api/contract";
import { DRY_MEASURES, measureLook, WET_MEASURES } from "../lib/catalog";
import { cropOf, type CropCode } from "../lib/crops";
import { formatDayMonth, formatLong, type Iso } from "../lib/dates";
import type { AreaGeometry, Position } from "../lib/geo";
import type { LandUse } from "../lib/iung";
import type { KbwWindow, RiskLevel } from "../lib/kbw";
import type { SourceEntry } from "../lib/sources";
import type { Parcel } from "../lib/uldk";
import type { LocalResult, TimelineDay } from "../analysis/local";
import { BAL, balStep } from "../ds/status";
import { dec, ha, mm, timePl } from "../pages/shared";

export interface SvgSilhouette {
  paths: string[];
  viewBox: string;
  widthM: number;
  heightM: number;
}

export interface ReportMeasureRow {
  id: string;
  rank: number;
  title: string;
  effect: string;
  permit: boolean;
  selected: boolean;
}

export interface ReportCauseRow {
  key: "klimat" | "gleba" | "splyw" | "uprawa";
  label: string;
  pct: number | null;
  note: string;
}

export interface ReportIrrigationBlock {
  title: string;
  text: string;
  meta: string | null;
  days: { date: Iso; valueMm: number; stress?: boolean; kind: "dose" | "rain" }[];
  strategies: { label: string; yieldLossPct: number; waterMm: number; stressDays: number; active: boolean }[];
}

export interface ReportSourceRow {
  name: string;
  what: string;
  resolution: string;
  retrieved: string;
  license: string;
}

export interface ParcelReportSheet {
  pageNumber: number;
  totalPages: number;
  parcelId: string;
  headline: string;
  subline: string;
  cropLabel: string;
  soilLabel: string;
  analysisDate: Iso;
  analysisDateLabel: string;
  silhouette: SvgSilhouette | null;
  nonAgriMessage: string | null;
  stage: number | null;
  stageName: string;
  risk: { level: RiskLevel; side?: "dry" | "wet"; veto: boolean; vetoReason: string | null } | null;
  confidence: { level: "wysoka" | "srednia" | "ograniczona"; detail: string } | null;
  caption: string | null;
  kpis: { label: string; value: string; unit: string }[];
  componentsSummary: string | null;
  season: number | null;
  dayMonthLabel: string;
  kbwWindows: KbwWindow[];
  activeWindow: number | null;
  years: { year: number; value: number | null }[];
  timeline: TimelineDay[];
  causesMethod: string | null;
  causes: ReportCauseRow[];
  measures: ReportMeasureRow[];
  whatIfSummary: string | null;
  irrigation: ReportIrrigationBlock | null;
  sources: ReportSourceRow[];
}

export interface ReportDocument {
  title: string;
  analysisDate: Iso;
  generatedAt: string;
  sheets: ParcelReportSheet[];
}

export interface ParcelSheetInput {
  parcelId: string;
  parcel: Parcel | null;
  crop: CropCode;
  land: LandUse | null;
  local: LocalResult | null;
  backend?: AnalysisResponse | null;
  rl?: RlRecommendResponse | null;
  rlCompare?: RlCompareResponse | null;
  whatIfBackend?: WhatIfScenarioResult | null;
  pickedMeasures?: Set<string>;
  sources?: SourceEntry[];
  nonAgriMessage?: string | null;
}

const r1 = (n: number) => Math.round(n * 10) / 10;

/**
 * Projects parcel polygon(s) from WGS84 to a scaled local metric SVG silhouette.
 * Multi-ring paths inside one polygon use `M ... Z` concatenation for evenodd hole fill.
 */
export function projectParcelSvg(geometry: AreaGeometry, width = 120, height = 72, pad = 7): SvgSilhouette | null {
  const polygons = geometry.type === "Polygon" ? [geometry.coordinates] : geometry.coordinates;
  const allPts: Position[] = [];
  for (const poly of polygons) {
    for (const ring of poly) {
      for (const pt of ring) allPts.push(pt);
    }
  }
  if (allPts.length < 3) return null;

  let minLon = Infinity, maxLon = -Infinity, minLat = Infinity, maxLat = -Infinity;
  for (const [lon, lat] of allPts) {
    if (!Number.isFinite(lon) || !Number.isFinite(lat)) return null;
    if (lon < minLon) minLon = lon;
    if (lon > maxLon) maxLon = lon;
    if (lat < minLat) minLat = lat;
    if (lat > maxLat) maxLat = lat;
  }

  const meanLat = (minLat + maxLat) / 2;
  const mPerDegLat = 111_320;
  const mPerDegLon = mPerDegLat * Math.max(0.2, Math.cos((meanLat * Math.PI) / 180));
  const rawW = (maxLon - minLon) * mPerDegLon;
  const rawH = (maxLat - minLat) * mPerDegLat;
  if (rawW <= 0 && rawH <= 0) return null;

  const innerW = Math.max(1, width - 2 * pad);
  const innerH = Math.max(1, height - 2 * pad);
  const scale = Math.min(innerW / Math.max(rawW, 1e-6), innerH / Math.max(rawH, 1e-6));
  const offX = pad + (innerW - rawW * scale) / 2;
  const offY = pad + (innerH - rawH * scale) / 2;

  const paths: string[] = [];
  for (const poly of polygons) {
    const segs: string[] = [];
    for (const ring of poly) {
      if (ring.length < 3) continue;
      const cmds = ring.map(([lon, lat], i) => {
        const x = r1(offX + (lon - minLon) * mPerDegLon * scale);
        const y = r1(offY + (maxLat - lat) * mPerDegLat * scale);
        return `${i === 0 ? "M" : "L"}${x} ${y}`;
      });
      segs.push(`${cmds.join(" ")} Z`);
    }
    if (segs.length) paths.push(segs.join(" "));
  }
  if (!paths.length) return null;

  return {
    paths,
    viewBox: `0 0 ${width} ${height}`,
    widthM: Math.max(1, Math.round(rawW)),
    heightM: Math.max(1, Math.round(rawH)),
  };
}

function buildCauses(local: LocalResult | null, backend?: AnalysisResponse | null): { method: string | null; rows: ReportCauseRow[] } {
  const c = backend?.causes;
  if (c) {
    const e = c.explanations;
    // No deficit: shares are not computed, so the report shows the factors without percentages.
    const pct = (v: number) => (c.has_deficit === false ? null : Math.round(v));
    return {
      method: c.method,
      rows: [
        { key: "klimat", label: "Klimat", pct: pct(c.climate_pct), note: e.klimat ?? e.climate ?? "" },
        { key: "gleba", label: "Gleba", pct: pct(c.soil_pct), note: e.gleba ?? e.soil ?? "" },
        { key: "splyw", label: "Spływ", pct: pct(c.runoff_pct), note: e.splyw ?? e.runoff ?? "" },
        { key: "uprawa", label: "Uprawa", pct: pct(c.crop_pct), note: e.uprawa ?? e.crop ?? "" },
      ],
    };
  }
  if (!local) return { method: null, rows: [] };
  return {
    method: "Analiza lokalna KBW + kategoria gleby IUNG",
    rows: local.causes.map((x) => ({ key: x.key, label: x.label, pct: null, note: x.text })),
  };
}

function buildMeasures(
  local: LocalResult | null,
  backend: AnalysisResponse | null | undefined,
  picked: Set<string>,
): { rows: ReportMeasureRow[]; whatIfSummary: string | null } {
  const recs = backend?.retention?.recommendations;
  if (recs?.length) {
    const sorted = [...recs].sort((a, b) => a.priority_rank - b.priority_rank).slice(0, 5);
    const rows = sorted.map((r) => {
      const look = measureLook(`${r.id} ${r.category}`);
      const parts = [
        r.delta_taw_mm > 0 ? `+${dec(r.delta_taw_mm, 0)} mm TAW` : null,
        r.runoff_reduction_pct > 0 ? `−${dec(r.runoff_reduction_pct, 0)}% spływu` : null,
        r.effect_yield_pp ? `−${dec(r.effect_yield_pp, 1)} p.p. straty` : null,
      ].filter(Boolean);
      return {
        id: r.id,
        rank: r.priority_rank,
        title: r.title,
        effect: parts.join(" · ") || r.rationale,
        permit: r.requires_permit ?? look.permit,
        selected: picked.has(r.id),
      };
    });
    return { rows, whatIfSummary: null };
  }

  const wet = (local?.stage ?? 0) >= 2;
  const list = wet ? WET_MEASURES : DRY_MEASURES;
  const rows = list.slice(0, 5).map((m, idx) => ({
    id: m.id,
    rank: idx + 1,
    title: m.title,
    effect: m.sim?.note ? `${m.effect} (${m.sim.note})` : m.effect,
    permit: m.permit,
    selected: picked.has(m.id),
  }));
  return { rows, whatIfSummary: null };
}

function formatWhatIf(
  whatIfBackend: WhatIfScenarioResult | null | undefined,
  backend: AnalysisResponse | null | undefined,
  measures: ReportMeasureRow[],
): string | null {
  if (whatIfBackend) {
    return `Symulacja „Co jeśli” (rok ${whatIfBackend.reference_dry_year}): strata plonu ${dec(whatIfBackend.baseline_yield_loss_pct, 1)}% → ${dec(whatIfBackend.scenario_yield_loss_pct, 1)}%, rezerwa +${whatIfBackend.delta_reserve_days} dni, zatrzymana woda +${dec(whatIfBackend.saved_water_mm, 0)} mm.`;
  }
  const pickedTitles = measures.filter((m) => m.selected).map((m) => m.title);
  if (pickedTitles.length) {
    return `Wybrane działania na działce: ${pickedTitles.join(", ")}.`;
  }
  const def = backend?.retention?.what_if_default;
  if (def) {
    return `Domyślne działania bez urządzeń (rok ${def.reference_dry_year}): strata plonu ${dec(def.baseline_yield_loss_pct, 0)}% → ${dec(def.scenario_yield_loss_pct, 0)}%, +${def.delta_reserve_days} dni rezerwy, +${dec(def.saved_water_mm, 0)} mm wody.`;
  }
  return null;
}

function buildIrrigation(
  local: LocalResult | null,
  rl: RlRecommendResponse | null | undefined,
  rlCompare: RlCompareResponse | null | undefined,
): ReportIrrigationBlock | null {
  const strategies = (rlCompare?.strategies ?? []).slice(0, 3).map((s) => ({
    label: s.label,
    yieldLossPct: s.avg_yield_loss_pct,
    waterMm: s.avg_water_mm,
    stressDays: s.avg_stress_days,
    active: s.strategy === rlCompare?.default_policy,
  }));

  if (local && !local.offSeason && rl?.status === "ok" && rl.recommendation) {
    const r = rl.recommendation;
    return {
      title: r.action_now,
      text: rl.message,
      meta: `Dawka dziś: ${dec(r.dose_today_mm, 0)} mm · suma 7 dni: ${dec(r.total_7d_mm, 0)} mm${rl.policy ? ` · ${rl.policy.label}` : ""}`,
      days: r.plan_7d.slice(0, 7).map((d) => ({ date: d.date, valueMm: d.dose_mm, stress: d.stress_expected, kind: "dose" })),
      strategies,
    };
  }
  if (local && !local.offSeason && local.irrigation) {
    return {
      title: local.irrigation.title,
      text: local.irrigation.text,
      meta: local.forecast ? `Prognoza opadu 7 dni: łącznie ${dec(local.forecast.sum7, 0)} mm` : null,
      days: (local.forecast?.days ?? []).slice(0, 7).map((d) => ({ date: d.date, valueMm: d.p, kind: "rain" })),
      strategies,
    };
  }
  // Off season, today: the RL hold-off decision with the rain forecast of the coming days.
  if (rl?.status === "out_of_season" && rl.recommendation) {
    const rain = local?.forecast?.days.length
      ? local.forecast.days.slice(0, 7).map((d) => ({ date: d.date, valueMm: d.p, kind: "rain" as const }))
      : rl.recommendation.plan_7d.slice(0, 7).map((d) => ({ date: d.date, valueMm: d.precip_assumed_mm, kind: "rain" as const }));
    return {
      title: rl.recommendation.action_now,
      text: rl.message,
      meta: rain.length ? `Prognoza opadu 7 dni: łącznie ${dec(rain.reduce((a, d) => a + d.valueMm, 0), 0)} mm` : null,
      days: rain,
      strategies,
    };
  }
  if (local?.offSeason) {
    return {
      title: "Poza sezonem wegetacji",
      text: "W wybranym terminie uprawa nie wymaga nawadniania.",
      meta: strategies.length ? "Poniżej porównanie strategii z 10 pełnych sezonów." : null,
      days: [],
      strategies,
    };
  }
  return null;
}

function buildSources(
  sources: SourceEntry[] | undefined,
  backend: AnalysisResponse | null | undefined,
  rl: RlRecommendResponse | null | undefined,
  rlCompare: RlCompareResponse | null | undefined,
): ReportSourceRow[] {
  const seen = new Set<string>();
  const out: ReportSourceRow[] = [];
  const push = (row: ReportSourceRow) => {
    if (!row.name || seen.has(row.name)) return;
    seen.add(row.name);
    out.push(row);
  };
  for (const s of sources ?? []) {
    push({ name: s.name, what: s.what, resolution: s.resolution, retrieved: timePl(s.retrievedAt), license: s.license });
  }
  for (const s of backend?.audit?.sources ?? []) {
    push({
      name: s.source_name,
      what: s.category,
      resolution: s.spatial_resolution,
      retrieved: timePl(new Date(s.retrieved_at)),
      license: s.license,
    });
  }
  for (const s of [...(rl?.sources ?? []), ...(rlCompare?.sources ?? [])] as RlSource[]) {
    push({
      name: s.name,
      what: "moduł nawadniania (RL)",
      resolution: s.detail ?? "punkt / siatka",
      retrieved: timePl(new Date(s.retrieved_at)),
      license: s.license,
    });
  }
  return out.slice(0, 6);
}

function buildComponentsSummary(backend?: AnalysisResponse | null): string | null {
  const a = backend?.assessment;
  if (!a) return null;
  if (a.components?.length) {
    return a.components
      .slice(0, 4)
      .map((c) => `${c.label}: ${c.value}`)
      .join(" · ");
  }
  if (a.water_score?.components?.length) {
    return a.water_score.components
      .filter((c) => c.value !== null && c.value !== undefined)
      .slice(0, 4)
      .map((c) => `${c.label}: ${Math.round(c.value!)}/100`)
      .join(" · ");
  }
  return null;
}

/** Deterministic Builder constructing 1 rigid A4 sheet per parcel for print/PDF export. */
export class ReportDocumentBuilder {
  private title = "Rusałka — Raport stanu wody i retencji";
  private analysisDate: Iso = "";
  private generatedAt = "";
  private readonly sheets: ParcelReportSheet[] = [];

  setTitle(title: string): this {
    this.title = title;
    return this;
  }

  setMetadata(analysisDate: Iso, generatedAt: string): this {
    this.analysisDate = analysisDate;
    this.generatedAt = generatedAt;
    return this;
  }

  addParcelSheet(input: ParcelSheetInput): this {
    const { parcel, land, local, backend } = input;
    const picked = input.pickedMeasures ?? new Set<string>();
    const cropInfo = cropOf(input.crop);
    const stage = balStep(local?.stage);
    const stageName = stage === null ? "Brak danych" : BAL[String(stage)]!;
    const silhouette = parcel ? projectParcelSvg(parcel.geometry) : null;
    const headline = parcel ? `Działka ${parcel.number}, ${parcel.region}` : `Działka ${input.parcelId}`;
    const subline = parcel
      ? `gm. ${parcel.commune}, pow. ${parcel.county} · ${ha(parcel.areaHa)} ha · TERYT ${parcel.id}`
      : `TERYT ${input.parcelId}`;
    const soilLabel = land ? land.label : "brak danych glebowych IUNG";
    const causes = buildCauses(local, backend);
    const { rows: measures } = buildMeasures(local, backend, picked);
    const whatIfSummary = formatWhatIf(input.whatIfBackend, backend, measures);
    const analysisDate = local?.date ?? this.analysisDate;

    this.sheets.push({
      pageNumber: 0,
      totalPages: 0,
      parcelId: input.parcelId,
      headline,
      subline,
      cropLabel: cropInfo.label,
      soilLabel,
      analysisDate,
      analysisDateLabel: analysisDate ? formatLong(analysisDate) : "—",
      silhouette,
      nonAgriMessage: input.nonAgriMessage ?? null,
      stage,
      stageName,
      risk: local?.risk ?? null,
      confidence: local?.confidence ?? null,
      caption: local?.caption ?? null,
      kpis: [
        { label: "Opad 30 dni", value: mm(local?.metrics.p30), unit: "mm" },
        { label: "Parowanie ET₀ 30 dni", value: mm(local?.metrics.et030), unit: "mm" },
        { label: "KBW 60 dni", value: mm(local?.kbw60), unit: "mm" },
      ],
      componentsSummary: buildComponentsSummary(backend),
      season: local?.season ?? null,
      dayMonthLabel: analysisDate ? formatDayMonth(analysisDate) : "",
      kbwWindows: local?.windows ?? [],
      activeWindow: local?.active ?? null,
      years: local?.years ?? [],
      timeline: local?.timeline ?? [],
      causesMethod: causes.method,
      causes: causes.rows,
      measures,
      whatIfSummary,
      irrigation: buildIrrigation(local, input.rl, input.rlCompare),
      sources: buildSources(input.sources, backend, input.rl, input.rlCompare),
    });
    return this;
  }

  build(): ReportDocument {
    const total = this.sheets.length;
    return {
      title: this.title,
      analysisDate: this.analysisDate,
      generatedAt: this.generatedAt,
      sheets: this.sheets.map((s, i) => ({ ...s, pageNumber: i + 1, totalPages: total })),
    };
  }
}
