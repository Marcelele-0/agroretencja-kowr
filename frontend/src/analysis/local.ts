import { addDays, formatDayMonth, inSeason, isoOf, lastDayOfMonth, parts, seasonYear, type Iso } from "../lib/dates";
import { CROP_GROUPS, cropOf, type Crop, type CropCode } from "../lib/crops";
import { CAT_WOD, IUNG_PROGI, thresholdsFor, type LandUse, type SoilCategory, type Threshold } from "../lib/iung";
import { activeWindow, gradingThreshold, kbw60, riskFromStage, sameTermByYear, seasonWindows, stageAt, stageFromKbw, stageRule, sumOf, type KbwWindow, type RiskLevel } from "../lib/kbw";
import { compareYears, type DailySeries } from "../lib/openMeteo";

export interface Forecast { days: { date: Iso; p: number }[]; sum5: number; sum7: number; firstRain: { date: Iso; p: number } | null }
export interface IrrigationHint { title: string; text: string; tone: "wait" | "go" | "no" | "watch" }
export interface CauseLine { key: "klimat" | "gleba"; label: string; text: string }
export interface TimelineDay { date: Iso; value: number | null; forecast: boolean }
export interface Explanation { from: Iso; to: Iso; p60: number; et060: number; k60: number; threshold: number | null; reference: boolean; window: string | null; rule: string | null; pMean: number | null; etMean: number | null }

export interface LocalResult {
  date: Iso;
  crop: Crop;
  season: number;
  offSeason: boolean;
  category: SoilCategory | null;
  thresholds: Threshold[] | null;
  windows: KbwWindow[];
  active: number | null;
  droughtCount: number;
  knownCount: number;
  kbw60: number | null;
  grading: { value: number; reference: boolean } | null;
  stage: number | null;
  risk: { level: RiskLevel; side: "dry" | "wet" | undefined; veto: boolean; vetoReason: string | null };
  confidence: { level: "wysoka" | "srednia" | "ograniczona"; detail: string };
  caption: string;
  metrics: { p30: number | null; et030: number | null };
  years: { year: number; date: Iso; value: number | null }[];
  yearsMonth: { year: number; date: Iso; value: number | null }[];
  causes: CauseLine[];
  explanation: Explanation | null;
  /** Daily water state from 45 days before the date to 15 days after it (forecast after today). */
  timeline: TimelineDay[];
  forecast: Forecast | null;
  irrigation: IrrigationHint | null;
}

const mm = (v: number) => `${Math.round(v)}`.replace("-", "−");
const mean = (xs: number[]) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null);

/** Water-retention levers of the what-if simulation: extra water kept in the soil over 60 days, and a cut of evaporation. */
export interface Levers { addMm: number; etCut: number }
/** Applies the levers to a copy of the series: +addMm spread evenly over each 60 days, ET0 × (1 − etCut). */
export function withLevers(s: DailySeries, l: Levers): DailySeries {
  if (!l.addMm && !l.etCut) return s;
  const perDay = l.addMm / 60;
  return { ...s, p: s.p.map((v) => (v === null ? null : v + perDay)), et0: s.et0.map((v) => (v === null ? null : v * (1 - l.etCut))) };
}

/** Largest evaporation cut of the what-if, the end of its slider. */
export const ET_CUT_MAX = 0.3;
/**
 * Levers of measures picked together (catalog assumptions): the water kept adds up; evaporation cuts act on one
 * flux, so each cuts a share of what is left (10% and 5% give 14.5%, not 15%), capped at the slider's 30%.
 */
export function leversOf(sims: ({ addMm?: number; etCut?: number } | undefined)[]): Levers {
  const addMm = sims.reduce((a, m) => a + (m?.addMm ?? 0), 0);
  const left = sims.reduce((a, m) => a * (1 - Math.min(1, Math.max(0, m?.etCut ?? 0))), 1);
  return { addMm, etCut: Math.min(ET_CUT_MAX, Math.round((1 - left) * 1000) / 1000) };
}
/** Days of the backend reference season used to spread its water over the year: 1 March to 30 September (IUNG season end). */
export const WHATIF_SEASON_DAYS = 214;
/**
 * Levers from the backend what-if: the water the measures keep in the reference season (mm), spread evenly over the season
 * and expressed per 60 days like `addMm`. The backend water already includes less evaporation and less runoff
 * (bigger root-zone store, lower curve number), so the local evaporation cut is not added on top.
 */
export function leversFromBackendWater(seasonMm: number): Levers {
  const addMm = Math.max(0, seasonMm) * 60 / WHATIF_SEASON_DAYS;
  return { addMm: Math.round(addMm * 10) / 10, etCut: 0 };
}
/** Water state before and after the levers, both from KBW 60 graded against the crop's IUNG threshold. */
export function stageShift(land: LandUse, s: DailySeries, cropCode: CropCode, date: Iso, l: Levers): { before: number | null; after: number | null; kbwGainMm: number | null } {
  const category = land.kind === "mineral" ? land.category : null;
  const thresholds = thresholdsFor(cropOf(cropCode).iung, category);
  const k0 = kbw60(s, date), k1 = kbw60(withLevers(s, l), date);
  return {
    before: gradeKbw(k0, thresholds, date, category).stage,
    after: gradeKbw(k1, thresholds, date, category).stage,
    kbwGainMm: k0 === null || k1 === null ? null : k1 - k0,
  };
}

