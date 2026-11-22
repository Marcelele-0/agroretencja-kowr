import type { CSSProperties, ReactNode } from "react";
import { AbsoluteFill, Easing, Img, OffthreadVideo, Sequence, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import type { Box, Clip } from "./assets";
import { C, FONT, H, SCALE, W } from "./theme";

const clamp = { extrapolateLeft: "clamp", extrapolateRight: "clamp" } as const;
const ease = Easing.bezier(0.22, 1, 0.36, 1);

/** Film grain and a soft vignette over everything, so AI clips, photos and screen recordings read as one film. */
export function Finish() {
  const frame = useCurrentFrame();
  return (
    <AbsoluteFill style={{ pointerEvents: "none" }}>
      <svg width={W} height={H} style={{ position: "absolute", inset: 0, opacity: 0.07, mixBlendMode: "overlay" }}>
        <filter id="grain"><feTurbulence type="fractalNoise" baseFrequency="0.85" numOctaves={2} seed={frame % 12} stitchTiles="stitch" /></filter>
        <rect width="100%" height="100%" filter="url(#grain)" />
      </svg>
      <AbsoluteFill style={{ background: "radial-gradient(ellipse at center, transparent 55%, rgba(5,12,8,.45) 100%)" }} />
    </AbsoluteFill>
  );
}

export type Grade = "drought" | "wet" | "dawn" | "green" | "neutral";
const GRADE: Record<Grade, { filter: string; overlay: string; blend: CSSProperties["mixBlendMode"] }> = {
  drought: { filter: "saturate(.35) sepia(.6) hue-rotate(-12deg) contrast(1.12) brightness(1.02)", overlay: "linear-gradient(180deg, rgba(240,154,82,.30), rgba(176,46,28,.22))", blend: "soft-light" },
  wet: { filter: "saturate(.75) contrast(1.05) brightness(.88)", overlay: "linear-gradient(180deg, rgba(40,115,181,.30), rgba(19,38,95,.42))", blend: "multiply" },
  dawn: { filter: "saturate(.85) brightness(.9)", overlay: "linear-gradient(180deg, rgba(166,212,239,.18), rgba(15,26,20,.35))", blend: "multiply" },
  green: { filter: "saturate(1.05) contrast(1.04) brightness(.92)", overlay: "linear-gradient(180deg, rgba(98,184,108,.12), rgba(15,26,20,.38))", blend: "multiply" },
  neutral: { filter: "none", overlay: "transparent", blend: "normal" },
};

/** An AI clip when present, otherwise a licensed photo with a slow push-in. Both get the same grade. */
export function BRoll({ video, photo, grade, blur = 0, from = 1.06, to = 1.16 }: { video: string | null; photo: string; grade: Grade; blur?: number; from?: number; to?: number }) {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();
  const g = GRADE[grade];
  const s = interpolate(frame, [0, durationInFrames], [from, to], clamp);
  const style: CSSProperties = { width: "100%", height: "100%", objectFit: "cover", filter: `${g.filter}${blur ? ` blur(${blur}px)` : ""}` };
  return (
    <AbsoluteFill style={{ background: C.night, overflow: "hidden" }}>
      {video ? <OffthreadVideo src={staticFile(video)} muted style={{ ...style, transform: blur ? "scale(1.08)" : undefined }} />
        : <Img src={staticFile(photo)} style={{ ...style, transform: `scale(${s})` }} />}
      <AbsoluteFill style={{ background: g.overlay, mixBlendMode: g.blend }} />
    </AbsoluteFill>
  );
}

/** Large display line with a masked rise, in the Rusałka display style. */
export function Line({ children, at = 0, size = 96, weight = 500, color = C.photoInk, dim = false, style }: { children: ReactNode; at?: number; size?: number; weight?: number; color?: string; dim?: boolean; style?: CSSProperties }) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const p = spring({ frame: frame - at, fps, config: { damping: 200, mass: 0.9 } });
  const shown = interpolate(p, [0, 0.4], [0, 1], clamp) * (dim ? 0.6 : 1);
  return (
    <div style={{ overflow: "hidden", paddingBottom: size * 0.12, ...style }}>
      <div style={{
        transform: `translateY(${(1 - p) * 110}%)`, opacity: shown,
        font: `${weight} ${size}px/1 ${FONT}`, fontStretch: "86%", letterSpacing: "-0.035em", color,
        textShadow: "0 2px 24px rgba(5,12,8,.35)",
      }}>{children}</div>
    </div>
  );
}

