import { useEffect, useMemo, useState, type CSSProperties } from "react";
import { Link, Navigate, useSearchParams } from "react-router-dom";
import { API_URL, backendEnabled, reportPdfUrl } from "../api/backend";
import type { AnalysisResponse, RlCompareResponse, RlRecommendResponse, RlSource } from "../api/contract";
import { computeLocal, longestDrought, soilCause, withLevers, type Levers, type LocalResult } from "../analysis/local";
import { landForCalc, useAnalysis, usePortfolio, type AnalysisState, type PortfolioRow } from "../analysis/useAnalysis";
import { ActionCard, BAL, Button, CauseBreakdown, ConfidenceBadge, CropSelect, DateBar, dateBounds, FloatingNav, KbwWindows, MetricTile, PhotoCard, Pill, RiskChip, signed, StateTimeline, WaterBalance, Wordmark, YearCompare, type IconName } from "../ds";
import { DRY_MEASURES, measureLook, PERMIT_URL, WET_MEASURES, type Measure } from "../lib/catalog";
import { backendCrop, cropOf as cropInfo, DEFAULT_CROP, isCropCode, type CropCode } from "../lib/crops";
import { addDays, formatDayMonth, formatLong, isIso, monthName, parts, todayIso, type Iso } from "../lib/dates";
import { riskFromStage } from "../lib/kbw";
import type { LandUse } from "../lib/iung";
import type { SourceEntry } from "../lib/sources";
import { containsPoint } from "../lib/geo";
import { ParcelMap, type MapParcel, type MapZone } from "../map/ParcelMap";
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
  const [picked, setPicked] = useState<Set<string>>(new Set());

  const write = (next: { id: string; crop: CropCode }[], nextActive: string, extra: Record<string, string> = {}) => {
    const q = new URLSearchParams({ dzialki: next.map((x) => `${x.id}~${x.crop}`).join(","), aktywna: nextActive, data: date, ...extra });
    setSp(q);
  };

  const parcel = st.parcel.status === "ready" ? st.parcel.data : null;
  const land = st.land.status === "ready" ? st.land.data : null;
  const backend = st.backend.status === "ready" ? st.backend.data : null;
  const local = useMemo(() => withBackend(st.local, backend), [st.local, backend]);
  const rl = st.rl.status === "ready" ? st.rl.data : null;
  const weatherFailed = st.weather.status === "error" ? st.weather.message : null;
  const rlCompare = st.rlCompare.status === "ready" ? st.rlCompare.data : null;
  const showIrr = !!local && ((!local.offSeason && (rl?.status === "ok" || !!local.irrigation)) || !!rlCompare?.strategies?.length);

  useEffect(() => {
    const els = NAV.map((n) => document.getElementById(n.id)).filter((e): e is HTMLElement => !!e);
    const io = new IntersectionObserver((es) => { const v = es.filter((e) => e.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0]; if (v) setActive(v.target.id); }, { rootMargin: "-20% 0px -60% 0px" });
    els.forEach((e) => io.observe(e));
    return () => io.disconnect();
  }, [st.parcel.status, !!local, showIrr, activeId]);

  const zones = useMemo<MapZone[] | null>(() => backend?.map_layers?.zones?.features.map((f) => ({ geometry: f.geometry, value: f.properties.value })) ?? null, [backend]);
  const stageOf = (r: PortfolioRow) => (r.id === activeId ? local?.stage ?? r.local?.stage ?? null : r.local?.stage ?? null);
  /** Every parcel on the map, coloured by its water state; the expanded one has the hatch. */
  const mapParcels = useMemo<MapParcel[]>(() => portfolio.flatMap((r) => (r.parcel.status === "ready"
    ? [{ id: r.id, geometry: r.parcel.data.geometry, value: stageOf(r), active: r.id === activeId, label: `${r.parcel.data.number}, ${r.parcel.data.region}` }] : [])), [portfolio, activeId, local?.stage]);

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
  const remove = (id: string) => {
    const next = items.filter((x) => x.id !== id);
    if (!next.length) return;
    write(next, id === activeId ? next[Math.max(0, items.findIndex((x) => x.id === id) - 1)]!.id : activeId);
  };
  const togglePick = (id: string) => setPicked((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n; });

  const body = (
    <>
      {st.parcel.status === "error" ? <ErrorCard text={st.parcel.message} /> : null}
      {land?.kind === "nonAgri" && parcel ? <ErrorCard text={`${land.label}: działka ${parcel.number} nie jest gruntem rolnym. Rusałka analizuje grunty orne, łąki, pastwiska i sady.`} /> : null}
      {st.parcel.status !== "error" && land?.kind !== "nonAgri" ? (
        <>
          <section id="ocena" className="rs-section">
            {local ? <Assessment local={local} components={backend?.assessment?.components} onPickDate={(d) => write(items, activeId, { data: d })} /> : weatherFailed ? <ErrorCard text={weatherFailed} retry /> : <Skeleton lines={["Stan wody", "Okna IUNG", "10 lat"]} />}
          </section>
          <section id="przyczyny" className="rs-section">
            <h3 className="ar-section-title">{causesTitle(local)}</h3>
            <Causes local={local} backend={backend} land={landForCalc(st.land)} weatherFailed={!!weatherFailed} />
          </section>
          <section id="retencja" className="rs-section">
            <h3 className="ar-section-title">Od czego zacząć</h3>
            <Retention local={local} backend={backend} weatherFailed={!!weatherFailed} picked={picked} onPick={togglePick} />
          </section>
          {local && st.weather.status === "ready" ? (
            <section id="symulacja" className="rs-section">
              <h3 className="ar-section-title">Co jeśli</h3>
              <WhatIf base={local} st={st} crop={crop} date={date} picked={picked} onPick={togglePick} />
            </section>
          ) : null}
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
        {pdf ? <Button variant="light" arrow="down" href={pdf}>Pobierz raport PDF</Button> : <Button variant="light" arrow="down" onClick={() => window.print()}>Pobierz raport PDF</Button>}
      </header>

      <div className="rs-grid">
        <aside className="rs-side">
          <ParcelMap className="rs-side__map" parcels={mapParcels} bbox={parcel?.bbox} zones={zones} padding={{ top: 70, bottom: 40 }}
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
              return (
                <li key={r.id} className={`rs-pitem${on ? " is-on" : ""}`} style={{ "--state": stateVar(stage) } as CSSProperties}>
                  <div className="rs-pitem__row" role="button" tabIndex={0} aria-pressed={on}
                    onClick={() => show(r.id)} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); show(r.id); } }}>
                    <div className="rs-pitem__name">
                      <b>{p ? `Działka ${p.number}, ${p.region}` : r.id}</b>
                      <span>{cropInfo(r.crop).label}{p ? ` · ${ha(p.areaHa)} ha` : ""}{r.land?.kind === "mineral" ? ` · gleba kat. ${r.land.category}` : ""}</span>
                    </div>
                    <div className="rs-pitem__state">{r.local || (on && local) ? <WaterBalance size="sm" value={stage} /> : <span className="ar-muted">{r.land?.kind === "nonAgri" ? "grunt nierolny" : r.error ?? "…"}</span>}</div>
                    {r.local ? <div className="rs-pitem__mini"><StateTimeline compact days={r.local.timeline} selected={r.local.date} today={todayIso()} /></div> : <span />}
                    {items.length > 1 ? (
                      <span className="rs-pitem__remove" onClick={(e) => e.stopPropagation()}>
                        <Button variant="icon" size="sm" icon="minus" label={`Usuń działkę ${p?.number ?? r.id} z listy`} onClick={() => remove(r.id)} />
                      </span>
                    ) : null}
                  </div>
                </li>
              );
            })}
          </ol>

          <section className="rs-detail" aria-label="Wybrana działka" style={{ "--state": stateVar(local?.stage) } as CSSProperties}>
            <div className="rs-detail__head">
              <div>
                <h2 className="ar-section-title">{parcel ? `Działka ${parcel.number}, ${parcel.region}` : parcelId}</h2>
                {parcel ? <p className="ar-muted rs-detail__sub">gm. {parcel.commune}, pow. {parcel.county} · {ha(parcel.areaHa)} ha · {parcel.id}</p> : null}
              </div>
              <label className="rs-detail__crop"><span className="ar-form__label">Uprawa</span>
                <CropSelect value={crop} onChange={(c) => setCropOf(activeId, c)} label={`Uprawa na działce ${parcel?.number ?? parcelId}`} />
              </label>
            </div>
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
    </div>
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