/** Grades KBW 60 against a crop's IUNG threshold: the window of the date, or the last window of the season off season. */
export function gradeKbw(k60: number | null, thresholds: Threshold[] | null, date: Iso, category: SoilCategory | null) {
  const gIdx = inSeason(date) ? activeWindow(date) ?? 0 : 13;
  const grading = gradingThreshold(thresholds, gIdx, category);
  const stage = k60 === null || grading === null ? null : stageFromKbw(k60, grading.value);
  return { gIdx, grading, stage };
}

export interface CropStage { crop: Crop; group: string; stage: number | null }
/**
 * Water state of the same parcel, soil and date for every crop with its own IUNG thresholds on this soil category.
 * Uses the grading of computeLocal; sorted from driest to wettest, crop-group order within one state.
 */
export function cropComparison(category: SoilCategory | null, s: DailySeries, date: Iso): CropStage[] {
  if (!category) return [];
  const k60 = kbw60(s, date);
  const rows = CROP_GROUPS.flatMap((g) => g.crops.map((crop) => ({ crop, group: g.label }))).flatMap(({ crop, group }) => {
    const th = thresholdsFor(crop.iung, category);
    if (!th) return [];
    const { grading, stage } = gradeKbw(k60, th, date, category);
    // A crop with no numeric threshold on this soil falls back to the spring-cereal reference: it has no own IUNG threshold.
    return grading && !grading.reference ? [{ crop, group, stage }] : [];
  });
  return rows.sort((a, b) => (a.stage ?? 99) - (b.stage ?? 99));
}

