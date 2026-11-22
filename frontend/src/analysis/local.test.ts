import { describe, expect, it } from "vitest";
import { computeLocal, cropComparison, ET_CUT_MAX, leversFromBackendWater, leversOf, stageShift, WHATIF_SEASON_DAYS } from "./local";
import { DRY_MEASURES } from "../lib/catalog";
import { toDay } from "../lib/dates";
import type { LandUse } from "../lib/iung";
import type { DailySeries } from "../lib/openMeteo";

/** Constant daily rain and ET0, so KBW of 60 days is 60 × (p − e). */
function series(from: string, days: number, p: number, e: number): DailySeries {
  return {
    start: from, startDay: toDay(from), p: Array(days).fill(p), et0: Array(days).fill(e),
    forecastFrom: "2099-01-01", forecastOk: true, grid: { lat: 50, lon: 19, elevation: null }, retrievedAt: new Date(0),
  };
}
const LAND: LandUse = { kind: "mineral", code: 3, category: "III", label: "kat. III" };
const TODAY = "2026-11-22";
// KBW of 60 days = −230 mm: below the maize threshold (−224 mm on soil III), far above the potato one (−267 mm).
const DRY = series("2019-01-01", 400, 0, 230 / 60);

describe("cropComparison", () => {
  it("grades every crop with IUNG thresholds like computeLocal, driest first", () => {
    const rows = cropComparison("III", DRY, "2019-07-15");
    expect(rows.length).toBe(14);
    expect(rows.some((r) => r.crop.code === "uzytki_zielone")).toBe(false);
    for (const r of rows) expect(r.stage).toBe(computeLocal(LAND, DRY, r.crop.code, "2019-07-15", TODAY).stage);
    const stages = rows.map((r) => r.stage ?? 99);
    expect([...stages].sort((a, b) => a - b)).toEqual(stages);
    expect(rows.find((r) => r.crop.code === "kukurydza_ziarno")!.stage).toBe(-4);
    expect(rows.find((r) => r.crop.code === "ziemniak")!.stage).toBe(-2);
  });

  it("uses the end-of-season thresholds off season, like the stage", () => {
    const rows = cropComparison("III", DRY, "2019-11-15");
    for (const r of rows) expect(r.stage).toBe(computeLocal(LAND, DRY, r.crop.code, "2019-11-15", TODAY).stage);
  });

  it("is empty without a mineral soil category", () => {
    expect(cropComparison(null, DRY, "2019-07-15")).toEqual([]);
  });
});

describe("measures → water state", () => {
  const sim = (id: string) => DRY_MEASURES.find((m) => m.id === id)!.sim;

  it("adds the water kept, cuts one evaporation flux once, and caps the cut at the slider's 30%", () => {
    expect(leversOf([sim("zastawka"), sim("pas_buforowy")])).toEqual({ addMm: 35, etCut: 0 });
    // −10% then −5% of what is left: 14.5%, not 15%
    expect(leversOf([sim("miedzyplon_mulcz"), sim("uprawa_bezorkowa")]).etCut).toBe(0.145);
    expect(leversOf([sim("miedzyplon_mulcz")])).toEqual({ addMm: 0, etCut: 0.1 });
    expect(leversOf([{ etCut: 0.4 }, { etCut: 0.3 }, undefined]).etCut).toBe(ET_CUT_MAX);
    expect(leversOf([])).toEqual({ addMm: 0, etCut: 0 });
  });

  it("spreads the backend season water evenly over the season, per 60 days", () => {
    expect(leversFromBackendWater(WHATIF_SEASON_DAYS)).toEqual({ addMm: 60, etCut: 0 });
    expect(leversFromBackendWater(-5)).toEqual({ addMm: 0, etCut: 0 });
  });

  it("moves the state from the same base as computeLocal", () => {
    const s = stageShift(LAND, DRY, "kukurydza_ziarno", "2019-07-15", leversOf([sim("zastawka"), sim("pas_buforowy")]));
    expect(s.before).toBe(computeLocal(LAND, DRY, "kukurydza_ziarno", "2019-07-15", TODAY).stage);
    expect(s.before).toBe(-4);
    expect(s.after).toBe(-2);
    expect(s.kbwGainMm).toBeCloseTo(35);
  });

  it("keeps the state when the lever is too small, but reports the water gain", () => {
    const s = stageShift(LAND, DRY, "kukurydza_ziarno", "2019-07-15", { addMm: 2, etCut: 0 });
    expect(s.after).toBe(s.before);
    expect(s.kbwGainMm).toBeCloseTo(2);
  });
});
