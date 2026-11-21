import { useEffect, useMemo, useRef, useState } from "react";
import { backendEnabled, postAnalyze, postRlCompare, postRlRecommend } from "../api/backend";
import type { AnalysisResponse, RlCompareResponse, RlRecommendResponse } from "../api/contract";
import { backendCrop, type CropCode } from "../lib/crops";
import { inSeason, todayIso, type Iso } from "../lib/dates";
import { isAbort, SourceError } from "../lib/http";
import { classifyIung, IUNG_PROGI, IUNG_WMS, landUseAt, type LandUse } from "../lib/iung";
import { ARCHIVE_URL, weatherFor, type DailySeries } from "../lib/openMeteo";
import { STATIC_SOURCES, type SourceEntry } from "../lib/sources";
import { parcelById, ParcelNotFound, ULDK_URL, type Parcel } from "../lib/uldk";
import { computeLocal, type LocalResult } from "./local";

export type Res<T> = { status: "loading" } | { status: "ready"; data: T } | { status: "error"; message: string } | { status: "off" };
export interface AnalysisQuery { parcelId: string; crop: CropCode; date: Iso }
export interface AnalysisState {
  parcel: Res<Parcel>;
  land: Res<LandUse>;
  weather: Res<DailySeries>;
  local: LocalResult | null;
  backend: Res<AnalysisResponse>;
  rl: Res<RlRecommendResponse>;
  rlCompare: Res<RlCompareResponse>;
  sources: SourceEntry[];
  elapsedMs: number | null;
}

const LOADING = { status: "loading" } as const;
const OFF = { status: "off" } as const;
const RL_CROPS = new Set(["kukurydza", "ziemniak", "pszenica_ozima", "burak_cukrowy"]);

export function message(e: unknown): string {
  if (e instanceof ParcelNotFound) return "ULDK nie zna działki o tym numerze.";
  if (e instanceof SourceError) return e.message;
  return e instanceof Error ? e.message : "Nieznany błąd.";
}
/** Land use for the calculation: the IUNG answer, or "no data" when the layer failed (the KBW still works without it). */
export function landForCalc(land: Res<LandUse>): LandUse | null {
  if (land.status === "ready") return land.data;
  if (land.status === "error") return { ...classifyIung(null), label: land.message };
  return null;
}

/**
 * Parcel and soil load once per parcel; weather once per (parcel, date); the crop only re-grades the same data.
 * "Przelicz" therefore keeps the map and the parcel and recomputes in place.
 */
