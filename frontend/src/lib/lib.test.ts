import { describe, expect, it } from "vitest";
import { BackendError, parseBackendError } from "../api/backend";
import { quickPicks } from "../ds/inputs";
import { projectParcelSvg, ReportDocumentBuilder } from "../report/reportBuilder";
import { measureLook, simForBackend } from "./catalog";
import { isCropCode } from "./crops";
import { addDays, inSeason, isIso, seasonYear, toDay } from "./dates";
import { parseUug } from "./geocoder";
import { parseWkt, polygonsArea } from "./geo";
import { classifyIung, IUNG_PROGI, thresholdsFor } from "./iung";
import { activeWindow, kbw, progressiveSeasonWindows, riskFromStage, seasonWindows, stageFromKbw, windowsFor } from "./kbw";
import { fao56Et0Daily, parsePower } from "./nasaPower";
import { compareYears, coversRange, neededRanges, type DailySeries } from "./openMeteo";
import { DEMO_PRESETS } from "./presets";
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
  return { start: from, startDay: toDay(from), p: new Array(days).fill(p), et0: new Array(days).fill(e), forecastFrom: addDays(from, days), forecastOk: true, grid: { lat: 0, lon: 0, elevation: null }, retrievedAt: new Date(), providers: ["open-meteo"] };
}

describe("NASA POWER backup", () => {
  it("computes FAO-56 ET₀ like the backend (src/hydro_core/et0.py)", () => {
    expect(fao56Et0Daily("2018-07-20", 52.7, 85, 27.4, 14.1, 68, 2.6, 22.5)).toBeCloseTo(4.623, 3);
    expect(fao56Et0Daily("2023-04-10", 50.1, 250, 12, 2, 80, 3.5, 12)).toBeCloseTo(1.6466, 3);
  });
  it("parses the daily point answer and skips fill values", () => {
    const j = {
      geometry: { coordinates: [18.25, 52.7, 85] as [number, number, number] },
      header: { fill_value: -999 },
      properties: { parameter: {
        PRECTOTCORR: { "20180720": 3.2, "20180721": -999 }, T2M_MAX: { "20180720": 27.4, "20180721": 25 },
        T2M_MIN: { "20180720": 14.1, "20180721": 13 }, RH2M: { "20180720": 68, "20180721": 70 },
        WS2M: { "20180720": 2.6, "20180721": -999 }, ALLSKY_SFC_SW_DWN: { "20180720": 22.5, "20180721": 20 },
      } },
    };
    const c = parsePower(j, 52.7, 18.25);
    expect(c.from).toBe("2018-07-20");
    expect(c.p).toEqual([3.2, null]);
    expect(c.e[0]).toBeCloseTo(4.62, 2);
    expect(c.e[1]).toBeNull();
  });
});

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

describe("geocoder (GUGiK UUG)", () => {
  it("parses single city, multiple hits, street address, and error text", () => {
    const single = JSON.stringify({
      type: "city",
      results: {
        "1": {
          city: "Gniezno",
          commune: "Gniezno",
          county: "gnieźnieński",
          voivodeship: "wielkopolskie",
          x: "17.601389",
          y: "52.533333",
        },
      },
    });
    expect(parseUug(single)).toEqual([
      {
        label: "Gniezno",
        sub: "pow. gnieźnieński, wielkopolskie",
        lon: 17.601389,
        lat: 52.533333,
        kind: "city",
      },
    ]);

    const multi = JSON.stringify({
      type: "city",
      results: {
        "1": { city: "Kowalewo", commune: "Szubin", county: "powiat nakielski", voivodeship: "kujawsko-pomorskie", x: "17.7278", y: "52.9709" },
        "2": { city: "Kowalewo", commune: "Nowe", county: "świecki", voivodeship: "kujawsko-pomorskie", x: "18.67", y: "53.63" },
        "3": { city: "Outside", x: "5.0", y: "50.0" },
      },
    });
    const hits = parseUug(multi);
    expect(hits).toHaveLength(2);
    expect(hits[0]!.sub).toBe("gm. Szubin, pow. nakielski, kujawsko-pomorskie");

    const addr = JSON.stringify({
      type: "address",
      results: {
        "1": { city: "Poznań", street: "Głogowska", number: "10", commune: "Poznań", county: "Poznań", voivodeship: "wielkopolskie", x: "16.905", y: "52.405" },
      },
    });
    expect(parseUug(addr)[0]).toMatchObject({
      label: "Poznań, Głogowska 10",
      sub: "gm. Poznań, pow. Poznań, wielkopolskie",
      kind: "address",
    });

    expect(parseUug("Blad zapytania.")).toEqual([]);
  });
});

