import { useEffect, useRef, useState } from "react";
import { analyzeCache } from "../api/analyzeCache";
import { backendEnabled, getTerrainStatus } from "../api/backend";

export const TERRAIN_POLL_MS = 8000;
/** The backend gives the GUGiK WCS 120 s in the background; a little more here. */
export const TERRAIN_POLL_MAX_MS = 150000;

/**
 * Parcels analysed on the 30 m fallback terrain while NMT 1 m downloads: asks GET /api/terrain/status
 * every few seconds. When a 1 m model is ready the parcel's cached analyses are dropped and `onReady`
 * fires, so the page analyses it again (now on NMT 1 m). Returns the parcels still being waited for.
 */
export function useTerrainUpgrade(pendingIds: string[], onReady: (ids: string[]) => void): string[] {
  const sig = [...new Set(pendingIds)].sort().join(",");
  const [waiting, setWaiting] = useState<string[]>([]);
  const readyRef = useRef(onReady);
  readyRef.current = onReady;

  useEffect(() => {
    if (!backendEnabled || !sig) {
      setWaiting([]);
      return;
    }
    let left = sig.split(",");
    setWaiting(left);
    const ctl = new AbortController();
    const started = Date.now();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async () => {
      const states = await Promise.all(left.map((id) => getTerrainStatus(id, ctl.signal).then((s) => s.nmt_1m, () => "downloading" as const)));
      if (ctl.signal.aborted) return;
      const ready = left.filter((_, i) => states[i] === "ready");
      // "none": the download ended without a model (GUGiK failed); the fallback terrain stays.
      left = left.filter((_, i) => states[i] === "downloading");
      if (Date.now() - started >= TERRAIN_POLL_MAX_MS) left = [];
      setWaiting(left);
      if (ready.length) {
        ready.forEach((id) => analyzeCache.invalidateParcel(id));
        readyRef.current(ready);
      }
      if (left.length) timer = setTimeout(tick, TERRAIN_POLL_MS);
    };
    timer = setTimeout(tick, TERRAIN_POLL_MS);
    return () => {
      ctl.abort();
      clearTimeout(timer);
    };
  }, [sig]);

  return waiting;
}
