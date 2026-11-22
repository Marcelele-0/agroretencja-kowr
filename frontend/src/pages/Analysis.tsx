import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { Link, Navigate, useSearchParams } from "react-router-dom";
import { API_URL, backendEnabled, postWhatIf, reportPdfUrl } from "../api/backend";
import type { AnalysisResponse, RlCompareResponse, RlPlan14d, RlRecommendResponse, RlSoilZoneDay, RlSource, WhatIfScenarioResult } from "../api/contract";
import { computeLocal, soilCause, withLevers, type Levers, type LocalResult, type TimelineDay } from "../analysis/local";
import { landForCalc, message, useAnalysis, usePortfolio, type AnalysisState, type PortfolioRow } from "../analysis/useAnalysis";
import { useRowAnalyses } from "../analysis/useRowAnalyses";
import { alignBins, zoneBins, zoneClassOrder, zonesFromFeatures, type JointZoneInput } from "../analysis/jointZones";
import { useJointZones } from "../analysis/useJointZones";
import { BAL, balKey, balStep, cx, Icon, Button, CauseBreakdown, ConfidenceBadge, CropSelect, DateBar, dateBounds, FloatingNav, KbwWindows, LayerSwitch, MetricTile, PhotoCard, Pill, RiskChip, StateTimeline, WaterBalance, Wordmark, YearCompare, type IconName } from "../ds";
import { DRY_MEASURES, measureLook, PERMIT_URL, simForBackend, WET_MEASURES, type Measure } from "../lib/catalog";
import { backendCrop, cropOf, DEFAULT_CROP, isCropCode, type CropCode } from "../lib/crops";
import { addDays, formatDayMonth, formatLong, isIso, isoOf, minIso, monthName, parts, todayIso, type Iso } from "../lib/dates";
import { isAbort } from "../lib/http";
import { thresholdsFor, type LandUse } from "../lib/iung";
import { activeWindow, kbw60, progressiveSeasonWindows, riskFromStage, seasonWindows, stageAt, sumOf, type KbwWindow } from "../lib/kbw";
import { coversRange, seasonWeatherFor, type DailySeries } from "../lib/openMeteo";
import type { SourceEntry } from "../lib/sources";
import { containsPoint } from "../lib/geo";
import { ParcelMap, type MapParcel, type MapZone } from "../map/ParcelMap";
import { PrintReport } from "../report/PrintReport";
import { ReportDocumentBuilder } from "../report/reportBuilder";
import { dec, ha, mm, timePl } from "./shared";

const NAV: { id: string; label: string; icon: IconName }[] = [
  { id: "ocena", label: "Ocena", icon: "gauge" }, { id: "przyczyny", label: "Przyczyny", icon: "layers" },
  { id: "retencja", label: "Retencja", icon: "sprout" }, { id: "nawadnianie", label: "Woda", icon: "drop" }, { id: "raport", label: "Raport", icon: "file" },
];
const NO_LEVERS: Levers = { addMm: 0, etCut: 0 };

/** Water-state colour token of a stage, for the frame of an expanded parcel. */
function stateVar(n: number | null | undefined): string {
  if (n === null || n === undefined) return "var(--line-strong)";
  return `var(--${n < 0 ? `dry-${-n}` : n > 0 ? `wet-${n}` : "optimum"})`;
}

/** Drawable /api/analyze zones of a parcel (empty geometries of narrow strips are dropped, so the flat fill stays). */
function zonesOf(resp: AnalysisResponse | null): MapZone[] | null {
  return zonesFromFeatures(resp?.map_layers?.zones);
}

function seasonReplayDates(season: number, today: Iso): Iso[] {
  const start = isoOf(season, 4, 1);
  const end = minIso(isoOf(season, 9, 30), today);
  if (start > end) return [];
  const out: Iso[] = [];
  for (let cur = start; cur <= end; cur = addDays(cur, 1)) out.push(cur);
  return out;
}

interface ReplayState {
  active: boolean;
  playing: boolean;
  loading: boolean;
  error: string | null;
  idx: number;
  speed: 1 | 2;
  extra: Record<string, DailySeries>;
}
const REPLAY_INIT: ReplayState = { active: false, playing: false, loading: false, error: null, idx: 0, speed: 1, extra: {} };

/**
 * URL: dzialki=id~crop,id~crop (fixed order of the list), aktywna=id (expanded parcel), data=ISO.
 * Old links (dzialka, uprawa, inne) still open.
 */
function readList(sp: URLSearchParams): { items: { id: string; crop: CropCode }[]; activeId: string } {
  const parse = (raw: string | null, fallback: CropCode) => (raw ?? "").split(",").map((x) => x.trim()).filter(Boolean)
    .map((e) => { const [id, c] = e.split("~"); return { id: id!, crop: isCropCode(c) ? c : fallback }; });
  const legacyCrop = isCropCode(sp.get("uprawa")) ? (sp.get("uprawa") as CropCode) : DEFAULT_CROP;
  let items = parse(sp.get("dzialki"), DEFAULT_CROP);
  if (!items.length && sp.get("dzialka")) items = [{ id: sp.get("dzialka")!, crop: legacyCrop }, ...parse(sp.get("inne"), legacyCrop)];
  items = items.filter((x, i) => items.findIndex((y) => y.id === x.id) === i);
  const want = sp.get("aktywna") ?? sp.get("dzialka");
  return { items, activeId: items.some((x) => x.id === want) ? want! : items[0]?.id ?? "" };
}

