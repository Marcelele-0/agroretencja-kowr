import { describe, expect, it } from "vitest";
import { BackendError } from "../api/backend";
import type { ZonesRequest, ZonesResponse } from "../api/contract";
import { alignBins, createZonesCache, jointRequestOf, roundState, zoneBins, zoneClassOrder, zonesByParcel, zonesFromFeatures } from "./jointZones";

const zone = (value: number, areaPct?: number) => ({ geometry: null, value, areaPct });
const square = { type: "Polygon", coordinates: [[[0, 0], [1, 0], [1, 1], [0, 0]]] };
const fc = (values: [number, number][], geometry: unknown = square) => ({ type: "FeatureCollection" as const, features: values.map(([value, area_pct]) => ({ type: "Feature" as const, properties: { value, area_pct }, geometry })) });

describe("roundState", () => {
  it("rounds to 0.25, clamps to −5…+5 and never returns −0", () => {
    expect(roundState(-1.37)).toBe(-1.25);
    expect(roundState(-1.38)).toBe(-1.5);
    expect(roundState(2.12)).toBe(2);
    expect(roundState(9)).toBe(5);
    expect(Object.is(roundState(-0.1), 0)).toBe(true);
  });
});

describe("jointRequestOf", () => {
  it("does not wait for pending rows and re-keys when they arrive", () => {
    const early = jointRequestOf([{ id: "a", state: -1, pending: false }, { id: "b", state: null, pending: true }]);
    const late = jointRequestOf([{ id: "a", state: -1, pending: false }, { id: "b", state: 0.6, pending: false }]);
    expect(early?.key).toBe("a~=-1.00");
    expect(late?.key).toBe("a~=-1.00,b~=0.50");
  });

  it("keys by sorted ids and rounded states, skipping rows without a state", () => {
    const p = jointRequestOf([{ id: "b", state: -1.37, pending: false }, { id: "a", state: 0, pending: false }, { id: "c", state: null, pending: false }]);
    expect(p?.key).toBe("a~=0.00,b~=-1.25");
    expect(p?.req.parcels).toEqual([{ parcel_id: "a", water_state: 0 }, { parcel_id: "b", water_state: -1.25 }]);
  });

  it("keeps the key for changes below 0.25 and changes it at the next step", () => {
    const k = (s: number) => jointRequestOf([{ id: "a", state: s, pending: false }])?.key;
    expect(k(-1)).toBe(k(-1.1));
    expect(k(-1)).not.toBe(k(-1.25));
  });

  it("sends the crop and re-keys when a crop changes", () => {
    const k = (crop: string) => jointRequestOf([{ id: "a", crop, state: -1, pending: false }]);
    expect(k("kukurydza_ziarno")?.req.parcels[0]).toEqual({ parcel_id: "a", water_state: -1, crop: "kukurydza_ziarno" });
    expect(k("kukurydza_ziarno")?.key).not.toBe(k("pszenica_ozima")?.key);
  });

  it("returns null without states and caps the request at 12 parcels", () => {
    expect(jointRequestOf([])).toBeNull();
    expect(jointRequestOf([{ id: "a", state: null, pending: true }])).toBeNull();
    expect(jointRequestOf(Array.from({ length: 13 }, (_, i) => ({ id: `p${String(i).padStart(2, "0")}`, state: 0, pending: false })))?.req.parcels).toHaveLength(12);
  });
});

describe("zone class order", () => {
  it("is the union of classes with area across parcels, dry→wet", () => {
    const order = zoneClassOrder([
      { zones: [zone(1, 30), zone(-1, 70)], fallbackStage: 0 },
      { zones: null, fallbackStage: -3 },
      { zones: [zone(-1, 100), zone(2, 0)], fallbackStage: 0 },
    ]);
    expect(order).toEqual([-3, -1, 1]);
  });

  it("aligns every row to the same slots with 0 % for absent classes", () => {
    const order = [-3, -1, 1];
    expect(alignBins(zoneBins([zone(1, 25), zone(-1, 75)], 0), order).map((b) => [b.step, b.pct])).toEqual([[-3, 0], [-1, 75], [1, 25]]);
    expect(alignBins(zoneBins(null, -3, 2), order).map((b) => [b.step, b.pct, b.ha])).toEqual([[-3, 100, 2], [-1, 0, 0], [1, 0, 0]]);
  });
});

describe("zonesByParcel", () => {
  it("drops parcels with errors or empty collections", () => {
    const resp: ZonesResponse = { zones: { a: fc([[-1, 60], [0, 40]]), b: fc([]), c: fc([[1, 100]]) }, errors: { c: "brak NMT" } };
    const z = zonesByParcel(resp);
    expect(Object.keys(z)).toEqual(["a"]);
    expect(z.a!.map((x) => x.value)).toEqual([-1, 0]);
  });
});

describe("drawable zones", () => {
  it("drops empty geometries so a narrow strip keeps its flat fill", () => {
    expect(zonesFromFeatures(fc([[-1, 100]], { type: "Polygon", coordinates: [] }))).toBeNull();
    expect(zonesFromFeatures(fc([[-1, 100]], { type: "MultiPolygon", coordinates: [[]] }))).toBeNull();
    expect(zonesFromFeatures(fc([[Number.NaN, 100]]))).toBeNull();
    expect(zonesFromFeatures(fc([[-1, 100]]))?.length).toBe(1);
  });
});

describe("zonesCache", () => {
  it("shares one request per key and marks the endpoint off after 404", async () => {
    let t = 0;
    const calls: ZonesRequest[] = [];
    const cache = createZonesCache(async (req) => { calls.push(req); throw new BackendError(404, "Not Found", "/api/zones"); }, { now: () => t, retryAfterMs: 1000 });
    const req = { parcels: [{ parcel_id: "a", water_state: 0 }] };
    const p1 = cache.get("k", req);
    const p2 = cache.get("k", req);
    await expect(p1).rejects.toBeInstanceOf(BackendError);
    await expect(p2).rejects.toBeInstanceOf(BackendError);
    expect(calls).toHaveLength(1);
    expect(cache.available()).toBe(false);
    t = 1000;
    expect(cache.available()).toBe(true);
  });

  it("keeps resolved results for peek", async () => {
    const cache = createZonesCache(async () => ({ zones: {} }));
    await cache.get("k", { parcels: [] });
    expect(cache.peek("k")).toEqual({ zones: {} });
  });
});
