import type { FC } from "react";
import { AbsoluteFill, Audio, Sequence, interpolate, staticFile, useCurrentFrame } from "remotion";
import { audio, clip, keptLen } from "./assets";
import { BandWipe, Finish, clamp } from "./parts";
import { AiRole, Future, Models, Shares } from "./scenes2";
import { Analysis, Crops, Extremes, Hook, MAX, Measures, Today, Mobile, Ortho, Outro, Proof, RATES, Replay, Reveal, Search, Sources } from "./scenes";

type Enter = "cut" | "fade" | "wipe";
interface Scene { id: string; dur: number; enter: Enter; C: FC }

const FADE = 14;
const WIPE = 26;
const clipDur = (name: keyof typeof RATES, fallback: number, max = 600) => {
  const c = clip(name);
  return c ? Math.min(max, Math.round(keptLen(c) / RATES[name])) : fallback;
};

const SCENES: Scene[] = [
  { id: "hook", dur: 186, enter: "cut", C: Hook },
  { id: "extremes", dur: 126, enter: "fade", C: Extremes },
  { id: "sources", dur: 216, enter: "fade", C: Sources },
  { id: "reveal", dur: 176, enter: "cut", C: Reveal },
  { id: "ortho", dur: 132, enter: "wipe", C: Ortho },
  { id: "search", dur: clipDur("map_search", 300), enter: "fade", C: Search },
  { id: "analysis", dur: clipDur("analysis_load", 270), enter: "fade", C: Analysis },
  { id: "today", dur: clipDur("today", 330), enter: "fade", C: Today },
  { id: "replay", dur: clipDur("replay", 240, MAX.replay), enter: "fade", C: Replay },
  { id: "measures", dur: clipDur("detail_scroll", 300, MAX.detail_scroll), enter: "fade", C: Measures },
  { id: "crops", dur: clipDur("crops", 480), enter: "fade", C: Crops },
  { id: "models", dur: 240, enter: "wipe", C: Models },
  { id: "shares", dur: 300, enter: "fade", C: Shares },
  { id: "airole", dur: 300, enter: "fade", C: AiRole },
  { id: "mobile", dur: 170, enter: "fade", C: Mobile },
  { id: "proof", dur: 120, enter: "fade", C: Proof },
  { id: "future", dur: 240, enter: "wipe", C: Future },
  { id: "outro", dur: 190, enter: "fade", C: Outro },
];

/** Scene start frames: fades overlap the previous scene, cuts and wipes do not. */
export const TIMELINE = (() => {
  let t = 0;
  return SCENES.map((s, i) => {
    const start = i === 0 ? 0 : t - (s.enter === "fade" ? FADE : 0);
    t = start + s.dur;
    return { ...s, start };
  });
})();
export const TOTAL = TIMELINE[TIMELINE.length - 1]!.start + TIMELINE[TIMELINE.length - 1]!.dur;

const FadeIn: FC<{ children: React.ReactNode; on: boolean }> = ({ children, on }) => {
  const frame = useCurrentFrame();
  return <AbsoluteFill style={{ opacity: on ? interpolate(frame, [0, FADE], [0, 1], clamp) : 1 }}>{children}</AbsoluteFill>;
};

export const Promo: FC = () => {
  const musicVol = (f: number) => interpolate(f, [0, 30, TOTAL - 60, TOTAL], [0, audio.voice ? 0.32 : 0.8, audio.voice ? 0.32 : 0.8, 0], clamp);
  return (
    <AbsoluteFill style={{ background: "#0f1a14" }}>
      {TIMELINE.map((s) => (
        <Sequence key={s.id} from={s.start} durationInFrames={s.dur} name={s.id}>
          <FadeIn on={s.enter === "fade"}><s.C /></FadeIn>
        </Sequence>
      ))}
      {TIMELINE.filter((s) => s.enter === "wipe").map((s) => (
        <Sequence key={`w-${s.id}`} from={s.start - Math.round(WIPE / 2) - 6} durationInFrames={WIPE + 14} name={`wipe-${s.id}`}>
          <BandWipe duration={WIPE} />
        </Sequence>
      ))}
      <Finish />
      {audio.music ? <Audio src={staticFile(audio.music)} volume={musicVol} /> : null}
      {audio.voice ? <Audio src={staticFile(audio.voice)} /> : null}
    </AbsoluteFill>
  );
};
