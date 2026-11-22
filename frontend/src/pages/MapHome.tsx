import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { flushSync } from "react-dom";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { message } from "../analysis/useAnalysis";
import { Button, CropSelect, DateBar, Icon, ParcelPopover, ParcelSearch, Wordmark, cx } from "../ds";
import { DEFAULT_CROP, cropOf, isCropCode, type CropCode } from "../lib/crops";
import { todayIso, type Iso } from "../lib/dates";
import { unionBbox, type Position } from "../lib/geo";
import { searchPlace, type PlaceHit } from "../lib/geocoder";
import { landUseAt, type LandUse } from "../lib/iung";
import { weatherFor } from "../lib/openMeteo";
import { DEMO_PRESETS } from "../lib/presets";
import { ParcelNotFound, looksLikeParcelId, parcelById, parcelByPoint, type Parcel } from "../lib/uldk";
import { ParcelMap, type MapParcel } from "../map/ParcelMap";
import { ha } from "./shared";

type Land = LandUse | "loading" | "error";

function soilText(l: Land | undefined): string {
  if (!l || l === "loading") return "…";
  if (l === "error") return "brak danych";
  return l.kind === "mineral" ? `gleba kat. ${l.category}` : l.kind === "organic" ? "gleba organiczna" : l.label.toLowerCase();
}

