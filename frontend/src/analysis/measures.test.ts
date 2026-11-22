import { describe, expect, it } from "vitest";
import type { RetentionRecommendation, WhatIfScenarioResult } from "../api/contract";
import { measuresForStage, WET_MEASURES, DRY_MEASURES } from "../lib/catalog";
import { WHATIF_SEASON_DAYS } from "./local";
import { activePicks, backendChips, backendLevers, measureText, parcelRunoffMm, pickedLabel, plain, signed, signedUnit, stageTrend, whatIfSummary } from "./measures";

const MINUS = "−";
const rec = (id: string, over: Partial<RetentionRecommendation> = {}): RetentionRecommendation => ({
  id, category: "agrotechnika", title: id, rationale: "", delta_taw_mm: 0, runoff_reduction_pct: 0, priority_rank: 1, ...over,
});

describe("signed numbers", () => {
  it("uses a real minus sign, a decimal comma and no sign on zero", () => {
    expect(signed(12)).toBe("+12");
    expect(signed(-3.46, 1)).toBe(`${MINUS}3,5`);
    expect(signed(0)).toBe("0");
    expect(signed(-0.4)).toBe("0");
    expect(signedUnit(-12, "mm")).toBe(`${MINUS}12 mm`);
    expect(plain(54.62, 1)).toBe("54,6");
    expect(plain(-230)).toBe(`${MINUS}230`);
    expect(signed(-5)).not.toContain("-");
  });
});

describe("stageTrend", () => {
  it("calls closer to the optimum better, on both sides", () => {
    expect(stageTrend(-4, -2)).toBe("better");
    expect(stageTrend(2, 3)).toBe("worse");
    expect(stageTrend(3, 1)).toBe("better");
    expect(stageTrend(-1, 1)).toBe("same");
    expect(stageTrend(0, 0)).toBe("same");
  });
});

describe("backendChips", () => {
  it("gives every result the sign of its meaning", () => {
    const chips = backendChips(rec("pas", { delta_taw_mm: 20, retained_runoff_mm: 12.4, effect_yield_pp: 1.94, effect_reserve_days: 3 }));
    expect(chips).toEqual([
      { label: "woda w glebie", value: "+20 mm" },
      { label: "spływ", value: `${MINUS}12 mm` },
      { label: "strata plonu", value: `${MINUS}1,9 p.p.` },
      { label: "dni stresu", value: `${MINUS}3` },
    ]);
  });

  it("shows water taken out as minus and leaves zero effects out", () => {
    expect(backendChips(rec("drain", { delta_taw_mm: -15 }))).toEqual([{ label: "woda w glebie", value: `${MINUS}15 mm` }]);
    expect(backendChips(rec("none", { delta_taw_mm: 0, retained_runoff_mm: 0.2, effect_yield_pp: 0, effect_reserve_days: 0 }))).toEqual([]);
    expect(backendChips(rec("more_stress", { effect_reserve_days: -2 }))).toEqual([{ label: "dni stresu", value: "+2" }]);
  });

  it("drops the server's effect sentence from the card text", () => {
    expect(measureText("Ogranicza parowanie. W najsuchszym sezonie 2015: strata plonu mniejsza o 1,5 p.p.")).toBe("Ogranicza parowanie.");
    expect(measureText("Bez symulacji.")).toBe("Bez symulacji.");
  });
});

describe("backendLevers", () => {
  const dam = rec("zastawka_na_rowie", { delta_taw_mm: 20, pool: "wody_gruntowe" });
  const drain = rec("sterowany_odplyw", { delta_taw_mm: 15, pool: "wody_gruntowe" });
  const mulch = rec("mulcz", { delta_taw_mm: 8 });
  const humus = rec("materia_organiczna", { delta_taw_mm: 10 });

  it("counts one groundwater table once: the stronger structure", () => {
    expect(backendLevers([dam, drain])).toEqual({ addMm: 20, etCut: 0 });
    expect(backendLevers([drain, dam])).toEqual({ addMm: 20, etCut: 0 });
  });

  it("adds different reserves up to the server cap of 40 mm, each measure once", () => {
    expect(backendLevers([mulch, humus])).toEqual({ addMm: 18, etCut: 0 });
    expect(backendLevers([mulch, mulch])).toEqual({ addMm: 8, etCut: 0 });
    // live parcel: mulch + dam + controlled drainage gave +43 mm before (summed, above the cap)
    expect(backendLevers([mulch, dam, drain])).toEqual({ addMm: 28, etCut: 0 });
    expect(backendLevers([mulch, humus, dam, rec("miedzyplon", { delta_taw_mm: 5 }), rec("uprawa", { delta_taw_mm: 6 })]).addMm).toBe(40);
  });

  it("spreads the retained runoff per 60 days and never past the parcel's runoff", () => {
    const buffer = rec("pas_buforowy", { retained_runoff_mm: 30, runoff_reduction_pct: 60 });
    const pond = rec("zbiornik_w_zaglebieniu", { delta_taw_mm: 0, retained_runoff_mm: 40, runoff_reduction_pct: 80 });
    expect(parcelRunoffMm([mulch, buffer])).toBeCloseTo(50);
    expect(backendLevers([buffer])).toEqual({ addMm: Math.round((30 * 60) / WHATIF_SEASON_DAYS), etCut: 0 });
    // 30 + 40 mm retained, but the parcel only has 50 mm of runoff
    expect(backendLevers([buffer, pond], [buffer, pond, mulch])).toEqual({ addMm: Math.round((50 * 60) / WHATIF_SEASON_DAYS), etCut: 0 });
  });

  it("is zero without picks", () => {
    expect(backendLevers([])).toEqual({ addMm: 0, etCut: 0 });
  });
});

