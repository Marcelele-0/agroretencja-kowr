import { describe, expect, it } from "vitest";
import { analyzeKey, createAnalyzeCache } from "./analyzeCache";
import type { AnalysisRequest, AnalysisResponse } from "./contract";

interface Pending { req: AnalysisRequest; resolve: (v: AnalysisResponse) => void; reject: (e: unknown) => void }

function controlledFetcher() {
  const calls: Pending[] = [];
  const fetcher = (req: AnalysisRequest) => new Promise<AnalysisResponse>((resolve, reject) => { calls.push({ req, resolve, reject }); });
  return { calls, fetcher };
}
const req = (id: string, date = "2019-07-01"): AnalysisRequest => ({ parcel_id: id, crop: "kukurydza", analysis_date: date });
const resp = (id: string): AnalysisResponse => ({ parcel_id: id } as unknown as AnalysisResponse);
const tick = () => new Promise((r) => setTimeout(r, 0));

describe("analyzeCache", () => {
  it("keys by parcel, crop and date", () => {
    expect(analyzeKey(req("a"))).toBe("a|kukurydza|2019-07-01");
  });

  it("keeps the farmer's data in the key with the date last", () => {
    expect(analyzeKey({ ...req("a"), tile_drainage: true, gauge_rain_7d_mm: 12.5 })).toBe("a|kukurydza~d1r12.5|2019-07-01");
    expect(analyzeKey({ ...req("a"), tile_drainage: false })).toBe("a|kukurydza~d0|2019-07-01");
    expect(analyzeKey({ ...req("a"), gauge_rain_7d_mm: 0 })).toBe("a|kukurydza~r0|2019-07-01");
  });

  it("drops the settled results of one parcel when its terrain was upgraded", async () => {
    const { calls, fetcher } = controlledFetcher();
    const cache = createAnalyzeCache(fetcher);
    cache.get(req("a"));
    cache.get({ ...req("a"), tile_drainage: true });
    cache.get(req("ab"));
    calls.forEach((c) => c.resolve(resp(c.req.parcel_id)));
    await tick();
    cache.get(req("a", "2019-07-02")); // still pending: stays for its callers
    cache.invalidateParcel("a");
    expect(cache.peek(req("a"))).toBeUndefined();
    expect(cache.peek({ ...req("a"), tile_drainage: true })).toBeUndefined();
    expect(cache.peek(req("ab"))).toEqual(resp("ab"));
    cache.get(req("a", "2019-07-02"));
    expect(calls).toHaveLength(4);
    cache.get(req("a"));
    expect(calls).toHaveLength(5);
  });

  it("dedupes concurrent callers and keeps the resolved result", async () => {
    const { calls, fetcher } = controlledFetcher();
    const cache = createAnalyzeCache(fetcher);
    const p1 = cache.get(req("a"));
    const p2 = cache.get(req("a"));
    expect(calls).toHaveLength(1);
    expect(cache.peek(req("a"))).toBeUndefined();
    calls[0]!.resolve(resp("a"));
    await expect(p1).resolves.toEqual(resp("a"));
    await expect(p2).resolves.toEqual(resp("a"));
    expect(cache.peek(req("a"))).toEqual(resp("a"));
    await expect(cache.get(req("a"))).resolves.toEqual(resp("a"));
    expect(calls).toHaveLength(1);
    cache.get(req("a", "2019-07-02"));
    expect(calls).toHaveLength(2);
  });

  it("drops a rejected entry so the next call retries", async () => {
    const { calls, fetcher } = controlledFetcher();
    const cache = createAnalyzeCache(fetcher);
    const p1 = cache.get(req("a"));
    calls[0]!.reject(new TypeError("network"));
    await expect(p1).rejects.toThrow("network");
    expect(cache.peek(req("a"))).toBeUndefined();
    const p2 = cache.get(req("a"));
    expect(calls).toHaveLength(2);
    calls[1]!.resolve(resp("a"));
    await expect(p2).resolves.toEqual(resp("a"));
  });

  it("does not share one caller's abort with the request or other callers", async () => {
    const { calls, fetcher } = controlledFetcher();
    const cache = createAnalyzeCache(fetcher);
    const ctl = new AbortController();
    const aborted = cache.get(req("a"), { signal: ctl.signal });
    const other = cache.get(req("a"));
    ctl.abort();
    await expect(aborted).rejects.toMatchObject({ name: "AbortError" });
    expect(calls).toHaveLength(1);
    calls[0]!.resolve(resp("a"));
    await expect(other).resolves.toEqual(resp("a"));
    expect(cache.peek(req("a"))).toEqual(resp("a"));
    const pre = new AbortController();
    pre.abort();
    await expect(cache.get(req("a"), { signal: pre.signal })).rejects.toMatchObject({ name: "AbortError" });
  });

  it("limits concurrency and lets priority requests jump the queue", async () => {
    const { calls, fetcher } = controlledFetcher();
    const cache = createAnalyzeCache(fetcher, { maxConcurrent: 2 });
    cache.get(req("a"));
    cache.get(req("b"));
    cache.get(req("c"));
    cache.get(req("d"));
    expect(calls.map((c) => c.req.parcel_id)).toEqual(["a", "b"]);
    expect(cache.inFlight()).toBe(2);
    cache.get(req("d"), { priority: true });
    calls[0]!.resolve(resp("a"));
    await tick();
    expect(calls.map((c) => c.req.parcel_id)).toEqual(["a", "b", "d"]);
    calls[1]!.reject(new Error("x"));
    await tick();
    expect(calls.map((c) => c.req.parcel_id)).toEqual(["a", "b", "d", "c"]);
  });

  it("evicts the least recently used settled entry", async () => {
    const { calls, fetcher } = controlledFetcher();
    const cache = createAnalyzeCache(fetcher, { maxEntries: 2 });
    for (const id of ["a", "b"]) {
      const p = cache.get(req(id));
      calls.at(-1)!.resolve(resp(id));
      await p;
    }
    await cache.get(req("a"));
    const pc = cache.get(req("c"));
    calls.at(-1)!.resolve(resp("c"));
    await pc;
    expect(cache.peek(req("a"))).toEqual(resp("a"));
    expect(cache.peek(req("b"))).toBeUndefined();
    expect(cache.peek(req("c"))).toEqual(resp("c"));
  });
});
