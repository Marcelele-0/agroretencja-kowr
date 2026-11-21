/// <reference types="vitest" />
import { defineConfig, type Connect, type Plugin } from "vite";
import react from "@vitejs/plugin-react";

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

/**
 * Caching + coalescing Open-Meteo proxy with clock-skew date clamping and retry on 429/5xx.
 */
function openMeteoProxyPlugin(): Plugin {
  const cache = new Map<string, { status: number; body: string; exp: number }>();
  const inflight = new Map<string, Promise<{ status: number; body: string }>>();

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

  const setCache = (key: string, status: number, body: string, ttlMs: number) => {
    if (cache.size >= OM_CACHE_MAX) {
      const oldest = cache.keys().next().value;
      if (oldest) cache.delete(oldest);
    }
    cache.set(key, { status, body, exp: Date.now() + ttlMs });
  };

  const mount = (middlewares: Connect.Server, prefix: string, upstreamBase: string, ttlMs: number, clampArchiveDate: boolean) => {
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
          });
          res.end(hit.body);
          return;
        }

        let job = inflight.get(key);
        if (!job) {
          const target = `${upstreamBase}?${params.toString()}`;
          const t0 = Date.now();
          job = (async () => {
            const pauses = [500, 2000];
            let lastStatus = 502;
            let lastBody = JSON.stringify({ error: true, reason: "Open-Meteo upstream error" });
            for (let attempt = 0; attempt < 3; attempt++) {
              try {
                const upstream = await fetch(target, {
                  headers: { "User-Agent": "AgroRetencja-KOWR/1.0" },
                  signal: AbortSignal.timeout(15000),
                });
                lastStatus = upstream.status;
                lastBody = await upstream.text();
                if (upstream.status !== 429 && upstream.status < 500) {
                  if (upstream.status === 200 && lastBody.includes('"daily"')) {
                    setCache(key, 200, lastBody, ttlMs);
                  }
                  console.info(`[om] ${prefix} ${upstream.status} attempt=${attempt + 1} (${Date.now() - t0}ms) ${key}`);
                  return { status: upstream.status, body: lastBody };
                }
                console.warn(`[om] retry ${attempt + 1}/3: ${prefix} http_${upstream.status} ${key}`);
              } catch (err) {
                lastStatus = 504;
                lastBody = JSON.stringify({ error: true, reason: err instanceof Error ? err.message : String(err) });
                console.warn(`[om] retry ${attempt + 1}/3: ${prefix} error=${lastBody} ${key}`);
              }
              if (attempt < pauses.length) await new Promise((r) => setTimeout(r, pauses[attempt]!));
            }
            console.error(`[om] failed ${prefix} status=${lastStatus} (${Date.now() - t0}ms) ${key}`);
            return { status: lastStatus, body: lastBody };
          })().finally(() => inflight.delete(key));
          inflight.set(key, job);
        }

        const out = await job;
        res.writeHead(out.status, {
          "Content-Type": "application/json; charset=utf-8",
          "Access-Control-Allow-Origin": "*",
          "X-OM-Cache": "miss",
        });
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
  "/uug": { target: "https://services.gugik.gov.pl", changeOrigin: true, rewrite: (p: string) => p.replace(/^\/uug\/?/, "/uug/") },
  "/iung": { target: "https://sgeo.iung.pl", changeOrigin: true, rewrite: (p: string) => p.replace(/^\/iung\/?/, "/geoserver/Susza/wms") },
};

export default defineConfig({
  plugins: [react(), uldkProxyPlugin(), openMeteoProxyPlugin()],
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
