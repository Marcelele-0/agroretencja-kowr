// Records real screen footage of the Rusałka web app for the promo film.
// Usage (from promo/): node capture/record.mjs [scene…] [--no-warm] [--stills] [--no-stills]
// Output: public/rec/<scene>.mp4, public/rec/still_*@2x.png, public/rec/manifest.json
import { chromium } from "playwright-core";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PROMO = path.resolve(HERE, "..");
const REC = path.join(PROMO, "public/rec");
const PROFILE = path.join(HERE, ".profile");
const BASE = process.env.APP_URL ?? "http://localhost:4173";
const FPS = 30;
const EXE = path.join(os.homedir(), ".cache/ms-playwright/chromium_headless_shell-1254/chrome-headless-shell-linux64/chrome-headless-shell");
// The real GPU through ANGLE/Vulkan gives a steady 60 fps screencast; SwiftShader is the fallback.
const GPU_FLAGS = {
  vulkan: ["--use-angle=vulkan", "--enable-features=Vulkan", "--ignore-gpu-blocklist", "--enable-gpu"],
  swift: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"],
};
let gpu = process.env.GPU ?? "vulkan";

const CROP = "kukurydza_ziarno";
const DATE = "2019-07-15";
const PREFIX = "241606_5.0001.AR_4.";
// Click order in map_search: the searched parcel first, then its neighbours. The analysis scenes use the same set and order.
const PARCELS = ["520/1", "520/2", "521", "519"].map((n) => PREFIX + n);
const enc = (id) => encodeURIComponent(id);
const analysisUrl = (active = PARCELS[0], ids = PARCELS) =>
  `${BASE}/analiza?dzialki=${ids.map((id) => `${enc(id)}~${CROP}`).join(",")}&aktywna=${enc(active)}&data=${DATE}`;

const log = (...a) => console.log(`[${new Date().toISOString().slice(11, 19)}]`, ...a);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const now = () => Date.now() / 1000;

/* ------------------------------------------------------------------ overlay */

