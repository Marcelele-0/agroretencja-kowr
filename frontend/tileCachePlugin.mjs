import { createHash } from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const TILE_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "data", "cache", "tiles");
const ORTHO_WMTS_UPSTREAM = "https://mapy.geoportal.gov.pl/wss/service/PZGIK/ORTO/WMTS/StandardResolution";
const ORTHO_WMS_UPSTREAM = "https://mapy.geoportal.gov.pl/wss/service/PZGIK/ORTO/WMS/StandardResolution";
const KIEG_UPSTREAM = "https://integracja.gugik.gov.pl/cgi-bin/KrajowaIntegracjaEwidencjiGruntow";
const CACHE_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Cache-Control": "public, max-age=2592000",
};

function isDigits(s) {
  return /^\d+$/.test(s);
}

async function readExisting(filePath) {
  try {
    return await fs.readFile(filePath);
  } catch {
    return null;
  }
}

async function writeAtomic(filePath, buf) {
  await fs.mkdir(path.dirname(filePath), { recursive: true });
  const tmp = `${filePath}.${process.pid}.${Math.random().toString(36).slice(2)}.tmp`;
  try {
    await fs.writeFile(tmp, buf);
    await fs.rename(tmp, filePath);
  } catch (err) {
    await fs.unlink(tmp).catch(() => {});
    throw err;
  }
}

function resolveTileTarget(rawUrl) {
  const qIdx = rawUrl.indexOf("?");
  const pathname = (qIdx >= 0 ? rawUrl.slice(0, qIdx) : rawUrl).replace(/\/+$/, "");
  const qs = qIdx >= 0 ? rawUrl.slice(qIdx + 1) : "";

  const wmMatch = /^\/ortho-wmts\/(\d+)\/(\d+)\/(\d+)\.jpg$/i.exec(pathname);
  if (wmMatch) {
    const [, z, x, y] = wmMatch;
    if (!isDigits(z) || !isDigits(x) || !isDigits(y) || Number(z) > 20) return null;
    return {
      rel: `ortho-wmts/${z}/${x}/${y}.jpg`,
      upstream: `${ORTHO_WMTS_UPSTREAM}?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=ORTOFOTOMAPA&STYLE=default&FORMAT=image/jpeg&TILEMATRIXSET=EPSG:3857&TILEMATRIX=EPSG:3857:${z}&TILEROW=${y}&TILECOL=${x}`,
      mime: "image/jpeg",
    };
  }

  if (pathname === "/ortho-wms" && qs) {
    const h = createHash("sha1").update(qs).digest("hex");
    return {
      rel: `ortho-wms/${h.slice(0, 2)}/${h}.jpg`,
      upstream: `${ORTHO_WMS_UPSTREAM}?${qs}`,
      mime: "image/jpeg",
    };
  }

  if (pathname === "/kieg" && qs) {
    const h = createHash("sha1").update(qs).digest("hex");
    return {
      rel: `kieg/${h.slice(0, 2)}/${h}.png`,
      upstream: `${KIEG_UPSTREAM}?${qs}`,
      mime: "image/png",
    };
  }

  return null;
}

export function tileCachePlugin() {
  const inflight = new Map();

  const attach = (middlewares) => {
    middlewares.use("/tiles", (req, res) => {
      const { url = "/", method = "GET" } = req;
      void (async () => {
        if (method === "OPTIONS") {
          res.writeHead(204, { ...CACHE_HEADERS, "Access-Control-Allow-Methods": "GET, OPTIONS" });
          res.end();
          return;
        }
        if (method !== "GET" && method !== "HEAD") {
          res.writeHead(405, CACHE_HEADERS);
          res.end();
          return;
        }
        const target = resolveTileTarget(url);
        if (!target) {
          res.writeHead(404, CACHE_HEADERS);
          res.end();
          return;
        }
        const filePath = path.resolve(TILE_ROOT, target.rel);
        if (!filePath.startsWith(TILE_ROOT + path.sep)) {
          res.writeHead(403, CACHE_HEADERS);
          res.end();
          return;
        }

        const cached = await readExisting(filePath);
        if (cached && cached.length > 0) {
          res.writeHead(200, { ...CACHE_HEADERS, "Content-Type": target.mime, "X-Tile-Cache": "hit" });
          res.end(method === "HEAD" ? undefined : cached);
          return;
        }

        let job = inflight.get(target.rel);
        if (!job) {
          job = (async () => {
            try {
              const upstream = await fetch(target.upstream, {
                headers: { "User-Agent": "AgroRetencja-KOWR/1.0" },
                signal: AbortSignal.timeout(15000),
              });
              const ct = upstream.headers.get("content-type") ?? "";
              const ab = await upstream.arrayBuffer();
              const buf = Buffer.from(ab);
              if (upstream.ok && ct.startsWith("image/") && buf.length > 0) {
                await writeAtomic(filePath, buf).catch(() => {});
                return { status: 200, mime: ct.split(";")[0] || target.mime, buf };
              }
              return { status: upstream.ok ? 502 : upstream.status, mime: ct || "text/plain", buf };
            } catch {
              return { status: 504, mime: "text/plain", buf: Buffer.alloc(0) };
            }
          })().finally(() => inflight.delete(target.rel));
          inflight.set(target.rel, job);
        }

        const out = await job;
        res.writeHead(out.status, {
          ...CACHE_HEADERS,
          "Content-Type": out.mime,
          "X-Tile-Cache": out.status === 200 ? "miss" : "error",
        });
        res.end(method === "HEAD" ? undefined : out.buf);
      })();
    });
  };

  return {
    name: "tile-disk-cache-proxy",
    configureServer(server) { attach(server.middlewares); },
    configurePreviewServer(server) { attach(server.middlewares); },
  };
}