describe("activePicks", () => {
  it("keeps only picks the current list can simulate, in list order", () => {
    const items = [{ id: "a", selectable: true }, { id: "b", selectable: false }, { id: "c", selectable: true }];
    // "b" is advice only, "x" is left over from another crop or the local list
    expect(activePicks(items, new Set(["c", "b", "x", "a"])).map((m) => m.id)).toEqual(["a", "c"]);
    expect(activePicks(items, new Set())).toEqual([]);
  });

  it("names the count in Polish", () => {
    expect(pickedLabel(1)).toBe("1 wybrane działanie");
    expect(pickedLabel(3)).toBe("3 wybrane działania");
    expect(pickedLabel(5)).toBe("5 wybranych działań");
    expect(pickedLabel(12)).toBe("12 wybranych działań");
    expect(pickedLabel(22)).toBe("22 wybrane działania");
  });
});

describe("whatIfSummary", () => {
  const w: WhatIfScenarioResult = {
    baseline_yield_loss_pct: 54.6, scenario_yield_loss_pct: 46.9, delta_reserve_days: 3, saved_water_mm: 35.2, reference_dry_year: 2015,
    baseline_stress_days: 101, scenario_stress_days: 98, retained_runoff_mm: 8.4, baseline_runoff_mm: 12.1, scenario_runoff_mm: 3.7, delta_taw_mm: 28,
  };

  it("shows signed changes and the true baseline in 'Teraz'", () => {
    const s = whatIfSummary(w);
    expect(s.tiles).toEqual([
      { key: "water", label: "Woda dla roślin", value: "+35 mm" },
      { key: "loss", label: "Strata plonu", value: `${MINUS}7,7 p.p.` },
      { key: "stress", label: "Dni stresu", value: `${MINUS}3` },
      { key: "runoff", label: "Spływ z działki", value: `${MINUS}8 mm` },
    ]);
    expect(s.rows).toEqual([
      { key: "loss", label: "Strata plonu (2015)", now: "54,6%", after: "46,9%" },
      { key: "stress", label: "Dni stresu w sezonie", now: "101", after: "98" },
      { key: "runoff", label: "Spływ z działki w sezonie", now: "12 mm", after: "4 mm" },
    ]);
  });

  it("never prints '+−' for a negative water change", () => {
    const s = whatIfSummary({ ...w, saved_water_mm: -3.2, retained_runoff_mm: 0, baseline_runoff_mm: 0, scenario_runoff_mm: 0 });
    const all = s.tiles.map((t) => t.value).join(" ");
    expect(s.tiles[0]).toEqual({ key: "water", label: "Woda dla roślin", value: `${MINUS}3 mm` });
    expect(all).not.toMatch(/\+[-−]/);
    expect(s.rows.some((r) => r.key === "runoff")).toBe(false);
  });

  it("stays consistent: the stress change equals the reserve days of the server and the table", () => {
    const s = whatIfSummary(w);
    expect(s.tiles.find((t) => t.key === "stress")!.value).toBe(signed(w.scenario_stress_days! - w.baseline_stress_days!));
    expect(s.tiles.find((t) => t.key === "stress")!.value).toBe(signed(-w.delta_reserve_days));
  });

  it("leaves out tiles with no change, but always shows the yield loss and the table rows", () => {
    const s = whatIfSummary({ ...w, delta_reserve_days: 0, scenario_stress_days: 101, saved_water_mm: 0.2, retained_runoff_mm: 0 });
    expect(s.tiles.map((t) => t.key)).toEqual(["loss"]);
    expect(s.rows.map((r) => r.key)).toEqual(["loss", "stress", "runoff"]);
  });
});

describe("measuresForStage", () => {
  it("switches to the excess-water list from 'nadmiar wody' (+2)", () => {
    expect(measuresForStage(2)).toBe(WET_MEASURES);
    expect(measuresForStage(1)).toBe(DRY_MEASURES);
    expect(measuresForStage(-4)).toBe(DRY_MEASURES);
    expect(measuresForStage(null)).toBe(DRY_MEASURES);
    // excess-water measures are advice only: they never add water in the what-if
    expect(WET_MEASURES.every((m) => !m.sim)).toBe(true);
  });
});
