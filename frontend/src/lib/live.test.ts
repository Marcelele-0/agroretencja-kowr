import { describe, expect, it } from "vitest";
import { computeLocal } from "../analysis/local";
import { todayIso } from "./dates";
import { landUseAt } from "./iung";
import { weatherFor } from "./openMeteo";
import { parcelById, parcelByPoint } from "./uldk";

// Real services. Run with: pnpm test:live
describe.skipIf(!(globalThis as { process?: { env: Record<string, string | undefined> } }).process?.env.LIVE)("live services", () => {
  it("analyses a real parcel end to end in under 10 s", async () => {
    const t0 = Date.now();
    const p = await parcelById("040706_5.0050.78/2");
    expect(p.commune).toBe("Kruszwica");
    const [land, s] = await Promise.all([landUseAt(p.centroid[0], p.centroid[1]), weatherFor(p.centroid[1], p.centroid[0], "2018-07-20")]);
    const r = computeLocal(land, s, "kukurydza_ziarno", "2018-07-20", todayIso());
    expect(r.windows.filter((w) => w.kbw !== null)).toHaveLength(7);
    expect(r.stage).not.toBeNull();
    expect(r.years).toHaveLength(10);
    console.log({ land: land.label, stage: r.stage, kbw60: r.kbw60, drought: `${r.droughtCount}/${r.knownCount}`, ms: Date.now() - t0 });
    expect(Date.now() - t0).toBeLessThan(10_000);
  }, 20_000);
  it("finds a parcel by a map click", async () => {
    const p = await parcelByPoint(23.70, 50.75);
    expect(p.id).toMatch(/^060408_2\./);
  }, 15_000);
});