// Injected before page scripts. Draws a cursor that follows mousemove and a ripple on mousedown;
// in touch mode it draws a touch dot instead. Pointer events pass through.
function overlayScript(mode) {
  const install = () => {
    if (document.getElementById("__cap_cur")) return;
    const st = document.createElement("style");
    st.textContent = `
      #__cap_cur{position:fixed;left:0;top:0;width:22px;height:22px;margin:-11px 0 0 -11px;border-radius:50%;
        background:#fff;box-shadow:0 0 0 1.5px rgba(18,22,26,.88),0 2px 10px rgba(0,0,0,.35);pointer-events:none;
        z-index:2147483647;opacity:0;transition:opacity .2s, scale .12s ease-out;will-change:transform}
      #__cap_cur.on{opacity:1} #__cap_cur.down{scale:.82}
      #__cap_cur.touch{width:34px;height:34px;margin:-17px 0 0 -17px;background:rgba(255,255,255,.55);
        box-shadow:0 0 0 1.5px rgba(18,22,26,.55),0 2px 10px rgba(0,0,0,.25)}
      .__cap_rip{position:fixed;left:0;top:0;width:22px;height:22px;margin:-11px 0 0 -11px;border-radius:50%;
        border:2px solid rgba(255,255,255,.95);box-shadow:0 0 0 1px rgba(18,22,26,.45);pointer-events:none;
        z-index:2147483646;animation:__cap_rip 350ms ease-out forwards}
      @keyframes __cap_rip{from{scale:1;opacity:.95}to{scale:2.7;opacity:0}}`;
    document.head.appendChild(st);
    const cur = document.createElement("div");
    cur.id = "__cap_cur";
    if (mode === "touch") cur.classList.add("touch");
    document.documentElement.appendChild(cur);
    const at = (x, y) => { cur.style.transform = `translate(${x}px, ${y}px)`; };
    const ripple = (x, y) => {
      const r = document.createElement("div");
      r.className = "__cap_rip";
      r.style.transform = `translate(${x}px, ${y}px)`;
      document.documentElement.appendChild(r);
      setTimeout(() => r.remove(), 400);
    };
    const opts = { capture: true, passive: true };
    if (mode === "touch") {
      const t = (e) => e.touches[0] ?? e.changedTouches[0];
      addEventListener("touchstart", (e) => { const p = t(e); at(p.clientX, p.clientY); cur.classList.add("on"); ripple(p.clientX, p.clientY); }, opts);
      addEventListener("touchmove", (e) => { const p = t(e); at(p.clientX, p.clientY); }, opts);
      addEventListener("touchend", () => cur.classList.remove("on"), opts);
    } else {
      addEventListener("mousemove", (e) => { at(e.clientX, e.clientY); cur.classList.add("on"); }, opts);
      addEventListener("mousedown", (e) => { cur.classList.add("down"); ripple(e.clientX, e.clientY); }, opts);
      addEventListener("mouseup", () => cur.classList.remove("down"), opts);
    }
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", install);
  else install();
}

/* --------------------------------------------------------------- map access */

// The app does not expose the MapLibre instance; this walks the React fiber of the map container
// and returns the instance kept in a ref of ParcelMap. Read-only use plus camera moves.
const FIND_MAP = `(() => {
  const el = document.querySelector(".rs-map__canvas");
  if (!el) return null;
  const k = Object.keys(el).find((x) => x.startsWith("__reactFiber$"));
  for (let f = k ? el[k] : null; f; f = f.return) {
    for (let h = f.memoizedState; h && typeof h === "object" && "next" in h; h = h.next) {
      const v = h.memoizedState && h.memoizedState.current;
      if (v && typeof v.getCanvas === "function" && typeof v.project === "function") return v;
    }
  }
  return null;
})()`;

async function mapIdle(page, timeout = 30000) {
  await page.waitForFunction(`(() => { const m = ${FIND_MAP}; return !!m && m.loaded() && m.areTilesLoaded() && !m.isMoving(); })()`, null, { timeout, polling: 150 });
}

/** Screen point on a parcel strip: fraction f along its long axis, measured from the south-west end. */
async function parcelPoint(page, wkt, f = 0.3) {
  const pts = wkt.match(/-?\d+\.\d+ -?\d+\.\d+/g).map((s) => s.split(" ").map(Number));
  const ring = pts.slice(0, -1);
  return page.evaluate(({ ring, f, src }) => {
    const m = eval(src);
    // The strips are quadrilaterals: the two southern vertices form the short south-west edge.
    const byLat = [...ring].sort((a, b) => a[1] - b[1]);
    const sw = [(byLat[0][0] + byLat[1][0]) / 2, (byLat[0][1] + byLat[1][1]) / 2];
    const ne = [(byLat[2][0] + byLat[3][0]) / 2, (byLat[2][1] + byLat[3][1]) / 2];
    const p = m.project([sw[0] + (ne[0] - sw[0]) * f, sw[1] + (ne[1] - sw[1]) * f]);
    const r = m.getCanvas().getBoundingClientRect();
    return { x: r.left + p.x, y: r.top + p.y };
  }, { ring, f, src: FIND_MAP });
}

/* ----------------------------------------------------------------- recorder */

class Recorder {
  constructor(page, cdp, name, vw, vh, scale) {
    Object.assign(this, { page, cdp, name, vw, vh, scale });
    this.dir = path.join(REC, `${name}.raw`);
    this.frames = [];
    this.marks = {};
    this.boxes = {};
    this.onFrame = (f) => {
      const file = path.join(this.dir, `f${String(this.frames.length).padStart(6, "0")}.jpg`);
      fs.writeFileSync(file, Buffer.from(f.data, "base64"));
      this.frames.push({ ts: f.metadata.timestamp, file });
      cdp.send("Page.screencastFrameAck", { sessionId: f.sessionId }).catch(() => {});
    };
  }
  async start() {
    fs.rmSync(this.dir, { recursive: true, force: true });
    fs.mkdirSync(this.dir, { recursive: true });
    this.cdp.on("Page.screencastFrame", this.onFrame);
    this.t0call = now();
    await this.cdp.send("Page.startScreencast", {
      format: "jpeg", quality: 92, everyNthFrame: 1,
      maxWidth: Math.round(this.vw * this.scale), maxHeight: Math.round(this.vh * this.scale),
    });
    const t = Date.now();
    while (!this.frames.length && Date.now() - t < 5000) await sleep(20);
    // Force one compositor frame so a static page still yields a first frame.
    await this.page.evaluate(() => new Promise((r) => requestAnimationFrame(() => r())));
    log(`${this.name}: recording`);
  }
  mark(label, t = now()) { this.marks[label] = t; }
  async box(label, selector, t = now()) {
    const r = await this.page.evaluate((s) => {
      const el = document.querySelector(s);
      if (!el) return null;
      const b = el.getBoundingClientRect();
      return { x: b.left, y: b.top, w: b.width, h: b.height };
    }, selector);
    if (!r) throw new Error(`${this.name}: no element for box ${label} (${selector})`);
    // Clip the box to the viewport, so the editor never places overlays off-frame.
    const x0 = Math.max(0, r.x), y0 = Math.max(0, r.y);
    const x1 = Math.min(this.vw, r.x + r.w), y1 = Math.min(this.vh, r.y + r.h);
    this.boxes[label] = { t, x: x0, y: y0, w: Math.max(0, x1 - x0), h: Math.max(0, y1 - y0) };
  }
  async stop() {
    this.t1 = now();
    await this.cdp.send("Page.stopScreencast");
    this.cdp.off("Page.screencastFrame", this.onFrame);
    await sleep(150);
  }
  /** Constant 30 fps: output frame i shows the newest screencast frame at time t0 + i/30. */
  encode() {
    const fr = this.frames;
    if (fr.length < 2) throw new Error(`${this.name}: only ${fr.length} frames`);
    const t0 = fr[0].ts;
    const n = Math.max(1, Math.floor((this.t1 - t0) * FPS));
    const seq = path.join(this.dir, "seq");
    fs.mkdirSync(seq, { recursive: true });
    let j = 0;
    for (let i = 0; i < n; i++) {
      const t = t0 + i / FPS;
      while (j + 1 < fr.length && fr[j + 1].ts <= t) j++;
      fs.linkSync(fr[j].file, path.join(seq, `${String(i).padStart(6, "0")}.jpg`));
    }
    const [w, h] = execFileSync("ffprobe", ["-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0", fr[0].file])
      .toString().trim().split(",").map(Number);
    const W = Math.round(this.vw * this.scale), H = Math.round(this.vh * this.scale);
    if (w !== W || h !== H) log(`${this.name}: frame ${w}x${h}, scaling to ${W}x${H}`);
    const out = path.join(REC, `${this.name}.mp4`);
    execFileSync("ffmpeg", ["-y", "-v", "error", "-framerate", String(FPS), "-i", path.join(seq, "%06d.jpg"),
      "-vf", `scale=${W}:${H}:flags=lanczos:in_range=pc:out_range=tv,format=yuv420p`, "-c:v", "libx264", "-crf", "16", "-preset", "slow",
      "-pix_fmt", "yuv420p", "-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-r", String(FPS), "-movflags", "+faststart", out], { stdio: "inherit" });
    const gaps = fr.slice(1).map((f, i) => f.ts - fr[i].ts).sort((a, b) => a - b);
    log(`${this.name}: ${fr.length} source frames over ${(fr.at(-1).ts - t0).toFixed(2)} s (p50 gap ${(gaps[gaps.length >> 1] * 1000).toFixed(0)} ms, p95 ${(gaps[Math.floor(gaps.length * 0.95)] * 1000).toFixed(0)} ms), clock skew ${((t0 - this.t0call) * 1000).toFixed(0)} ms -> ${n} frames`);
    const frameOf = (t) => Math.min(n - 1, Math.max(0, Math.round((t - t0) * FPS)));
    const k = this.scale;
    const clip = {
      file: `rec/${this.name}.mp4`, width: W, height: H, durationInFrames: n,
      marks: Object.fromEntries(Object.entries(this.marks).map(([l, t]) => [l, frameOf(t)])),
      boxes: Object.fromEntries(Object.entries(this.boxes).map(([l, b]) => [l, {
        frame: frameOf(b.t), x: Math.round(b.x * k), y: Math.round(b.y * k), w: Math.round(b.w * k), h: Math.round(b.h * k),
      }])),
    };
    fs.rmSync(this.dir, { recursive: true, force: true });
    return clip;
  }
}

/** Stand-in for warm-up runs: same calls, no recording. */
class NoRecorder {
  async start() {}
  mark() {}
  async box() {}
  async stop() {}
}

/* -------------------------------------------------------------- interaction */

class Hand {
  constructor(page) { this.page = page; this.x = 960; this.y = 540; }
  async place(x, y) { await this.page.mouse.move(x, y); this.x = x; this.y = y; }
  async move(x, y, steps) {
    const d = Math.hypot(x - this.x, y - this.y);
    const n = steps ?? Math.round(Math.min(40, Math.max(25, d / 25)));
    // Ease-in-out path: page.mouse.move interpolates linearly, so drive the steps here.
    const x0 = this.x, y0 = this.y;
    for (let i = 1; i <= n; i++) {
      const s = i / n, e = s < 0.5 ? 2 * s * s : 1 - (-2 * s + 2) ** 2 / 2;
      await this.page.mouse.move(x0 + (x - x0) * e, y0 + (y - y0) * e);
      await sleep(14);
    }
    this.x = x; this.y = y;
  }
  async moveTo(locator, dx = 0.5, dy = 0.5) {
    await locator.scrollIntoViewIfNeeded();
    const b = await locator.boundingBox();
    if (!b) throw new Error("no bounding box");
    await this.move(b.x + b.width * dx, b.y + b.height * dy);
  }
  /** Moves, pauses like a person, clicks. Returns the time of the press. */
  async click(locatorOrPoint, pause = 420, onPress) {
    if (typeof locatorOrPoint.x === "number") await this.move(locatorOrPoint.x, locatorOrPoint.y);
    else await this.moveTo(locatorOrPoint);
    await sleep(pause);
    const t = now();
    onPress?.(t);
    await this.page.mouse.down();
    await sleep(90);
    await this.page.mouse.up();
    return t;
  }
  /** Smooth wheel scroll of the element under the cursor, like a trackpad. Time-based, so `ms` is the real duration. */
  async scroll(dy, ms = 1400) {
    const t0 = Date.now();
    let done = 0;
    for (;;) {
      const s = Math.min(1, (Date.now() - t0) / ms), e = s < 0.5 ? 2 * s * s : 1 - (-2 * s + 2) ** 2 / 2;
      const want = Math.round(dy * e);
      if (want !== done) await this.page.mouse.wheel(0, want - done);
      done = want;
      if (s >= 1) break;
      await sleep(8);
    }
  }
}

/** Touch drag through CDP: real touch events, so the page scrolls as on a phone. */
async function swipe(cdp, x, y0, y1, ms = 600) {
  const steps = Math.max(8, Math.round(ms / 16));
  await cdp.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x, y: y0 }] });
  for (let i = 1; i <= steps; i++) {
    const s = i / steps, e = 1 - (1 - s) ** 2;
    await cdp.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [{ x, y: y0 + (y1 - y0) * e }] });
    await sleep(16);
  }
  await cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
}