describe("backend measure levers, quick picks, and presets", () => {
  it("maps backend retention metrics and falls back to catalog defaults", () => {
    expect(simForBackend({ id: "rec_1", category: "retention", delta_taw_mm: 18.4, runoff_reduction_pct: 20 })).toEqual({
      addMm: 18,
      etCut: 0.1,
      note: "+18 mm, −20% spływu",
    });
    expect(simForBackend({ id: "miedzyplon_mulcz", category: "agrotechnika", delta_taw_mm: 0, runoff_reduction_pct: 0 })).toEqual({
      etCut: 0.1,
      note: "−10% parowania",
    });
    expect(simForBackend({ id: "unknown_0", category: "other", delta_taw_mm: 0, runoff_reduction_pct: 0 })).toBeUndefined();
    expect(measureLook("sterowany_odplyw urzadzenie_wodne").permit).toBe(true);
  });

  it("includes Susza 2019 in quickPicks for 2026-11-21", () => {
    const picks = quickPicks("2026-11-21");
    expect(picks).toEqual([
      { label: "Dziś", date: "2026-11-21" },
      { label: "Szczyt sezonu 2026", date: "2026-07-15" },
      { label: "Susza 2019", date: "2019-07-15" },
    ]);
  });

  it("provides valid preset URLs with TERYT IDs, CropCodes, and ISO dates", () => {
    expect(DEMO_PRESETS).toHaveLength(3);
    for (const p of DEMO_PRESETS) {
      const q = new URLSearchParams(p.href.split("?")[1] ?? "");
      const dzialki = (q.get("dzialki") ?? "").split(",").filter(Boolean);
      const aktywna = q.get("aktywna") ?? "";
      const data = q.get("data") ?? "";
      expect(dzialki.length).toBeGreaterThanOrEqual(1);
      expect(isIso(data)).toBe(true);
      const ids = dzialki.map((entry) => {
        const [id, crop] = entry.split("~");
        expect(looksLikeParcelId(id ?? "")).toBe(true);
        expect(isCropCode(crop)).toBe(true);
        return id;
      });
      expect(ids).toContain(aktywna);
    }
  });

  it("checks series coverage with coversRange including bounds and missing-day threshold", () => {
    const s = series("2026-03-20", 30, 2, 3);
    expect(coversRange(s, "2026-03-20", "2026-04-18")).toBe(true);
    expect(coversRange(s, "2026-03-19", "2026-04-10")).toBe(false);
    expect(coversRange(s, "2026-03-25", "2026-04-19")).toBe(false);
    expect(coversRange(s, "2026-04-10", "2026-04-05")).toBe(false);

    s.p[5] = null;
    s.p[6] = null;
    expect(coversRange(s, "2026-03-20", "2026-04-18")).toBe(true);
    s.p[7] = null;
    expect(coversRange(s, "2026-03-20", "2026-04-18")).toBe(false);
  });

  it("accumulates progressiveSeasonWindows across completed, active, and future windows", () => {
    const s = series("2018-01-01", 365, 1, 4);
    const th = thresholdsFor("Kukurydza na ziarno", "II");
    const prog = progressiveSeasonWindows(s, 2018, th, "2018-05-31");
    // Window 0 (21.III-20.V): 61 days complete -> -3 * 61 = -183
    expect(prog[0]!.kbw).toBe(-183);
    // Window 4 (1.V-30.VI): partial 31 days -> -3 * 31 = -93
    expect(prog[4]!.kbw).toBe(-93);
    expect(prog[4]!.drought).toBe(false);
    // Window 7 (1.VI-31.VII): hasn't started yet on 2018-05-31 -> null
    expect(prog[7]!.kbw).toBeNull();
    expect(prog[7]!.drought).toBe(false);
  });
});