/** Bottom-left smoked-glass caption, as the GlassCard of the design system. */
export function Caption({ title, body, at = 8, out, side = "right" }: { title: string; body?: string; at?: number; out?: number; side?: "left" | "right" }) {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();
  const p = spring({ frame: frame - at, fps, config: { damping: 200 } });
  const end = out ?? durationInFrames - 10;
  const o = interpolate(frame, [end - 8, end], [1, 0], clamp);
  return (
    <div style={{
      position: "absolute", [side]: 72, bottom: 72, maxWidth: 760, padding: "26px 30px 28px", borderRadius: 16,
      border: `1px solid ${C.glassLine}`, background: C.glassDark, backdropFilter: "blur(18px)", color: C.photoInk,
      opacity: p * o, transform: `translateY(${(1 - p) * 24}px)`, boxShadow: "0 22px 44px -22px #0b17108c",
    }}>
      <div style={{ font: `500 44px/1.05 ${FONT}`, fontStretch: "86%", letterSpacing: "-0.03em" }}>{title}</div>
      {body ? <div style={{ marginTop: 12, font: `400 22px/1.4 ${FONT}`, opacity: 0.82 }}>{body}</div> : null}
    </div>
  );
}

/** Rusałka mark: the ring and three waves draw on, then the name. */
export function Wordmark({ at = 0, size = 120, color = C.lime, ink = C.onNight }: { at?: number; size?: number; color?: string; ink?: string }) {
  const frame = useCurrentFrame() - at;
  const draw = (start: number, len: number) => interpolate(frame, [start, start + 22], [len, 0], { ...clamp, easing: ease });
  const name = interpolate(frame, [26, 46], [0, 1], clamp);
  return (
    <div style={{ display: "flex", alignItems: "center", gap: size * 0.3 }}>
      <svg width={size} height={size} viewBox="0 0 32 32" style={{ overflow: "visible" }}>
        <circle cx={16} cy={16} r={15} fill="none" stroke={color} strokeWidth={1.2} strokeDasharray={94.3} strokeDashoffset={draw(0, 94.3)} transform="rotate(-90 16 16)" />
        <g fill="none" stroke={color} strokeWidth={1.2} strokeLinecap="round">
          <path d="M7 12.5c3-2.6 6-2.6 9 0s6 2.6 9 0" strokeDasharray={22} strokeDashoffset={draw(8, 22)} />
          <path d="M7 17.5c3-2.6 6-2.6 9 0s6 2.6 9 0" strokeDasharray={22} strokeDashoffset={draw(13, 22)} />
          <path d="M10.5 22.5c1.8-1.5 3.7-1.5 5.5 0s3.7 1.5 5.5 0" strokeDasharray={14} strokeDashoffset={draw(18, 14)} />
        </g>
      </svg>
      <span style={{ font: `600 ${size * 0.62}px/1 ${FONT}`, fontStretch: "92%", letterSpacing: "-0.02em", color: ink, opacity: name, transform: `translateX(${(1 - name) * -16}px)` }}>Rusałka</span>
    </div>
  );
}

/** Scene cut covered by the water-state scale: eleven bands sweep across, dry to wet. */
export function BandWipe({ duration = 26 }: { duration?: number }) {
  const frame = useCurrentFrame();
  const bw = W / SCALE.length;
  return (
    <AbsoluteFill style={{ pointerEvents: "none" }}>
      {SCALE.map((c, i) => {
        const t0 = i * 1.1;
        const inP = interpolate(frame, [t0, t0 + duration / 2], [0, 1], { ...clamp, easing: ease });
        const outP = interpolate(frame, [t0 + duration / 2, t0 + duration], [0, 1], { ...clamp, easing: ease });
        const top = (1 - inP) * H;
        const bottom = outP * H;
        return <div key={c} style={{ position: "absolute", left: i * bw - 0.5, width: bw + 1, top: bottom > 0 ? 0 : top, height: H - (bottom > 0 ? bottom : top), background: c }} />;
      })}
    </AbsoluteFill>
  );
}