/* ----------------------------------------------------------------- browser */

const errors = [];
function watch(page) {
  page.on("response", (r) => {
    if (r.status() >= 500 && !r.url().includes("/tiles/")) errors.push(`${r.status()} ${r.url().slice(0, 140)}`);
  });
}

async function launch({ width, height, scale = 1, mobile = false, touch = false }) {
  const ctx = await chromium.launchPersistentContext(PROFILE, {
    // A forced device scale makes the screencast deliver frames in device pixels, not CSS pixels.
    executablePath: EXE, headless: true, args: [...GPU_FLAGS[gpu], ...(scale !== 1 ? [`--force-device-scale-factor=${scale}`] : [])],
    viewport: { width, height }, deviceScaleFactor: scale, isMobile: mobile, hasTouch: touch,
    locale: "pl-PL", timezoneId: "Europe/Warsaw", colorScheme: "light", reducedMotion: "no-preference",
  });
  await ctx.addInitScript(overlayScript, touch ? "touch" : "mouse");
  const page = ctx.pages()[0] ?? (await ctx.newPage());
  watch(page);
  const cdp = await ctx.newCDPSession(page);
  return { ctx, page, cdp };
}

async function checkGpu() {
  const { ctx, page } = await launch({ width: 800, height: 600 });
  const r = await page.evaluate(() => {
    const gl = document.createElement("canvas").getContext("webgl");
    const e = gl && gl.getExtension("WEBGL_debug_renderer_info");
    return gl ? (e ? gl.getParameter(e.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER)) : "none";
  });
  await ctx.close();
  log(`WebGL renderer (${gpu}): ${r}`);
  if (gpu === "vulkan" && !/NVIDIA|AMD|Intel/i.test(r)) { gpu = "swift"; log("falling back to SwiftShader"); }
}

