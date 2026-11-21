import { addDays, formatDayMonth, inSeason, isoOf, lastDayOfMonth, parts, seasonYear, type Iso } from "../lib/dates";
import { cropOf, type Crop, type CropCode } from "../lib/crops";
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
  const gIdx = offSeason ? 13 : active ?? 0;
  const grading = gradingThreshold(thresholds, gIdx, category);
  const stage = k60 === null || grading === null ? null : stageFromKbw(k60, grading.value);
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