export function useAnalysis(q: AnalysisQuery): AnalysisState {
  const [parcel, setParcel] = useState<Res<Parcel>>(LOADING);
  const [land, setLand] = useState<Res<LandUse>>(LOADING);
  const [landAt, setLandAt] = useState<Date | null>(null);
  const [weather, setWeather] = useState<{ key: string; res: Res<DailySeries> }>({ key: "", res: LOADING });
  const [backend, setBackend] = useState<Res<AnalysisResponse>>(LOADING);
  const [rl, setRl] = useState<Res<RlRecommendResponse>>(LOADING);
  const [rlCompare, setRlCompare] = useState<Res<RlCompareResponse>>(LOADING);
  const t0 = useRef(performance.now());
  const [elapsedMs, setElapsed] = useState<number | null>(null);

  useEffect(() => {
    let alive = true;
    t0.current = performance.now();
    setParcel(LOADING); setLand(LOADING); setElapsed(null);
    parcelById(q.parcelId).then((p) => {
      if (!alive) return;
      setParcel({ status: "ready", data: p });
      landUseAt(p.centroid[0], p.centroid[1]).then(
        (l) => { if (alive) { setLand({ status: "ready", data: l }); setLandAt(new Date()); } },
        (e) => { if (alive) setLand({ status: "error", message: message(e) }); },
      );
    }, (e) => { if (alive) { setParcel({ status: "error", message: message(e) }); setLand(OFF); } });
    return () => { alive = false; };
  }, [q.parcelId]);

  const p = parcel.status === "ready" ? parcel.data : null;
  const wkey = p ? `${p.id}|${q.date}` : "";
  useEffect(() => {
    if (!p) return;
    let alive = true;
    const key = `${p.id}|${q.date}`;
    t0.current = performance.now();
    setWeather({ key, res: LOADING });
    weatherFor(p.centroid[1], p.centroid[0], q.date).then(
      (w) => { if (alive) { setWeather({ key, res: { status: "ready", data: w } }); setElapsed(Math.round(performance.now() - t0.current)); } },
      (e) => { if (alive) setWeather({ key, res: { status: "error", message: message(e) } }); },
    );
    return () => { alive = false; };
  }, [p, q.date]);

  useEffect(() => {
    if (!p) return;
    const ctl = new AbortController();
    const bc = backendCrop(q.crop);
    if (backendEnabled && bc) {
      setBackend(LOADING);
      postAnalyze({ parcel_id: p.id, crop: bc, analysis_date: q.date }, ctl.signal).then((data) => setBackend({ status: "ready", data }), (e) => !isAbort(e) && setBackend({ status: "error", message: message(e) }));
    } else if (import.meta.env.DEV && new URLSearchParams(location.search).has("fixtures")) {
      import("../api/fixtures").then((m) => { if (!ctl.signal.aborted) { setBackend({ status: "ready", data: m.BACKEND_FIXTURE }); setRlCompare({ status: "ready", data: m.RL_COMPARE_FIXTURE }); } });
    } else setBackend(OFF);
    // The 10-season strategy comparison does not depend on the date: it also helps out of season.
    if (backendEnabled && bc && RL_CROPS.has(bc)) {
      setRlCompare(LOADING);
      postRlCompare({ parcel_id: p.id, crop: bc }, ctl.signal).then((data) => setRlCompare({ status: "ready", data }), (e) => !isAbort(e) && setRlCompare({ status: "error", message: message(e) }));
    } else if (!(import.meta.env.DEV && new URLSearchParams(location.search).has("fixtures"))) setRlCompare(OFF);
    if (backendEnabled && bc && RL_CROPS.has(bc) && inSeason(q.date)) {
      setRl(LOADING);
      postRlRecommend({ parcel_id: p.id, crop: bc, date: q.date }, ctl.signal).then((data) => setRl({ status: "ready", data }), (e) => !isAbort(e) && setRl({ status: "error", message: message(e) }));
    } else setRl(OFF);
    return () => ctl.abort();
  }, [p, q.crop, q.date]);

  const w = weather.key === wkey ? weather.res : LOADING;
  const lc = useMemo(() => landForCalc(land), [land]);
  const local = useMemo(() => (w.status === "ready" && lc && lc.kind !== "nonAgri" ? computeLocal(lc, w.data, q.crop, q.date, todayIso()) : null), [w, lc, q.crop, q.date]);

  const sources = useMemo<SourceEntry[]>(() => {
    if (!p || w.status !== "ready") return [];
    const wd = w.data;
    return [
      { name: "GUGiK, Usługa Lokalizacji Działek Katastralnych", what: "granica, gmina, obręb, powierzchnia z granic", url: ULDK_URL, retrievedAt: p.retrievedAt, resolution: "działka ewidencyjna", license: "otwarte dane PZGiK" },
      { name: "IUNG-PIB, warstwa Susza:katgl_landuse2024 (WMS)", what: "kategoria gleby I–IV i użytkowanie", url: IUNG_WMS, retrievedAt: landAt, resolution: "punkt w środku działki", license: "publiczna usługa IUNG-PIB" },
      { name: "IUNG-PIB, progi suszy SMSR", what: "krytyczne KBW dla uprawy, gleby i okna", url: IUNG_PROGI.source, retrievedAt: new Date(`${IUNG_PROGI.retrieved}T12:00:00`), resolution: "cała Polska, jeden próg", license: "Rozporządzenie MRiRW z 11.04.2019" },
      { name: "Open-Meteo, archiwum ERA5 i prognoza", what: "dzienny opad i ewapotranspiracja ET₀ (FAO-56)", url: ARCHIVE_URL, retrievedAt: wd.retrievedAt, resolution: `siatka ok. 9 km (${wd.grid.lat.toFixed(2)}° N, ${wd.grid.lon.toFixed(2)}° E)`, license: "CC BY 4.0" },
      { ...STATIC_SOURCES.ortho, retrievedAt: null },
      { ...STATIC_SOURCES.kieg, retrievedAt: null },
    ];
  }, [p, w, landAt]);

  return { parcel, land, weather: w, local, backend, rl, rlCompare, sources, elapsedMs };
}

export interface PortfolioRow { id: string; crop: CropCode; parcel: Res<Parcel>; land: LandUse | null; local: LocalResult | null; error: string | null }
/** Light analysis of extra parcels for the comparison table. Uses the same caches as the main analysis. */
export function usePortfolio(items: { id: string; crop: CropCode }[], date: Iso): PortfolioRow[] {
  const ids = items.map((i) => i.id);
  const [rows, setRows] = useState<Record<string, { parcel: Res<Parcel>; land: LandUse | null; weather: DailySeries | null; error: string | null }>>({});
  const key = ids.join(",");
  useEffect(() => {
    let alive = true;
    ids.forEach((id) => {
      setRows((r) => ({ ...r, [id]: { parcel: LOADING, land: null, weather: null, error: null } }));
      parcelById(id).then(async (p) => {
        if (!alive) return;
        setRows((r) => ({ ...r, [id]: { ...r[id]!, parcel: { status: "ready", data: p } } }));
        const [l, wd] = await Promise.allSettled([landUseAt(p.centroid[0], p.centroid[1]), weatherFor(p.centroid[1], p.centroid[0], date)]);
        if (!alive) return;
        const land = l.status === "fulfilled" ? l.value : { ...classifyIung(null), label: message(l.reason) };
        setRows((r) => ({ ...r, [id]: { ...r[id]!, land, weather: wd.status === "fulfilled" ? wd.value : null, error: wd.status === "rejected" ? message(wd.reason) : null } }));
      }, (e) => { if (alive) setRows((r) => ({ ...r, [id]: { parcel: { status: "error", message: message(e) }, land: null, weather: null, error: message(e) } })); });
    });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, date]);
  const crops = items.map((i) => i.crop).join(",");
  return useMemo(() => items.map(({ id, crop }) => {
    const r = rows[id];
    if (!r) return { id, crop, parcel: LOADING, land: null, local: null, error: null };
    const local = r.land && r.weather && r.land.kind !== "nonAgri" ? computeLocal(r.land, r.weather, crop, date, todayIso()) : null;
    return { id, crop, parcel: r.parcel, land: r.land, local, error: r.error };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [rows, key, crops, date]);
}
