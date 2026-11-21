import { useEffect, useMemo, useRef, useState } from "react";
import { backendEnabled, postAnalyze, postRlCompare, postRlRecommend } from "../api/backend";
import type { AnalysisResponse, RlCompareResponse, RlRecommendResponse } from "../api/contract";
import { backendCrop, type CropCode } from "../lib/crops";
import { inSeason, todayIso, type Iso } from "../lib/dates";
import { isAbort, SourceError } from "../lib/http";
import { classifyIung, IUNG_PROGI, IUNG_WMS, landUseAt, type LandUse } from "../lib/iung";
import { ARCHIVE_URL, cachedWeatherFor, weatherFor, type DailySeries } from "../lib/openMeteo";
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
  updating: boolean;
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
  const [lastReady, setLastReady] = useState<{ parcelId: string; weather: DailySeries; date: Iso } | null>(null);
  const [backend, setBackend] = useState<Res<AnalysisResponse>>(LOADING);
  const [rl, setRl] = useState<Res<RlRecommendResponse>>(LOADING);
  const [rlCompare, setRlCompare] = useState<Res<RlCompareResponse>>(LOADING);
  const prevTarget = useRef<{ parcelId: string; crop: CropCode } | null>(null);
  const t0 = useRef(performance.now());
  const [elapsedMs, setElapsed] = useState<number | null>(null);

  useEffect(() => {
    let alive = true;
    t0.current = performance.now();
    setParcel(LOADING); setLand(LOADING); setLastReady(null); setElapsed(null);
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
  const syncHit = useMemo(() => (p ? cachedWeatherFor(p.centroid[1], p.centroid[0], q.date) : null), [p, q.date]);

  useEffect(() => {
    if (!p) return;
    let alive = true;
    const key = `${p.id}|${q.date}`;
    if (syncHit) {
      setWeather({ key, res: { status: "ready", data: syncHit } });
      setLastReady({ parcelId: p.id, weather: syncHit, date: q.date });
      return;
    }
    t0.current = performance.now();
    setWeather({ key, res: LOADING });
    weatherFor(p.centroid[1], p.centroid[0], q.date).then(
      (wd) => {
        if (!alive) return;
        setWeather({ key, res: { status: "ready", data: wd } });
        setLastReady({ parcelId: p.id, weather: wd, date: q.date });
        setElapsed(Math.round(performance.now() - t0.current));
      },
      (e) => { if (alive) setWeather({ key, res: { status: "error", message: message(e) } }); },
    );
    return () => { alive = false; };
  }, [p, q.date, syncHit]);

  useEffect(() => {
    if (!p) return;
    const ctl = new AbortController();
    const bc = backendCrop(q.crop);
    if (import.meta.env.DEV && new URLSearchParams(location.search).has("fixtures")) {
      import("../api/fixtures").then((m) => { if (!ctl.signal.aborted) setRlCompare({ status: "ready", data: m.RL_COMPARE_FIXTURE }); });
    } else if (backendEnabled && bc && RL_CROPS.has(bc)) {
      setRlCompare(LOADING);
      postRlCompare({ parcel_id: p.id, crop: bc }, ctl.signal).then((data) => setRlCompare({ status: "ready", data }), (e) => !isAbort(e) && setRlCompare({ status: "error", message: message(e) }));
    } else {
      setRlCompare(OFF);
    }
    return () => ctl.abort();
  }, [p, q.crop]);

  useEffect(() => {
    if (!p) return;
    const ctl = new AbortController();
    const bc = backendCrop(q.crop);
    const sameTarget = prevTarget.current?.parcelId === p.id && prevTarget.current?.crop === q.crop;
    prevTarget.current = { parcelId: p.id, crop: q.crop };
    if (backendEnabled && bc) {
      if (!sameTarget) setBackend(LOADING);
      postAnalyze({ parcel_id: p.id, crop: bc, analysis_date: q.date }, ctl.signal).then((data) => setBackend({ status: "ready", data }), (e) => !isAbort(e) && setBackend({ status: "error", message: message(e) }));
    } else if (import.meta.env.DEV && new URLSearchParams(location.search).has("fixtures")) {
      import("../api/fixtures").then((m) => { if (!ctl.signal.aborted) setBackend({ status: "ready", data: m.BACKEND_FIXTURE }); });
    } else setBackend(OFF);
    if (backendEnabled && bc && RL_CROPS.has(bc) && inSeason(q.date)) {
      if (!sameTarget) setRl(LOADING);
      postRlRecommend({ parcel_id: p.id, crop: bc, date: q.date }, ctl.signal).then((data) => setRl({ status: "ready", data }), (e) => !isAbort(e) && setRl({ status: "error", message: message(e) }));
    } else setRl(OFF);
    return () => ctl.abort();
  }, [p, q.crop, q.date]);

  const w: Res<DailySeries> = weather.key === wkey ? weather.res : syncHit ? { status: "ready", data: syncHit } : LOADING;
  const lc = useMemo(() => landForCalc(land), [land]);
  const stale = w.status === "loading" && lastReady?.parcelId === q.parcelId ? lastReady : null;
  const activeWeather = w.status === "ready" ? w.data : stale ? stale.weather : null;
  const activeDate = w.status === "ready" ? q.date : stale ? stale.date : q.date;
  const local = useMemo(
    () => (w.status !== "error" && activeWeather && lc && lc.kind !== "nonAgri" ? computeLocal(lc, activeWeather, q.crop, activeDate, todayIso()) : null),
    [w.status, activeWeather, lc, q.crop, activeDate],
  );
  const updating = w.status === "loading" && local !== null;

  const sources = useMemo<SourceEntry[]>(() => {
    if (!p || !activeWeather || w.status === "error") return [];
    const wd = activeWeather;
    return [
      { name: "GUGiK, Usługa Lokalizacji Działek Katastralnych", what: "granica, gmina, obręb, powierzchnia z granic", url: ULDK_URL, retrievedAt: p.retrievedAt, resolution: "działka ewidencyjna", license: "otwarte dane PZGiK" },
      { name: "IUNG-PIB, warstwa Susza:katgl_landuse2024 (WMS)", what: "kategoria gleby I–IV i użytkowanie", url: IUNG_WMS, retrievedAt: landAt, resolution: "punkt w środku działki", license: "publiczna usługa IUNG-PIB" },
      { name: "IUNG-PIB, progi suszy SMSR", what: "krytyczne KBW dla uprawy, gleby i okna", url: IUNG_PROGI.source, retrievedAt: new Date(`${IUNG_PROGI.retrieved}T12:00:00`), resolution: "cała Polska, jeden próg", license: "Rozporządzenie MRiRW z 11.04.2019" },
      { name: "Open-Meteo, archiwum ERA5 i prognoza", what: "dzienny opad i ewapotranspiracja ET₀ (FAO-56)", url: ARCHIVE_URL, retrievedAt: wd.retrievedAt, resolution: `siatka ok. 9 km (${wd.grid.lat.toFixed(2)}° N, ${wd.grid.lon.toFixed(2)}° E)`, license: "CC BY 4.0" },
      { ...STATIC_SOURCES.ortho, retrievedAt: null },
      { ...STATIC_SOURCES.kieg, retrievedAt: null },
    ];
  }, [p, activeWeather, w.status, landAt]);

  return { parcel, land, weather: w, local, updating, backend, rl, rlCompare, sources, elapsedMs };
}

interface PortfolioStateRow { parcel: Res<Parcel>; land: LandUse | null; weather: DailySeries | null; weatherDate: Iso | null; error: string | null }
export interface PortfolioRow { id: string; crop: CropCode; parcel: Res<Parcel>; land: LandUse | null; weather: DailySeries | null; local: LocalResult | null; error: string | null }
/** Light analysis of extra parcels for the comparison table. Splits geometry/soil loading from date weather updates so rows never flash. */
export function usePortfolio(items: { id: string; crop: CropCode }[], date: Iso): PortfolioRow[] {
  const ids = items.map((i) => i.id);
  const [rows, setRows] = useState<Record<string, PortfolioStateRow>>({});
  const rowsRef = useRef(rows);
  rowsRef.current = rows;
  const key = ids.join(",");

  useEffect(() => {
    let alive = true;
    ids.forEach((id) => {
      const cur = rowsRef.current[id];
      if (cur && cur.parcel.status !== "error") return;
      setRows((r) => (r[id] && r[id]!.parcel.status !== "error" ? r : { ...r, [id]: { parcel: LOADING, land: null, weather: null, weatherDate: null, error: null } }));
      parcelById(id).then(
        (p) => {
          if (!alive) return;
          setRows((r) => ({ ...r, [id]: { ...(r[id] ?? { land: null, weather: null, weatherDate: null, error: null }), parcel: { status: "ready", data: p } } }));
          landUseAt(p.centroid[0], p.centroid[1]).then(
            (l) => { if (alive) setRows((r) => (r[id] ? { ...r, [id]: { ...r[id]!, land: l } } : r)); },
            (e) => { if (alive) setRows((r) => (r[id] ? { ...r, [id]: { ...r[id]!, land: { ...classifyIung(null), label: message(e) } } } : r)); },
          );
        },
        (e) => {
          if (alive) setRows((r) => ({ ...r, [id]: { parcel: { status: "error", message: message(e) }, land: null, weather: null, weatherDate: null, error: message(e) } }));
        },
      );
    });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  const readyParcelSig = ids.map((id) => (rows[id]?.parcel.status === "ready" ? id : "")).join(",");
  useEffect(() => {
    let alive = true;
    ids.forEach((id) => {
      const cur = rowsRef.current[id];
      if (!cur || cur.parcel.status !== "ready") return;
      const p = cur.parcel.data;
      const hit = cachedWeatherFor(p.centroid[1], p.centroid[0], date);
      if (hit) {
        setRows((r) => {
          const prev = r[id];
          if (!prev || (prev.weather === hit && prev.weatherDate === date && prev.error === null)) return r;
          return { ...r, [id]: { ...prev, weather: hit, weatherDate: date, error: null } };
        });
        return;
      }
      weatherFor(p.centroid[1], p.centroid[0], date).then(
        (wd) => { if (alive) setRows((r) => (r[id] ? { ...r, [id]: { ...r[id]!, weather: wd, weatherDate: date, error: null } } : r)); },
        (e) => { if (alive) setRows((r) => (r[id] ? { ...r, [id]: { ...r[id]!, error: message(e) } } : r)); },
      );
    });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, date, readyParcelSig]);

  const crops = items.map((i) => i.crop).join(",");
  return useMemo(() => items.map(({ id, crop }) => {
    const r = rows[id];
    if (!r) return { id, crop, parcel: LOADING, land: null, weather: null, local: null, error: null };
    const d = r.weatherDate ?? date;
    const local = r.land && r.weather && r.land.kind !== "nonAgri" ? computeLocal(r.land, r.weather, crop, d, todayIso()) : null;
    return { id, crop, parcel: r.parcel, land: r.land, weather: r.weather, local, error: r.error };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [rows, key, crops, date]);
}