function Assessment({ local, onPickDate, components }: { local: LocalResult; onPickDate: (d: Iso) => void; components?: NonNullable<AnalysisResponse["assessment"]>["components"] }) {
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
      <div className="ar-card rs-hero">
        <WaterBalance value={local.stage} caption={local.caption} />
        <div className="rs-status">
          {local.stage !== null ? <RiskChip level={local.risk.level} side={local.risk.side} veto={local.risk.veto} vetoReason={local.risk.vetoReason} /> : null}
          <ConfidenceBadge level={local.confidence.level} detail={local.confidence.detail} />
        </div>
        <Why local={local} components={components} />
      </div>
      <div className="ar-card">
        <StateTimeline title={local.timeline.some((d) => d.forecast) ? "Stan wody: ostatnie 45 dni i prognoza" : "Stan wody dzień po dniu"} days={local.timeline} selected={local.date} today={todayIso()} onPick={onPickDate} summary={timelineSummary(local)} />
      </div>
      <div className="rs-metrics">
        <MetricTile icon="drop" label="Opad 30 dni" value={mm(local.metrics.p30)} unit="mm" />
        <MetricTile icon="sun" label="Parowanie ET₀ 30 dni" value={mm(local.metrics.et030)} unit="mm" />
        <MetricTile icon="gauge" label="KBW 60 dni" value={mm(local.kbw60)} unit="mm" />
      </div>
      <details className="ar-card rs-more">
        <summary>Okna suszy IUNG w sezonie {local.season}: {local.knownCount ? `susza w ${local.droughtCount} z ${local.knownCount}` : "brak progu"}</summary>
        <KbwWindows title={`Sezon ${local.season} w oknach IUNG`} windows={local.windows} active={local.active} thresholdLabel={thLabel} />
      </details>
      <details className="ar-card rs-more">
        <summary>{formatDayMonth(local.date)} w ostatnich 10 latach: susza w {local.years.filter((x) => x.value !== null && x.value <= -3).length} z {local.years.length}</summary>
        <YearCompare title={mode === "day" ? `${formatDayMonth(local.date)} w latach ${span}` : `${monthName(m)} (stan na koniec miesiąca) w latach ${span}`} years={ys} highlightYear={y}>
          <div className="ar-form__chips">
            <Pill pressed={mode === "day"} onClick={() => setMode("day")}>Ten dzień</Pill>
            <Pill pressed={mode === "month"} onClick={() => setMode("month")}>Cały miesiąc</Pill>
          </div>
        </YearCompare>
      </details>
    </>
  );
}

