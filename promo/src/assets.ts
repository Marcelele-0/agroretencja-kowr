import raw from "./assets.generated.json";

export interface Box { frame: number; x: number; y: number; w: number; h: number }
export interface Clip { file: string; width: number; height: number; durationInFrames: number; marks: Record<string, number>; boxes: Record<string, Box> }
export interface Manifest { fps: number; clips: Record<string, Clip>; stills?: Record<string, { file: string; width: number; height: number }> }

type AiSlot = "01-susza" | "02-nadmiar" | "03-z-gory" | "04-pole-swit" | "05-makro" | "06-final";

const data = raw as unknown as { ai: Record<AiSlot, string | null>; audio: { voice: string | null; music: string | null }; manifest: Manifest | null };

export const ai = (slot: AiSlot): string | null => data.ai[slot] ?? null;
export const audio = data.audio;
export const clip = (name: string): Clip | null => data.manifest?.clips?.[name] ?? null;
/** True when at least one fallback photo appears, so the end card must credit it. */
export const usesFallbackPhotos = (["01-susza", "02-nadmiar", "04-pole-swit", "05-makro", "06-final"] as AiSlot[]).some((s) => !data.ai[s]);
