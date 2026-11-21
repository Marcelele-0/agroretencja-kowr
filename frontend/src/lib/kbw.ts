import { addDays, isoOf, lastDayOfMonth, parts, romanDate, seasonYear, inSeason, type Iso } from "./dates";
import { thresholdsFor, type SoilCategory, type Threshold } from "./iung";
import { dayIndex, type DailySeries } from "./openMeteo";

/** The 14 sześciodekadowe windows of the IUNG-PIB drought monitor (SMSR), as [month, day] pairs. */
export const WINDOW_STARTS: [number, number][] = [[3, 21], [4, 1], [4, 11], [4, 21], [5, 1], [5, 11], [5, 21], [6, 1], [6, 11], [6, 21], [7, 1], [7, 11], [7, 21], [8, 1]];
export const WINDOW_ENDS: [number, number][] = [[5, 20], [5, 31], [6, 10], [6, 20], [6, 30], [7, 10], [7, 20], [7, 31], [8, 10], [8, 20], [8, 31], [9, 10], [9, 20], [9, 30]];

/** Sum of a daily variable over [from, to] inclusive. null if the range leaves the series or more than 2 days are missing. */
export function sumOf(values: (number | null)[], s: DailySeries, from: Iso, to: Iso): number | null {
  const a = dayIndex(s, from), b = dayIndex(s, to);
  if (a < 0 || b >= values.length || b < a) return null;
  let sum = 0, missing = 0;
  for (let i = a; i <= b; i++) { const v = values[i]; if (v === null || v === undefined) missing++; else sum += v; }
  return missing > 2 ? null : sum;
}
/** Climatic water balance KBW = Σ(P − ET0) over [from, to]. */
export function kbw(s: DailySeries, from: Iso, to: Iso): number | null {
  const p = sumOf(s.p, s, from, to), e = sumOf(s.et0, s, from, to);
  return p === null || e === null ? null : p - e;
}
export function kbw60(s: DailySeries, at: Iso): number | null {
  return kbw(s, addDays(at, -59), at);
}

export interface KbwWindow {
  index: number;
  start: Iso;
  end: Iso;
  label: string;
  kbw: number | null;
  threshold: number | null;
  /** "-" in the IUNG table: the crop does not grow on this soil category. */
  notGrown: boolean;
  drought: boolean;
}

export function windowsFor(year: number): { start: Iso; end: Iso; label: string }[] {
  return WINDOW_STARTS.map((st, i) => {
    const en = WINDOW_ENDS[i]!;
    return { start: isoOf(year, st[0], st[1]), end: isoOf(year, en[0], en[1]), label: `${romanDate(st[0], st[1])}–${romanDate(en[0], en[1])}` };
  });
}

/** KBW in the 14 windows of a season. Windows that end after `upTo` (today) have no value. */
export function seasonWindows(s: DailySeries, year: number, thresholds: Threshold[] | null, upTo: Iso): KbwWindow[] {
  return windowsFor(year).map((w, i) => {
    const t = thresholds?.[i];
    const k = w.end <= upTo ? kbw(s, w.start, w.end) : null;
    const threshold = typeof t === "number" ? t : null;
    return { index: i, ...w, kbw: k === null ? null : Math.round(k), threshold, notGrown: t === "-", drought: k !== null && threshold !== null && k < threshold };
  });
}

/** Progressive KBW across all 14 IUNG windows as of `at`: completed windows lock at `w.end`, active windows accumulate from `w.start` to `at`. */
export function progressiveSeasonWindows(s: DailySeries, year: number, thresholds: Threshold[] | null, at: Iso): KbwWindow[] {
  return windowsFor(year).map((w, i) => {
    const t = thresholds?.[i];
    const threshold = typeof t === "number" ? t : null;
    if (at < w.start) {
      return { index: i, ...w, kbw: null, threshold, notGrown: t === "-", drought: false };
    }
    const end = at < w.end ? at : w.end;
    const k = kbw(s, w.start, end);
    return { index: i, ...w, kbw: k === null ? null : Math.round(k), threshold, notGrown: t === "-", drought: k !== null && threshold !== null && k < threshold };
  });
}

/** Index of the last window that ends on or before `at` in the season of `at`. */
export function activeWindow(at: Iso): number | null {
  const y = seasonYear(at);
  const ends = windowsFor(y).map((w) => w.end);
  let idx: number | null = null;
  ends.forEach((e, i) => { if (e <= at) idx = i; });
  return idx;
}