/** Waits until every card has its histogram, the detail is filled and the map shows zones. */
async function analysisReady(page, timeout = 120000) {
  await page.waitForFunction(() => {
    const cards = [...document.querySelectorAll(".rs-pitem")];
    if (!cards.length) return false;
    const histOk = cards.every((c) => c.querySelector(".rs-zhist__seg") && !c.querySelector(".rs-zhist__wait"));
    const stateOk = cards.every((c) => !c.querySelector(".rs-pitem__state .ar-muted"));
    const zones = Number(document.querySelector(".rs-map__canvas")?.dataset.zones ?? 0) > 0;
    const detail = !!document.querySelector("#ocena .rs-hero") && !!document.querySelector("#retencja .rs-measure");
    const busy = document.querySelector(".rs-updating, .rs-skel");
    return histOk && stateOk && zones && detail && !busy;
  }, null, { timeout, polling: 200 });
}

async function noErrorsVisible(page, where) {
  const bad = await page.evaluate(() => [...document.querySelectorAll(".rs-error, .rs-search__error, [role=alert]")].map((e) => e.textContent?.trim()).filter(Boolean));
  if (bad.length) throw new Error(`${where}: error on screen: ${bad.join(" | ")}`);
}

/* ------------------------------------------------------------------ scenes */

let wkt = {};
async function loadGeometry() {
  for (const id of PARCELS) {
    for (let a = 0; a < 6 && !wkt[id]; a++) {
      const qs = new URLSearchParams({ request: "GetParcelById", id, result: "geom_wkt,teryt", srid: "4326" });
      const t = await (await fetch(`${BASE}/uldk?${qs}`)).text().catch(() => "");
      const m = t.match(/POLYGON\(\((.*?)\)\)/);
      if (m) wkt[id] = m[1];
      else await sleep(500);
    }
    if (!wkt[id]) throw new Error(`ULDK geometry missing for ${id}`);
  }
}

/** Scenes 1 + 2 share one page: map_search ends where analysis_load starts. */
async function sceneSearchAndLoad(rec1, rec2, warm) {
  const { ctx, page, cdp } = await launch({ width: 1920, height: 1080 });
  try {
    await page.goto(`${BASE}/`, { waitUntil: "load" });
    await mapIdle(page, 60000);
    await sleep(warm ? 300 : 1200);
    const hand = new Hand(page);
    const input = page.locator(".rs-panel__search input");
    const ib = await input.boundingBox();
    await hand.place(ib.x + ib.width + 260, ib.y + 300);
    const r1 = rec1 ? new Recorder(page, cdp, "map_search", 1920, 1080, 1) : new NoRecorder();
    await r1.start();
    await sleep(500);
    await hand.click(input, 350);
    await r1.box("searchInput", ".rs-panel__search");
    await page.keyboard.type("Fugasówka", { delay: 70 });
    await sleep(350);
    await hand.click(page.locator(".rs-panel__search button[type=submit]"), 300, (t) => r1.mark("searchSubmit", t));
    await page.waitForSelector(".rs-places__note", { timeout: 15000 });
    await page.waitForFunction(`!(${FIND_MAP}).isMoving()`, null, { timeout: 10000, polling: 50 });
    await mapIdle(page).catch(() => {});
    await sleep(200);
    for (let i = 0; i < PARCELS.length; i++) {
      const id = PARCELS[i];
      const pt = await parcelPoint(page, wkt[id], i === 0 ? 0.35 : 0.28);
      await hand.click(pt, i === 0 ? 380 : 320);
      const num = id.slice(PREFIX.length);
      await page.waitForFunction((n) => [...document.querySelectorAll(".rs-panel__list .rs-panel__name b")].some((b) => b.textContent?.includes(`Działka ${n},`)), num, { timeout: 20000, polling: 30 });
      if (i === 0) r1.mark("parcelSelected");
      // Let the map finish its refit to the clicked parcel before aiming at the next strip.
      await sleep(120);
      await page.waitForFunction(`!(${FIND_MAP}).isMoving()`, null, { timeout: 10000, polling: 30 });
    }
    const names = await page.$$eval(".rs-panel__list .rs-panel__name b", (bs) => bs.map((b) => b.textContent));
    if (names.length !== PARCELS.length) throw new Error(`map_search: selected ${names.join(" | ")}`);
    await page.waitForFunction(() => ![...document.querySelectorAll(".rs-panel__name span")].some((s) => s.textContent?.includes("…")), null, { timeout: 15000 }).catch(() => {});
    await hand.move(hand.x - 380, hand.y + 120);
    r1.mark("listReady");
    await r1.box("list", ".rs-panel__list");
    await noErrorsVisible(page, "map_search");
    await sleep(1200);
    await r1.stop();
    const clip1 = rec1 ? r1.encode() : null;

    const r2 = rec2 ? new Recorder(page, cdp, "analysis_load", 1920, 1080, 1) : new NoRecorder();
    await r2.start();
    await sleep(400);
    await hand.click(page.locator(".rs-panel__date .ar-pill, .rs-panel__date button").filter({ hasText: "Susza 2019" }).first(), 380);
    await sleep(500);
    await hand.click(page.getByRole("button", { name: /Analizuj działki/ }), 450, (t) => r2.mark("analyzeClick", t));
    await page.waitForURL(/\/analiza/, { timeout: 10000 });
    await page.waitForFunction(() => {
      const cards = [...document.querySelectorAll(".rs-pitem")];
      return cards.length > 0 && cards.every((c) => c.querySelector(".rs-zhist__seg") && !c.querySelector(".rs-zhist__wait") && !c.querySelector(".rs-pitem__state .ar-muted"));
    }, null, { timeout: 120000, polling: 50 });
    r2.mark("cardsReady");
    await page.waitForFunction(() => Number(document.querySelector(".rs-map__canvas")?.dataset.zones ?? 0) > 0, null, { timeout: 120000, polling: 50 });
    r2.mark("zonesVisible");
    await analysisReady(page);
    await mapIdle(page).catch(() => {});
    await noErrorsVisible(page, "analysis_load");
    const tb = now();
    await r2.box("map", ".rs-side__map", tb);
    await r2.box("cards", ".rs-plist", tb);
    await r2.box("firstHistogram", ".rs-pitem .rs-zhist", tb);
    await r2.box("detail", ".rs-detail", tb);
    // Glide over the cards to the zone map, then hold.
    await sleep(700);
    await hand.move(450, 330, 40);
    await sleep(600);
    await hand.move(1240, 560, 40);
    await sleep(2600);
    await r2.stop();
    const clip2 = rec2 ? r2.encode() : null;
    return { map_search: clip1, analysis_load: clip2 };
  } finally {
    await ctx.close();
  }
}