export function computeLocal(land: LandUse, s: DailySeries, cropCode: CropCode, date: Iso, today: Iso): LocalResult {
  const crop = cropOf(cropCode);
  const category = land.kind === "mineral" ? land.category : null;
  const thresholds = thresholdsFor(crop.iung, category);
  const season = seasonYear(date);
  const offSeason = !inSeason(date);
  // In season the chart stops at the analysis date: it shows the state "as of" that day, without hindsight.
  const windows = seasonWindows(s, season, thresholds, offSeason || date > today ? today : date);
  const active = offSeason ? null : activeWindow(date);
  const known = windows.filter((w) => w.kbw !== null && w.threshold !== null);
  const k60 = kbw60(s, date);
  const { gIdx, grading, stage } = gradeKbw(k60, thresholds, date, category);
  const r = stage === null ? { level: "umiarkowane" as RiskLevel, side: undefined } : riskFromStage(stage);
  const aw = active !== null ? windows[active] : undefined;
  const veto = !!aw?.drought;
  const vetoReason = veto && aw && aw.kbw !== null && aw.threshold !== null ? `KBW okna ${aw.label}: ${mm(aw.kbw)} mm, próg IUNG ${mm(aw.threshold)} mm.` : null;

  const detail: string[] = ["Opad i parowanie z siatki pogodowej ok. 9 km, bez bilansu wody w glebie."];
  if (!category) detail.push(land.kind === "organic" ? "Gleba organiczna: progi IUNG są dla gleb mineralnych." : "Brak kategorii gleby IUNG w tym punkcie.");
  if (grading?.reference) detail.push("Próg odniesienia: zboża jare.");
  if (!s.forecastOk) detail.push("Prognoza niedostępna: ostatnie dni mogą być niepełne.");
  const confidence = { level: (category && thresholds && !grading?.reference ? "srednia" : "ograniczona") as LocalResult["confidence"]["level"], detail: detail.join(" ") };

  const thLabel = grading ? `${grading.reference ? "progu odniesienia" : "progu IUNG"} ${mm(grading.value)} mm` : null;
  const caption = k60 === null ? "Brak pełnych danych pogodowych dla 60 dni przed tą datą."
    : `KBW z 60 dni: ${mm(k60)} mm${thLabel ? ` przy ${thLabel}` : ""} (${(grading?.reference ? "zboża jare" : crop.label).toLowerCase()}${category ? `, gleba kat. ${category}` : ""}).`;

  const years = compareYears(date, today);
  const sameDay = sameTermByYear(s, date, years, "day", thresholds, category, today);
  const sameMonth = sameTermByYear(s, date, years, "month", thresholds, category, today);

  // Climate of the same 60 days in the other years: the reference for "is it drier than usual".
  const from60 = addDays(date, -59);
  const p60 = sumOf(s.p, s, from60, date), e60 = sumOf(s.et0, s, from60, date);
  const [y, m, d] = parts(date);
  const others = years.filter((yy) => yy !== y).map((yy) => isoOf(yy, m, Math.min(d, lastDayOfMonth(yy, m)))).filter((end) => end <= today);
  const pMean = mean(others.map((end) => sumOf(s.p, s, addDays(end, -59), end)).filter((v): v is number => v !== null));
  const etMean = mean(others.map((end) => sumOf(s.et0, s, addDays(end, -59), end)).filter((v): v is number => v !== null));

  const causes: CauseLine[] = [];
  if (p60 !== null && e60 !== null) {
    causes.push({ key: "klimat", label: "Klimat", text: `Opad z 60 dni: ${mm(p60)} mm${pMean !== null ? `, średnio w tym okresie ${mm(pMean)} mm` : ""}. Parowanie: ${mm(e60)} mm${etMean !== null ? `, średnio ${mm(etMean)} mm` : ""}.` });
  }
  causes.push(soilCause(land));

  const explanation: Explanation | null = k60 === null || p60 === null || e60 === null ? null : {
    from: from60, to: date, p60, et060: e60, k60, threshold: grading?.value ?? null, reference: !!grading?.reference,
    window: windows[gIdx]?.label ?? null, rule: grading ? stageRule(k60, grading.value) : null, pMean, etMean,
  };

  let forecast: Forecast | null = null;
  let irrigation: IrrigationHint | null = null;
  const live = date >= addDays(today, -2) && date <= today && s.forecastOk;
  if (live) {
    const days = Array.from({ length: 7 }, (_, i) => addDays(today, i + 1)).map((dd) => ({ date: dd, p: sumOf(s.p, s, dd, dd) ?? 0 }));
    const sum5 = days.slice(0, 5).reduce((a, x) => a + x.p, 0), sum7 = days.reduce((a, x) => a + x.p, 0);
    const firstRain = days.find((x) => x.p >= 5) ?? null;
    forecast = { days, sum5, sum7, firstRain };
    if (!offSeason && stage !== null) {
      const when = firstRain ? ` (najwięcej ${formatDayMonth(firstRain.date)})` : "";
      if (stage >= 0) irrigation = { tone: "no", title: "Nie nawadniaj", text: `Woda w glebie wystarcza. Prognoza na 7 dni: ${mm(sum7)} mm opadu.` };
      else if (sum5 >= 15) irrigation = { tone: "wait", title: "Poczekaj z nawadnianiem", text: `Prognoza: ${mm(sum5)} mm opadu w ciągu 5 dni${when}. Sprawdź stan wody po deszczu.` };
      else if (stage === -1) irrigation = { tone: "watch", title: "Obserwuj", text: `Lekki niedobór. Prognoza na 7 dni: ${mm(sum7)} mm opadu. Sprawdź ponownie za 3 dni.` };
      else irrigation = { tone: "go", title: "Nawadnianie ma sens", text: `Prognoza na 7 dni: tylko ${mm(sum7)} mm opadu. Jeśli masz źródło wody, nawadniaj.` };
    }
  }

  const lastDay = addDays(today, 15);
  const timeline: TimelineDay[] = Array.from({ length: 61 }, (_, i) => addDays(date, i - 45)).filter((dd) => dd <= lastDay)
    .map((dd) => ({ date: dd, value: stageAt(s, dd, thresholds, category), forecast: dd > today }));

  return {
    date, crop, season, offSeason, timeline, category, thresholds, windows, active,
    droughtCount: known.filter((w) => w.drought).length, knownCount: known.length,
    kbw60: k60 === null ? null : Math.round(k60), grading, stage,
    risk: { ...r, veto, vetoReason }, confidence, caption,
    metrics: { p30: sumOf(s.p, s, addDays(date, -29), date), et030: sumOf(s.et0, s, addDays(date, -29), date) },
    years: sameDay, yearsMonth: sameMonth, causes, explanation, forecast, irrigation,
  };
}

export function soilCause(land: LandUse): CauseLine {
  if (land.kind === "mineral") return { key: "gleba", label: "Gleba", text: `Kategoria IUNG ${land.category}: ${land.label.replace(/^kat\. \w+: /, "")}, woda dostępna dla roślin ${CAT_WOD[land.category]}.` };
  return { key: "gleba", label: "Gleba", text: `${land.label}.` };
}

/** Longest run of drought windows, as "21.V–20.VII". */
export function longestDrought(w: KbwWindow[]): string | null {
  let best: [number, number] | null = null, start = -1;
  w.forEach((x, i) => {
    if (x.drought) { if (start < 0) start = i; if (!best || i - start > best[1] - best[0]) best = [start, i]; }
    else start = -1;
  });
  if (!best) return null;
  const a = w[best[0]]!.label.split("–")[0], b = w[best[1]]!.label.split("–")[1];
  return `${a}–${b}`;
}
export const IUNG_RETRIEVED = IUNG_PROGI.retrieved;