export function Analysis() {
  const [sp, setSp] = useSearchParams();
  const { items, activeId } = readList(sp);
  const parcelId = activeId;
  const crop: CropCode = items.find((x) => x.id === activeId)?.crop ?? DEFAULT_CROP;
  const rawDate = sp.get("data");
  const { min, max } = dateBounds();
  const date: Iso = isIso(rawDate) && rawDate >= min && rawDate <= max ? rawDate : todayIso();
  const st = useAnalysis({ parcelId, crop, date });
  const key = items.map((x) => `${x.id}~${x.crop}`).join(",");
  const stableItems = useMemo(() => items, [key]);
  const portfolio = usePortfolio(stableItems, date);

  const [active, setActive] = useState("ocena");
  const [showZones, setShowZones] = useState(true);
  const [pickedByParcel, setPickedByParcel] = useState<Record<string, string[]>>({});
  const picked = useMemo(() => new Set(pickedByParcel[activeId] ?? []), [pickedByParcel, activeId]);
  const [whatIfData, setWhatIfData] = useState<WhatIfScenarioResult | null>(null);
  const [replay, setReplay] = useState<ReplayState>(REPLAY_INIT);
  const replaySeq = useRef(0);

  const write = (next: { id: string; crop: CropCode }[], nextActive: string, extra: Record<string, string> = {}) => {
    const q = new URLSearchParams({ dzialki: next.map((x) => `${x.id}~${x.crop}`).join(","), aktywna: nextActive, data: date, ...extra });
    setSp(q);
  };

  const parcel = st.parcel.status === "ready" ? st.parcel.data : null;
  const land = st.land.status === "ready" ? st.land.data : null;
  const backend = st.backend.status === "ready" ? st.backend.data : null;
  const backendErr = st.backend.status === "error" ? st.backend : null;
  const backendRefused = !!backendErr && (backendErr.code === "not_arable" || backendErr.httpStatus === 422);
  const backendNonFatalError = backendErr && !backendRefused ? backendErr.message : null;
  const local = useMemo(() => withBackend(st.local, backend), [st.local, backend]);
  const rl = st.rl.status === "ready" ? st.rl.data : null;
  const weatherFailed = st.weather.status === "error" ? st.weather.message : null;
  const rlCompare = st.rlCompare.status === "ready" ? st.rlCompare.data : null;
  const showIrr = !!local && ((!local.offSeason && (rl?.status === "ok" || !!local.irrigation)) || !!rlCompare?.strategies?.length);

  const today = todayIso();
  const replayDates = useMemo(() => (local ? seasonReplayDates(local.season, today) : []), [local?.season, today]);
  const replayDate = replay.active && replayDates.length ? replayDates[Math.min(replay.idx, replayDates.length - 1)]! : null;

  useEffect(() => {
    replaySeq.current++;
    setReplay(REPLAY_INIT);
    setWhatIfData(null);
  }, [activeId, date]);

  useEffect(() => {
    if (!replay.active || !replay.playing || replayDates.length <= 1) return;
    const stepMs = replay.speed === 2 ? 35 : 75;
    let raf = 0;
    let last = performance.now();
    let acc = 0;
    const tick = (now: number) => {
      acc += Math.min(250, now - last);
      last = now;
      const adv = Math.floor(acc / stepMs);
      if (adv > 0) {
        acc -= adv * stepMs;
        setReplay((r) => {
          if (!r.active || !r.playing) return r;
          const next = Math.min(replayDates.length - 1, r.idx + adv);
          return { ...r, idx: next, playing: next < replayDates.length - 1 };
        });
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [replay.active, replay.playing, replay.speed, replayDates.length]);

  useEffect(() => {
    const els = NAV.map((n) => document.getElementById(n.id)).filter((e): e is HTMLElement => !!e);
    const io = new IntersectionObserver((es) => { const v = es.filter((e) => e.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0]; if (v) setActive(v.target.id); }, { rootMargin: "-20% 0px -60% 0px" });
    els.forEach((e) => io.observe(e));
    return () => io.disconnect();
  }, [st.parcel.status, !!local, showIrr, activeId]);

  const rowItems = useMemo(
    () => portfolio.map((r) => ({ id: r.id, crop: r.crop, eligible: r.parcel.status === "ready" && r.land?.kind !== "nonAgri" })),
    [portfolio],
  );
  const rowAnalyses = useRowAnalyses(rowItems, date);

  const rowBackend = (r: PortfolioRow): AnalysisResponse | null => {
    const own = rowAnalyses[r.id]?.data ?? null;
    return r.id === activeId ? own ?? backend : own;
  };
  const rowBackendLoading = (r: PortfolioRow): boolean => {
    if (r.id === activeId) return backendEnabled && st.backend.status === "loading" && !rowAnalyses[r.id]?.data;
    return !!rowAnalyses[r.id]?.loading;
  };

  // Joint zones of all listed parcels on one grid. Base state: backend water_state when in, else the local stage.
  const jointInputs = useMemo<JointZoneInput[]>(() => portfolio.flatMap((r) => {
    if (r.parcel.status === "error") return [];
    if (r.parcel.status === "loading" || !r.land) return [{ id: r.id, crop: r.crop, state: null, pending: true }];
    if (r.land.kind === "nonAgri") return [];
    const ws = rowBackend(r)?.assessment?.water_state;
    const state = typeof ws === "number" && Number.isFinite(ws) ? ws : r.local?.stage ?? (r.id === activeId ? st.local?.stage : null) ?? null;
    return [{ id: r.id, crop: r.crop, state, pending: state === null && !r.error }];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [portfolio, rowAnalyses, backend, activeId, st.local?.stage]);
  const joint = useJointZones(jointInputs);

  /** Fallback order per parcel: joint zones, then the zones of its own /api/analyze, then (in the histogram) the single-bin stage. */
  const rowZones = (r: PortfolioRow): MapZone[] | null => joint.zones[r.id] ?? zonesOf(rowBackend(r));
  /** The zones indicator shows only while the parcel has no zones at all. */
  const rowZonesLoading = (r: PortfolioRow): boolean => !rowZones(r)?.length && (joint.loading || rowBackendLoading(r));
  const zones = useMemo(() => joint.zones[activeId] ?? zonesOf(backend), [joint.zones, activeId, backend]);
  const zonesLoading = backendEnabled && !zones?.length && (joint.loading || st.backend.status === "loading");

  const activeLand = landForCalc(st.land);
  const activeCat = activeLand?.kind === "mineral" ? activeLand.category : null;
  const activeThresholds = useMemo(() => thresholdsFor(cropOf(crop).iung, activeCat), [crop, activeCat]);
  const replaySeries = replay.active ? (replay.extra[activeId] ?? (st.weather.status === "ready" ? st.weather.data : null)) : null;

  const replayTimeline = useMemo<TimelineDay[] | null>(() => {
    if (!replay.active || !replaySeries || !replayDate) return null;
    const out: TimelineDay[] = [];
    for (let i = -44; i <= 0; i++) {
      const d = addDays(replayDate, i);
      out.push({ date: d, value: stageAt(replaySeries, d, activeThresholds, activeCat), forecast: d > today });
    }
    return out;
  }, [replay.active, replaySeries, replayDate, activeThresholds, activeCat, today]);

  const replayStage = useMemo(() => {
    if (!replay.active || !replaySeries || !replayDate) return null;
    return stageAt(replaySeries, replayDate, activeThresholds, activeCat);
  }, [replay.active, replaySeries, replayDate, activeThresholds, activeCat]);

  const replayMetrics = useMemo(() => {
    if (!replay.active || !replaySeries || !replayDate) return null;
    const k = kbw60(replaySeries, replayDate);
    return {
      p30: sumOf(replaySeries.p, replaySeries, addDays(replayDate, -29), replayDate),
      et030: sumOf(replaySeries.et0, replaySeries, addDays(replayDate, -29), replayDate),
      kbw60: k === null ? null : Math.round(k),
    };
  }, [replay.active, replaySeries, replayDate]);

  const replayWin = useMemo(() => (replay.active && replayDate ? (activeWindow(replayDate) ?? 0) : null), [replay.active, replayDate]);
  const replayWindows = useMemo(() => {
    if (!replay.active || !replaySeries || !replayDate || !local) return null;
    return progressiveSeasonWindows(replaySeries, local.season, local.thresholds, replayDate);
  }, [replay.active, replaySeries, replayDate, local]);
  const replayScaleWindows = useMemo(() => {
    if (!replay.active || !replaySeries || !local || !replayDates.length) return null;
    return seasonWindows(replaySeries, local.season, local.thresholds, replayDates[replayDates.length - 1]!);
  }, [replay.active, replaySeries, local, replayDates]);

  const replayRisk = useMemo<LocalResult["risk"] | null>(() => {
    if (!replay.active || replayStage === null) return null;
    const r = riskFromStage(replayStage);
    const aw = replayWin !== null && replayWindows ? replayWindows[replayWin] : undefined;
    const veto = !!aw?.drought;
    const vetoReason = veto && aw && aw.kbw !== null && aw.threshold !== null ? `KBW okna ${aw.label}: ${mm(aw.kbw)} mm, próg IUNG ${mm(aw.threshold)} mm.` : null;
    return { ...r, veto, vetoReason };
  }, [replay.active, replayStage, replayWin, replayWindows]);

  const stageOf = (r: PortfolioRow) => {
    if (replay.active && replayDate) {
      const s = replay.extra[r.id] ?? r.weather ?? (r.id === activeId && st.weather.status === "ready" ? st.weather.data : null);
      const l = r.id === activeId ? activeLand : r.land;
      if (!s || !l || l.kind === "nonAgri") return null;
      const cat = l.kind === "mineral" ? l.category : null;
      return stageAt(s, replayDate, thresholdsFor(cropOf(r.crop).iung, cat), cat);
    }
    const rb = rowBackend(r)?.assessment?.water_state;
    if (rb !== undefined) return balStep(rb);
    return r.id === activeId ? local?.stage ?? r.local?.stage ?? null : r.local?.stage ?? null;
  };

  const allZones = useMemo<MapZone[] | null>(() => {
    const list = portfolio.flatMap((r) => rowZones(r) ?? []);
    return list.length ? list : null;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [portfolio, activeId, backend, rowAnalyses, joint.zones, date]);

  const hasMultiZones = useMemo(
    () => portfolio.some((r) => (rowZones(r)?.length ?? 0) > 1),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [portfolio, activeId, backend, rowAnalyses, joint.zones, date],
  );

  /** One class order (dry→wet) shared by every row histogram and the detail histogram: a class keeps its slot across parcels. */
  const zoneOrder = useMemo(() => zoneClassOrder(portfolio.flatMap((r) => (r.land?.kind === "nonAgri" ? [] : [{ zones: replay.active ? null : rowZones(r), fallbackStage: stageOf(r) }]))),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [portfolio, activeId, local?.stage, backend, rowAnalyses, joint.zones, replay.active, replay.extra, replayDate, activeLand, st.weather, date]);

  /** Every parcel on the map, coloured by its water state; parcels with loaded zones render them cleanly without overlapping fill/hatch. */
  const mapParcels = useMemo<MapParcel[]>(() => portfolio.flatMap((r) => {
    if (r.parcel.status !== "ready") return [];
    const hasZ = !replay.active && showZones && !!(rowZones(r)?.length);
    return [{
      id: r.id,
      geometry: r.parcel.data.geometry,
      value: stageOf(r),
      active: r.id === activeId,
      hasZones: hasZ,
    }];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [portfolio, activeId, local?.stage, backend, rowAnalyses, joint.zones, showZones, replay.active, replay.extra, replayDate, activeLand, st.weather, date]);

  if (!parcelId) return <Navigate to="/" replace />;
  const pdf = parcel && backendCrop(crop) ? reportPdfUrl({ parcel_id: parcel.id, crop: backendCrop(crop)!, analysis_date: date }) : null;
  const go = (id: string) => document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });

  const show = (id: string) => write(items, id);
  /** A click on the map selects the listed parcel under the cursor. */
  const pickListed = (lon: number, lat: number) => {
    const hit = portfolio.find((r) => r.parcel.status === "ready" && containsPoint(r.parcel.data.geometry, [lon, lat]));
    if (hit && hit.id !== activeId) show(hit.id);
  };
  const setCropOf = (id: string, c: CropCode) => write(items.map((x) => (x.id === id ? { ...x, crop: c } : x)), activeId);
  const togglePick = (id: string) => setPickedByParcel((m) => {
    const cur = m[activeId] ?? [];
    const next = cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id];
    return { ...m, [activeId]: next };
  });

  const startReplay = async () => {
    if (!local || replayDates.length < 2) return;
    if (replay.active && Object.keys(replay.extra).length) {
      setReplay((r) => ({ ...r, playing: !r.playing, idx: !r.playing && r.idx >= replayDates.length - 1 ? 0 : r.idx }));
      return;
    }
    const seq = ++replaySeq.current;
    setReplay((r) => ({ ...r, loading: true, error: null }));
    const from = isoOf(local.season - 1, 12, 5);
    const to = replayDates[replayDates.length - 1]!;
    try {
      const readyRows = portfolio.filter((r): r is PortfolioRow & { parcel: { status: "ready"; data: NonNullable<typeof parcel> } } =>
        r.parcel.status === "ready" && r.land?.kind !== "nonAgri",
      );
      const entries = await Promise.all(readyRows.map(async (r) => {
        const existing = r.weather ?? (r.id === activeId && st.weather.status === "ready" ? st.weather.data : null);
        if (existing && coversRange(existing, from, to)) return [r.id, existing] as const;
        const fetched = await seasonWeatherFor(r.parcel.data.centroid[1], r.parcel.data.centroid[0], local.season);
        return [r.id, fetched] as const;
      }));
      if (seq !== replaySeq.current) return;
      setReplay((r) => ({ active: true, playing: true, loading: false, error: null, idx: 0, speed: r.speed, extra: Object.fromEntries(entries) }));
    } catch (e) {
      if (seq !== replaySeq.current) return;
      setReplay((r) => ({ ...r, loading: false, error: message(e) }));
    }
  };

  const miniTimelineOf = (r: PortfolioRow): { days: TimelineDay[]; selected: Iso } | null => {
    if (replay.active && replayDate) {
      const s = replay.extra[r.id] ?? r.weather ?? (r.id === activeId && st.weather.status === "ready" ? st.weather.data : null);
      const l = r.id === activeId ? activeLand : r.land;
      if (!s || !l || l.kind === "nonAgri") return null;
      const cat = l.kind === "mineral" ? l.category : null;
      const th = thresholdsFor(cropOf(r.crop).iung, cat);
      const days: TimelineDay[] = [];
      for (let i = -29; i <= 0; i++) {
        const d = addDays(replayDate, i);
        days.push({ date: d, value: stageAt(s, d, th, cat), forecast: d > today });
      }
      return { days, selected: replayDate };
    }
    return r.local ? { days: r.local.timeline, selected: r.local.date } : null;
  };

  const replayView: ReplayView = {
    active: replay.active,
    playing: replay.playing,
    loading: replay.loading,
    error: replay.error,
    idx: replay.idx,
    speed: replay.speed,
    dates: replayDates,
    date: replayDate,
    stage: replayStage,
    risk: replayRisk,
    metrics: replayMetrics,
    timeline: replayTimeline,
    windows: replayWindows,
    scaleWindows: replayScaleWindows,
    win: replayWin,
    onStart: () => void startReplay(),
    onStop: () => setReplay(REPLAY_INIT),
    onToggleSpeed: () => setReplay((r) => ({ ...r, speed: r.speed === 1 ? 2 : 1 })),
    onScrub: (idx) => setReplay((r) => ({ ...r, playing: false, idx: Math.max(0, Math.min(replayDates.length - 1, idx)) })),
    onApplyDate: () => { if (replayDate && replayDate >= min && replayDate <= max) write(items, activeId, { data: replayDate }); },
  };

  const reportDoc = useMemo(() => {
    const builder = new ReportDocumentBuilder().setMetadata(date, timePl(new Date()));
    for (const r of portfolio) {
      const isAct = r.id === activeId;
      const rowParcel = r.parcel.status === "ready" ? r.parcel.data : (isAct ? parcel : null);
      const rowLand = isAct ? activeLand : r.land;
      const rowNonAgri = rowLand?.kind === "nonAgri" && rowParcel
        ? `${rowLand.label}: działka ${rowParcel.number} nie jest gruntem rolnym. Rusałka analizuje grunty orne, łąki, pastwiska i sady.`
        : isAct && backendRefused && backendErr
          ? backendErr.message
          : null;
      builder.addParcelSheet({
        parcelId: r.id,
        parcel: rowParcel,
        crop: r.crop,
        land: rowLand,
        local: isAct ? local : r.local,
        backend: isAct ? backend : null,
        rl: isAct ? rl : null,
        rlCompare: isAct ? rlCompare : null,
        whatIfBackend: isAct ? whatIfData : null,
        pickedMeasures: new Set(pickedByParcel[r.id] ?? []),
        sources: isAct ? st.sources : undefined,
        nonAgriMessage: rowNonAgri,
      });
    }
    return builder.build();
  }, [portfolio, activeId, date, parcel, activeLand, local, backend, backendRefused, st.backend, rl, rlCompare, whatIfData, pickedByParcel, st.sources]);

  const body = (
    <>
      {st.parcel.status === "error" ? <ErrorCard text={st.parcel.message} /> : null}
      {land?.kind === "nonAgri" && parcel ? <ErrorCard text={`${land.label}: działka ${parcel.number} nie jest gruntem rolnym. Rusałka analizuje grunty orne, łąki, pastwiska i sady.`} /> : null}
      {backendRefused && backendErr && land?.kind !== "nonAgri" ? <ErrorCard text={backendErr.message} /> : null}
      {st.parcel.status !== "error" && land?.kind !== "nonAgri" && !backendRefused ? (
        <>
          <section id="ocena" className="rs-section">
            {local ? (
              <Assessment
                local={local}
                components={backendComponents(backend?.assessment)}
                zones={zones}
                zoneOrder={zoneOrder}
                parcelAreaHa={parcel?.areaHa ?? null}
                zonesLoading={zonesLoading}
                replay={replayView}
                onPickDate={(d) => write(items, activeId, { data: d })}
              />
            ) : weatherFailed ? <ErrorCard text={weatherFailed} retry /> : <Skeleton lines={["Stan wody", "Okna IUNG", "10 lat"]} />}
          </section>
          <section id="przyczyny" className="rs-section">
            <h3 className="ar-section-title">{causesTitle(local)}</h3>
            <Causes local={local} backend={backend} land={landForCalc(st.land)} weatherFailed={!!weatherFailed} />
          </section>
          <section id="retencja" className="rs-section">
            <h3 className="ar-section-title">Co możesz zrobić</h3>
            <Measures parcelId={activeId} local={local} st={st} backend={backend} crop={crop} date={date} weatherFailed={!!weatherFailed} picked={picked} onPick={togglePick} onWhatIfData={setWhatIfData} />
          </section>
          {local && showIrr ? (
            <section id="nawadnianie" className="rs-section">
              <h3 className="ar-section-title">Nawadnianie</h3>
              <Irrigation local={local} rl={rl} compare={rlCompare} />
            </section>
          ) : null}
        </>
      ) : null}
    </>
  );

  return (
    <div className="rs-analysis">
      <header className="rs-ahead">
        <Link to={`/?dzialki=${encodeURIComponent(key)}`} aria-label="Wróć do mapy"><Wordmark /></Link>
        <p className="rs-ahead__parcel ar-muted">{items.length === 1 ? "1 działka" : `${items.length} działki`} · {formatLong(date)}</p>
        {items.length === 1 && pdf ? <Button variant="light" arrow="down" href={pdf}>Pobierz raport PDF</Button> : <Button variant="light" arrow="down" onClick={() => window.print()}>Pobierz raport PDF</Button>}
      </header>

      <div className="rs-grid">
        <aside className="rs-side">
          <ParcelMap className="rs-side__map" parcels={mapParcels} bbox={parcel?.bbox} zones={showZones && !replay.active ? allZones : null} padding={{ top: 70, bottom: 40 }}
            overlay={!replay.active && hasMultiZones ? <LayerSwitch label="Widok stref na działce" value={showZones ? "zones" : "flat"} onChange={(v) => setShowZones(v === "zones")} options={[{ id: "zones", label: "Strefy na działce" }, { id: "flat", label: "Jednolity kolor" }]} /> : null}
            onPick={pickListed} />
        </aside>

        <main className="rs-main">
          <DateBar className="rs-recalc" date={date} onDate={(d) => write(items, activeId, { data: d })} />
          <div className="rs-listtitle">
            <h2 className="ar-section-title">Twoje działki</h2>
            <Link className="rs-listtitle__link" to={`/?dzialki=${encodeURIComponent(key)}`}>Zmień wybór</Link>
          </div>
          <ol className="rs-plist" aria-label="Wybierz działkę">
            {portfolio.map((r) => {
              const p = r.parcel.status === "ready" ? r.parcel.data : null;
              const on = r.id === activeId;
              const stage = stageOf(r);
              const mini = miniTimelineOf(r);
              const rZones = rowZones(r);
              return (
                <li key={r.id} className={`rs-pitem${on ? " is-on" : ""}`} style={{ "--state": stateVar(stage) } as CSSProperties}>
                  <div className="rs-pitem__row" role="button" tabIndex={0} aria-pressed={on}
                    onClick={() => show(r.id)} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); show(r.id); } }}>
                    <div className="rs-pitem__name">
                      <b>{p ? `Działka ${p.number}, ${p.region}` : r.id}</b>
                    </div>
                    <div className="rs-pitem__state">{r.local || (on && local) || stage !== null ? <WaterBalance size="sm" value={stage} /> : <span className="ar-muted">{r.land?.kind === "nonAgri" ? "grunt nierolny" : r.error ?? "…"}</span>}</div>
                    {mini ? <div className="rs-pitem__mini"><StateTimeline compact days={mini.days} selected={mini.selected} today={todayIso()} /></div> : <span />}
                  </div>
                  {r.land?.kind !== "nonAgri" && (stage !== null || rZones?.length) ? (
                    <ZoneHistogram compact zones={replay.active ? null : rZones} order={zoneOrder} fallbackStage={stage} areaHa={p?.areaHa} loading={!replay.active && rowZonesLoading(r)} />
                  ) : (
                    <p className="rs-zhist rs-zhist--compact rs-zhist__note ar-muted">
                      {r.land?.kind === "nonAgri" ? "Grunt nierolny: bez stref wilgotności." : r.parcel.status === "error" || r.error ? "Brak danych do stref wilgotności." : "Liczę stan wody…"}
                    </p>
                  )}
                </li>
              );
            })}
          </ol>

          <section className={cx("rs-detail", st.updating && "is-updating")} aria-label="Wybrana działka" style={{ "--state": stateVar(replay.active ? replayStage : local?.stage) } as CSSProperties}>
            <div className="rs-detail__head">
              <div>
                <h2 className="ar-section-title">{parcel ? `Działka ${parcel.number}, ${parcel.region}` : parcelId}</h2>
                {parcel ? <p className="ar-muted rs-detail__sub">gm. {parcel.commune}, pow. {parcel.county} · {ha(parcel.areaHa)} ha · {parcel.id}</p> : null}
              </div>
              <label className="rs-detail__crop"><span className="ar-form__label">Uprawa</span>
                <CropSelect value={crop} onChange={(c) => setCropOf(activeId, c)} label={`Uprawa na działce ${parcel?.number ?? parcelId}`} />
              </label>
            </div>
            {st.updating ? <p className="rs-updating" role="status" aria-live="polite">Aktualizuję dane dla {formatLong(date)}…</p> : null}
            {backendNonFatalError ? <p className="rs-updating" role="status">{backendNonFatalError} (wyświetlam ocenę lokalną).</p> : null}
            {body}
          </section>

          <section id="raport" className="rs-section">
            <h2 className="ar-section-title">Źródła danych</h2>
            <Sources sources={st.sources} backend={backend} rlSources={[...(rl?.sources ?? []), ...(rlCompare?.sources ?? [])]} elapsed={st.elapsedMs} />
          </section>
          {import.meta.env.DEV ? <DevHooks st={st} /> : null}
        </main>
      </div>
      <FloatingNav className="rs-fnav" items={NAV.filter((n) => n.id !== "nawadnianie" || showIrr)} active={active} onChange={go} />
      <PrintReport doc={reportDoc} />
    </div>
  );
}

/** When the backend answers, its formula (FAO-56, satellite/ML, soil, terrain) decides the state; the browser estimate stays as fallback. */
function withBackend(local: LocalResult | null, backend: AnalysisResponse | null): LocalResult | null {
  const a = backend?.assessment;
  if (!local || !a || (a.water_state === undefined && !a.water_state_timeline)) return local;
  const stage = a.water_state !== undefined ? balStep(a.water_state) : local.stage;
  const r = stage === null ? null : riskFromStage(stage);
  const veto = a.water_risk?.veto_triggered ?? local.risk.veto;
  const timeline = a.water_state_timeline
    ? a.water_state_timeline.map((d) => ({ ...d, value: balStep(d.value) }))
    : local.timeline;
  return {
    ...local,
    stage,
    risk: r ? { level: (a.water_risk?.level as typeof local.risk.level) ?? r.level, side: r.side, veto, vetoReason: a.water_risk?.veto_reasons?.join(" ") || local.risk.vetoReason } : local.risk,
    caption: a.summary_text ?? local.caption,
    confidence: a.confidence ? { level: a.confidence.level, detail: [...(a.confidence.resolution_warnings ?? []), ...(a.confidence.missing_factors ?? [])].join(" ") || "Wynik z modelu serwera." } : local.confidence,
    timeline,
  };
}

function backendComponents(assessment: AnalysisResponse["assessment"] | undefined): NonNullable<AnalysisResponse["assessment"]>["components"] {
  if (!assessment) return undefined;
  if (assessment.components?.length) return assessment.components;
  const scoreComponents = assessment.water_score?.components;
  if (!scoreComponents?.length) return undefined;
  return scoreComponents.map((c) => ({
    key: c.name,
    label: c.label,
    value: c.value != null ? `${Math.round(c.value)}/100` : "brak danych",
    weight: c.weight_used > 0 ? c.weight_used : undefined,
    detail: c.explanation ?? undefined,
  }));
}

function timelineSummary(local: LocalResult): string | null {
  const today = todayIso();
  const in7 = local.timeline.find((d) => d.date === addDays(today, 7));
  const step = balStep(in7?.value);
  if (step === null) return null;
  return `Za 7 dni: ${BAL[String(step)]!.toLowerCase()} (prognoza)`;
}

function causesTitle(local: LocalResult | null): string {
  if (!local || local.stage === null || local.stage < 0) return "Dlaczego brakuje wody";
  return local.stage > 0 ? "Skąd nadmiar wody" : "Co wpływa na wodę na działce";
}

function ErrorCard({ text, retry }: { text: string; retry?: boolean }) {
  return (
    <div className="ar-card rs-error">
      <p>{text}</p>
      <div className="rs-row">
        {retry ? <Button variant="light" onClick={() => location.reload()}>Spróbuj ponownie</Button> : null}
        <Link className="ar-btn ar-btn--light" to="/">Wróć do mapy</Link>
      </div>
    </div>
  );
}
function Skeleton({ lines }: { lines: string[] }) {
  return <div className="rs-skel" aria-busy="true" aria-label="Wczytuję dane">{lines.map((l) => <div key={l} className="ar-card rs-skel__card"><span className="rs-skel__bar" /><span className="rs-skel__bar rs-skel__bar--short" /></div>)}</div>;
}

interface ReplayView {
  active: boolean;
  playing: boolean;
  loading: boolean;
  error: string | null;
  idx: number;
  speed: 1 | 2;
  dates: Iso[];
  date: Iso | null;
  stage: number | null;
  risk: LocalResult["risk"] | null;
  metrics: { p30: number | null; et030: number | null; kbw60: number | null } | null;
  timeline: TimelineDay[] | null;
  windows: KbwWindow[] | null;
  scaleWindows: KbwWindow[] | null;
  win: number | null;
  onStart: () => void;
  onStop: () => void;
  onToggleSpeed: () => void;
  onScrub: (idx: number) => void;
  onApplyDate: () => void;
}

function Assessment({ local, onPickDate, components, zones, zoneOrder, parcelAreaHa, zonesLoading, replay }: {
  local: LocalResult;
  onPickDate: (d: Iso) => void;
  components?: NonNullable<AnalysisResponse["assessment"]>["components"];
  zones: MapZone[] | null;
  zoneOrder: number[];
  parcelAreaHa: number | null;
  zonesLoading: boolean;
  replay: ReplayView;
}) {
  const [mode, setMode] = useState<"day" | "month">("day");
  const { min, max } = dateBounds();
  const ys = mode === "day" ? local.years : local.yearsMonth;
  const [y, m] = parts(local.date);
  const span = `${ys[0]?.year}–${ys[ys.length - 1]?.year}`;
  const thLabel = local.category && local.thresholds ? `Próg IUNG: ${local.crop.label.toLowerCase()}, gleba kat. ${local.category}` : undefined;
  const heroStage = replay.active ? replay.stage : local.stage;
  const heroRisk = replay.active && replay.risk ? replay.risk : local.risk;
  const heroCaption = replay.active && replay.date ? `Odtwarzanie sezonu ${local.season} · ${formatLong(replay.date)}` : local.caption;
  return (
    <>
      <div className="ar-card rs-hero">
        <WaterBalance value={heroStage} caption={heroCaption} />
        <ZoneHistogram zones={replay.active ? null : zones} order={zoneOrder} fallbackStage={heroStage} areaHa={parcelAreaHa} loading={!replay.active && zonesLoading} />
        <div className="rs-status">
          {heroStage !== null ? <RiskChip level={heroRisk.level} side={heroRisk.side} veto={heroRisk.veto} vetoReason={heroRisk.vetoReason} /> : null}
          <ConfidenceBadge level={local.confidence.level} detail={local.confidence.detail} />
        </div>
        <Why local={local} components={components} />
      </div>
      <div className="ar-card">
        {replay.dates.length >= 2 ? (
          <div className="rs-replay">
            {!replay.active ? (
              <div className="rs-replay__bar">
                <Button variant="light" size="sm" icon="play" disabled={replay.loading} onClick={replay.onStart}>
                  {replay.loading ? "Wczytuję sezon…" : `Odtwórz sezon ${local.season}`}
                </Button>
                {replay.error ? <span className="ar-muted">{replay.error}</span> : null}
              </div>
            ) : (
              <div className="rs-replay__bar">
                <Button
                  variant="icon"
                  size="sm"
                  icon={replay.playing ? "pause" : "play"}
                  label={replay.playing ? "Wstrzymaj odtwarzanie" : "Wznów odtwarzanie"}
                  onClick={replay.onStart}
                />
                <b className="rs-replay__date">{formatLong(replay.date!)}</b>
                <input
                  className="rs-replay__range"
                  type="range"
                  min={0}
                  max={replay.dates.length - 1}
                  value={replay.idx}
                  onChange={(e) => replay.onScrub(Number(e.target.value))}
                  aria-label="Dzień sezonu"
                />
                <Pill pressed={replay.speed === 2} onClick={replay.onToggleSpeed}>{replay.speed}×</Pill>
                <Button variant="outline" size="sm" onClick={replay.onApplyDate}>Ustaw datę</Button>
                <Button variant="icon" size="sm" icon="close" label="Zakończ odtwarzanie" onClick={replay.onStop} />
              </div>
            )}
          </div>
        ) : null}
        {replay.active && replay.timeline && replay.date ? (
          <StateTimeline
            title={`Okno ruchome: ostatnie 45 dni (${local.season})`}
            days={replay.timeline}
            selected={replay.date}
            today={todayIso()}
            sliding
            onPick={(d) => { const i = replay.dates.indexOf(d); if (i >= 0) replay.onScrub(i); }}
            summary={formatLong(replay.date)}
          />
        ) : (
          <StateTimeline title={local.timeline.some((d) => d.forecast) ? "Stan wody: ostatnie 45 dni i prognoza" : "Stan wody dzień po dniu"} days={local.timeline} selected={local.date} today={todayIso()} onPick={onPickDate} summary={timelineSummary(local)} />
        )}
      </div>
      <div className="rs-metrics">
        <MetricTile icon="drop" label="Opad 30 dni" value={mm(replay.active && replay.metrics ? replay.metrics.p30 : local.metrics.p30)} unit="mm" />
        <MetricTile icon="sun" label="Parowanie ET₀ 30 dni" value={mm(replay.active && replay.metrics ? replay.metrics.et030 : local.metrics.et030)} unit="mm" />
        <MetricTile icon="gauge" label="KBW 60 dni" value={mm(replay.active && replay.metrics ? replay.metrics.kbw60 : local.kbw60)} unit="mm" />
      </div>
      <div className="ar-card">
        <KbwWindows
          title={`Sezon ${local.season} w oknach IUNG`}
          windows={replay.active && replay.windows ? replay.windows : local.windows}
          scaleWindows={replay.active ? replay.scaleWindows ?? undefined : undefined}
          active={replay.active ? replay.win : local.active}
          thresholdLabel={thLabel}
          onPickWindow={(idx) => { const w = local.windows[idx]; if (w && w.end >= min && w.end <= max) onPickDate(w.end); }}
        />
      </div>
      <div className="ar-card">
        <YearCompare
          title={mode === "day" ? `${formatDayMonth(local.date)} w latach ${span}` : `${monthName(m)} (stan na koniec miesiąca) w latach ${span}`}
          years={ys}
          highlightYear={y}
          onPickYear={(yr) => { const hit = ys.find((x) => x.year === yr); if (hit && hit.date >= min && hit.date <= max) onPickDate(hit.date); }}
        >
          <div className="ar-form__chips">
            <Pill pressed={mode === "day"} onClick={() => setMode("day")}>Ten dzień</Pill>
            <Pill pressed={mode === "month"} onClick={() => setMode("month")}>Cały miesiąc</Pill>
          </div>
        </YearCompare>
      </div>
    </>
  );
}

/**
 * Distribution histogram of intra-parcel wetness zones strictly within parcel boundaries; falls back immediately to parcel-wide stage before zones arrive.
 * `order` is the class order shared by all parcels: absent classes keep their slot (0 %), so a class sits in the same place in every row.
 */
function ZoneHistogram({ zones, order, fallbackStage, areaHa, loading, compact }: {
  zones: MapZone[] | null;
  order?: number[];
  fallbackStage: number | null;
  areaHa?: number | null;
  loading?: boolean;
  compact?: boolean;
}) {
  const isFallback = !zones?.length;
  const own = zoneBins(zones, fallbackStage, areaHa);
  if (!own.length) return null;
  const bins = alignBins(own, order);
  const present = own.filter((b) => b.pct > 0).length;

  if (compact) {
    return (
      <div className="rs-zhist rs-zhist--compact" aria-label="Histogram wilgotności na działce">
        <div className="rs-zhist__stack" aria-hidden="true">
          {bins.filter((b) => b.pct > 0).map((b) => {
            const label = BAL[String(b.step)] ?? String(b.step);
            return <span key={b.step} className={cx("rs-zhist__seg", `ar-b-${balKey(b.step)}`)} style={{ width: `${b.pct}%` }} title={`${label} · ${dec(b.pct, 0)}%`} />;
          })}
        </div>
        <div className="rs-zhist__chips">
          {bins.map((b) => {
            const cls = `ar-b-${balKey(b.step)}`;
            const label = BAL[String(b.step)] ?? String(b.step);
            return (
              <span key={b.step} className={cx("rs-zhist__chip", b.pct <= 0 && "is-empty")} title={`${label} · ${dec(b.pct, 0)}%`}>
                <i className={cx("rs-zhist__sw", cls)} aria-hidden="true" />
                <span className="rs-zhist__name">{label}</span> <b>{dec(b.pct, 0)}%</b>
              </span>
            );
          })}
          {loading && isFallback ? <span className="ar-muted rs-zhist__wait">· strefy…</span> : null}
        </div>
      </div>
    );
  }

  const meta = loading && isFallback
    ? "obliczam strefy…"
    : present === 1
      ? "jednolity stan"
      : `${present} ${present >= 2 && present <= 4 ? "strefy" : "stref"}`;
  const note = loading && isFallback
    ? "Wstępny rozkład z bilansu działki; szczegółowe strefy z modelu terenu ładują się w tle."
    : isFallback
      ? "Jednolity rozkład dla całej działki na wybraną datę."
      : present === 1
        ? "Cała działka ma jednolity stan wilgotności (płaski teren)."
        : "Udział powierzchni wg globalnej rzeźby terenu (TPI) w granicach działki.";

  return (
    <div className="rs-zhist" aria-label="Histogram wilgotności na działce">
      <div className="rs-zhist__head">
        <h4 className="rs-subtitle">Rozkład wilgotności na działce</h4>
        <span className="ar-muted rs-zhist__meta">{meta}</span>
      </div>
      <ul className="rs-zhist__list">
        {bins.map((b) => {
          const cls = `ar-b-${balKey(b.step)}`;
          const label = BAL[String(b.step)] ?? String(b.step);
          return (
            <li key={b.step} className={cx("rs-zhist__row", b.pct <= 0 && "is-empty")}>
              <span className="rs-zhist__lbl"><i className={cx("rs-zhist__sw", cls)} aria-hidden="true" />{label}</span>
              <span className="rs-zhist__track" aria-hidden="true">{b.pct > 0 ? <span className={cx("rs-zhist__bar", cls)} style={{ width: `${Math.max(4, b.pct)}%` }} /> : null}</span>
              <span className="rs-zhist__val">{dec(b.pct, 0)}%{b.ha > 0 ? <span className="ar-muted"> · {dec(b.ha, 2)} ha</span> : null}</span>
            </li>
          );
        })}
      </ul>
      <p className="ar-muted rs-zhist__note">{note}</p>
    </div>
  );
}

/** Explanation: the exact numbers and the rule that produced the water state. */
function Why({ local, components }: { local: LocalResult; components?: NonNullable<AnalysisResponse["assessment"]>["components"] }) {
  const e = local.explanation;
  if (components?.length) {
    return (
      <div className="rs-why">
        <h4 className="rs-subtitle">Skąd ten wynik</h4>
        <table><tbody>{components.map((c) => <tr key={c.key}><td>{c.label}{c.weight !== undefined ? ` (waga ${dec(c.weight * 100, 0)}%)` : ""}</td><td><b>{c.value}</b>{c.detail ? <span className="ar-muted"> · {c.detail}</span> : null}{c.source ? <span className="ar-muted"> · {c.source}</span> : null}</td></tr>)}</tbody></table>
        <p className="ar-muted rs-why__note">Wynik z modelu serwera. <Link to="/metoda">Jak liczymy</Link></p>
      </div>
    );
  }
  if (!e) return null;
  const rainGap = e.pMean !== null ? e.pMean - e.p60 : null;
  const etGap = e.etMean !== null ? e.et060 - e.etMean : null;
  let driver: string | null = null;
  if (rainGap !== null && etGap !== null && local.stage !== null && local.stage < 0) {
    driver = rainGap >= etGap
      ? `Głównie brak opadu: o ${mm(rainGap)} mm mniej niż zwykle w tym okresie.`
      : `Głównie parowanie: o ${mm(etGap)} mm więcej niż zwykle w tym okresie.`;
  }
  return (
    <div className="rs-why">
      <h4 className="rs-subtitle">Skąd ten wynik</h4>
      <table>
        <tbody>
          <tr><td>Okres</td><td>{formatDayMonth(e.from)} – {formatLong(e.to)} (60 dni)</td></tr>
          <tr><td>Opad</td><td>+{mm(e.p60)} mm{e.pMean !== null ? <span className="ar-muted"> · średnio {mm(e.pMean)} mm</span> : null}</td></tr>
          <tr><td>Parowanie ET₀</td><td>−{mm(e.et060)} mm{e.etMean !== null ? <span className="ar-muted"> · średnio {mm(e.etMean)} mm</span> : null}</td></tr>
          <tr><td>KBW = opad − parowanie</td><td><b>{mm(e.k60)} mm</b></td></tr>
          {e.threshold !== null ? <tr><td>{e.reference ? "Próg odniesienia (zboża jare)" : "Próg IUNG"}{e.window ? ` w oknie ${e.window}` : ""}</td><td>{mm(e.threshold)} mm</td></tr> : null}
          {e.rule && local.stage !== null ? <tr><td>Reguła</td><td>{e.rule}, więc stan wody: {BAL[String(local.stage)]!.toLowerCase()}.</td></tr> : null}
        </tbody>
      </table>
      {driver ? <p className="rs-why__driver">{driver}</p> : null}
      <p className="ar-muted rs-why__note">Skala jest tymczasowa: wynika z KBW i progu IUNG. Backend doda bilans wody w glebie (FAO-56), zdjęcia satelitarne i model terenu. <Link to="/metoda">Jak liczymy</Link></p>
    </div>
  );
}

function Causes({ local, backend, land, weatherFailed }: { local: LocalResult | null; backend: AnalysisResponse | null; land: LandUse | null; weatherFailed: boolean }) {
  const c = backend?.causes;
  if (c) {
    const e = c.explanations;
    return <div className="ar-card"><CauseBreakdown causes={[
      { key: "klimat", pct: c.climate_pct, note: e.klimat ?? e.climate }, { key: "gleba", pct: c.soil_pct, note: e.gleba ?? e.soil },
      { key: "splyw", pct: c.runoff_pct, note: e.splyw ?? e.runoff }, { key: "uprawa", pct: c.crop_pct, note: e.uprawa ?? e.crop },
    ]} /></div>;
  }
  const lines = local?.causes ?? (land ? [soilCause(land)] : null);
  if (!lines) return weatherFailed ? <div className="ar-card"><p className="ar-card__text">Brak danych pogodowych, więc przyczyny klimatyczne są niedostępne.</p></div> : <Skeleton lines={["Przyczyny"]} />;
  return (
    <div className="ar-card">
      <ul className="ar-cause__list">
        {lines.map((x) => (
          <li key={x.key} className="ar-cause__item">
            <span className={`ar-cause__sw ar-c-${x.key}`} aria-hidden="true" />
            <span className="ar-cause__name">{x.label}</span>
            <span className="ar-cause__note">{x.text}</span>
          </li>
        ))}
      </ul>
      {weatherFailed ? <p className="ar-card__text">Brak danych pogodowych: część klimatyczna jest niedostępna.</p> : null}
    </div>
  );
}

interface MeasureItem { id: string; title: string; effect: string; permit: boolean; image: string; sim?: Measure["sim"]; backendNote?: string }
const stageName = (n: number | null) => (n === null ? "brak danych" : BAL[String(n)]!.toLowerCase());

/**
 * One list of measures: the whole card toggles it into the simulation. Each measure is simulated alone to rank
 * the list by water gain; the selected ones are simulated together below the list.
 */
function Measures({ parcelId, local, st, backend, crop, date, weatherFailed, picked, onPick, onWhatIfData }: {
  parcelId: string; local: LocalResult | null; st: AnalysisState; backend: AnalysisResponse | null; crop: CropCode; date: Iso; weatherFailed: boolean; picked: Set<string>; onPick: (id: string) => void; onWhatIfData?: (res: WhatIfScenarioResult | null) => void;
}) {
  const [own, setOwn] = useState<Levers | null>(null);
  const [whatIf, setWhatIf] = useState<{ key: string; loading: boolean; data: WhatIfScenarioResult | null; error: string | null }>({
    key: "",
    loading: false,
    data: null,
    error: null,
  });
  const land = landForCalc(st.land);
  const series = st.weather.status === "ready" ? st.weather.data : null;
  const recs = backend?.retention?.recommendations;
  const items: MeasureItem[] = recs?.length
    ? recs.map((r) => {
      const look = measureLook(`${r.id} ${r.category}`);
      const note = [r.delta_taw_mm > 0 ? `+${dec(r.delta_taw_mm, 0)} mm wody` : null, r.runoff_reduction_pct > 0 ? `${dec(r.runoff_reduction_pct, 0)}% mniej spływu` : null].filter(Boolean).join(" · ");
      return { id: r.id, title: r.title, effect: r.rationale || note, permit: r.requires_permit ?? look.permit, image: look.image, sim: simForBackend(r), backendNote: note };
    })
    : ((local?.stage ?? 0) >= 2 ? WET_MEASURES : DRY_MEASURES).map((m) => ({ id: m.id, title: m.title, effect: m.effect, permit: m.permit, image: m.image, sim: m.sim }));

  const run = (lv: Levers) => (series && land && land.kind !== "nonAgri" ? computeLocal(land, withLevers(series, lv), crop, date, todayIso()) : null);
  const solo = useMemo(() => new Map(items.map((m) => [m.id, m.sim ? run({ addMm: m.sim.addMm ?? 0, etCut: m.sim.etCut ?? 0 }) : null])), [series, land, crop, date, items.map((m) => m.id).join()]);
  const gain = (id: string) => { const r = solo.get(id); return r && local?.kbw60 !== null && local?.kbw60 !== undefined && r.kbw60 !== null ? r.kbw60 - local.kbw60 : -Infinity; };
  const ordered = recs?.length ? items : [...items].sort((a, b) => gain(b.id) - gain(a.id));

  const preset = useMemo<Levers>(() => items.filter((m) => picked.has(m.id) && m.sim).reduce((acc, m) => ({ addMm: acc.addMm + (m.sim?.addMm ?? 0), etCut: Math.min(0.5, acc.etCut + (m.sim?.etCut ?? 0)) }), NO_LEVERS), [picked, items.map((m) => m.id).join()]);
  useEffect(() => setOwn(null), [preset, parcelId]);
  const lv = own ?? preset;
  const combined = useMemo(() => (lv.addMm || lv.etCut ? run(lv) : null), [lv, series, land, crop, date]);
  const dryYears = (r: LocalResult) => r.years.filter((y) => y.value !== null && y.value <= -3).length;

  const bc = backendCrop(crop);
  const pickedSorted = [...picked].sort().join(",");
  const whatIfKey = backendEnabled && backend && bc && own === null && picked.size > 0
    ? `${parcelId}|${bc}|${date}|${pickedSorted}`
    : "";

  useEffect(() => {
    if (!whatIfKey || !bc) {
      setWhatIf({ key: "", loading: false, data: null, error: null });
      onWhatIfData?.(null);
      return;
    }
    const ctl = new AbortController();
    setWhatIf((prev) => ({ key: whatIfKey, loading: true, data: prev.data, error: null }));
    postWhatIf({ parcel_id: parcelId, crop: bc, analysis_date: date, enabled_measures: [...picked] }, ctl.signal).then(
      (data) => {
        if (ctl.signal.aborted) return;
        setWhatIf({ key: whatIfKey, loading: false, data, error: null });
        onWhatIfData?.(data);
      },
      (e) => {
        if (ctl.signal.aborted || isAbort(e)) return;
        setWhatIf({ key: whatIfKey, loading: false, data: null, error: message(e) });
        onWhatIfData?.(null);
      },
    );
    return () => ctl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [whatIfKey]);

  const activeWhatIf = whatIfKey && whatIf.key === whatIfKey ? whatIf.data : null;

  if (!local && !recs && !weatherFailed) return <Skeleton lines={["Działania"]} />;
  const groups = [{ title: "Bez urządzeń wodnych", items: ordered.filter((m) => !m.permit) }, { title: "Wymaga zgody Wód Polskich", items: ordered.filter((m) => m.permit) }];
  const effectLine = (m: MeasureItem) => {
    if (m.backendNote) return m.backendNote;
    const r = solo.get(m.id);
    if (!r || !local) return null;
    if (r.stage !== null && local.stage !== null && r.stage !== local.stage) return `${BAL[String(local.stage)]} → ${stageName(r.stage)}`;
    const g = gain(m.id);
    return Number.isFinite(g) && g > 0 ? `+${dec(g, 0)} mm wody w bilansie 60 dni` : null;
  };
  return (
    <>
      <p className="rs-lead">Kliknij działania, które rozważasz. Wynik dla tej działki pokaże się pod listą.</p>
      {groups.filter((g) => g.items.length).map((g) => (
        <div key={g.title} className="rs-group">
          <h4 className="rs-subtitle">{g.title}</h4>
          <div className="rs-measures">
            {g.items.map((m) => {
              const on = picked.has(m.id);
              const can = !!m.sim && !!local;
              const eff = effectLine(m);
              return (
                <button key={m.id} type="button" className={`rs-measure${on ? " is-on" : ""}`} aria-pressed={can ? on : undefined} disabled={!can && !m.backendNote} onClick={() => can && onPick(m.id)}>
                  <span className="rs-measure__img" style={{ backgroundImage: `url("${m.image}")` }} aria-hidden="true" />
                  <span className="rs-measure__body">
                    <b className="rs-measure__title">{m.title}</b>
                    <span className="rs-measure__effect">{m.effect}</span>
                    {eff ? <span className="rs-measure__result">{eff}</span> : null}
                  </span>
                  {can ? <span className="rs-measure__check" aria-hidden="true">{on ? <Icon name="check" /> : null}</span> : null}
                </button>
              );
            })}
          </div>
        </div>
      ))}
      {items.some((m) => m.permit) ? <p className="ar-muted rs-why__note">Zgody i zgłoszenia wodnoprawne: <a href={PERMIT_URL} target="_blank" rel="noreferrer">Wody Polskie</a>.</p> : null}
      {local ? (
        <div className="ar-card rs-whatif">
          <h4 className="rs-subtitle">Z wybranymi działaniami</h4>
          <p className="ar-card__text">
            {activeWhatIf
              ? `Symulacja FAO-56 + SCS-CN (rok odniesienia ${activeWhatIf.reference_dry_year}).`
              : combined
                ? "Porównanie stanu obecnego z wybranymi założeniami."
                : "Wybierz działanie powyżej albo przesuń suwaki poniżej."}
          </p>
          {whatIf.loading && whatIfKey ? <p className="rs-updating" role="status">Przeliczam symulację na serwerze…</p> : null}
          {whatIf.error && whatIfKey ? <p className="rs-updating" role="status">{whatIf.error} (wynik lokalny poniżej).</p> : null}
          <table className="rs-compare">
            <colgroup><col className="rs-compare__k" /><col className="rs-compare__v" /><col className="rs-compare__v" /></colgroup>
            <thead><tr><th /><th>Teraz</th><th>Z działaniami</th></tr></thead>
            {activeWhatIf ? (
              <tbody>
                <tr>
                  <td>Strata plonu ({activeWhatIf.reference_dry_year})</td>
                  <td>{dec(activeWhatIf.baseline_yield_loss_pct, 1)}%</td>
                  <td><b>{dec(activeWhatIf.scenario_yield_loss_pct, 1)}%</b></td>
                </tr>
                <tr>
                  <td>Dni stresu w sezonie</td>
                  <td>{activeWhatIf.baseline_stress_days ?? "—"}</td>
                  <td><b>{activeWhatIf.scenario_stress_days ?? "—"}</b></td>
                </tr>
                <tr>
                  <td>Dodatkowa rezerwa wody</td>
                  <td>0 dni</td>
                  <td><b>{activeWhatIf.delta_reserve_days > 0 ? `+${activeWhatIf.delta_reserve_days}` : activeWhatIf.delta_reserve_days < 0 ? `−${-activeWhatIf.delta_reserve_days}` : "0"} dni</b></td>
                </tr>
                <tr>
                  <td>Zatrzymana woda{activeWhatIf.retained_runoff_mm ? ` (spływ −${dec(activeWhatIf.retained_runoff_mm, 0)} mm)` : ""}</td>
                  <td>0 mm</td>
                  <td><b>+{dec(activeWhatIf.saved_water_mm, 0)} mm</b></td>
                </tr>
              </tbody>
            ) : (
              <tbody>
                <tr>
                  <td>Stan wody</td>
                  <td><WaterBalance size="sm" value={local.stage} /></td>
                  <td className={combined ? undefined : "rs-compare__same"}><WaterBalance size="sm" value={(combined ?? local).stage} /></td>
                </tr>
                <tr>
                  <td>Bilans wody z 60 dni</td>
                  <td>{mm(local.kbw60)} mm</td>
                  <td className={combined ? undefined : "rs-compare__same"}>{mm((combined ?? local).kbw60)} mm</td>
                </tr>
                <tr>
                  <td>Okna z suszą IUNG w sezonie {local.season}</td>
                  <td>{local.droughtCount} z {local.knownCount}</td>
                  <td className={combined ? undefined : "rs-compare__same"}>{(combined ?? local).droughtCount} z {(combined ?? local).knownCount}</td>
                </tr>
                <tr>
                  <td>Lata z suszą ({formatDayMonth(date)}, 10 lat)</td>
                  <td>{dryYears(local)} z {local.years.length}</td>
                  <td className={combined ? undefined : "rs-compare__same"}>{dryYears(combined ?? local)} z {(combined ?? local).years.length}</td>
                </tr>
              </tbody>
            )}
          </table>
          <div className="rs-more rs-more--inline">
            <div className="rs-sliders__head">
              <h4 className="rs-subtitle">Dostosuj założenia</h4>
              {own ? <button type="button" className="rs-linkbtn" onClick={() => setOwn(null)}>Przywróć z listy</button> : null}
            </div>
            <div className="rs-sliders">
              <label><span>Woda zatrzymana w glebie: <b>+{lv.addMm} mm</b> na 60 dni</span>
                <input type="range" min={0} max={80} step={5} value={lv.addMm} onChange={(e) => setOwn({ ...lv, addMm: Number(e.target.value) })} />
              </label>
              <label><span>Mniej parowania: <b>−{Math.round(lv.etCut * 100)}%</b></span>
                <input type="range" min={0} max={30} step={1} value={Math.round(lv.etCut * 100)} onChange={(e) => setOwn({ ...lv, etCut: Number(e.target.value) / 100 })} />
              </label>
            </div>
            <p className="ar-muted rs-why__note">Założenia: {DRY_MEASURES.filter((m) => m.sim).map((m) => `${m.title.toLowerCase()} ${m.sim!.note}`).join(", ")}. Backend zastąpi je bilansem FAO-56 i modelem terenu.</p>
          </div>
        </div>
      ) : null}
    </>
  );
}

const WATER_CLASS: Record<string, string> = { "bardzo sucho": "bardzo sucho", sucho: "sucho", "umiarkowany zapas": "umiarkowany zapas", wilgotno: "wilgotno", "pelny zapas": "pełny zapas" };
const SOIL_ZONE: Record<string, string> = { "0-28 cm": "0–28 cm (wierzchnia)", "28-100 cm": "28–100 cm", "100-200 cm": "100–200 cm (głęboka)" };
const dayShort = (iso: string) => new Date(`${iso}T12:00:00`).toLocaleDateString("pl-PL", { weekday: "short", day: "numeric" });

/** 14-day plan (dose + chance of irrigating) and the soil water of three zones as classes with their probability. */
function Plan14({ plan }: { plan: RlPlan14d }) {
  const z = plan.soil_zones;
  const ens = plan.probability_source === "ecmwf_ens";
  const picks = z ? [2, 6, 13].map((i) => z.days[i]).filter((d): d is RlSoilZoneDay => !!d) : [];
  const w = plan.water_need_mm;
  const withPlan = picks.some((d) => d.stress_probability_with_plan !== undefined);
  const scenarios = ens ? `${w?.n_scenarios ?? 51} wariantów prognozy ECMWF` : "pogoda tych samych dni z innych lat";
  return (
    <div className="ar-card rs-table rs-plan14">
      <h4 className="rs-subtitle">Plan i woda w glebie na 14 dni</h4>
      <div className="rs-fc" aria-label="Plan nawadniania na 14 dni">
        {plan.days.map((d) => (
          <div key={d.date} className="rs-fc__day" title={`Pewność planu: ${d.confidence}`}>
            <span className="rs-fc__mm">{dec(d.dose_mm, 0)}</span>
            <span className="rs-fc__d">{dayShort(d.date)}</span>
            {d.irrigation_probability !== null ? <span className="rs-fc__p">{dec(100 * d.irrigation_probability, 0)}%</span> : null}
          </div>
        ))}
      </div>
      <p className="ar-muted rs-why__note">
        Duża liczba to dawka w mm, procent to szansa, że tego dnia trzeba będzie podlać ({scenarios}). Pewność planu: dni 1–3 wysoka, 4–7 średnia, dalej niska.
        {w ? ` Woda na 14 dni: ok. ${dec(w.expected_mm, 0)} mm (zwykle ${dec(w.p10_mm, 0)}–${dec(w.p90_mm, 0)} mm).` : ""}
      </p>
      {z && picks.length ? (
        <table>
          <thead><tr><th>Strefa gleby</th><th>Dziś</th>{picks.map((d) => <th key={d.date}>{formatDayMonth(d.date as Iso)}</th>)}</tr></thead>
          <tbody>
            <tr>
              <td><b>Strefa korzeni</b></td>
              <td>{dec(z.today.root_zone_water_pct, 0)}%</td>
              {picks.map((d) => <td key={d.date}><b>{WATER_CLASS[d.root_zone.class] ?? d.root_zone.class}</b><span className="rs-table__sub">{dec(d.root_zone.water_pct_median, 0)}% (zwykle {dec(d.root_zone.water_pct_p10, 0)}–{dec(d.root_zone.water_pct_p90, 0)}%)</span></td>)}
            </tr>
            {z.today.zones.map((t, k) => (
              <tr key={t.zone}>
                <td>{SOIL_ZONE[t.zone] ?? t.zone}</td>
                <td>{WATER_CLASS[t.class] ?? t.class}<span className="rs-table__sub">{dec(t.water_pct, 0)}%</span></td>
                {picks.map((d) => {
                  const c = d.zones[k];
                  if (!c) return <td key={d.date}>–</td>;
                  return <td key={d.date}>{WATER_CLASS[c.class] ?? c.class}<span className="rs-table__sub">pewność {dec(100 * (c.class_probabilities[c.class] ?? 0), 0)}%</span></td>;
                })}
              </tr>
            ))}
            <tr><td>Ryzyko stresu bez nawadniania</td><td>–</td>{picks.map((d) => <td key={d.date}>{dec(100 * d.stress_probability_no_irrigation, 0)}%</td>)}</tr>
            {withPlan ? <tr><td>Ryzyko stresu z planem</td><td>–</td>{picks.map((d) => <td key={d.date}>{d.stress_probability_with_plan !== undefined ? `${dec(100 * d.stress_probability_with_plan, 0)}%` : "–"}</td>)}</tr> : null}
          </tbody>
        </table>
      ) : null}
      {z ? (
        <p className="ar-muted rs-why__note">
          Procent strefy to woda dostępna dla roślin (0% = punkt więdnięcia, 100% = pojemność polowa). Klasy co 20%, z pewnością prognozy.
          Pogoda: {ens ? "prognoza zespołowa ECMWF (siatka ok. 25 km)" : "te same dni w innych latach archiwum"}; gleba: {z.soil_profile_source.startsWith("SoilGrids") ? "mapy SoilGrids (250 m) dopasowane do kategorii IUNG-PIB" : "kategoria IUNG-PIB"}. Wynik dotyczy punktu w środku działki.
        </p>
      ) : null}
    </div>
  );
}

function Irrigation({ local, rl, compare }: { local: LocalResult; rl: RlRecommendResponse | null; compare: RlCompareResponse | null }) {
  const fc = local.forecast;
  const strip = fc ? (
    <div className="rs-fc" aria-label="Prognoza opadu na 7 dni">
      {fc.days.map((d) => <div key={d.date} className="rs-fc__day"><span className="rs-fc__mm">{dec(d.p, 0)}</span><span className="rs-fc__d">{new Date(`${d.date}T12:00:00`).toLocaleDateString("pl-PL", { weekday: "short", day: "numeric" })}</span></div>)}
    </div>
  ) : null;
  let card: JSX.Element | null = null;
  if (!local.offSeason && rl?.status === "ok" && rl.recommendation) {
    const r = rl.recommendation;
    card = (
      <PhotoCard image="/img/rozlewiska-biebrza.jpg" title={r.action_now} text={rl.message}>
        <p className="ar-card__text">Dawka dziś: {dec(r.dose_today_mm, 0)} mm · w 7 dni: {dec(r.total_7d_mm, 0)} mm{rl.policy ? ` · ${rl.policy.label}` : ""}.</p>
        {r.plan_14d ? null : (
          <>
            <div className="rs-fc" aria-label="Plan nawadniania na 7 dni">
              {r.plan_7d.map((d) => <div key={d.date} className="rs-fc__day"><span className="rs-fc__mm">{dec(d.dose_mm, 0)}</span><span className="rs-fc__d">{dayShort(d.date)}</span></div>)}
            </div>
            <p className="ar-muted rs-why__note">Liczby to dawka w mm na dzień; plan zakłada prognozę opadu ECMWF.</p>
          </>
        )}
      </PhotoCard>
    );
  } else if (!local.offSeason && local.irrigation) {
    card = <PhotoCard image="/img/rozlewiska-biebrza.jpg" title={local.irrigation.title} text={local.irrigation.text}>{strip}</PhotoCard>;
  }
  const strategies = compare?.strategies ?? [];
  const plan14 = !local.offSeason && rl?.status === "ok" ? rl.recommendation?.plan_14d : undefined;
  return (
    <>
      {card}
      {plan14 ? <Plan14 plan={plan14} /> : null}
      {strategies.length ? (
        <div className="ar-card rs-table">
          <h4 className="rs-subtitle">Strategie nawadniania w 10 sezonach tej działki</h4>
          <table>
            <thead><tr><th>Strategia</th><th>Strata plonu</th><th>Woda</th><th>Przesiąk</th><th>Dni stresu</th></tr></thead>
            <tbody>{strategies.map((x) => (
              <tr key={x.strategy} aria-current={x.strategy === compare?.default_policy ? "true" : undefined}>
                <td><b>{x.label}</b></td><td>{dec(x.avg_yield_loss_pct, 1)}%</td><td>{dec(x.avg_water_mm, 0)} mm</td><td>{dec(x.avg_deep_percolation_mm, 0)} mm</td><td>{dec(x.avg_stress_days, 0)}</td>
              </tr>
            ))}</tbody>
          </table>
          <p className="ar-muted rs-why__note">Średnie z symulacji FAO-56/33 dla 10 sezonów tej działki. Wyróżniony wiersz to strategia domyślna.</p>
        </div>
      ) : null}
    </>
  );
}

function Sources({ sources, backend, rlSources, elapsed }: { sources: SourceEntry[]; backend: AnalysisResponse | null; rlSources: RlSource[]; elapsed: number | null }) {
  const rows = [...sources.map((s) => [s.name, s.what, timePl(s.retrievedAt), s.resolution, s.license] as const),
    ...(backend?.audit?.sources ?? []).map((s) => [s.source_name, s.category, timePl(new Date(s.retrieved_at)), s.spatial_resolution, s.license] as const),
    ...rlSources.filter((x, i, a) => a.findIndex((y) => y.name === x.name) === i).map((s) => [s.name, "moduł nawadniania (RL)", timePl(new Date(s.retrieved_at)), s.detail ?? "", s.license] as const)];
  if (!rows.length) return <Skeleton lines={["Źródła"]} />;
  return (
    <div className="ar-card rs-table">
      <table>
        <thead><tr><th>Źródło</th><th>Co daje</th><th>Pobrano</th><th>Rozdzielczość</th><th>Licencja</th></tr></thead>
        <tbody>{rows.map((r, idx) => <tr key={`${r[0]}-${idx}`}>{r.map((c, i) => <td key={i}>{c}</td>)}</tr>)}</tbody>
      </table>
      <p className="ar-card__text">{elapsed !== null ? `Pogoda pobrana w ${dec(elapsed / 1000, 1)} s. ` : ""}Analiza używa tylko danych działki, bez danych osobowych.</p>
    </div>
  );
}

function DevHooks({ st }: { st: AnalysisState }) {
  return (
    <details className="rs-dev">
      <summary>Podpięcia backendu (widoczne tylko w trybie deweloperskim)</summary>
      <ul>
        <li>VITE_API_URL: {backendEnabled ? (API_URL || "/ (same-origin proxy)") : "puste: kroki serwera są wyłączone"}</li>
        <li>POST /api/analyze: {st.backend.status}{st.backend.status === "error" ? ` (${st.backend.message})` : ""}</li>
        <li>POST /rl/recommend: {st.rl.status}{st.rl.status === "error" ? ` (${st.rl.message})` : ""}</li>
        <li>Próbka sekcji serwera bez backendu: dodaj <code>&amp;fixtures</code> do adresu.</li>
      </ul>
    </details>
  );
}
