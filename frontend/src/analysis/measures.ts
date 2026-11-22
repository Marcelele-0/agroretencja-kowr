import type { RetentionRecommendation, WhatIfScenarioResult } from "../api/contract";
import { WHATIF_SEASON_DAYS, type Levers } from "./local";

/** Same cap as the server (hydro_core.retention.MAX_DELTA_TAW_MM): the root zone cannot hold more than +40 mm. */
export const MAX_ROOT_ZONE_GAIN_MM = 40;
const MINUS = "−";

/** "+12", "−3,5", "0": Polish decimal comma and a real minus sign; a value that rounds to zero has no sign. */
export function signed(v: number, digits = 0): string {
  const r = Number(v.toFixed(digits));
  if (r === 0) return "0";
  const abs = Math.abs(r).toLocaleString("pl-PL", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  return `${r > 0 ? "+" : MINUS}${abs}`;
}
/** "+12 mm", "−3 mm", "0 mm". */
export function signedUnit(v: number, unit: string, digits = 0): string {
  return `${signed(v, digits)} ${unit}`;
}
/** Unsigned amount with the minus sign of negatives: "54,6", "−230". */
export function plain(v: number, digits = 0): string {
  const r = Number(v.toFixed(digits));
  return `${r < 0 ? MINUS : ""}${Math.abs(r).toLocaleString("pl-PL", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
}

/** Direction of a water-state change: closer to the optimum (0) is better, on the dry and on the wet side. */
export function stageTrend(before: number, after: number): "better" | "worse" | "same" {
  const b = Math.abs(before), a = Math.abs(after);
  return a < b ? "better" : a > b ? "worse" : "same";
}

/** One scannable result of a measure: a label and its signed value. */
export interface EffectChip { label: string; value: string }

type RecEffect = Pick<RetentionRecommendation, "delta_taw_mm" | "retained_runoff_mm" | "effect_yield_pp" | "effect_reserve_days">;
/**
 * Results of one server measure, each with the sign of its meaning: water kept in the soil "+",
 * less runoff "spływ −X mm", less yield loss "−X p.p.", fewer stress days "−N". Zero effects are left out.
 */
export function backendChips(r: RecEffect): EffectChip[] {
  const out: EffectChip[] = [];
  if (Math.abs(r.delta_taw_mm) >= 0.5) out.push({ label: "woda w glebie", value: signedUnit(r.delta_taw_mm, "mm") });
  if ((r.retained_runoff_mm ?? 0) >= 0.5) out.push({ label: "spływ", value: signedUnit(-r.retained_runoff_mm!, "mm") });
  if ((r.effect_yield_pp ?? 0) >= 0.05) out.push({ label: "strata plonu", value: `${signed(-r.effect_yield_pp!, 1)} p.p.` });
  if (r.effect_reserve_days) out.push({ label: "dni stresu", value: signed(-r.effect_reserve_days) });
  return out;
}

/** The measure text without the simulated-effect sentence of the server, which the chips already show. */
export function measureText(rationale: string): string {
  const i = rationale.indexOf(" W najsuchszym sezonie");
  return (i >= 0 ? rationale.slice(0, i) : rationale).trim();
}

type RecLever = Pick<RetentionRecommendation, "id" | "delta_taw_mm" | "retained_runoff_mm" | "runoff_reduction_pct" | "pool">;
/** Season runoff of the parcel without measures [mm], recovered from a measure that retains part of it. */
export function parcelRunoffMm(all: RecLever[]): number | null {
  const r = all.find((x) => x.runoff_reduction_pct > 0 && (x.retained_runoff_mm ?? 0) > 0);
  return r ? r.retained_runoff_mm! / (r.runoff_reduction_pct / 100) : null;
}
/**
 * Levers of server measures picked together, combined like the server: one pool (one water reserve)
 * counts its strongest measure, the root-zone gain is capped at 40 mm, and the retained runoff never
 * exceeds the parcel's runoff. The runoff of the reference season is spread per 60 days like `addMm`.
 * The server models less evaporation as a bigger root-zone store, so there is no evaporation cut.
 */
export function backendLevers(picked: RecLever[], all: RecLever[] = picked): Levers {
  const pools = new Map<string, number>();
  const seen = new Set<string>();
  let runoff = 0;
  for (const r of picked) {
    if (seen.has(r.id)) continue;
    seen.add(r.id);
    const k = r.pool || r.id;
    pools.set(k, pools.has(k) ? Math.max(pools.get(k)!, r.delta_taw_mm) : r.delta_taw_mm);
    runoff += Math.max(0, r.retained_runoff_mm ?? 0);
  }
  const taw = Math.min(MAX_ROOT_ZONE_GAIN_MM, [...pools.values()].reduce((a, b) => a + b, 0));
  const cap = parcelRunoffMm(all);
  const kept = cap === null ? runoff : Math.min(cap, runoff);
  return { addMm: Math.round(taw + (kept * 60) / WHATIF_SEASON_DAYS), etCut: 0 };
}

/** Picked ids the list can simulate, in list order: stale picks of another list (crop, date, data source) never count. */
export function activePicks<T extends { id: string; selectable: boolean }>(items: T[], picked: ReadonlySet<string>): T[] {
  return items.filter((m) => m.selectable && picked.has(m.id));
}

/** "1 wybrane działanie", "3 wybrane działania", "5 wybranych działań". */
export function pickedLabel(n: number): string {
  const few = n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 12 || n % 100 > 14);
  return n === 1 ? "1 wybrane działanie" : few ? `${n} wybrane działania` : `${n} wybranych działań`;
}

export interface SummaryTile { key: string; label: string; value: string }
export interface CompareRow { key: string; label: string; now: string; after: string }
/**
 * The server what-if as scannable results (signed changes) and a table of true values without and
 * with the measures. "Teraz" is the baseline season with no measures.
 */
export function whatIfSummary(w: WhatIfScenarioResult): { tiles: SummaryTile[]; rows: CompareRow[] } {
  const tiles: SummaryTile[] = [];
  const rows: CompareRow[] = [];
  if (Math.abs(w.saved_water_mm) >= 0.5) tiles.push({ key: "water", label: "Woda dla roślin", value: signedUnit(w.saved_water_mm, "mm") });
  const dLoss = w.scenario_yield_loss_pct - w.baseline_yield_loss_pct;
  tiles.push({ key: "loss", label: "Strata plonu", value: `${signed(dLoss, 1)} p.p.` });
  rows.push({ key: "loss", label: `Strata plonu (${w.reference_dry_year})`, now: `${plain(w.baseline_yield_loss_pct, 1)}%`, after: `${plain(w.scenario_yield_loss_pct, 1)}%` });
  // The stress-day change is the server's reserve days with the opposite sign; the table keeps both seasons.
  if (w.delta_reserve_days) tiles.push({ key: "stress", label: "Dni stresu", value: signed(-w.delta_reserve_days) });
  if (w.baseline_stress_days !== undefined && w.scenario_stress_days !== undefined) {
    rows.push({ key: "stress", label: "Dni stresu w sezonie", now: plain(w.baseline_stress_days), after: plain(w.scenario_stress_days) });
  }
  const kept = w.retained_runoff_mm ?? 0;
  if (kept >= 0.5) tiles.push({ key: "runoff", label: "Spływ z działki", value: signedUnit(-kept, "mm") });
  if (w.baseline_runoff_mm !== undefined && w.scenario_runoff_mm !== undefined && w.baseline_runoff_mm >= 0.5) {
    rows.push({ key: "runoff", label: "Spływ z działki w sezonie", now: `${plain(w.baseline_runoff_mm)} mm`, after: `${plain(w.scenario_runoff_mm)} mm` });
  }
  return { tiles, rows };
}