export function MapHome() {
  const [sp, setSp] = useSearchParams();
  const nav = useNavigate();
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  /** Selected parcels, in the order the user picked them. */
  const [sel, setSel] = useState<Parcel[]>([]);
  const [land, setLand] = useState<Record<string, Land>>({});
  /** The parcel the popover describes: the last one clicked (selected or rejected). */
  const [focus, setFocus] = useState<Parcel | null>(null);
  /** Crop per parcel; a new parcel gets the crop chosen last. */
  const [crops, setCrops] = useState<Record<string, CropCode>>({});
  const [lastCrop, setLastCrop] = useState<CropCode>(DEFAULT_CROP);
  const cropsRef = useRef<Record<string, CropCode>>(crops);
  cropsRef.current = crops;
  const lastCropRef = useRef<CropCode>(lastCrop);
  lastCropRef.current = lastCrop;
  const [flyTo, setFlyTo] = useState<{ center: Position; zoom?: number; seq: number } | null>(null);
  const [placeHits, setPlaceHits] = useState<PlaceHit[]>([]);
  const [activePlace, setActivePlace] = useState(0);
  const [placeNote, setPlaceNote] = useState<string | null>(null);
  const [date, setDate] = useState<Iso>(todayIso());
  const seq = useRef(0);
  const [narrow, setNarrow] = useState(() =>
    typeof window !== "undefined" && window.matchMedia("(max-width: 720px)").matches,
  );

  useEffect(() => {
    if (typeof window === "undefined") return;
    const mq = window.matchMedia("(max-width: 720px)");
    setNarrow(mq.matches);
    const onChange = (e: MediaQueryListEvent) => setNarrow(e.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  /** Phones: the panel is a floating island that folds into a one-line bar. Remembered for the visit. */
  const [collapsed, setCollapsedState] = useState(() => {
    try { return sessionStorage.getItem("rs-panel-collapsed") === "1"; } catch { return false; }
  });
  const setCollapsed = (v: boolean) => {
    setCollapsedState(v);
    try { sessionStorage.setItem("rs-panel-collapsed", v ? "1" : "0"); } catch { /* storage blocked */ }
  };
  const homeRef = useRef<HTMLElement>(null);
  const panelRef = useRef<HTMLElement>(null);
  // The map controls sit just above the island, whatever its height (and during the fold transition).
  useEffect(() => {
    const home = homeRef.current, panel = panelRef.current;
    if (!home || !panel || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => home.style.setProperty("--rs-island-h", `${panel.offsetHeight}px`));
    ro.observe(panel);
    return () => ro.disconnect();
  }, []);
  /** Collapsed bar, nothing selected: open the panel and put the caret in the search (inside the tap, so phones show the keyboard). */
  const openSearch = () => {
    flushSync(() => setCollapsed(false));
    panelRef.current?.querySelector<HTMLInputElement>(".rs-panel__search input")?.focus();
  };

  const writeUrl = (ps: Parcel[], cropMap = cropsRef.current, fallback = lastCropRef.current) =>
    setSp(ps.length ? { dzialki: ps.map((p) => `${p.id}~${cropMap[p.id] ?? fallback}`).join(",") } : {}, { replace: true });

  const cropOfId = (id: string): CropCode => crops[id] ?? lastCrop;
  const setCropFor = (id: string, c: CropCode) => {
    const nextCrops = { ...cropsRef.current, [id]: c };
    cropsRef.current = nextCrops;
    lastCropRef.current = c;
    setCrops(nextCrops);
    setLastCrop(c);
    writeUrl(sel, nextCrops, c);
  };
  /** Freezes the crop of a newly added parcel, so a later change of the default does not move it. */
  const assignCrop = (id: string) => {
    if (cropsRef.current[id]) return cropsRef.current;
    const nextCrops = { ...cropsRef.current, [id]: lastCropRef.current };
    cropsRef.current = nextCrops;
    setCrops(nextCrops);
    return nextCrops;
  };

  const checkLand = (p: Parcel) => {
    setLand((m) => (m[p.id] && m[p.id] !== "error" ? m : { ...m, [p.id]: "loading" }));
    landUseAt(p.centroid[0], p.centroid[1]).then(
      (l) => {
        setLand((m) => ({ ...m, [p.id]: l }));
        // A forest, water or built-up parcel cannot be analysed: it leaves the selection, the popover explains why.
        if (l.kind === "nonAgri") setSel((s) => { const n = s.filter((x) => x.id !== p.id); writeUrl(n); return n; });
      },
      () => setLand((m) => ({ ...m, [p.id]: "error" })),
    );
  };

  /** Click or search: adds the parcel, or removes it when it is already selected. */
  const toggle = useCallback(async (get: () => Promise<Parcel>, fromSearch = false) => {
    const n = ++seq.current;
    setBusy(true); setError(null);
    try {
      const p = await get();
      if (n !== seq.current) return;
      setFocus(p);
      setSel((s) => {
        const has = s.some((x) => x.id === p.id);
        const next = has && !fromSearch ? s.filter((x) => x.id !== p.id) : has ? s : [...s, p];
        const nextCrops = !has ? assignCrop(p.id) : cropsRef.current;
        writeUrl(next, nextCrops, lastCropRef.current);
        return next;
      });
      checkLand(p);
      if (fromSearch) setText("");
    } catch (e) {
      if (n !== seq.current) return;
      setError(e instanceof ParcelNotFound ? "ULDK nie zna działki o tym numerze. Sprawdź identyfikator albo kliknij działkę na mapie." : message(e));
    } finally {
      if (n === seq.current) setBusy(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const entries = (sp.get("dzialki") ?? sp.get("dzialka") ?? "").split(",").filter(Boolean).map((e) => e.split("~"));
    const ids = entries.map((e) => e[0]!);
    const initialCrops = Object.fromEntries(entries.flatMap(([id, c]) => (isCropCode(c) ? [[id!, c]] : []))) as Record<string, CropCode>;
    cropsRef.current = initialCrops;
    setCrops(initialCrops);
    if (!ids.length) return;
    Promise.allSettled(ids.map((id) => parcelById(id))).then((rs) => {
      const ps = rs.flatMap((r) => (r.status === "fulfilled" ? [r.value] : []));
      setSel(ps); setFocus(ps[ps.length - 1] ?? null); ps.forEach(checkLand); writeUrl(ps, initialCrops, DEFAULT_CROP);
    });
    // Only the first render reads the URL; later changes come from this page.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const search = async (v: string) => {
    setError(null);
    setPlaceHits([]);
    setPlaceNote(null);
    if (looksLikeParcelId(v)) {
      void toggle(() => parcelById(v), true);
      return;
    }
    setBusy(true);
    try {
      const hits = await searchPlace(v);
      if (!hits.length) {
        setError("Nie znaleziono miejscowości ani działki. Wpisz nazwę miejscowości lub numer TERYT (np. 060408_2.0166.7).");
      } else {
        setFlyTo({ center: [hits[0]!.lon, hits[0]!.lat], zoom: 15.2, seq: Date.now() });
        setPlaceHits(hits);
        setActivePlace(0);
        setPlaceNote(`Przeniesiono do: ${hits[0]!.label}. Kliknij działkę na mapie.`);
      }
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  };
  const pick = (lon: number, lat: number) => {
    setPlaceHits([]);
    setPlaceNote(null);
    void toggle(() => parcelByPoint(lon, lat));
  };
  const remove = (id: string) => setSel((s) => { const n = s.filter((x) => x.id !== id); writeUrl(n); if (focus?.id === id) setFocus(null); return n; });

  const analyse = () => {
    const [first, ...rest] = sel;
    if (!first) return;
    const q = new URLSearchParams({ dzialki: sel.map((p) => `${p.id}~${cropOfId(p.id)}`).join(","), aktywna: first.id, data: date });
    void rest;
    nav(`/analiza?${q}`);
  };
  /** Weather is fetched on intent (pointer or focus on the panel), not on every click: Open-Meteo has a daily quota. */
  const prefetch = () => sel.forEach((p) => weatherFor(p.centroid[1], p.centroid[0], date).catch(() => undefined));

  const mapParcels = useMemo<MapParcel[]>(
    () => sel.map((p) => ({ id: p.id, geometry: p.geometry, active: p.id === focus?.id, cropLabel: cropOf(cropOfId(p.id)).label })),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [sel, focus, crops, lastCrop],
  );
  // The map fits all selected parcels; focus only moves the popover, and an empty selection leaves the view alone.
  const bbox = useMemo(() => unionBbox(sel.map((p) => p.bbox)), [sel]);
  const fl = focus ? land[focus.id] : undefined;
  const nonAgri = typeof fl === "object" && fl.kind === "nonAgri";
  const selected = !!focus && sel.some((p) => p.id === focus.id);

  const popover = focus ? (nonAgri ? (
    <ParcelPopover muted icon="leaf" title={`${(fl as LandUse).label}: działka ${focus.number}`} sub={focus.id}
      note="Rusałka analizuje tylko grunty rolne: orne, łąki, pastwiska i sady. Wybierz inną działkę." />
  ) : (
    <ParcelPopover title={`Działka ${focus.number}, ${focus.region}`} sub={focus.id}
      rows={[["Gmina", focus.commune], ["Pow. z granic", `${ha(focus.areaHa)} ha`], ["Gleba (IUNG)", soilText(fl).replace(/^gleba /, "")]]}
      note={selected ? "Kliknij ponownie, aby usunąć z wyboru." : "Usunięta z wyboru. Kliknij, aby dodać."} />
  )) : null;

  // Phones: keep the fitted parcels above the island (58vh open, a 62px bar folded, 12px off the bottom edge).
  const pad = narrow ? { top: 60, bottom: collapsed ? 100 : Math.round(window.innerHeight * 0.58) + 28, left: 24, right: 24 } : { top: 80, bottom: 80, left: 60, right: 60 };

  const sumTitle = !sel.length ? "Wybierz działki na mapie" : sel.length === 1 ? "Wybrana działka" : `Wybrane działki: ${sel.length}`;
  const sumSub = busy ? "Wczytuję działkę…" : error ? error
    : !sel.length ? "albo wyszukaj miejscowość"
    : sel.length === 1 ? `Działka ${sel[0]!.number}, ${sel[0]!.region} · ${ha(sel[0]!.areaHa)} ha`
    : `Razem ${ha(sel.reduce((s, p) => s + p.areaHa, 0))} ha`;

  return (
    <main className="rs-home" ref={homeRef}>
      <aside ref={panelRef} className={cx("rs-panel", collapsed && "is-collapsed")} aria-label="Wybór działek" onPointerEnter={prefetch} onFocus={prefetch}>
        <div className="rs-panel__top">
          <Link to="/" className="rs-panel__brand" aria-label="Rusałka, strona startowa"><Wordmark /></Link>
          <Link className="ar-btn ar-btn--outline" to="/metoda">Jak liczymy</Link>
          {/* Phones only: folds the island; folded, the same button carries the summary. */}
          <button type="button" className="rs-panel__toggle" aria-expanded={!collapsed} aria-controls="rs-panel-body"
            aria-label={collapsed ? `Pokaż panel. ${sumTitle}. ${sumSub}` : "Schowaj panel"} title={collapsed ? "Pokaż panel" : "Schowaj panel"}
            onClick={() => setCollapsed(!collapsed)}>
            <span className="rs-panel__chev"><Icon name="down" /></span>
            <span className="rs-panel__sum" aria-hidden="true"><b>{sumTitle}</b><span className={cx(error && !busy && "is-error")}>{sumSub}</span></span>
          </button>
          {sel.length ? <Button variant="primary" arrow className="rs-panel__go" onClick={analyse}>Analizuj</Button>
            : <Button variant="icon" icon="search" label="Szukaj miejscowości lub działki" className="rs-panel__find" onClick={openSearch} />}
        </div>
        <div className="rs-panel__body" id="rs-panel-body">
          <ParcelSearch className="rs-panel__search" value={text} onChange={setText} onSubmit={search} busy={busy} error={error} placeholder="Miejscowość lub numer działki, np. Gniezno" />
          {placeNote ? (
            <div className="rs-places">
              <p className="rs-places__note">{placeNote}</p>
              {placeHits.length > 1 ? (
                <ul className="rs-places__list">
                  {placeHits.map((h, i) => (
                    <li key={`${h.label}-${i}`}>
                      <button
                        type="button"
                        className={cx("rs-places__btn", i === activePlace && "is-on")}
                        onClick={() => {
                          setActivePlace(i);
                          setFlyTo({ center: [h.lon, h.lat], zoom: 15.2, seq: Date.now() });
                          setPlaceNote(`Przeniesiono do: ${h.label}. Kliknij działkę na mapie.`);
                        }}
                      >
                        <b>{h.label}</b>
                        {h.sub ? <span>{h.sub}</span> : null}
                      </button>
                    </li>
                  ))}
                </ul>
              ) : null}
            </div>
          ) : null}
          {sel.length ? (
            <>
              <div className="rs-panel__head">
                <h2 className="rs-panel__title">{sel.length === 1 ? "Wybrana działka" : `Wybrane działki: ${sel.length}`}</h2>
                <span className="ar-muted rs-panel__hint">Klikaj na mapie, aby dodać kolejne.</span>
              </div>
              <ul className="rs-panel__list">
                {sel.map((p) => (
                  <li key={p.id} className={p.id === focus?.id ? "is-on" : ""}>
                    <div className="rs-panel__item">
                      <button type="button" className="rs-panel__name" onClick={() => setFocus(p)}>
                        <b>Działka {p.number}, {p.region}</b>
                        <span>{p.commune} · {ha(p.areaHa)} ha · {soilText(land[p.id])}</span>
                      </button>
                      <CropSelect className="rs-panel__crop" value={cropOfId(p.id)} onChange={(c) => setCropFor(p.id, c)} label={`Uprawa na działce ${p.number}`} />
                    </div>
                    <Button variant="icon" size="sm" icon="minus" label={`Usuń działkę ${p.number} z wyboru`} onClick={() => remove(p.id)} />
                  </li>
                ))}
              </ul>
              <div className="rs-panel__form">
                <DateBar className="rs-panel__date" date={date} onDate={setDate} />
                <Button variant="primary" size="lg" arrow onClick={analyse}>{sel.length === 1 ? "Analizuj działkę" : `Analizuj działki (${sel.length})`}</Button>
              </div>
            </>
          ) : (
            <>
              <p className="rs-panel__empty">Kliknij działki na mapie albo wpisz miejscowość lub numer. Możesz wybrać kilka.</p>
              <div className="rs-presets" aria-label="Przykładowe analizy">
                <span className="ar-form__label">Przykłady (1 klik)</span>
                {DEMO_PRESETS.map((p) => (
                  <Link key={p.id} to={p.href} className="rs-preset">
                    <b>{p.title}</b>
                    <span>{p.sub}</span>
                  </Link>
                ))}
              </div>
            </>
          )}
        </div>
      </aside>
      <ParcelMap className="rs-home__map" onPick={pick} parcels={mapParcels} bbox={bbox} flyTo={flyTo} popover={popover} popoverAt={focus ? focus.centroid : null} padding={pad} />
    </main>
  );
}