/** Threshold used to grade the state at window `idx`: the crop's own value, else the nearest window of the same crop, else spring cereals (reference). */
export function gradingThreshold(thresholds: Threshold[] | null, idx: number, cat: SoilCategory | null): { value: number; reference: boolean } | null {
  const own = thresholds?.[idx];
  if (typeof own === "number") return { value: own, reference: false };
  if (thresholds) {
    for (let d = 1; d < 14; d++) for (const j of [idx - d, idx + d]) { const v = thresholds[j]; if (typeof v === "number") return { value: v, reference: false }; }
  }
  const ref = thresholdsFor("Zboża jare", cat ?? "II");
  const v = ref?.[Math.max(0, Math.min(13, idx))];
  return typeof v === "number" ? { value: v, reference: true } : null;
}

/**
 * Water state −5…+5 from KBW of 60 days and the IUNG threshold (negative mm).
 * Provisional: the backend replaces it with the FAO-56 root-zone balance (Ks), see the WaterBalance README.
 */
export function stageFromKbw(k: number, th: number): number {
  if (k < th - 60) return -5;
  if (k < th) return -4;
  if (k < th + 25) return -3;
  if (k < th * 0.6) return -2;
  if (k < th * 0.3) return -1;
  if (k <= 40) return 0;
  if (k <= 80) return 1;
  if (k <= 130) return 2;
  if (k <= 200) return 3;
  if (k <= 280) return 4;
  return 5;
}

/** The rule of stageFromKbw that produced a stage, in words (for the explanation panel). */
export function stageRule(k: number, th: number): string {
  const t = Math.round(th);
  if (k < th - 60) return `KBW jest niższy od progu ${t} mm o ponad 60 mm`;
  if (k < th) return `KBW jest poniżej progu IUNG ${t} mm`;
  if (k < th + 25) return `KBW jest mniej niż 25 mm nad progiem ${t} mm`;
  if (k < th * 0.6) return `KBW jest poniżej 60% progu (${Math.round(th * 0.6)} mm)`;
  if (k < th * 0.3) return `KBW jest poniżej 30% progu (${Math.round(th * 0.3)} mm)`;
  if (k <= 40) return "KBW mieści się między 30% progu a +40 mm";
  if (k <= 80) return "KBW wynosi od +40 do +80 mm";
  if (k <= 130) return "KBW wynosi od +80 do +130 mm";
  if (k <= 200) return "KBW wynosi od +130 do +200 mm";
  if (k <= 280) return "KBW wynosi od +200 do +280 mm";
  return "KBW przekracza +280 mm";
}

export type RiskLevel = "niskie" | "umiarkowane" | "wysokie" | "krytyczne";
export function riskFromStage(stage: number): { level: RiskLevel; side: "dry" | "wet" | undefined } {
  const a = Math.abs(stage);
  const level: RiskLevel = a <= 1 ? "niskie" : a === 2 ? "umiarkowane" : a === 3 ? "wysokie" : "krytyczne";
  return { level, side: stage < 0 ? "dry" : stage > 0 ? "wet" : undefined };
}

/** Window index for grading a date: the window ending on that date, else the active one, else the last of the season. */
function gradingIndex(at: Iso): number {
  if (!inSeason(at)) return 13;
  return activeWindow(at) ?? 0;
}

export function stageAt(s: DailySeries, at: Iso, thresholds: Threshold[] | null, cat: SoilCategory | null): number | null {
  const k = kbw60(s, at);
  const th = gradingThreshold(thresholds, gradingIndex(at), cat);
  return k === null || th === null ? null : stageFromKbw(k, th.value);
}

/** Same calendar day (or the last day of the same month) in each of `years`. Dates after `upTo` stay empty. */
export function sameTermByYear(s: DailySeries, at: Iso, years: number[], mode: "day" | "month", thresholds: Threshold[] | null, cat: SoilCategory | null, upTo: Iso) {
  const [, m, d] = parts(at);
  return years.map((y) => {
    const day = mode === "day" ? Math.min(d, lastDayOfMonth(y, m)) : lastDayOfMonth(y, m);
    const iso = isoOf(y, m, day);
    return { year: y, date: iso, value: iso > upTo ? null : stageAt(s, iso, thresholds, cat) };
  });
}
