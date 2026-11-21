import { useEffect, useMemo, useState } from "react";
import { Link, Navigate, useSearchParams } from "react-router-dom";
import { API_URL, backendEnabled, reportPdfUrl } from "../api/backend";
import type { AnalysisResponse, RlRecommendResponse } from "../api/contract";
import { computeLocal, longestDrought, soilCause, withLevers, type Levers, type LocalResult } from "../analysis/local";
import { landForCalc, message, useAnalysis, usePortfolio, type AnalysisState, type PortfolioRow } from "../analysis/useAnalysis";
import { ActionCard, BAL, Button, CauseBreakdown, ConfidenceBadge, CropDateForm, dateBounds, FloatingNav, KbwWindows, MetricTile, PhotoCard, Pill, RiskChip, signed, StateTimeline, WaterBalance, Wordmark, YearCompare, type IconName } from "../ds";
import { DRY_MEASURES, measureLook, PERMIT_URL, WET_MEASURES, type Measure } from "../lib/catalog";
import { backendCrop, cropOf as cropInfo, DEFAULT_CROP, isCropCode, type CropCode } from "../lib/crops";
import { addDays, formatDayMonth, formatLong, isIso, monthName, parts, todayIso, type Iso } from "../lib/dates";
import { riskFromStage } from "../lib/kbw";
import type { LandUse } from "../lib/iung";
import type { SourceEntry } from "../lib/sources";
import { parcelByPoint } from "../lib/uldk";
import { ParcelMap, type MapParcel, type MapZone } from "../map/ParcelMap";
import { dec, ha, mm, timePl } from "./shared";

const NAV: { id: string; label: string; icon: IconName }[] = [
  { id: "ocena", label: "Ocena", icon: "gauge" }, { id: "przyczyny", label: "Przyczyny", icon: "layers" },
  { id: "retencja", label: "Retencja", icon: "sprout" }, { id: "nawadnianie", label: "Woda", icon: "drop" }, { id: "raport", label: "Raport", icon: "file" },
];
const NO_LEVERS: Levers = { addMm: 0, etCut: 0 };