/** Explanation: the exact numbers and the rule that produced the water state. */
function Why({ local, components }: { local: LocalResult; components?: NonNullable<AnalysisResponse["assessment"]>["components"] }) {
  const e = local.explanation;
  if (components?.length) {
    return (
      <details className="rs-why">
        <summary>Skąd ten wynik</summary>
        <table><tbody>{components.map((c) => <tr key={c.key}><td>{c.label}{c.weight !== undefined ? ` (waga ${dec(c.weight * 100, 0)}%)` : ""}</td><td><b>{c.value}</b>{c.detail ? <span className="ar-muted"> · {c.detail}</span> : null}{c.source ? <span className="ar-muted"> · {c.source}</span> : null}</td></tr>)}</tbody></table>
        <p className="ar-muted rs-why__note">Wynik z modelu serwera. <Link to="/metoda">Jak liczymy</Link></p>
      </details>
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
        <div className="rs-fc" aria-label="Plan nawadniania na 7 dni">
          {r.plan_7d.map((d) => <div key={d.date} className="rs-fc__day"><span className="rs-fc__mm">{dec(d.dose_mm, 0)}</span><span className="rs-fc__d">{new Date(`${d.date}T12:00:00`).toLocaleDateString("pl-PL", { weekday: "short", day: "numeric" })}</span></div>)}
        </div>
        <p className="ar-muted rs-why__note">Liczby to dawka w mm na dzień; plan zakłada prognozę opadu ECMWF.</p>
      </PhotoCard>
    );
  } else if (!local.offSeason && local.irrigation) {
    card = <PhotoCard image="/img/rozlewiska-biebrza.jpg" title={local.irrigation.title} text={local.irrigation.text}>{strip}</PhotoCard>;
  }
  const strategies = compare?.strategies ?? [];
  return (
    <>
      {card}
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
