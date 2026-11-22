/// <reference types="vitest" />
import { defineConfig, type Connect, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import { tileCachePlugin } from "./tileCachePlugin.mjs";

const ULDK_UPSTREAM = "https://uldk.gugik.gov.pl/";

/**
 * Resilient server-side ULDK proxy: prevents the browser from following GUGiK's
 * 302 overload redirect to CORS-less /resp/uldk.php and retries corrupt WKB responses.
 */
function uldkProxyPlugin(): Plugin {
  const attach = (middlewares: Connect.Server) => {
    middlewares.use("/uldk", (req, res) => {
      const { url, method } = req as { url?: string; method?: string };
      void (async () => {
        if (method === "OPTIONS") {
          res.writeHead(204, { "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "GET, OPTIONS" });
          res.end();
          return;
        }
        const qIdx = url?.indexOf("?") ?? -1;
        const qs = qIdx >= 0 ? url!.slice(qIdx) : "";
        const target = `${ULDK_UPSTREAM}${qs}`;
        const t0 = Date.now();
        let lastReason = "upstream_error";

        for (let attempt = 0; attempt < 5; attempt++) {
          try {
            const upstream = await fetch(target, {
              redirect: "manual",
              headers: { "User-Agent": "AgroRetencja-KOWR/1.0" },
              signal: AbortSignal.timeout(8000),
            });
            if (upstream.status >= 300 && upstream.status < 400) {
              const loc = upstream.headers.get("location") ?? "";
              lastReason = `redirect_${upstream.status}_${loc.includes("/resp/") ? "tarpit" : "other"}`;
              console.warn(`[uldk] retry ${attempt + 1}/5: ${lastReason} ${qs.slice(0, 80)}`);
            } else if (upstream.status >= 500 || upstream.status === 429) {
              lastReason = `http_${upstream.status}`;
              console.warn(`[uldk] retry ${attempt + 1}/5: ${lastReason} ${qs.slice(0, 80)}`);
            } else {
              const text = await upstream.text();
              const lines = text.trim().split(/\r?\n/);
              if (lines[0]?.trim() === "0" && !(lines[1] ?? "").includes("POLYGON")) {
                lastReason = "corrupt_wkb";
                console.warn(`[uldk] retry ${attempt + 1}/5: ${lastReason} ${qs.slice(0, 80)}`);
              } else {
                console.info(`[uldk] ${upstream.status} ok attempt=${attempt + 1} (${Date.now() - t0}ms) ${qs.slice(0, 80)}`);
                res.writeHead(upstream.status, {
                  "Content-Type": "text/plain; charset=UTF-8",
                  "Access-Control-Allow-Origin": "*",
                  "Cache-Control": "no-store",
                });
                res.end(text);
                return;
              }
            }
          } catch (err) {
            lastReason = err instanceof Error ? err.message : String(err);
            console.warn(`[uldk] retry ${attempt + 1}/5: fetch_error=${lastReason} ${qs.slice(0, 80)}`);
          }
          if (attempt < 4) await new Promise((r) => setTimeout(r, 250));
        }

        console.error(`[uldk] failed after 5 attempts (${Date.now() - t0}ms): ${lastReason} ${qs.slice(0, 80)}`);
        res.writeHead(503, {
          "Content-Type": "text/plain; charset=UTF-8",
          "Access-Control-Allow-Origin": "*",
          "Cache-Control": "no-store",
        });
        res.end(`ULDK unavailable: ${lastReason}`);
      })();
    });
  };

  return {
    name: "uldk-resilient-proxy",
    configureServer(server) { attach(server.middlewares); },
    configurePreviewServer(server) { attach(server.middlewares); },
  };
}

const OM_ARCHIVE_UPSTREAM = "https://archive-api.open-meteo.com/v1/archive";
const OM_FORECAST_UPSTREAM = "https://api.open-meteo.com/v1/forecast";
const OM_ARCHIVE_TTL_MS = 24 * 3600_000;
const OM_FORECAST_TTL_MS = 30 * 60_000;
const OM_CACHE_MAX = 1500;
/** Backend route with the same daily JSON shape (its own cache, NASA POWER fallback). */
const OM_BACKEND_FALLBACK = "http://127.0.0.1:8000/api/weather/daily";
const HOUR_MS = 3600_000;
const DAY_MS = 24 * HOUR_MS;

/** Open-Meteo 429 bodies name the exhausted quota; returns when that quota resets, or null for an unnamed 429. */
function omLimitResetAt(body: string, now: number): { kind: "minutely" | "hourly" | "daily"; until: number } | null {
  const b = body.toLowerCase();
  if (b.includes("minutely")) return { kind: "minutely", until: now + 60_000 };
  if (b.includes("hourly")) return { kind: "hourly", until: Math.min(Math.floor(now / HOUR_MS + 1) * HOUR_MS, now + HOUR_MS) };
  if (b.includes("daily")) return { kind: "daily", until: Math.floor(now / DAY_MS + 1) * DAY_MS };
  return null;
}

interface OmOut { status: number; body: string; source: string; retryAfterS?: number }

/**
 * Caching + coalescing Open-Meteo proxy with clock-skew date clamping.
 * A 429 that names a quota opens a circuit until the quota resets: requests then skip upstream.
 * Archive requests fall back to the backend weather route; the forecast fails fast instead.
 * Only 5xx and network errors get the short retry.
 */
function openMeteoProxyPlugin(): Plugin {
  const cache = new Map<string, { status: number; body: string; exp: number; source: string }>();
  const inflight = new Map<string, Promise<OmOut>>();

  const serverToday = () => new Date().toISOString().slice(0, 10);

  const normalizedKey = (kind: string, params: URLSearchParams): string => {
    const entries = [...params.entries()].map(([k, v]): [string, string] => {
      if ((k === "latitude" || k === "longitude") && Number.isFinite(Number(v))) {
        return [k, Number(v).toFixed(2)];
      }
      return [k, v];
    });
    entries.sort((a, b) => (a[0] === b[0] ? a[1].localeCompare(b[1]) : a[0].localeCompare(b[0])));
    return `${kind}:${new URLSearchParams(entries).toString()}`;
  };

  const setCache = (key: string, status: number, body: string, ttlMs: number, source: string) => {
    if (cache.size >= OM_CACHE_MAX) {
      const oldest = cache.keys().next().value;
      if (oldest) cache.delete(oldest);
    }
    cache.set(key, { status, body, exp: Date.now() + ttlMs, source });
  };

  /** Same daily series from the backend; null when the request has no explicit range or the backend fails. */
  const fromBackend = async (params: URLSearchParams): Promise<OmOut | null> => {
    const lat = params.get("latitude");
    const lon = params.get("longitude");
    const start = params.get("start_date");
    const end = params.get("end_date");
    if (!lat || !lon || !start || !end) return null;
    try {
      const qs = new URLSearchParams({ lat, lon, start, end });
      const r = await fetch(`${OM_BACKEND_FALLBACK}?${qs.toString()}`, { signal: AbortSignal.timeout(20000) });
      const body = await r.text();
      if (r.status === 200 && body.includes('"daily"')) return { status: 200, body, source: r.headers.get("x-om-source") ?? "backend" };
      console.warn(`[om] backend fallback http_${r.status} ${body.slice(0, 120)}`);
    } catch (err) {
      console.warn(`[om] backend fallback error=${err instanceof Error ? err.message : String(err)}`);
    }
    return null;
  };

  const mount = (middlewares: Connect.Server, prefix: string, upstreamBase: string, ttlMs: number, clampArchiveDate: boolean) => {
    let circuit: { kind: string; until: number } | null = null;

    const limitedReply = (until: number, kind: string): OmOut => {
      const at = new Date(until).toISOString().slice(11, 16);
      return {
        status: 429,
        body: JSON.stringify({ error: true, reason: `Open-Meteo: wyczerpany limit zapytań (${kind}), ponowna próba po ${at} UTC.` }),
        source: "circuit-open",
        retryAfterS: Math.max(1, Math.ceil((until - Date.now()) / 1000)),
      };
    };

    /** Backend data when possible, else the given failure; fallback answers are cached like upstream hits. */
    const fallback = async (key: string, params: URLSearchParams, failure: OmOut): Promise<OmOut> => {
      const fb = clampArchiveDate ? await fromBackend(params) : null;
      if (!fb) return failure;
      setCache(key, fb.status, fb.body, ttlMs, fb.source);
      return fb;
    };

    const upstreamJob = async (key: string, params: URLSearchParams): Promise<OmOut> => {
      const t0 = Date.now();
      if (circuit && circuit.until > t0) return fallback(key, params, limitedReply(circuit.until, circuit.kind));
      const target = `${upstreamBase}?${params.toString()}`;
      const pauses = [500, 2000];
      let last: OmOut = { status: 502, body: JSON.stringify({ error: true, reason: "Open-Meteo upstream error" }), source: "open-meteo" };
      for (let attempt = 0; attempt < 3; attempt++) {
        try {
          const upstream = await fetch(target, {
            headers: { "User-Agent": "AgroRetencja-KOWR/1.0" },
            signal: AbortSignal.timeout(15000),
          });
          const body = await upstream.text();
          last = { status: upstream.status, body, source: "open-meteo" };
          if (upstream.status === 429) {
            const limit = omLimitResetAt(body, Date.now());
            if (limit) {
              if (!circuit || circuit.until <= Date.now()) {
                console.warn(`[om] circuit open ${prefix}: ${limit.kind} limit, upstream skipped until ${new Date(limit.until).toISOString()}`);
              }
              circuit = limit;
              last = limitedReply(limit.until, limit.kind);
            }
            break;
          }
          if (upstream.status < 500) {
            if (upstream.status === 200 && body.includes('"daily"')) setCache(key, 200, body, ttlMs, "open-meteo");
            console.info(`[om] ${prefix} ${upstream.status} attempt=${attempt + 1} (${Date.now() - t0}ms) ${key}`);
            return last;
          }
          console.warn(`[om] retry ${attempt + 1}/3: ${prefix} http_${upstream.status} ${key}`);
        } catch (err) {
          last = { status: 504, body: JSON.stringify({ error: true, reason: err instanceof Error ? err.message : String(err) }), source: "open-meteo" };
          console.warn(`[om] retry ${attempt + 1}/3: ${prefix} error=${last.body} ${key}`);
        }
        if (attempt < pauses.length) await new Promise((r) => setTimeout(r, pauses[attempt]!));
      }
      const out = await fallback(key, params, last);
      if (out === last) console.error(`[om] failed ${prefix} status=${last.status} (${Date.now() - t0}ms) ${key}`);
      return out;
    };

    middlewares.use(prefix, (req, res) => {
      const { url, method } = req as { url?: string; method?: string };
      void (async () => {
        if (method === "OPTIONS") {
          res.writeHead(204, { "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "GET, OPTIONS" });
          res.end();
          return;
        }
        const qIdx = url?.indexOf("?") ?? -1;
        const params = new URLSearchParams(qIdx >= 0 ? url!.slice(qIdx + 1) : "");
        if (clampArchiveDate) {
          const today = serverToday();
          const end = params.get("end_date");
          const start = params.get("start_date");
          if (end && end > today) params.set("end_date", today);
          if (start && start > today) params.set("start_date", today);
        }

        const key = normalizedKey(prefix, params);
        const hit = cache.get(key);
        if (hit && hit.exp > Date.now()) {
          res.writeHead(hit.status, {
            "Content-Type": "application/json; charset=utf-8",
            "Access-Control-Allow-Origin": "*",
            "X-OM-Cache": "hit",
            "X-OM-Source": hit.source,
          });
          res.end(hit.body);
          return;
        }

        let job = inflight.get(key);
        if (!job) {
          job = upstreamJob(key, params).finally(() => inflight.delete(key));
          inflight.set(key, job);
        }

        const out = await job;
        const headers: Record<string, string> = {
          "Content-Type": "application/json; charset=utf-8",
          "Access-Control-Allow-Origin": "*",
          "Access-Control-Expose-Headers": "X-OM-Source, X-OM-Cache",
          "X-OM-Cache": "miss",
          "X-OM-Source": out.source,
        };
        if (out.retryAfterS) headers["Retry-After"] = String(out.retryAfterS);
        res.writeHead(out.status, headers);
        res.end(out.body);
      })();
    });
  };

  const attach = (middlewares: Connect.Server) => {
    mount(middlewares, "/om/archive", OM_ARCHIVE_UPSTREAM, OM_ARCHIVE_TTL_MS, true);
    mount(middlewares, "/om/forecast", OM_FORECAST_UPSTREAM, OM_FORECAST_TTL_MS, false);
  };

  return {
    name: "open-meteo-resilient-proxy",
    configureServer(server) { attach(server.middlewares); },
    configurePreviewServer(server) { attach(server.middlewares); },
  };
}

// External APIs go through our server: bypasses client-side Wi-Fi limits, VPN/Private Relay blocks, and mobile CORS issues.
const proxy = {
  "/api": { target: "http://127.0.0.1:8000", changeOrigin: true, timeout: 60000, proxyTimeout: 60000 },
  "/rl": { target: "http://127.0.0.1:8000", changeOrigin: true, timeout: 60000, proxyTimeout: 60000 },
  "/uug": { target: "https://services.gugik.gov.pl", changeOrigin: true, rewrite: (p: string) => p.replace(/^\/uug\/?/, "/uug/") },
  "/iung": { target: "https://sgeo.iung.pl", changeOrigin: true, rewrite: (p: string) => p.replace(/^\/iung\/?/, "/geoserver/Susza/wms") },
};

export default defineConfig({
  plugins: [react(), uldkProxyPlugin(), openMeteoProxyPlugin(), tileCachePlugin()],
  server: { port: 5173, host: true, proxy },
  preview: { port: 4173, host: true, proxy },
  build: {
    target: "es2022",
    chunkSizeWarningLimit: 900,
    // MapLibre changes rarely: its own chunk stays in the browser cache across app releases.
    rollupOptions: { output: { manualChunks: { maplibre: ["maplibre-gl"], react: ["react", "react-dom", "react-router-dom"] } } },
  },
  test: { environment: "node", include: ["src/**/*.test.ts"] },
});