export function Analysis() {
  const [sp, setSp] = useSearchParams();
  const parcelId = sp.get("dzialka") ?? "";
  /** Other parcels as "id~crop"; a missing crop falls back to the crop of the displayed parcel. */
  const otherEntries = (sp.get("inne") ?? "").split(",").map((s) => s.trim()).filter(Boolean).map((e) => e.split("~")).filter(([id]) => id && id !== parcelId);
  const others = otherEntries.map((e) => e[0]!);
  const rawCrop = sp.get("uprawa");
  const crop: CropCode = isCropCode(rawCrop) ? rawCrop : DEFAULT_CROP;
  const rawDate = sp.get("data");
  const { min, max } = dateBounds();
  const date: Iso = isIso(rawDate) && rawDate >= min && rawDate <= max ? rawDate : todayIso();
  const st = useAnalysis({ parcelId, crop, date });
  const ids = useMemo(() => [parcelId, ...others], [parcelId, others.join(",")]);
  const cropOf = (id: string): CropCode => { if (id === parcelId) return crop; const c = otherEntries.find(([x]) => x === id)?.[1]; return isCropCode(c) ? c : crop; };
  const items = useMemo(() => ids.map((id) => ({ id, crop: cropOf(id) })), [ids.join(","), sp.get("inne"), crop]);
  const portfolio = usePortfolio(ids.length > 1 ? items : [], date);
  const encodeOthers = (list: string[]) => list.map((id) => `${id}~${cropOf(id)}`).join(",");

  const [active, setActive] = useState("ocena");
  const [addMode, setAddMode] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());

  const setQuery = (patch: Record<string, string | null>) => {
    const next = new URLSearchParams(sp);
    Object.entries(patch).forEach(([k, v]) => (v === null || v === "" ? next.delete(k) : next.set(k, v)));
    setSp(next);
  };

  const parcel = st.parcel.status === "ready" ? st.parcel.data : null;
  const land = st.land.status === "ready" ? st.land.data : null;
  const backend = st.backend.status === "ready" ? st.backend.data : null;
  const local = useMemo(() => withBackend(st.local, backend), [st.local, backend]);
  const rl = st.rl.status === "ready" ? st.rl.data : null;
  const weatherFailed = st.weather.status === "error" ? st.weather.message : null;
  const showIrr = !!local && !local.offSeason && (rl?.status === "ok" || !!local.irrigation);

  useEffect(() => {
    const els = NAV.map((n) => document.getElementById(n.id)).filter((e): e is HTMLElement => !!e);
    const io = new IntersectionObserver((es) => { const v = es.filter((e) => e.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0]; if (v) setActive(v.target.id); }, { rootMargin: "-20% 0px -60% 0px" });
    els.forEach((e) => io.observe(e));
    return () => io.disconnect();
  }, [st.parcel.status, !!local, showIrr]);

  const zones = useMemo<MapZone[] | null>(() => backend?.map_layers?.zones?.features.map((f) => ({ geometry: f.geometry, value: f.properties.value })) ?? null, [backend]);
  /** Every selected parcel on the map, coloured by its water state; the displayed one has the hatch. */
  const mapParcels = useMemo<MapParcel[]>(() => {
    const main = parcel ? [{ id: parcel.id, geometry: parcel.geometry, value: local?.stage ?? null, active: true, label: `${parcel.number}, ${parcel.region}` }] : [];
    const rest = portfolio.flatMap((r) => (r.id !== parcelId && r.parcel.status === "ready" ? [{ id: r.id, geometry: r.parcel.data.geometry, value: r.local?.stage ?? null, active: false, label: `${r.parcel.data.number}, ${r.parcel.data.region}` }] : []));
    return [...main, ...rest];
  }, [parcel, local?.stage, portfolio, parcelId]);

  if (!parcelId) return <Navigate to="/" replace />;
  const pdf = parcel && backendCrop(crop) ? reportPdfUrl({ parcel_id: parcel.id, crop: backendCrop(crop)!, analysis_date: date }) : null;
  const go = (id: string) => document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });

  const addAt = async (lon: number, lat: number) => {
    setAddError(null);
    try {
      const p = await parcelByPoint(lon, lat);
      if (p.id === parcelId || others.includes(p.id)) return;
      setQuery({ inne: [encodeOthers(others), `${p.id}~${crop}`].filter(Boolean).join(",") });
    } catch (e) {
      setAddError(message(e));
    }
  };
  const makeActive = (id: string) => setQuery({ dzialka: id, uprawa: cropOf(id), inne: encodeOthers(ids.filter((x) => x !== id)) });
  const remove = (id: string) => {
    if (id === parcelId) { const [next, ...rest] = others; if (next) setQuery({ dzialka: next, uprawa: cropOf(next), inne: encodeOthers(rest) }); return; }
    setQuery({ inne: encodeOthers(others.filter((x) => x !== id)) });
  };
  const togglePick = (id: string) => setPicked((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n; });

  const overlay = (
    <>
      <Pill tone="glass" pressed={addMode} onClick={() => { setAddMode((v) => !v); setAddError(null); }}>{addMode ? "Zakończ dodawanie" : "Dodaj działkę do porównania"}</Pill>
      {addMode ? <p className="rs-map__hint">{addError ?? "Kliknij działkę na mapie."}</p> : null}
    </>
  );

  return (
    <div className="rs-analysis">
      <header className="rs-ahead">
        <Link to={parcel ? `/?dzialka=${encodeURIComponent(parcel.id)}` : "/"} aria-label="Wróć do mapy"><Wordmark /></Link>
        <p className="rs-ahead__parcel ar-muted">{parcel ? <>Działka {parcel.number}, obręb {parcel.region}, gm. {parcel.commune} · {ha(parcel.areaHa)} ha · {cropInfo(crop).label.toLowerCase()} · {formatLong(date)}</> : parcelId}</p>
        {pdf ? <Button variant="light" arrow="down" href={pdf}>Pobierz raport PDF</Button> : <Button variant="light" arrow="down" onClick={() => window.print()}>Pobierz raport PDF</Button>}
      </header>

      <div className="rs-grid">
        <aside className="rs-side">
          <ParcelMap className="rs-side__map" parcels={mapParcels} bbox={parcel?.bbox} zones={zones} padding={{ top: 70, bottom: 40 }}
            onPick={addMode ? addAt : null} overlay={overlay} legend={<MapLegend parcels={mapParcels} />} />
        </aside>

        <main className="rs-main">
          <CropDateForm className="rs-recalc" crop={crop} date={date} hideSubmit onSubmit={() => undefined}
            onCrop={(c) => setQuery({ uprawa: c })} onDate={(d) => setQuery({ data: d })} />

          {ids.length > 1 ? <Portfolio rows={portfolio} activeId={parcelId} onShow={makeActive} onRemove={remove} /> : null}

          {st.parcel.status === "error" ? <ErrorCard text={st.parcel.message} /> : null}
          {land?.kind === "nonAgri" && parcel ? <ErrorCard text={`${land.label}: działka ${parcel.number} nie jest gruntem rolnym. Rusałka analizuje grunty orne, łąki, pastwiska i sady.`} /> : null}

          {st.parcel.status !== "error" && land?.kind !== "nonAgri" ? (
            <>
              <section id="ocena" className="rs-section">
                <h2 className="ar-section-title">Sytuacja wodna {formatLong(date)}</h2>
                {local ? <Assessment local={local} onPickDate={(d) => setQuery({ data: d })} /> : weatherFailed ? <ErrorCard text={weatherFailed} retry /> : <Skeleton lines={["Stan wody", "Okna IUNG", "10 lat"]} />}
              </section>
              <section id="przyczyny" className="rs-section">
                <h2 className="ar-section-title">{causesTitle(local)}</h2>
                <Causes local={local} backend={backend} land={landForCalc(st.land)} weatherFailed={!!weatherFailed} />
              </section>
              <section id="retencja" className="rs-section">
                <h2 className="ar-section-title">Od czego zacząć</h2>
                <Retention local={local} backend={backend} weatherFailed={!!weatherFailed} picked={picked} onPick={togglePick} />
              </section>
              {local && st.weather.status === "ready" ? (
                <section id="symulacja" className="rs-section">
                  <h2 className="ar-section-title">Co jeśli</h2>
                  <WhatIf base={local} st={st} crop={crop} date={date} picked={picked} onPick={togglePick} />
                </section>
              ) : null}
              {local && showIrr ? (
                <section id="nawadnianie" className="rs-section">
                  <h2 className="ar-section-title">Nawadnianie</h2>
                  <Irrigation local={local} rl={rl} />
                </section>
              ) : null}
              <section id="raport" className="rs-section">
                <h2 className="ar-section-title">Źródła danych</h2>
                <Sources sources={st.sources} backend={backend} elapsed={st.elapsedMs} />
              </section>
            </>
          ) : null}
          {import.meta.env.DEV ? <DevHooks st={st} /> : null}
        </main>
      </div>
      <FloatingNav className="rs-fnav" items={NAV.filter((n) => n.id !== "nawadnianie" || showIrr)} active={active} onChange={go} />
    </div>
  );
}

