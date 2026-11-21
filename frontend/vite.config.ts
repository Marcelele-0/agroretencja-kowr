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

// External APIs go through our server: bypasses client-side Wi-Fi limits, VPN/Private Relay blocks, and mobile CORS issues.
const proxy = {
  "/om/archive": { target: "https://archive-api.open-meteo.com", changeOrigin: true, rewrite: (p: string) => p.replace(/^\/om\/archive/, "/v1/archive") },
  "/om/forecast": { target: "https://api.open-meteo.com", changeOrigin: true, rewrite: (p: string) => p.replace(/^\/om\/forecast/, "/v1/forecast") },
  "/uug": { target: "https://services.gugik.gov.pl", changeOrigin: true, rewrite: (p: string) => p.replace(/^\/uug\/?/, "/uug/") },
  "/iung": { target: "https://sgeo.iung.pl", changeOrigin: true, rewrite: (p: string) => p.replace(/^\/iung\/?/, "/geoserver/Susza/wms") },
};

export default defineConfig({
  plugins: [react(), uldkProxyPlugin()],
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