async function openAnalysis(opts, url = analysisUrl()) {
  const env = await launch(opts);
  await env.page.goto(url, { waitUntil: "load" });
  await analysisReady(env.page);
  await mapIdle(env.page, 60000);
  await noErrorsVisible(env.page, url);
  return env;
}

/** Distance in px that brings the element's top to `offset` px below the top of the left panel. */
const panelDelta = (page, selector, offset = 24) => page.evaluate(({ selector, offset }) => {
  const main = document.querySelector(".rs-main");
  const el = document.querySelector(selector);
  return Math.round(el.getBoundingClientRect().top - main.getBoundingClientRect().top - offset);
}, { selector, offset });

async function sceneDetailScroll(record) {
  const { ctx, page } = await openAnalysis({ width: 1920, height: 1080 });
  try {
    const hand = new Hand(page);
    await hand.place(420, 300);
    await sleep(800);
    const rec = record ? new Recorder(page, await ctx.newCDPSession(page), "detail_scroll", 1920, 1080, 1) : new NoRecorder();
    await rec.start();
    await sleep(500);
    const target = page.locator(".rs-pitem").nth(3);
    await hand.click(target.locator(".rs-pitem__row"), 420, (t) => rec.mark("cardClick", t));
    await page.waitForFunction(() => document.querySelectorAll(".rs-pitem")[3]?.classList.contains("is-on"), null, { timeout: 10000 });
    await analysisReady(page);
    await rec.box("activeCard", ".rs-pitem.is-on");
    await sleep(500);
    // Scroll the left panel through the detail: water state, causes, measures.
    await hand.move(330, 620, 25);
    await hand.scroll(await panelDelta(page, ".rs-detail", 16), 1300);
    await sleep(600);
    await hand.scroll(await panelDelta(page, "#przyczyny", 24), 1400);
    await sleep(700);
    await hand.scroll(await panelDelta(page, "#retencja", 16), 1300);
    await sleep(200);
    rec.mark("measuresVisible");
    await rec.box("measures", "#retencja");
    const ms = page.locator("#retencja .rs-measure:not([disabled])");
    const count = await ms.count();
    for (let i = 0; i < Math.min(2, count); i++) {
      await hand.click(ms.nth(i), 330);
      await sleep(350);
    }
    await page.waitForFunction(() => !document.querySelector("#retencja .rs-updating"), null, { timeout: 30000 }).catch(() => {});
    await sleep(250);
    // Show the simulation result under the list, then scroll back up a bit.
    if (await page.$("#retencja .rs-whatif")) await hand.scroll(await panelDelta(page, "#retencja .rs-whatif", 420), 1200);
    await sleep(1000);
    await hand.scroll(-480, 1100);
    await sleep(700);
    await noErrorsVisible(page, "detail_scroll");
    await rec.stop();
    return record ? rec.encode() : null;
  } finally {
    await ctx.close();
  }
}