export interface ZoomKey { at: number; box?: Pick<Box, "x" | "y" | "w" | "h"> | null; scale?: number }

/** A screen recording that eases between framings: full frame or zoomed onto a UI box. */
export function ScreenClip({ clip, rate = 1, keys = [], offset = 0 }: { clip: Clip; rate?: number; keys?: ZoomKey[]; offset?: number }) {
  const frame = useCurrentFrame();
  const fit = Math.min(W / clip.width, H / clip.height);
  const framing = (k?: ZoomKey) => {
    if (!k?.box) return { s: k?.scale ?? 1, cx: clip.width / 2, cy: clip.height / 2 };
    const b = k.box;
    const s = k.scale ?? Math.min(2.1, Math.max(1.15, Math.min((clip.width * 0.82) / b.w, (clip.height * 0.82) / b.h)));
    return { s, cx: b.x + b.w / 2, cy: b.y + b.h / 2 };
  };
  const sorted = [...keys].sort((a, b) => a.at - b.at);
  let cur = framing(), nxt = framing(), p = 0;
  for (let i = 0; i < sorted.length; i++) {
    const k = sorted[i]!;
    if (frame >= k.at) { cur = framing(k); nxt = cur; p = 0; const n = sorted[i + 1]; if (n && frame >= n.at - 24) { nxt = framing(n); p = interpolate(frame, [n.at - 24, n.at], [0, 1], { ...clamp, easing: ease }); } }
    else if (i === 0 && frame >= k.at - 24) { nxt = framing(k); p = interpolate(frame, [k.at - 24, k.at], [0, 1], { ...clamp, easing: ease }); }
  }
  const s = cur.s + (nxt.s - cur.s) * p;
  let cx = cur.cx + (nxt.cx - cur.cx) * p;
  let cy = cur.cy + (nxt.cy - cur.cy) * p;
  // Keep the zoomed frame inside the recording.
  const halfW = clip.width / (2 * s), halfH = clip.height / (2 * s);
  cx = Math.min(Math.max(cx, halfW), clip.width - halfW);
  cy = Math.min(Math.max(cy, halfH), clip.height - halfH);
  const tx = clip.width / 2 - cx, ty = clip.height / 2 - cy;
  return (
    <AbsoluteFill style={{ background: C.night, alignItems: "center", justifyContent: "center", overflow: "hidden" }}>
      <div style={{ width: clip.width, height: clip.height, transform: `scale(${fit}) scale(${s}) translate(${tx}px, ${ty}px)`, transformOrigin: "center center" }}>
        <Sequence from={-Math.round(offset / rate)} layout="none">
          <OffthreadVideo src={staticFile(clip.file)} muted playbackRate={rate} style={{ width: clip.width, height: clip.height }} />
        </Sequence>
      </div>
    </AbsoluteFill>
  );
}

/** A plain phone body around a real mobile recording. */
export function Phone({ clip, rate = 1, height = 900 }: { clip: Clip; rate?: number; height?: number }) {
  const ratio = clip.width / clip.height;
  const sh = height, sw = Math.round(height * ratio);
  const bezel = 14;
  return (
    <div style={{ position: "relative", width: sw + bezel * 2, height: sh + bezel * 2, borderRadius: 64, background: C.nav, padding: bezel, boxShadow: "0 60px 120px -40px rgba(0,0,0,.7), inset 0 0 0 1.5px #ffffff22" }}>
      <div style={{ width: sw, height: sh, borderRadius: 50, overflow: "hidden", background: "#000" }}>
        <OffthreadVideo src={staticFile(clip.file)} muted playbackRate={rate} style={{ width: sw, height: sh, objectFit: "cover" }} />
      </div>
      <div style={{ position: "absolute", top: bezel + 12, left: "50%", width: 110, height: 30, marginLeft: -55, borderRadius: 20, background: "#000" }} />
    </div>
  );
}

/** Shown in place of a recording that does not exist yet (studio preview only). */
export function Missing({ name }: { name: string }) {
  return (
    <AbsoluteFill style={{ background: C.night, alignItems: "center", justifyContent: "center", color: C.muted, font: `500 32px/1.3 ${FONT}` }}>
      Brak nagrania „{name}”: uruchom pnpm capture
    </AbsoluteFill>
  );
}

export { clamp, ease };
