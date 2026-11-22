import raw from "./assets.generated.json";

export interface Box { frame: number; x: number; y: number; w: number; h: number }
export interface Clip { file: string; width: number; height: number; durationInFrames: number; marks: Record<string, number>; boxes: Record<string, Box>; keep?: [number, number][] }

/** Segments of the clip that play (frozen stretches cut by capture/trim.mjs); the whole clip without them. */
export const segments = (c: Clip): [number, number][] => (c.keep?.length ? c.keep : [[0, c.durationInFrames]]);
/** Number of frames that play. */
export const keptLen = (c: Clip): number => segments(c).reduce((t, [a, b]) => t + b - a, 0);
/** Clip frame -> frame of the trimmed clip (a frame inside a cut maps to the start of the next segment). */
export const toKept = (c: Clip, f: number): number => {
  let acc = 0;
  for (const [a, b] of segments(c)) {
    if (f < a) return acc;
    if (f < b) return acc + (f - a);
    acc += b - a;
  }
  return acc;
};
export interface Manifest { fps: number; clips: Record<string, Clip>; stills?: Record<string, { file: string; width: number; height: number }> }

type AiSlot = "01-susza" | "02-nadmiar" | "03-z-gory" | "04-pole-swit" | "05-makro" | "06-final";

const data = raw as unknown as { ai: Record<AiSlot, string | null>; audio: { voice: string | null; music: string | null }; manifest: Manifest | null };

export const ai = (slot: AiSlot): string | null => data.ai[slot] ?? null;
export const audio = data.audio;
export const clip = (name: string): Clip | null => data.manifest?.clips?.[name] ?? null;
/** True when at least one fallback photo appears, so the end card must credit it. */
export const usesFallbackPhotos = (["01-susza", "02-nadmiar", "04-pole-swit", "05-makro", "06-final"] as AiSlot[]).some((s) => !data.ai[s]);