/** The core feature: irrigate or hold off today, why, and the 14-day plan with soil water. */
async function sceneIrrigation(record) {
  const { ctx, page } = await openAnalysis({ width: 1920, height: 1080 });
  try {
    await page.waitForSelector("#nawadnianie .rs-plan14", { timeout: 90000 });
    const hand = new Hand(page);
    await hand.place(330, 560);
    await sleep(600);
    const rec = record ? new Recorder(page, await ctx.newCDPSession(page), "irrigation", 1920, 1080, 1) : new NoRecorder();
    await rec.start();
    await sleep(600);
    await hand.scroll(await panelDelta(page, "#nawadnianie", 16), 1700);
    await sleep(300);
    rec.mark("decision");
    await rec.box("decision", "#nawadnianie");
    await sleep(2600);
    await hand.scroll(await panelDelta(page, "#nawadnianie .rs-plan14", 16), 1400);
    await sleep(300);
    rec.mark("plan");
    await rec.box("plan", "#nawadnianie .rs-plan14");
    // Point along the 14 days, so the eye follows the plan.
    const days = page.locator("#nawadnianie .rs-plan14 .rs-fc__day");
    const n = await days.count();
    for (const i of [0, 3, 7, Math.max(0, n - 1)]) { await hand.moveTo(days.nth(i)); await sleep(450); }
    await sleep(1800);
    await noErrorsVisible(page, "irrigation");
    await rec.stop();
    return record ? rec.encode() : null;
  } finally {
    await ctx.close();
  }
}

const urlFor = (date) => `${BASE}/analiza?dzialki=${PARCELS.map((id) => `${enc(id)}~${CROP}`).join(",")}&aktywna=${enc(PARCELS[0])}&data=${date}`;
const todayIso = () => new Date().toLocaleDateString("sv-SE", { timeZone: "Europe/Warsaw" });

/** "Dziś": the water-state forecast, the irrigate-or-hold decision and the 14-day plan for today. */
async function sceneToday(record) {
  const { ctx, page } = await openAnalysis({ width: 1920, height: 1080 }, urlFor(todayIso()));
  try {
    await page.waitForSelector("#nawadnianie .rs-plan14", { timeout: 120000 });
    const hand = new Hand(page);
    await hand.place(330, 560);
    await sleep(600);
    const rec = record ? new Recorder(page, await ctx.newCDPSession(page), "today", 1920, 1080, 1) : new NoRecorder();
    await rec.start();
    await sleep(500);
    // The timeline with forecast days sits in the detail; bring it up first.
    const tl = page.locator(".rs-detail h3, .rs-detail h4").filter({ hasText: /prognoz/i }).first();
    if (await tl.count()) {
      await hand.scroll(await page.evaluate((el) => { const m = document.querySelector(".rs-main"); return Math.round(el.getBoundingClientRect().top - m.getBoundingClientRect().top - 40); }, await tl.elementHandle()), 1500);
      rec.mark("forecast");
      await sleep(2200);
    }
    await hand.scroll(await panelDelta(page, "#nawadnianie", 16), 1600);
    await sleep(300);
    rec.mark("decision");
    await rec.box("decision", "#nawadnianie");
    await sleep(2600);
    await hand.scroll(await panelDelta(page, "#nawadnianie .rs-plan14", 16), 1400);
    await sleep(300);
    rec.mark("plan");
    const days = page.locator("#nawadnianie .rs-plan14 .rs-fc__day");
    const n = await days.count();
    for (const i of [0, 4, 9, Math.max(0, n - 1)]) { if (n) { await hand.moveTo(days.nth(i)); await sleep(420); } }
    await sleep(1600);
    await noErrorsVisible(page, "today");
    await rec.stop();
    return record ? rec.encode() : null;
  } finally {
    await ctx.close();
  }
}

/** The same parcel under other crops, then picked measures and the water state before -> after. */
async function sceneCrops(record) {
  const { ctx, page } = await openAnalysis({ width: 1920, height: 1080 }, urlFor(process.env.CROPS_DATE ?? "2019-07-25"));
  try {
    await page.waitForSelector(".rs-cropcmp", { timeout: 120000 });
    const hand = new Hand(page);
    await hand.place(330, 560);
    await sleep(600);
    const rec = record ? new Recorder(page, await ctx.newCDPSession(page), "crops", 1920, 1080, 1) : new NoRecorder();
    await rec.start();
    await sleep(400);
    await hand.scroll(await panelDelta(page, ".rs-cropcmp", 24), 1600);
    await sleep(200);
    // Unfold the full crop list, so drier and wetter crops both show.
    const more = page.locator(".rs-cropcmp__more");
    if (await more.count()) { await hand.click(more, 300); await sleep(500); }
    rec.mark("crops");
    await rec.box("crops", ".rs-cropcmp");
    const rows = page.locator(".rs-cropcmp__row");
    const k = await rows.count();
    for (let i = 0; i < Math.min(5, k); i++) { await hand.moveTo(rows.nth(i), 0.35); await sleep(380); }
    await sleep(900);
    await hand.scroll(await panelDelta(page, "#retencja", 16), 1500);
    await sleep(300);
    // Measures that together keep enough water to move the state (names from the app's catalog).
    const want = (process.env.CROPS_MEASURES ?? "materii organicznej|Mulcz|Zastawka").split("|");
    for (const name of want) {
      const m = page.locator("#retencja .rs-measure:not([disabled])").filter({ hasText: new RegExp(name, "i") }).first();
      if (await m.count()) { await hand.click(m, 330); await sleep(450); }
    }
    await page.waitForSelector("#retencja .rs-shift", { timeout: 60000 }).catch(() => {});
    await sleep(300);
    if (await page.$("#retencja .rs-shift")) await hand.scroll(await panelDelta(page, "#retencja .rs-shift", 360), 1200);
    await sleep(200);
    rec.mark("shift");
    if (await page.$("#retencja .rs-shift")) await rec.box("shift", "#retencja .rs-shift");
    await sleep(2600);
    await noErrorsVisible(page, "crops");
    await rec.stop();
    return record ? rec.encode() : null;
  } finally {
    await ctx.close();
  }
}

