import { AbsoluteFill, Sequence, interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { ai, clip, keptLen, toKept, usesFallbackPhotos, type Clip } from "./assets";
import { BRoll, Caption, Line, Missing, Phone, ScreenClip, Wordmark, clamp, ease, type ZoomKey } from "./parts";
import { C, FONT, H, SCALE, W } from "./theme";

const Scrim = ({ side = "left" }: { side?: "left" | "bottom" }) => (
  <AbsoluteFill style={{ background: side === "left" ? `linear-gradient(90deg, ${C.scrim} 0%, ${C.scrimSoft} 48%, transparent 75%)` : `linear-gradient(0deg, ${C.scrim} 0%, transparent 60%)` }} />
);
const Block = ({ children, top = 360 }: { children: React.ReactNode; top?: number }) => (
  <div style={{ position: "absolute", left: 120, top, display: "grid", gap: 4 }}>{children}</div>
);

export function Hook() {
  return (
    <AbsoluteFill>
      <BRoll video={ai("01-susza")} photo="img/pola-konturowe.jpg" grade="drought" />
      <Scrim />
      <Sequence durationInFrames={118} layout="none">
        <Block><Line at={8}>Susza nie zaczyna się,</Line><Line at={18}>gdy liście więdną.</Line></Block>
      </Sequence>
      <Sequence from={118} layout="none">
        <Block><Line at={4}>Zaczyna się tygodnie wcześniej.</Line><Line at={16} dim>Pod ziemią.</Line></Block>
      </Sequence>
    </AbsoluteFill>
  );
}

/** Too little water, or too much: the scale marker runs from dry to wet. */
export function Extremes() {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();
  const t = interpolate(frame, [10, durationInFrames - 20], [0, 1], { ...clamp, easing: ease });
  const barW = 880, bw = barW / SCALE.length;
  return (
    <AbsoluteFill>
      <BRoll video={ai("02-nadmiar")} photo="img/rozlewiska-biebrza.jpg" grade="wet" />
      <Scrim />
      <Block top={330}><Line at={4}>Za mało wody.</Line><Line at={30} dim>Albo za dużo.</Line></Block>
      <div style={{ position: "absolute", left: 120, top: 640, width: barW, opacity: interpolate(frame, [20, 34], [0, 1], clamp) }}>
        <div style={{ display: "flex", height: 14, borderRadius: 7, overflow: "hidden" }}>{SCALE.map((c) => <div key={c} style={{ flex: 1, background: c }} />)}</div>
        <div style={{ position: "absolute", top: -9, left: t * (barW - bw) + bw / 2 - 16, width: 32, height: 32, borderRadius: 16, border: `3px solid ${C.photoInk}`, boxShadow: "0 4px 14px rgba(0,0,0,.4)" }} />
        <div style={{ display: "flex", justifyContent: "space-between", marginTop: 18, color: C.photoInk, font: `500 22px/1 ${FONT}`, opacity: 0.85 }}><span>susza</span><span>optimum</span><span>zalanie</span></div>
      </div>
    </AbsoluteFill>
  );
}

const SOURCES: [string, string][] = [
  ["IUNG-PIB", "gleby i progi suszy dla 14 upraw"],
  ["ISRIC SoilGrids", "ile wody zatrzyma gleba"],
  ["ERA5 · Open-Meteo", "opad i parowanie, 10 lat wstecz"],
  ["ECMWF", "51 wariantów prognozy na 14 dni"],
  ["IMGW-PIB", "pomiary z najbliższej stacji"],
  ["Sentinel-2", "wilgotność łanu według fazy uprawy"],
  ["GUGiK NMT", "teren, spadki i spływ"],
  ["GUGiK BDOT10k", "rowy, cieki i zbiorniki"],
  ["GUGiK ULDK", "granice działek i ortofotomapa"],
  ["NASA POWER", "zapas danych pogodowych"],
];

/** Scattered data: thin-line board in the GridFrame style. */
export function Sources() {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const grid = interpolate(frame, [0, 30], [0, 1], clamp);
  return (
    <AbsoluteFill style={{ background: C.night, color: C.onNight }}>
      <svg width={W} height={H} style={{ position: "absolute", inset: 0, opacity: 0.5 * grid }}>
        {[1, 2, 3].map((i) => <line key={`v${i}`} x1={(W / 4) * i} y1={0} x2={(W / 4) * i} y2={H} stroke={C.lineNight} strokeWidth={1} />)}
        {[1, 2].map((i) => <line key={`h${i}`} x1={0} y1={(H / 3) * i} x2={W} y2={(H / 3) * i} stroke={C.lineNight} strokeWidth={1} />)}
      </svg>
      <div style={{ position: "absolute", left: 120, top: 200, width: 640 }}>
        <Line at={4} size={84}>Dane o wodzie</Line>
        <Line at={12} size={84}>są rozproszone.</Line>
        <Sequence from={130} layout="none"><div style={{ marginTop: 36 }}><Line at={0} size={40} weight={400} color={C.lime}>Rolnik nie ma czasu ich składać.</Line></div></Sequence>
      </div>
      <div style={{ position: "absolute", left: 800, top: 190, width: 1010, display: "grid", gridTemplateColumns: "1fr 1fr", columnGap: 40 }}>
        {SOURCES.map(([name, what], i) => {
          const p = spring({ frame: frame - 16 - i * 5, fps, config: { damping: 200 } });
          return (
            <div key={name} style={{ display: "grid", gap: 6, padding: "20px 0 22px", borderTop: `1px solid ${C.lineNight}`, opacity: p, transform: `translateY(${(1 - p) * 18}px)` }}>
              <span style={{ font: `600 30px/1.1 ${FONT}`, letterSpacing: "-0.02em" }}>{name}</span>
              <span style={{ font: `400 21px/1.3 ${FONT}`, color: C.muted }}>{what}</span>
            </div>
          );
        })}
      </div>
    </AbsoluteFill>
  );
}

/** The scale rises as eleven columns, folds into the brand bar, and the wordmark draws on. */
export function Reveal() {
  const frame = useCurrentFrame();
  const n = SCALE.length, colW = W / n;
  const barW = 660, segW = barW / n, barX = (W - barW) / 2, barY = 660, barH = 12;
  const fold = interpolate(frame, [44, 78], [0, 1], { ...clamp, easing: ease });
  return (
    <AbsoluteFill style={{ background: C.night }}>
      {SCALE.map((c, i) => {
        const rise = interpolate(frame, [i * 2, i * 2 + 26], [0, 1], { ...clamp, easing: ease });
        const x = i * colW + (barX + i * segW - i * colW) * fold;
        const w = colW + (segW - colW) * fold;
        const hFull = H * rise;
        const h = hFull + (barH - hFull) * fold;
        const y = H - hFull + (barY - (H - hFull)) * fold;
        return <div key={c} style={{ position: "absolute", left: x - 0.5, top: y, width: w + 1, height: h, background: c, borderRadius: fold > 0.9 ? 0 : 0 }} />;
      })}
      <AbsoluteFill style={{ alignItems: "center", justifyContent: "center", paddingBottom: 120 }}>
        <Sequence from={74} layout="none"><Wordmark size={150} /></Sequence>
      </AbsoluteFill>
      <div style={{ position: "absolute", top: barY + 46, width: "100%", textAlign: "center" }}>
        <Sequence from={104} layout="none"><Line at={0} size={46} weight={400}>Stan wody na każdej działce rolnej w Polsce.</Line></Sequence>
      </div>
    </AbsoluteFill>
  );
}

/** Aerial into the real orthophoto map (match cut when the AI drone clip exists). */
export function Ortho() {
  const c = clip("ortho_zoom");
  const drone = ai("03-z-gory");
  return (
    <AbsoluteFill style={{ background: C.night }}>
      {c ? <Sequence from={drone ? 54 : 0}><ScreenClip clip={c} rate={drone ? 1.4 : 1.25} /></Sequence> : <Missing name="ortho_zoom" />}
      {drone ? <Sequence durationInFrames={60}><BRoll video={drone} photo="img/pola-konturowe.jpg" grade="neutral" from={1} to={1} /></Sequence> : null}
      <Sequence from={drone ? 66 : 14} layout="none">
        <div style={{ position: "absolute", left: 120, top: 110 }}><Line at={0} size={64}>Każde pole. Cała Polska.</Line></div>
      </Sequence>
      <div style={{ position: "absolute", right: 40, bottom: 28, color: C.photoInk, font: `500 18px/1 ${FONT}`, opacity: 0.75 }}>Ortofotomapa © GUGiK</div>
    </AbsoluteFill>
  );
}

const box = (c: Clip, name: string) => c.boxes[name] ?? null;
const mark = (c: Clip, name: string, rate: number, fallback = 0) => Math.round(toKept(c, c.marks[name] ?? fallback) / rate);

export const RATES = { map_search: 1.2, analysis_load: 1, irrigation: 1, today: 1, crops: 1.15, replay: 1, detail_scroll: 1.25, mobile: 1.1 } as const;
export const MAX = { replay: 180, detail_scroll: 300 } as const;

export function Search() {
  const c = clip("map_search");
  if (!c) return <Missing name="map_search" />;
  const r = RATES.map_search;
  const keys: ZoomKey[] = [
    { at: Math.max(1, mark(c, "searchSubmit", r) - 40), box: box(c, "searchInput"), scale: 1.7 },
    { at: mark(c, "parcelSelected", r, c.durationInFrames * 0.4), box: null },
    { at: mark(c, "listReady", r, c.durationInFrames * 0.8), box: box(c, "list"), scale: 1.45 },
  ];
  return (
    <AbsoluteFill>
      <ScreenClip clip={c} rate={r} keys={keys} />
      <Caption title="Znajdź swoją działkę" body="Wpisz numer albo kliknij ją na mapie. Dodaj sąsiednie pola." at={10} />
    </AbsoluteFill>
  );
}

export function Analysis() {
  const c = clip("analysis_load");
  if (!c) return <Missing name="analysis_load" />;
  const r = RATES.analysis_load;
  const zones = mark(c, "zonesVisible", r, c.durationInFrames * 0.5);
  const keys: ZoomKey[] = [
    { at: zones + 18, box: box(c, "map"), scale: 1.35 },
    { at: Math.round(keptLen(c) / r) - 50, box: box(c, "firstHistogram") ?? box(c, "cards"), scale: 1.8 },
  ];
  return (
    <AbsoluteFill>
      <ScreenClip clip={c} rate={r} keys={keys} />
      <Caption title="Stan wody na każdym polu" body="Od suszy po zalanie. Strefy pokazują, gdzie woda ucieka, a gdzie się zbiera." at={zones} />
    </AbsoluteFill>
  );
}

/** The core feature: irrigate or hold off today, and the 14-day plan. */
export function Irrigation() {
  const c = clip("irrigation");
  if (!c) return <Missing name="irrigation" />;
  const r = RATES.irrigation;
  const decision = mark(c, "decision", r, 80);
  const plan = mark(c, "plan", r, 210);
  const keys: ZoomKey[] = [
    { at: decision + 10, box: { x: 36, y: 380, w: 560, h: 300 }, scale: 1.75 },
    { at: plan + 6, box: { x: 36, y: 77, w: 560, h: 330 }, scale: 1.75 },
  ];
  return (
    <AbsoluteFill>
      <ScreenClip clip={c} rate={r} keys={keys} />
      <Caption title="Podlać czy poczekać?" body="Decyzja na dziś z powodem, a do tego plan na 14 dni: dawka i szansa, że trzeba będzie podlać." at={decision} />
    </AbsoluteFill>
  );
}

/** "Dziś": irrigate or hold off today, why, and the 14-day soil-water forecast. */
export function Today() {
  const c = clip("today");
  if (!c) return <Missing name="today" />;
  const r = RATES.today;
  const decision = mark(c, "decision", r, 75);
  const plan = mark(c, "plan", r, 200);
  const keys: ZoomKey[] = [
    { at: decision + 10, box: { x: 36, y: 300, w: 560, h: 380 }, scale: 1.7 },
    { at: plan + 6, box: { x: 36, y: 77, w: 560, h: 420 }, scale: 1.65 },
  ];
  return (
    <AbsoluteFill>
      <ScreenClip clip={c} rate={r} keys={keys} />
      <Caption title="Podlać czy poczekać?" body="Decyzja na dziś z powodem i prognoza wody w glebie na 14 dni, z 51 wariantów prognozy ECMWF." at={decision} />
    </AbsoluteFill>
  );
}

/** Crop choice and picked measures change the water state. */
export function Crops() {
  const c = clip("crops");
  if (!c) return <Missing name="crops" />;
  const r = RATES.crops;
  const crops = mark(c, "crops", r, 100);
  const shift = mark(c, "shift", r, 420);
  const keys: ZoomKey[] = [
    { at: crops + 8, box: box(c, "crops") ?? { x: 36, y: 85, w: 547, h: 600 }, scale: 1.45 },
    { at: shift + 6, box: box(c, "shift") ?? { x: 55, y: 421, w: 509, h: 114 }, scale: 1.8 },
  ];
  return (
    <AbsoluteFill>
      <ScreenClip clip={c} rate={r} keys={keys} />
      <Caption title="Uprawa i działania zmieniają wynik" body="Ta sama działka przy innej uprawie oraz stan wody i strata plonu po wybranych działaniach." at={crops} />
    </AbsoluteFill>
  );
}

export function Replay() {
  const c = clip("replay");
  if (!c) return <Missing name="replay" />;
  return (
    <AbsoluteFill>
      <ScreenClip clip={c} rate={RATES.replay} />
      <Caption title="Cały sezon w kilka sekund" body="Cofnij się do suszy 2019 albo sprawdź prognozę na najbliższe dni." at={8} />
    </AbsoluteFill>
  );
}

export function Measures() {
  const c = clip("detail_scroll");
  if (!c) return <Missing name="detail_scroll" />;
  const r = RATES.detail_scroll;
  const keys: ZoomKey[] = [
    { at: mark(c, "cardClick", r, 20) + 6, box: box(c, "activeCard"), scale: 1.5 },
    { at: mark(c, "measuresVisible", r, c.durationInFrames * 0.6), box: box(c, "measures"), scale: 1.55 },
  ];
  return (
    <AbsoluteFill>
      <ScreenClip clip={c} rate={r} keys={keys} />
      <Caption title="Od czego zacząć" body="Działania uszeregowane według zysku wody, z symulacją efektu na działce." at={mark(c, "measuresVisible", r, 60) - 10} />
    </AbsoluteFill>
  );
}

export function Mobile() {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const c = clip("mobile");
  const p = spring({ frame: frame - 6, fps, config: { damping: 18, mass: 0.9 } });
  return (
    <AbsoluteFill>
      <BRoll video={ai("04-pole-swit")} photo="img/pola-konturowe.jpg" grade="dawn" blur={ai("04-pole-swit") ? 0 : 10} />
      <Scrim />
      <Block top={380}><Line at={10}>W telefonie,</Line><Line at={20} dim>na miedzy.</Line>
        <div style={{ marginTop: 20 }}><Line at={34} size={34} weight={400}>Bez instalacji i bez zakładania konta.</Line></div>
      </Block>
      <div style={{ position: "absolute", right: 260, top: 60, transform: `translateY(${(1 - p) * 220}px) rotate(${(1 - p) * 4 - 2}deg)`, opacity: Math.min(1, p * 1.4) }}>
        {c ? <Phone clip={c} rate={RATES.mobile} height={920} /> : null}
      </div>
    </AbsoluteFill>
  );
}

export function Proof() {
  return (
    <AbsoluteFill>
      <BRoll video={ai("05-makro")} photo="img/trawa-rosa.jpg" grade="green" />
      <AbsoluteFill style={{ background: "rgba(10,18,13,.45)" }} />
      <AbsoluteFill style={{ alignItems: "center", justifyContent: "center", textAlign: "center" }}>
        <div style={{ display: "grid", gap: 10 }}>
          <Line at={6} size={78}>Metoda IUNG-PIB.</Line>
          <Line at={26} size={78}>Dane publiczne.</Line>
          <Line at={46} size={78} dim>Za darmo, dla każdej działki.</Line>
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
}

export function Outro() {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();
  const fadeOut = interpolate(frame, [durationInFrames - 24, durationInFrames], [0, 1], clamp);
  return (
    <AbsoluteFill>
      <BRoll video={ai("06-final")} photo="img/pola-konturowe.jpg" grade="green" from={1.18} to={1.06} />
      <AbsoluteFill style={{ background: "radial-gradient(ellipse at center, rgba(10,18,13,.55) 0%, rgba(10,18,13,.75) 70%)" }} />
      <AbsoluteFill style={{ alignItems: "center", justifyContent: "center", gap: 40, paddingBottom: 40 }}>
        <Wordmark size={130} at={6} ink={C.photoInk} />
        <Sequence from={40} layout="none"><Line at={0} size={52} weight={400}>Woda na twoim polu, zanim jej zabraknie.</Line></Sequence>
      </AbsoluteFill>
      <div style={{ position: "absolute", left: 0, right: 0, bottom: 44, textAlign: "center", color: C.photoInk, font: `400 17px/1.5 ${FONT}`, opacity: 0.7 * interpolate(frame, [60, 80], [0, 1], clamp) }}>
        HackNation 2026 · wyzwanie KOWR
        {usesFallbackPhotos ? <div>Zdjęcia: Erwin Cole, USDA NRCS (domena publiczna); Frank Vassen (CC BY 2.0); Leon Brooks (domena publiczna). Ortofotomapa © GUGiK.</div> : <div>Ortofotomapa © GUGiK.</div>}
      </div>
      <AbsoluteFill style={{ background: C.night, opacity: fadeOut }} />
    </AbsoluteFill>
  );
}