describe("backend error parsing and report builder", () => {
  it("extracts Polish message and status code from backend HTTP errors", () => {
    const refusal = parseBackendError(
      "/api/analyze",
      422,
      JSON.stringify({ status: "not_arable", message: "To jest las, nie grunt rolny." }),
    );
    expect(refusal).toBeInstanceOf(BackendError);
    expect(refusal.status).toBe(422);
    expect(refusal.code).toBe("not_arable");
    expect(refusal.message).toBe("To jest las, nie grunt rolny.");

    const unavailable = parseBackendError(
      "/api/analyze",
      503,
      JSON.stringify({ status: "no_data", message: "Brak odpowiedzi z bazy pogody." }),
    );
    expect(unavailable.status).toBe(503);
    expect(unavailable.code).toBe("no_data");
    expect(unavailable.message).toBe("Brak odpowiedzi z bazy pogody.");

    const detail = parseBackendError("/api/what-if", 422, JSON.stringify({ detail: "Błędna data analizy." }));
    expect(detail.message).toBe("Błędna data analizy.");

    const plain = parseBackendError("/api/analyze", 502, "<html>Bad Gateway</html>");
    expect(plain.message).toBe("/api/analyze: HTTP 502");
  });

  it("projects parcel geometry to SVG and builds 1 A4 sheet per parcel", () => {
    const p = parseUldk(ULDK_OK);
    const sil = projectParcelSvg(p.geometry, 120, 72, 6);
    expect(sil).not.toBeNull();
    expect(sil!.viewBox).toBe("0 0 120 72");
    expect(sil!.paths).toHaveLength(1);
    expect(sil!.paths[0]).toMatch(/^M[\d.]+ [\d.]+(?: L[\d.]+ [\d.]+)+ Z$/);
    expect(sil!.widthM).toBeGreaterThan(100);
    expect(sil!.heightM).toBeGreaterThan(200);

    const manyRecs = Array.from({ length: 8 }, (_, i) => ({
      id: `m_${i}`,
      category: "agrotechnika",
      title: `Działanie ${i + 1}`,
      rationale: "Efekt",
      delta_taw_mm: 5 + i,
      runoff_reduction_pct: 10,
      priority_rank: i + 1,
    }));

    const doc = new ReportDocumentBuilder()
      .setMetadata("2026-07-15", "21.11.2026, 22:00")
      .addParcelSheet({
        parcelId: p.id,
        parcel: p,
        crop: "kukurydza_ziarno",
        land: classifyIung(2),
        local: null,
        backend: {
          retention: {
            recommendations: manyRecs,
            what_if_default: {
              baseline_yield_loss_pct: 25,
              scenario_yield_loss_pct: 15,
              delta_reserve_days: 7,
              saved_water_mm: 30,
              reference_dry_year: 2019,
            },
          },
        },
      })
      .addParcelSheet({
        parcelId: "300302_5.0005.99",
        parcel: null,
        crop: "zboza_ozime",
        land: classifyIung(11),
        local: null,
        nonAgriMessage: "Las: działka 99 nie jest gruntem rolnym.",
      })
      .build();

    expect(doc.sheets).toHaveLength(2);
    expect(doc.sheets[0]!.pageNumber).toBe(1);
    expect(doc.sheets[0]!.totalPages).toBe(2);
    expect(doc.sheets[0]!.measures).toHaveLength(5);
    expect(doc.sheets[1]!.pageNumber).toBe(2);
    expect(doc.sheets[1]!.totalPages).toBe(2);
    expect(doc.sheets[1]!.nonAgriMessage).toContain("Las");
  });
});