async function sceneReplay(record) {
  const { ctx, page } = await openAnalysis({ width: 1920, height: 1080 });
  try {
    // Start with the replay bar in view; once it plays, the panel scrolls up to the cards.
    await page.evaluate(() => {
      const main = document.querySelector(".rs-main");
      const bar = document.querySelector(".rs-replay");
      main.scrollTop += bar.getBoundingClientRect().top - main.getBoundingClientRect().top - 620;
    });
    await sleep(500);
    const hand = new Hand(page);
    await hand.place(600, 420);
    const rec = record ? new Recorder(page, await ctx.newCDPSession(page), "replay", 1920, 1080, 1) : new NoRecorder();
    await rec.start();
    const t0 = Date.now();
    await sleep(500);
    await hand.click(page.locator(".rs-replay__bar button").filter({ hasText: /Odtwórz sezon/ }).first(), 400, (t) => rec.mark("start", t));
    await page.waitForSelector(".rs-replay__date", { timeout: 60000 });
    await sleep(900);
    await hand.move(330, 560, 30);
    const top = await page.evaluate(() => document.querySelector(".rs-main").scrollTop);
    await hand.scroll(-top, 1500);
    await sleep(500);
    await hand.move(1250, 600, 40);
    // Play at 1x: the clip ends in early August, around the drought peak of 2019.
    while (Date.now() - t0 < 12000) await sleep(50);
    await noErrorsVisible(page, "replay");
    await rec.stop();
    return record ? rec.encode() : null;
  } finally {
    await ctx.close();
  }
}

async function sceneMobile(record) {
  const env = await openAnalysis({ width: 390, height: 844, scale: 2, mobile: true, touch: true });
  const { ctx, page, cdp } = env;
  try {
    await page.evaluate(() => window.scrollTo(0, 0));
    await sleep(800);
    const rec = record ? new Recorder(page, cdp, "mobile", 390, 844, 2) : new NoRecorder();
    await rec.start();
    await sleep(1400);
    await swipe(cdp, 230, 700, 390, 650);
    await sleep(1300);
    const card = page.locator(".rs-pitem").nth(1).locator(".rs-pitem__row");
    await card.scrollIntoViewIfNeeded();
    const b = await card.boundingBox();
    const t = now();
    rec.mark("cardTap", t);
    await page.touchscreen.tap(b.x + b.width * 0.55, b.y + b.height * 0.4);
    await page.waitForFunction(() => document.querySelectorAll(".rs-pitem")[1]?.classList.contains("is-on"), null, { timeout: 10000 });
    await analysisReady(page);
    await sleep(1600);
    await swipe(cdp, 220, 720, 300, 700);
    await sleep(1500);
    await swipe(cdp, 220, 700, 380, 650);
    await sleep(1600);
    await swipe(cdp, 220, 300, 560, 600);
    await sleep(1500);
    await noErrorsVisible(page, "mobile");
    await rec.stop();
    return record ? rec.encode() : null;
  } finally {
    await ctx.close();
  }
}

// Hides every panel, overlay and control: only the map canvas with the parcel and zone layers stays.
const MAP_ONLY_CSS = `
  .rs-ahead, .rs-main, .rs-fnav, .rs-map__overlay, .rs-map__tag, .rs-map__zoom, .rs-map__locate, .rs-map__credit,
  .rs-map__pop, .rs-map__anchor, .maplibregl-control-container, #__cap_cur { display: none !important; }
  .rs-analysis { display: block !important; height: 100vh !important; }
  .rs-grid { display: block !important; padding: 0 !important; margin: 0 !important; max-width: none !important; }
  .rs-side { position: fixed !important; inset: 0 !important; height: 100vh !important; width: 100vw !important; top: 0 !important; }
  .rs-side__map { border-radius: 0 !important; }`;

// End zoom: at 16.3 the four strips cover only ~300 px of the frame height; 17.6 makes them fill it.
const ZEND = Number(process.env.ORTHO_ZOOM ?? 17.6);

async function sceneOrtho(record) {
  const { ctx, page, cdp } = await openAnalysis({ width: 1920, height: 1080 });
  try {
    await page.addStyleTag({ content: MAP_ONLY_CSS });
    await sleep(400);
    const center = PARCELS.map((id) => wkt[id].match(/-?\d+\.\d+ -?\d+\.\d+/g).map((s) => s.split(" ").map(Number)))
      .flat().reduce((a, p, _, all) => [a[0] + p[0] / all.length, a[1] + p[1] / all.length], [0, 0]);
    const cam = async (z, extra = "") => page.evaluate(`(() => { const m = ${FIND_MAP}; m.resize(); m.jumpTo({ center: [${center[0]}, ${center[1]}], zoom: ${z}, bearing: 0, pitch: 0 }); ${extra} })()`);
    // Pre-load every zoom level of the flight, then start from the top.
    for (const z of [ZEND, 17, 16.3, 15.5, 14.5, 13.5, 12.5]) { await cam(z); await sleep(200); await mapIdle(page, 60000); }
    await sleep(800);
    const rec = record ? new Recorder(page, cdp, "ortho_zoom", 1920, 1080, 1) : new NoRecorder();
    await rec.start();
    await sleep(700);
    const DUR = 6200;
    await page.evaluate(`(() => { const m = ${FIND_MAP}; m.easeTo({ zoom: ${ZEND}, duration: ${DUR}, easing: (t) => t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2 }); })()`);
    await sleep(DUR);
    await page.waitForFunction(`!(${FIND_MAP}).isMoving()`, null, { timeout: 5000 });
    rec.mark("end");
    await mapIdle(page).catch(() => {});
    await sleep(900);
    await rec.stop();
    return record ? rec.encode() : null;
  } finally {
    await ctx.close();
  }
}

