import { useEffect, useMemo, useRef, useState } from "react";
import { analyzeCache, analyzeKey } from "../api/analyzeCache";
import { BackendError, backendEnabled } from "../api/backend";
import type { AnalysisRequest, AnalysisResponse } from "../api/contract";
import { backendCrop, type CropCode } from "../lib/crops";
import type { Iso } from "../lib/dates";
import { message } from "./useAnalysis";

export interface RowAnalysisItem {
  id: string;
  crop: CropCode;
  /** Parcel geometry is loaded and the land is not marked as non-agricultural. */
  eligible: boolean;
}
export interface RowAnalysis { loading: boolean; data: AnalysisResponse | null; error: string | null }

const RETRY_DELAY_MS = 1500;

function requestOf(item: RowAnalysisItem, date: Iso): AnalysisRequest | null {
  if (!backendEnabled || !item.eligible) return null;
  const bc = backendCrop(item.crop);
  return bc ? { parcel_id: item.id, crop: bc, analysis_date: date } : null;
}

/** HTTP answers from the backend are final; network failures and timeouts get one retry. */
function retryable(e: unknown): boolean {
  return !(e instanceof BackendError);
}

/**
 * Backend analysis for every parcel of the list, through the shared analyze cache.
 * Requests are never cancelled by re-renders; results for another date or after unmount are ignored.
 */
export function useRowAnalyses(items: RowAnalysisItem[], date: Iso): Record<string, RowAnalysis> {
  const [rows, setRows] = useState<Record<string, RowAnalysis>>({});
  const started = useRef(new Set<string>());
  const mounted = useRef(true);
  const dateRef = useRef(date);
  dateRef.current = date;

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  const reqs = items.map((it) => requestOf(it, date));
  const sig = reqs.map((r) => (r ? analyzeKey(r) : "")).join(",");

  useEffect(() => {
    const settle = (key: string, value: RowAnalysis) => {
      if (!mounted.current) return;
      if (dateRef.current !== date) {
        // Late answer for an old date: forget it so a return to that date reads the cache again.
        started.current.delete(key);
        return;
      }
      const suffix = `|${date}`;
      setRows((prev) => {
        const next: Record<string, RowAnalysis> = {};
        for (const [k, v] of Object.entries(prev)) if (k.endsWith(suffix)) next[k] = v;
        next[key] = value;
        return next;
      });
    };
    const run = (req: AnalysisRequest, key: string, attempt: number) => {
      analyzeCache.get(req).then(
        (data) => settle(key, { loading: false, data, error: null }),
        (e) => {
          if (attempt === 0 && retryable(e) && mounted.current && dateRef.current === date) {
            setTimeout(() => {
              if (mounted.current && dateRef.current === date) run(req, key, 1);
              else started.current.delete(key);
            }, RETRY_DELAY_MS);
            return;
          }
          settle(key, { loading: false, data: null, error: message(e) });
        },
      );
    };
    const suffix = `|${date}`;
    for (const k of started.current) if (!k.endsWith(suffix)) started.current.delete(k);
    for (const req of reqs) {
      if (!req) continue;
      const key = analyzeKey(req);
      if (started.current.has(key)) continue;
      started.current.add(key);
      const hit = analyzeCache.peek(req);
      if (hit) {
        settle(key, { loading: false, data: hit, error: null });
        continue;
      }
      setRows((prev) => (prev[key]?.loading ? prev : { ...prev, [key]: { loading: true, data: null, error: null } }));
      run(req, key, 0);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sig, date]);

  return useMemo(() => {
    const out: Record<string, RowAnalysis> = {};
    items.forEach((it, i) => {
      const req = reqs[i];
      if (!req) return;
      const key = analyzeKey(req);
      const hit = analyzeCache.peek(req);
      out[it.id] = hit ? { loading: false, data: hit, error: null } : rows[key] ?? { loading: true, data: null, error: null };
    });
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, sig]);
}
