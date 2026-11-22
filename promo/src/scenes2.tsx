import { AbsoluteFill, Img, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import { Line, clamp, ease } from "./parts";
import { C, FONT, W } from "./theme";

const STEPS: [string, string][] = [
  ["Dane publiczne", "gleba, pogoda, prognoza, satelita i teren z 10 źródeł"],
  ["Progi suszy IUNG-PIB", "bilans opadu i parowania w 14 oknach, dla uprawy i gleby"],
  ["Bilans wody FAO-56", "woda w strefie korzeni dzień po dniu, z fazą rozwoju"],
  ["Satelita i HydroFusion-PL", "wilgotność łanu ważona fazą uprawy, także w dni z chmurami"],
  ["Agent RL", "podlać czy poczekać, plan na 14 dni z prognozy ECMWF"],
];

/** How a decision is made: data flows left to right through the models; a pulse lights each step. */
export function Models() {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const x0 = 300, x1 = W - 300, y = 540, n = STEPS.length;
  const xs = STEPS.map((_, i) => x0 + ((x1 - x0) * i) / (n - 1));
  const travel = interpolate(frame, [30, 150], [0, 1], { ...clamp, easing: ease });
  const px = x0 + (x1 - x0) * travel;
  const line = interpolate(frame, [8, 40], [0, 1], { ...clamp, easing: ease });
  return (
    <AbsoluteFill style={{ background: C.night, color: C.onNight }}>
      <div style={{ position: "absolute", left: 140, top: 200 }}>
        <Line at={4} size={84}>Jak powstaje decyzja</Line>
      </div>
      <svg width={W} height={1080} style={{ position: "absolute", inset: 0 }}>
        <line x1={x0} y1={y} x2={x0 + (x1 - x0) * line} y2={y} stroke={C.lineNight} strokeWidth={1.5} />
        <line x1={x0} y1={y} x2={px} y2={y} stroke={C.lime} strokeWidth={2} opacity={travel > 0 ? 0.9 : 0} />
        {travel > 0 && travel < 1 ? <circle cx={px} cy={y} r={7} fill={C.lime} /> : null}
      </svg>
      {STEPS.map(([name, what], i) => {
        const lit = px >= xs[i]! - 1;
        const p = spring({ frame: frame - 14 - i * 6, fps, config: { damping: 200 } });
        const glow = lit ? 1 : 0;
        return (
          <div key={name} style={{ position: "absolute", left: xs[i]! - 165, top: y - 11, width: 330, textAlign: "center", opacity: p }}>
            <div style={{ width: 22, height: 22, margin: "0 auto", borderRadius: 11, border: `2px solid ${lit ? C.lime : C.lineNight}`, background: lit ? C.lime : C.night, boxShadow: glow ? `0 0 24px ${C.lime}66` : "none" }} />
            <div style={{ marginTop: 34, font: `600 30px/1.1 ${FONT}`, letterSpacing: "-0.02em", whiteSpace: "nowrap", color: lit ? C.onNight : C.muted }}>{name}</div>
            <div style={{ marginTop: 12, padding: "0 22px", font: `400 21px/1.35 ${FONT}`, color: C.muted }}>{what}</div>
          </div>
        );
      })}
      <div style={{ position: "absolute", left: 140, bottom: 200, font: `400 30px/1.3 ${FONT}`, color: C.lime, opacity: interpolate(frame, [150, 170], [0, 1], clamp) }}>
        Każdy wynik w aplikacji ma podane źródło i czas pobrania danych.
      </div>
    </AbsoluteFill>
  );
}

const IDEAS: [string, string][] = [
  ["Widget „co dziś zrobić”", "raz wybrana działka i uprawa, decyzja na ekranie telefonu"],
  ["Poranne powiadomienie", "zanim przyjdzie susza albo ulewa"],
  ["Czujniki wilgotności w glebie", "pomiar z pola poprawia bilans wody działki"],
  ["Mapa suszy dla gminy", "wszystkie pola obrębu dla spółki wodnej i doradcy"],
];

/** "What's next": the widget concept on a phone home screen, with today's real values, and the next ideas. */
export function Future() {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const phone = spring({ frame: frame - 6, fps, config: { damping: 18, mass: 0.9 } });
  const widget = spring({ frame: frame - 34, fps, config: { damping: 14 } });
  const pw = 400, ph = 820;
  return (
    <AbsoluteFill style={{ background: C.night, color: C.onNight }}>
      <div style={{ position: "absolute", left: 140, top: 150, width: 860 }}>
        <Line at={4} size={96}>Co dalej</Line>
        <div style={{ marginTop: 40 }}>
          {IDEAS.map(([name, what], i) => {
            const p = spring({ frame: frame - 26 - i * 14, fps, config: { damping: 200 } });
            return (
              <div key={name} style={{ display: "grid", gap: 6, padding: "22px 0 24px", borderTop: `1px solid ${C.lineNight}`, opacity: p, transform: `translateX(${(1 - p) * 30}px)` }}>
                <span style={{ font: `600 34px/1.1 ${FONT}`, letterSpacing: "-0.02em", color: i === 0 ? C.lime : C.onNight }}>{name}</span>
                <span style={{ font: `400 23px/1.3 ${FONT}`, color: C.muted }}>{what}</span>
              </div>
            );
          })}
        </div>
      </div>
      <div style={{ position: "absolute", right: 250, top: 110, transform: `translateY(${(1 - phone) * 260}px)`, opacity: Math.min(1, phone * 1.4) }}>
        <div style={{ position: "relative", width: pw, height: ph, borderRadius: 60, background: C.nav, padding: 14, boxShadow: "0 60px 120px -40px rgba(0,0,0,.8), inset 0 0 0 1.5px #ffffff22" }}>
          <div style={{ position: "relative", width: pw - 28, height: ph - 28, borderRadius: 47, overflow: "hidden" }}>
            <Img src={staticFile("img/pola-konturowe.jpg")} style={{ position: "absolute", inset: 0, width: "100%", height: "100%", objectFit: "cover", filter: "blur(6px) saturate(.9) brightness(.6)", transform: "scale(1.15)" }} />
            <div style={{ position: "absolute", top: 18, left: 30, right: 30, display: "flex", justifyContent: "space-between", font: `600 17px/1 ${FONT}`, color: "#fff" }}><span>7:30</span><span>● ● ●</span></div>
            <div style={{ position: "absolute", top: 18, left: "50%", width: 100, height: 28, marginLeft: -50, borderRadius: 16, background: "#000" }} />
            {/* The widget: smoked glass of the design system, real values of today's analysis of parcel 520/1. */}
            <div style={{ position: "absolute", left: 18, right: 18, top: 74, padding: "20px 22px 22px", borderRadius: 28, border: `1px solid ${C.glassLine}`, background: C.glassDark, backdropFilter: "blur(18px)", color: "#fff", transform: `scale(${0.85 + 0.15 * widget})`, opacity: widget, transformOrigin: "top center" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 10, font: `500 16px/1 ${FONT}`, opacity: 0.8 }}>
                <svg width={22} height={22} viewBox="0 0 32 32"><circle cx={16} cy={16} r={15} fill="none" stroke={C.lime} strokeWidth={1.6} /><g fill="none" stroke={C.lime} strokeWidth={1.6} strokeLinecap="round"><path d="M7 12.5c3-2.6 6-2.6 9 0s6 2.6 9 0" /><path d="M7 17.5c3-2.6 6-2.6 9 0s6 2.6 9 0" /></g></svg>
                Rusałka · dziś
              </div>
              <div style={{ marginTop: 14, font: `600 34px/1.05 ${FONT}`, letterSpacing: "-0.02em" }}>Nie podlewaj teraz</div>
              <div style={{ marginTop: 6, font: `400 17px/1.3 ${FONT}`, opacity: 0.8 }}>Działka 520/1 · kukurydza</div>
              <div style={{ marginTop: 18, display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                <div><div style={{ font: `400 14px/1.2 ${FONT}`, opacity: 0.7 }}>Woda w glebie</div><div style={{ font: `600 26px/1.1 ${FONT}` }}>59%</div></div>
                <div><div style={{ font: `400 14px/1.2 ${FONT}`, opacity: 0.7 }}>Opad w 7 dni</div><div style={{ font: `600 26px/1.1 ${FONT}` }}>13 mm</div></div>
              </div>
            </div>
            <div style={{ position: "absolute", left: 26, right: 26, top: 340, display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 22, opacity: 0.5 }}>
              {Array.from({ length: 8 }, (_, i) => <div key={i} style={{ aspectRatio: "1", borderRadius: 18, background: "rgba(255,255,255,.18)" }} />)}
            </div>
            <div style={{ position: "absolute", left: 18, right: 18, bottom: 18, height: 92, borderRadius: 30, background: "rgba(255,255,255,.14)" }} />
          </div>
        </div>
        <div style={{ marginTop: 22, textAlign: "center", font: `400 19px/1.4 ${FONT}`, color: C.muted, opacity: interpolate(frame, [60, 80], [0, 1], clamp) }}>
          Projekt widgetu. Wartości z dzisiejszej analizy działki.
        </div>
      </div>
    </AbsoluteFill>
  );
}