function MapLegend({ parcels }: { parcels: (MapParcel & { label?: string })[] }) {
  if (!parcels.length) return null;
  return (
    <>
      <b>Stan wody</b>
      {parcels.map((p) => (
        <span key={p.id} className="rs-legend__row">
          <i className={`rs-legend__sw ${p.value === null || p.value === undefined ? "" : `ar-b-${p.value < 0 ? `m${-p.value}` : p.value > 0 ? `p${p.value}` : "0"}`}`} aria-hidden="true" />
          <span>Dz. {p.label}: {p.value === null || p.value === undefined ? "…" : `${signed(p.value)} ${BAL[String(p.value)]!.toLowerCase()}`}</span>
        </span>
      ))}
    </>
  );
}

/** When the backend answers, its formula (FAO-56, satellite/ML, soil, terrain) decides the state; the browser estimate stays as fallback. */
function withBackend(local: LocalResult | null, backend: AnalysisResponse | null): LocalResult | null {
  const a = backend?.assessment;
  if (!local || !a || (a.water_state === undefined && !a.water_state_timeline)) return local;
  const stage = a.water_state !== undefined ? Math.max(-5, Math.min(5, Math.round(a.water_state))) : local.stage;
  const r = stage === null ? null : riskFromStage(stage);
  const veto = a.water_risk?.veto_triggered ?? local.risk.veto;
  return {
    ...local,
    stage,
    risk: r ? { level: (a.water_risk?.level as typeof local.risk.level) ?? r.level, side: r.side, veto, vetoReason: a.water_risk?.veto_reasons?.join(" ") || local.risk.vetoReason } : local.risk,
    caption: a.summary_text ?? local.caption,
    confidence: a.confidence ? { level: a.confidence.level, detail: [...(a.confidence.resolution_warnings ?? []), ...(a.confidence.missing_factors ?? [])].join(" ") || "Wynik z modelu serwera." } : local.confidence,
    timeline: a.water_state_timeline ?? local.timeline,
  };
}

