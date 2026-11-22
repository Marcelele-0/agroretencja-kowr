import { postAnalyze } from "./backend";
import type { AnalysisRequest, AnalysisResponse } from "./contract";

export type AnalyzeFetcher = (req: AnalysisRequest) => Promise<AnalysisResponse>;
export interface AnalyzeGetOptions {
  /** Stops this caller from listening. The shared request keeps running for other callers and the cache. */
  signal?: AbortSignal;
  /** Moves the request to the front of the concurrency queue (the active parcel). */
  priority?: boolean;
}
export interface AnalyzeCache {
  get(req: AnalysisRequest, opts?: AnalyzeGetOptions): Promise<AnalysisResponse>;
  /** Resolved result for the request, or undefined when it is missing or still pending. */
  peek(req: AnalysisRequest): AnalysisResponse | undefined;
  clear(): void;
  /** Requests started and not yet settled (for tests and diagnostics). */
  inFlight(): number;
}

interface Entry {
  promise: Promise<AnalysisResponse>;
  data?: AnalysisResponse;
  /** Present while the request waits for a concurrency slot. */
  waiting?: () => void;
}

export function analyzeKey(req: AnalysisRequest): string {
  return `${req.parcel_id}|${req.crop}|${req.analysis_date}`;
}

function abortError(signal: AbortSignal): unknown {
  return signal.reason ?? new DOMException("aborted", "AbortError");
}

/** Detaches one caller from a shared promise: the caller rejects on abort, the shared promise is untouched. */
function listen<T>(shared: Promise<T>, signal?: AbortSignal): Promise<T> {
  if (!signal) return shared;
  if (signal.aborted) return Promise.reject(abortError(signal));
  return new Promise<T>((resolve, reject) => {
    const onAbort = () => reject(abortError(signal));
    signal.addEventListener("abort", onAbort, { once: true });
    shared.then(
      (v) => { signal.removeEventListener("abort", onAbort); resolve(v); },
      (e) => { signal.removeEventListener("abort", onAbort); reject(e); },
    );
  });
}

/**
 * Shared /api/analyze results keyed by (parcel, backend crop, date).
 * Concurrent callers share one request; resolved results stay in a bounded LRU; rejected entries are dropped so the next call retries.
 */
export function createAnalyzeCache(fetcher: AnalyzeFetcher, opts: { maxEntries?: number; maxConcurrent?: number } = {}): AnalyzeCache {
  const maxEntries = Math.max(1, opts.maxEntries ?? 64);
  const maxConcurrent = Math.max(1, opts.maxConcurrent ?? 3);
  const entries = new Map<string, Entry>();
  const queue: Entry[] = [];
  let running = 0;

  const pump = () => {
    while (running < maxConcurrent && queue.length) {
      const next = queue.shift()!;
      const start = next.waiting;
      next.waiting = undefined;
      start?.();
    }
  };

  const touch = (key: string, entry: Entry) => {
    entries.delete(key);
    entries.set(key, entry);
  };

  const evict = () => {
    // Only settled entries are evicted; pending ones must stay to dedupe their callers.
    for (const [key, e] of entries) {
      if (entries.size <= maxEntries) break;
      if (e.data !== undefined) entries.delete(key);
    }
  };

  const create = (key: string, req: AnalysisRequest, priority: boolean): Entry => {
    const entry = {} as Entry;
    entry.promise = new Promise<AnalysisResponse>((resolve, reject) => {
      entry.waiting = () => {
        running++;
        let p: Promise<AnalysisResponse>;
        try {
          p = fetcher(req);
        } catch (e) {
          p = Promise.reject(e);
        }
        p.then(
          (data) => {
            running--;
            if (entries.get(key) === entry) {
              entry.data = data;
              evict();
            }
            resolve(data);
            pump();
          },
          (e) => {
            running--;
            if (entries.get(key) === entry) entries.delete(key);
            reject(e);
            pump();
          },
        );
      };
    });
    // Callers that stop listening must not produce unhandled rejections.
    entry.promise.catch(() => undefined);
    if (priority) queue.unshift(entry);
    else queue.push(entry);
    return entry;
  };

  return {
    get(req, o = {}) {
      const key = analyzeKey(req);
      let entry = entries.get(key);
      if (entry) {
        touch(key, entry);
        if (o.priority && entry.waiting) {
          const i = queue.indexOf(entry);
          if (i > 0) { queue.splice(i, 1); queue.unshift(entry); }
        }
      } else {
        entry = create(key, req, !!o.priority);
        entries.set(key, entry);
        evict();
        pump();
      }
      return listen(entry.promise, o.signal);
    },
    peek(req) {
      return entries.get(analyzeKey(req))?.data;
    },
    clear() {
      entries.clear();
    },
    inFlight() {
      return running;
    },
  };
}

/** App-wide cache shared by the active parcel (useAnalysis) and the parcel list rows (useRowAnalyses). */
export const analyzeCache = createAnalyzeCache(postAnalyze);
