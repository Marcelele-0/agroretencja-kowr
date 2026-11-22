import { afterEach, describe, expect, it } from "vitest";
import { farmerData, farmerFields, parseMm, resetFarmerData, setDrained, setGaugeRain, withFarmer } from "./farmerData";

afterEach(() => resetFarmerData());

describe("farmerData", () => {
  it("sends nothing until the farmer answers", () => {
    expect(farmerFields("p1", "2025-07-15")).toEqual({});
    expect(withFarmer({ parcel_id: "p1", crop: "kukurydza", analysis_date: "2025-07-15" })).toEqual({ parcel_id: "p1", crop: "kukurydza", analysis_date: "2025-07-15" });
  });

  it("keeps drainage per parcel and the gauge total per date", () => {
    setDrained("p1", true);
    setGaugeRain("p1", "2025-07-15", 12.34);
    expect(farmerFields("p1", "2025-07-15")).toEqual({ tile_drainage: true, gauge_rain_7d_mm: 12.3 });
    expect(farmerFields("p1", "2025-07-16")).toEqual({ tile_drainage: true });
    expect(farmerFields("p2", "2025-07-15")).toEqual({});
    setDrained("p1", false);
    expect(farmerFields("p1", "2025-07-15").tile_drainage).toBe(false);
    setDrained("p1", null);
    setGaugeRain("p1", "2025-07-15", null);
    expect(farmerData("p1")).toEqual({ drained: null, rain7d: {} });
  });

  it("clamps the gauge total and reads a decimal comma", () => {
    setGaugeRain("p1", "2025-07-15", 900);
    expect(farmerFields("p1", "2025-07-15").gauge_rain_7d_mm).toBe(500);
    expect(parseMm("12,5")).toBe(12.5);
    expect(parseMm(" 0 ")).toBe(0);
    expect(parseMm("-3")).toBeNull();
    expect(parseMm("12 mm")).toBeNull();
    expect(parseMm("501")).toBeNull();
  });
});