function timelineSummary(local: LocalResult): string | null {
  const today = todayIso();
  const in7 = local.timeline.find((d) => d.date === addDays(today, 7));
  if (!in7 || in7.value === null) return null;
  return `Za 7 dni: ${signed(in7.value)} ${BAL[String(in7.value)]!.toLowerCase()} (prognoza)`;
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

function Portfolio({ rows, activeId, onShow, onRemove }: { rows: PortfolioRow[]; activeId: string; onShow: (id: string) => void; onRemove: (id: string) => void }) {
  return (
    <section className="rs-section" aria-label="Porównanie działek">
      <h2 className="ar-section-title">Twoje działki</h2>
      <div className="ar-card rs-table">
        <table>
          <thead><tr><th>Działka</th><th>Uprawa</th><th>Pow.</th><th>Gleba</th><th>Stan wody</th><th>Susza wg IUNG</th><th><span className="ar-sr">Akcje</span></th></tr></thead>
          <tbody>
            {rows.map((r) => {
              const p = r.parcel.status === "ready" ? r.parcel.data : null;
              const on = r.id === activeId;
              return (
                <tr key={r.id} aria-current={on ? "true" : undefined}>
                  <td><b>{p ? `${p.number}, ${p.region}` : r.id}</b>{p ? <span className="rs-table__sub">{p.commune}</span> : null}</td>
                  <td>{cropInfo(r.crop).label}</td>
                  <td>{p ? `${ha(p.areaHa)} ha` : "…"}</td>
                  <td>{r.land ? (r.land.kind === "mineral" ? `kat. ${r.land.category}` : r.land.kind === "nonAgri" ? r.land.label.toLowerCase() : "brak danych") : "…"}</td>
                  <td>{r.local ? <WaterBalance size="sm" value={r.local.stage} /> : r.error ?? (r.land?.kind === "nonAgri" ? "poza zakresem" : "…")}</td>
                  <td>{r.local ? (r.local.knownCount ? `${r.local.droughtCount} z ${r.local.knownCount} okien` : "brak progu") : ""}</td>
                  <td className="rs-table__act">
                    {on ? <span className="ar-muted">wyświetlana</span> : <Button variant="light" size="sm" onClick={() => onShow(r.id)}>Pokaż</Button>}
                    <Button variant="icon" size="sm" icon="minus" label={`Usuń działkę ${p?.number ?? r.id} z porównania`} onClick={() => onRemove(r.id)} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Assessment({ local, onPickDate }: { local: LocalResult; onPickDate: (d: Iso) => void }) {
  const [mode, setMode] = useState<"day" | "month">("day");
  const ys = mode === "day" ? local.years : local.yearsMonth;
  const [y, m] = parts(local.date);
  const span = `${ys[0]?.year}–${ys[ys.length - 1]?.year}`;
  const longest = useMemo(() => longestDrought(local.windows), [local.windows]);
  const thLabel = local.category && local.thresholds ? `Próg IUNG: ${local.crop.label.toLowerCase()}, gleba kat. ${local.category}` : undefined;
  return (
    <>
      {local.offSeason ? (
        <div className="ar-card rs-offseason">
          <h3 className="ar-card__title">Poza sezonem</h3>
          <p>Okna IUNG obejmują 21.III–30.IX. Pokazujemy sezon {local.season}: susza wg progów IUNG w {local.droughtCount} z {local.knownCount} okien{longest ? `, najdłużej ${longest}` : ""}.</p>
          <ul className="rs-list">
            <li>Zostaw międzyplon albo mulcz na zimę. Okryta gleba trzyma wodę z roztopów.</li>
            <li>Zastawka na rowie wymaga zgody Wód Polskich. Złóż wniosek przed sezonem. <a href={PERMIT_URL} target="_blank" rel="noreferrer">Wody Polskie</a></li>
            <li>Sprawdź działkę ponownie po 21 marca, gdy ruszą okna IUNG.</li>
          </ul>
        </div>
      ) : null}
      <div className="ar-card">
        <WaterBalance value={local.stage} caption={local.caption} />
        <Why local={local} />
      </div>
      <div className="rs-status">
        {local.stage !== null ? <RiskChip level={local.risk.level} side={local.risk.side} veto={local.risk.veto} vetoReason={local.risk.vetoReason} /> : null}
        <ConfidenceBadge level={local.confidence.level} detail={local.confidence.detail} />
      </div>
      <div className="rs-metrics">
        <MetricTile icon="drop" label="Opad 30 dni" value={mm(local.metrics.p30)} unit="mm" />
        <MetricTile icon="sun" label="Parowanie ET₀ 30 dni" value={mm(local.metrics.et030)} unit="mm" />
        <MetricTile icon="gauge" label="KBW 60 dni" value={mm(local.kbw60)} unit="mm" />
      </div>
      <div className="ar-card"><StateTimeline days={local.timeline} selected={local.date} today={todayIso()} onPick={onPickDate} summary={timelineSummary(local)} /></div>
      <div className="ar-card"><KbwWindows title={`Sezon ${local.season} w oknach IUNG`} windows={local.windows} active={local.active} thresholdLabel={thLabel} /></div>
      <div className="ar-card">
        <YearCompare title={mode === "day" ? `${formatDayMonth(local.date)} w latach ${span}` : `${monthName(m)} (stan na koniec miesiąca) w latach ${span}`} years={ys} highlightYear={y}>
          <div className="ar-form__chips">
            <Pill pressed={mode === "day"} onClick={() => setMode("day")}>Ten dzień</Pill>
            <Pill pressed={mode === "month"} onClick={() => setMode("month")}>Cały miesiąc</Pill>
          </div>
        </YearCompare>
      </div>
    </>
  );
}

/** Explanation: the exact numbers and the rule that produced the water state. */
function Why({ local }: { local: LocalResult }) {
  const e = local.explanation;
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
    <details className="rs-why">
      <summary>Skąd ten wynik</summary>
      <table>
        <tbody>
          <tr><td>Okres</td><td>{formatDayMonth(e.from)} – {formatLong(e.to)} (60 dni)</td></tr>
          <tr><td>Opad</td><td>+{mm(e.p60)} mm{e.pMean !== null ? <span className="ar-muted"> · średnio {mm(e.pMean)} mm</span> : null}</td></tr>
          <tr><td>Parowanie ET₀</td><td>−{mm(e.et060)} mm{e.etMean !== null ? <span className="ar-muted"> · średnio {mm(e.etMean)} mm</span> : null}</td></tr>
          <tr><td>KBW = opad − parowanie</td><td><b>{mm(e.k60)} mm</b></td></tr>
          {e.threshold !== null ? <tr><td>{e.reference ? "Próg odniesienia (zboża jare)" : "Próg IUNG"}{e.window ? ` w oknie ${e.window}` : ""}</td><td>{mm(e.threshold)} mm</td></tr> : null}
          {e.rule && local.stage !== null ? <tr><td>Reguła</td><td>{e.rule}, więc stan wody to {signed(local.stage)}: {BAL[String(local.stage)]!.toLowerCase()}.</td></tr> : null}
        </tbody>
      </table>
      {driver ? <p className="rs-why__driver">{driver}</p> : null}
      <p className="ar-muted rs-why__note">Skala jest tymczasowa: wynika z KBW i progu IUNG. Backend doda bilans wody w glebie (FAO-56), zdjęcia satelitarne i model terenu. <Link to="/metoda">Jak liczymy</Link></p>
    </details>
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

interface CardItem { id: string; title: string; metrics: string[]; permit: boolean; image: string; sim?: Measure["sim"] }
function Retention({ local, backend, weatherFailed, picked, onPick }: { local: LocalResult | null; backend: AnalysisResponse | null; weatherFailed: boolean; picked: Set<string>; onPick: (id: string) => void }) {
  const recs = backend?.retention?.recommendations;
  if (!local && !recs && !weatherFailed) return <Skeleton lines={["Działania"]} />;
  const cards: CardItem[] = recs?.length
    ? [...recs].sort((a, b) => a.priority_rank - b.priority_rank).map((r) => {
      const look = measureLook(`${r.id} ${r.category}`);
      const metrics = [r.delta_taw_mm > 0 ? `+${dec(r.delta_taw_mm, 0)} mm wody` : null, r.runoff_reduction_pct > 0 ? `${dec(r.runoff_reduction_pct, 0)}% mniej spływu` : null].filter((x): x is string => !!x);
      return { id: r.id, title: r.title, metrics, permit: look.permit, image: look.image };
    })
    : ((local?.stage ?? 0) >= 2 ? WET_MEASURES : DRY_MEASURES).map((m) => ({ ...m, metrics: [m.effect] }));
  const wi = backend?.retention?.what_if_default;
  const groups = [{ title: "Bez urządzeń wodnych", items: cards.filter((c) => !c.permit) }, { title: "Z urządzeniem wodnym", items: cards.filter((c) => c.permit) }];
  return (
    <>
      {wi ? <p className="rs-lead">W suchym roku {wi.reference_dry_year} te działania zmniejszają stratę plonu z {dec(wi.baseline_yield_loss_pct, 0)}% do {dec(wi.scenario_yield_loss_pct, 0)}% i dają +{wi.delta_reserve_days} dni zapasu wody.</p> : null}
      {groups.filter((g) => g.items.length).map((g) => (
        <div key={g.title} className="rs-group">
          <h3 className="rs-subtitle">{g.title}</h3>
          <div className="rs-cards">{g.items.map((c) => (
            <ActionCard key={c.id} title={c.title} image={c.image} metrics={c.metrics} permit={c.permit} permitHref={c.permit ? PERMIT_URL : undefined}
              selected={picked.has(c.id)} onToggle={c.sim && local ? () => onPick(c.id) : undefined} />
          ))}</div>
        </div>
      ))}
    </>
  );
}

function WhatIf({ base, st, crop, date, picked, onPick }: { base: LocalResult; st: AnalysisState; crop: CropCode; date: Iso; picked: Set<string>; onPick: (id: string) => void }) {
  const measures = ((base.stage ?? 0) >= 2 ? WET_MEASURES : DRY_MEASURES).filter((m) => m.sim);
  const preset = useMemo<Levers>(() => measures.filter((m) => picked.has(m.id)).reduce((a, m) => ({ addMm: a.addMm + (m.sim?.addMm ?? 0), etCut: Math.min(0.5, a.etCut + (m.sim?.etCut ?? 0)) }), NO_LEVERS), [picked, measures]);
  const [own, setOwn] = useState<Levers | null>(null);
  useEffect(() => setOwn(null), [preset]);
  const lv = own ?? preset;
  const land = landForCalc(st.land);
  const sim = useMemo(() => {
    if (st.weather.status !== "ready" || !land || (!lv.addMm && !lv.etCut)) return null;
    return computeLocal(land, withLevers(st.weather.data, lv), crop, date, todayIso());
  }, [st.weather, land, lv, crop, date]);
  const dryYears = (r: LocalResult) => r.years.filter((y) => y.value !== null && y.value <= -3).length;
  return (
    <div className="ar-card rs-whatif">
      <p className="rs-lead">Zaznacz działania albo ustaw własne wartości. Rusałka przeliczy KBW i stan wody na tych samych danych pogodowych.</p>
      <div className="ar-form__chips">{measures.map((m) => <Pill key={m.id} pressed={picked.has(m.id)} onClick={() => onPick(m.id)}>{m.title}</Pill>)}</div>
      <div className="rs-sliders">
        <label><span>Woda zatrzymana w glebie: <b>+{lv.addMm} mm</b> na 60 dni</span>
          <input type="range" min={0} max={80} step={5} value={lv.addMm} onChange={(e) => setOwn({ ...lv, addMm: Number(e.target.value) })} />
        </label>
        <label><span>Mniej parowania: <b>−{Math.round(lv.etCut * 100)}%</b></span>
          <input type="range" min={0} max={30} step={1} value={Math.round(lv.etCut * 100)} onChange={(e) => setOwn({ ...lv, etCut: Number(e.target.value) / 100 })} />
        </label>
      </div>
      {sim ? (
        <table className="rs-compare">
          <thead><tr><th /><th>Teraz</th><th>Z działaniami</th></tr></thead>
          <tbody>
            <tr><td>Stan wody</td><td><WaterBalance size="sm" value={base.stage} /></td><td><WaterBalance size="sm" value={sim.stage} /></td></tr>
            <tr><td>KBW 60 dni</td><td>{mm(base.kbw60)} mm</td><td>{mm(sim.kbw60)} mm</td></tr>
            <tr><td>Okna z suszą IUNG w sezonie {base.season}</td><td>{base.droughtCount} z {base.knownCount}</td><td>{sim.droughtCount} z {sim.knownCount}</td></tr>
            <tr><td>Lata z suszą ({formatDayMonth(date)}, 10 lat)</td><td>{dryYears(base)} z {base.years.length}</td><td>{dryYears(sim)} z {sim.years.length}</td></tr>
          </tbody>
        </table>
      ) : <p className="ar-card__text">Wybierz działanie albo przesuń suwak, aby zobaczyć wynik.</p>}
      <p className="ar-muted rs-why__note">Założenia symulacji: {DRY_MEASURES.filter((m) => m.sim).map((m) => `${m.title.toLowerCase()} ${m.sim!.note}`).join(", ")}. Backend zastąpi je bilansem FAO-56 i modelem terenu.</p>
    </div>
  );
}

function Irrigation({ local, rl }: { local: LocalResult; rl: RlRecommendResponse | null }) {
  const fc = local.forecast;
  const strip = fc ? (
    <div className="rs-fc" aria-label="Prognoza opadu na 7 dni">
      {fc.days.map((d) => <div key={d.date} className="rs-fc__day"><span className="rs-fc__mm">{dec(d.p, 0)}</span><span className="rs-fc__d">{new Date(`${d.date}T12:00:00`).toLocaleDateString("pl-PL", { weekday: "short", day: "numeric" })}</span></div>)}
    </div>
  ) : null;
  if (rl?.status === "ok" && rl.recommendation) {
    const r = rl.recommendation;
    return <PhotoCard image="/img/rozlewiska-biebrza.jpg" title={r.action_now} text={rl.message}><p className="ar-card__text">Dawka dziś: {dec(r.dose_today_mm, 0)} mm · w 7 dni: {dec(r.total_7d_mm, 0)} mm.</p>{strip}</PhotoCard>;
  }
  const h = local.irrigation!;
  return <PhotoCard image="/img/rozlewiska-biebrza.jpg" title={h.title} text={h.text}>{strip}</PhotoCard>;
}

function Sources({ sources, backend, elapsed }: { sources: SourceEntry[]; backend: AnalysisResponse | null; elapsed: number | null }) {
  const rows = [...sources.map((s) => [s.name, s.what, timePl(s.retrievedAt), s.resolution, s.license] as const),
    ...(backend?.audit?.sources ?? []).map((s) => [s.source_name, s.category, timePl(new Date(s.retrieved_at)), s.spatial_resolution, s.license] as const)];
  if (!rows.length) return <Skeleton lines={["Źródła"]} />;
  return (
    <div className="ar-card rs-table">
      <table>
        <thead><tr><th>Źródło</th><th>Co daje</th><th>Pobrano</th><th>Rozdzielczość</th><th>Licencja</th></tr></thead>
        <tbody>{rows.map((r) => <tr key={r[0]}>{r.map((c, i) => <td key={i}>{c}</td>)}</tr>)}</tbody>
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
        <li>VITE_API_URL: {backendEnabled ? API_URL : "puste: kroki serwera są wyłączone"}</li>
        <li>POST /api/analyze: {st.backend.status}{st.backend.status === "error" ? ` (${st.backend.message})` : ""}</li>
        <li>POST /rl/recommend: {st.rl.status}{st.rl.status === "error" ? ` (${st.rl.message})` : ""}</li>
        <li>Próbka sekcji serwera bez backendu: dodaj <code>&amp;fixtures</code> do adresu.</li>
      </ul>
    </details>
  );
}
