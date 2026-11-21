import { describe, expect, it } from "vitest";
import { addDays, inSeason, isIso, seasonYear, toDay } from "./dates";
import { parseWkt, polygonsArea } from "./geo";
import { classifyIung, IUNG_PROGI, thresholdsFor } from "./iung";
import { activeWindow, kbw, riskFromStage, seasonWindows, stageFromKbw, windowsFor } from "./kbw";
import { compareYears, neededRanges, type DailySeries } from "./openMeteo";
import { looksLikeParcelId, parseUldk, ParcelNotFound } from "./uldk";

// ULDK answer for 060408_2.0166.7 (Werbkowice), fetched 21.11.2026.
const ULDK_OK = "0\nSRID=4326;POLYGON((23.7320050291365 50.7731051225267,23.7307551215671 50.7766139936836,23.7305720565778 50.7765679019826,23.730383328333 50.7765204484094,23.7297026122523 50.7763484233375,23.7309440070061 50.7729025632761,23.7320050291365 50.7731051225267))|060408_2.0166.7|lubelskie|powiat hrubieszowski|Werbkowice|Wilków|7\n";

describe("ULDK", () => {
  it("parses fields, area and WGS84 geometry", () => {
    const p = parseUldk(ULDK_OK);
    expect(p.id).toBe("060408_2.0166.7");
    expect(p.commune).toBe("Werbkowice");
    expect(p.region).toBe("Wilków");
    expect(p.county).toBe("hrubieszowski");
    expect(p.areaHa).toBeCloseTo(3.12, 1);
    expect(p.centroid[0]).toBeGreaterThan(23.7);
    expect(p.centroid[0]).toBeLessThan(23.75);
    expect(p.centroid[1]).toBeGreaterThan(50.76);
    expect(p.centroid[1]).toBeLessThan(50.79);
  });
  it("throws ParcelNotFound on -1", () => {
    expect(() => parseUldk("-1 brak wyników\n")).toThrow(ParcelNotFound);
  });
  it("recognises parcel ids", () => {
    expect(looksLikeParcelId("040706_5.0050.78/2")).toBe(true);
    expect(looksLikeParcelId("Werbkowice 7")).toBe(false);
  });
});

describe("WKT", () => {
  it("parses multipolygons with holes", () => {
    const polys = parseWkt("MULTIPOLYGON(((0 0,10 0,10 10,0 10,0 0),(2 2,4 2,4 4,2 4,2 2)),((20 20,21 20,21 21,20 21,20 20)))");
    expect(polys).toHaveLength(2);
    expect(polygonsArea(polys)).toBeCloseTo(100 - 4 + 1);
  });
});

describe("dates and season", () => {
  it("knows the IUNG season", () => {
    expect(inSeason("2026-03-20")).toBe(false);
    expect(inSeason("2026-03-21")).toBe(true);
    expect(inSeason("2026-09-30")).toBe(true);
    expect(inSeason("2026-10-01")).toBe(false);
    expect(seasonYear("2026-02-10")).toBe(2025);
    expect(seasonYear("2026-11-21")).toBe(2026);
    expect(isIso("2026-02-30")).toBe(false);
  });
  it("builds 14 windows of 61 days in dekads", () => {
    const w = windowsFor(2018);
    expect(w).toHaveLength(14);
    expect(w[0]!.label).toBe("21.III–20.V");
    expect(w[13]!.label).toBe("1.VIII–30.IX");
    for (const x of w) expect(toDay(x.end) - toDay(x.start) + 1).toBeGreaterThanOrEqual(60);
    expect(activeWindow("2018-07-20")).toBe(6);
    expect(activeWindow("2018-05-19")).toBe(null);
  });
});

describe("IUNG table", () => {
  it("has 14 crops and the published values", () => {
    expect(Object.keys(IUNG_PROGI.crops)).toHaveLength(14);
    expect(thresholdsFor("Zboża ozime", "I")!.slice(0, 3)).toEqual([-129, -132, -137]);
    expect(thresholdsFor("Kukurydza na ziarno", "II")!.slice(0, 7)).toEqual(["x", "x", "x", -200, -193, -188, -185]);
    expect(thresholdsFor(null, "II")).toBeNull();
  });
  it("classifies the land-use raster", () => {
    expect(classifyIung(2)).toMatchObject({ kind: "mineral", category: "II" });
    expect(classifyIung(11)).toMatchObject({ kind: "nonAgri", label: "Las" });
    expect(classifyIung(5).kind).toBe("organic");
    expect(classifyIung(null).kind).toBe("noData");
  });
});

function series(from: string, days: number, p: number, e: number): DailySeries {
  return { start: from, startDay: toDay(from), p: new Array(days).fill(p), et0: new Array(days).fill(e), forecastFrom: addDays(from, days), forecastOk: true, grid: { lat: 0, lon: 0, elevation: null }, retrievedAt: new Date() };
}

describe("KBW and water state", () => {
  it("sums P − ET0 over a window", () => {
    const s = series("2018-01-01", 365, 1, 4);
    expect(kbw(s, "2018-03-21", "2018-05-20")).toBe(-3 * 61);
    const w = seasonWindows(s, 2018, thresholdsFor("Kukurydza na ziarno", "II"), "2026-11-21");
    expect(w[6]!.kbw).toBe(-183);
    expect(w[6]!.drought).toBe(false);
    expect(w[0]!.threshold).toBeNull();
  });
  it("grades the state against the threshold", () => {
    expect(stageFromKbw(-209, -185)).toBe(-4);
    expect(stageFromKbw(-260, -185)).toBe(-5);
    expect(stageFromKbw(-170, -185)).toBe(-3);
    expect(stageFromKbw(-20, -185)).toBe(0);
    expect(stageFromKbw(100, -185)).toBe(2);
    expect(riskFromStage(-4)).toEqual({ level: "krytyczne", side: "dry" });
    expect(riskFromStage(3)).toEqual({ level: "wysokie", side: "wet" });
    expect(riskFromStage(0).level).toBe("niskie");
  });
});

describe("weather ranges", () => {
  it("asks only for the season and 60 days before each comparison date", () => {
    const r = neededRanges("2018-07-20", "2026-11-21");
    expect(r).toContainEqual(["2018-03-20", "2018-08-04"]);
    expect(r.length).toBeLessThanOrEqual(11);
    const days = r.reduce((a, [x, y]) => a + toDay(y) - toDay(x) + 1, 0);
    expect(days).toBeLessThan(1400);
    expect(compareYears("2018-07-20", "2026-11-21")).toEqual([2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025, 2026]);
    expect(compareYears("2018-12-20", "2026-11-21")[9]).toBe(2025);
  });
});