async function stills() {
  const { ctx, page } = await openAnalysis({ width: 1920, height: 1080, scale: 2 });
  const out = {};
  try {
    // Stills carry no cursor.
    await page.addStyleTag({ content: "#__cap_cur, .__cap_rip { display: none !important; }" });
    await sleep(600);
    const f1 = path.join(REC, "still_analysis@2x.png");
    await page.screenshot({ path: f1, type: "png" });
    out.still_analysis = { file: "rec/still_analysis@2x.png", width: 3840, height: 2160 };
    await page.evaluate(() => {
      const main = document.querySelector(".rs-main");
      main.scrollTop += document.querySelector("#retencja").getBoundingClientRect().top - main.getBoundingClientRect().top - 16;
    });
    const ms = page.locator("#retencja .rs-measure:not([disabled])");
    if (await ms.count()) { await ms.nth(0).click(); await sleep(400); }
    await page.waitForFunction(() => !document.querySelector("#retencja .rs-updating"), null, { timeout: 30000 }).catch(() => {});
    await sleep(600);
    // The section is taller than the scrolling panel: keep only its part visible inside the panel.
    const b = await page.evaluate(() => {
      const s = document.querySelector("#retencja").getBoundingClientRect();
      const m = document.querySelector(".rs-main").getBoundingClientRect();
      const top = Math.max(s.top, m.top), bottom = Math.min(s.bottom, m.bottom, innerHeight);
      return { x: Math.floor(s.left) - 8, y: Math.floor(top) - 8, width: Math.ceil(s.width) + 16, height: Math.ceil(bottom - top) + 8 };
    });
    const f2 = path.join(REC, "still_measures@2x.png");
    await page.screenshot({ path: f2, type: "png", clip: b });
    const [w, h] = execFileSync("ffprobe", ["-v", "error", "-show_entries", "stream=width,height", "-of", "csv=p=0", f2]).toString().trim().split(",").map(Number);
    out.still_measures = { file: "rec/still_measures@2x.png", width: w, height: h };
    log(`stills: measures clip ${b.width}x${b.height} css px`);
  } finally {
    await ctx.close();
  }
  return out;
}

/* -------------------------------------------------------------------- main */

const ALL = ["map_search", "analysis_load", "detail_scroll", "irrigation", "today", "crops", "replay", "mobile", "ortho_zoom"];
const argv = process.argv.slice(2);
const flags = new Set(argv.filter((a) => a.startsWith("--")));
const wanted = argv.filter((a) => !a.startsWith("--"));
const scenes = wanted.length ? wanted : ALL;
for (const s of scenes) if (!ALL.includes(s) && s !== "stills") throw new Error(`unknown scene ${s}`);
const doStills = flags.has("--stills") || scenes.includes("stills") || (!wanted.length && !flags.has("--no-stills"));
const warm = !flags.has("--no-warm");

async function run(scene, record) {
  for (let attempt = 1; attempt <= 3; attempt++) {
    try {
      if (scene === "map_search" || scene === "analysis_load") {
        const both = await sceneSearchAndLoad(record && scenes.includes("map_search"), record && scenes.includes("analysis_load"), !record);
        return both;
      }
      const fn = { detail_scroll: sceneDetailScroll, irrigation: sceneIrrigation, today: sceneToday, crops: sceneCrops, replay: sceneReplay, mobile: sceneMobile, ortho_zoom: sceneOrtho }[scene];
      return { [scene]: await fn(record) };
    } catch (e) {
      log(`${scene} ${record ? "record" : "warm"} attempt ${attempt} failed: ${e.message.split("\n")[0]}`);
      if (attempt === 3) throw e;
      await sleep(1500);
    }
  }
}

fs.mkdirSync(REC, { recursive: true });
await checkGpu();
await loadGeometry();
const manifestPath = path.join(REC, "manifest.json");
const manifest = fs.existsSync(manifestPath) ? JSON.parse(fs.readFileSync(manifestPath, "utf8")) : { fps: FPS, clips: {}, stills: {} };
manifest.fps = FPS;

const units = [...new Set(scenes.filter((s) => s !== "stills").map((s) => (s === "analysis_load" ? "map_search" : s)))];
if (warm) {
  log("warming caches (backend analyses, weather, tiles)");
  for (const u of units) await run(u, false);
  for (const id of PARCELS) {
    const { ctx } = await openAnalysis({ width: 1920, height: 1080 }, analysisUrl(id));
    await ctx.close();
  }
}
for (const u of units) {
  const clips = await run(u, true);
  for (const [k, v] of Object.entries(clips)) if (v) manifest.clips[k] = v;
  fs.writeFileSync(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`);
}
if (doStills) {
  manifest.stills = { ...manifest.stills, ...(await stills()) };
  fs.writeFileSync(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`);
}
if (errors.length) log(`server errors seen (non-tile):\n  ${[...new Set(errors)].slice(0, 20).join("\n  ")}`);
log("done", JSON.stringify(Object.fromEntries(Object.entries(manifest.clips).map(([k, c]) => [k, c.durationInFrames]))));
