import { addDays, fromDay, isoOf, lastDayOfMonth, parts, seasonYear, toDay, todayIso, inSeason, type Iso } from "./dates";
import { request, sourceFailure } from "./http";

/** Open-Meteo: ERA5 reanalysis archive + forecast. Daily precipitation and FAO-56 reference evapotranspiration. CORS: *. */
export const ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive";
export const FORECAST_URL = "https://api.open-meteo.com/v1/forecast";
const DAILY = "precipitation_sum,et0_fao_evapotranspiration";
/** The archive lags behind real time; the forecast model fills the gap. */
const ARCHIVE_LAG_DAYS = 7;

export interface DailySeries {
  start: Iso;
  /** Day index of `start` (UTC days since 1970). */
  startDay: number;
  p: (number | null)[];
  et0: (number | null)[];
  /** Values from this date on are forecast, not observation. */
  forecastFrom: Iso;
  forecastOk: boolean;
  grid: { lat: number; lon: number; elevation: number | null };
  retrievedAt: Date;
}

interface Chunk { from: Iso; p: (number | null)[]; e: (number | null)[]; lat?: number; lon?: number; elevation?: number }
interface OmDaily { daily?: { time: string[]; precipitation_sum: (number | null)[]; et0_fao_evapotranspiration: (number | null)[] }; latitude?: number; longitude?: number; elevation?: number; reason?: string }

const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));
/** Same-origin proxy of our server (/om/…); without it (plain static hosting) the browser calls Open-Meteo directly. */
let proxyOk = true;
async function viaServer(url: string): Promise<{ status: number; ok: boolean; text: string }> {
  if (proxyOk) {
    const local = url.replace(ARCHIVE_URL, "/om/archive").replace(FORECAST_URL, "/om/forecast");
    try {
      const r = await request(local, 12000);
      if (r.status !== 404 && !r.text.trimStart().startsWith("<")) return r;
    } catch { /* fall through to the direct call */ }
    proxyOk = false;
  }
  return request(url, 12000);
}
/** Open-Meteo limits each network address to about 600 calls a minute: on 429 or a timeout, wait and try again (3 attempts). */
async function getJson(url: string): Promise<OmDaily> {
  let res: { status: number; ok: boolean; text: string } | null = null;
  for (const [i, pause] of [0, 4000, 15000].entries()) {
    if (pause) await wait(pause);
    try {
      res = await viaServer(url);
    } catch (e) {
      if (i === 2) throw sourceFailure("Open-Meteo", e);
      continue;
    }
    if (res.status !== 429 && res.status < 500) break;
  }
  if (!res || !res.ok) throw sourceFailure("Open-Meteo", null, res?.status ?? 0);
  const json = JSON.parse(res.text) as OmDaily;
  if (!json.daily) throw sourceFailure("Open-Meteo", null, 502);
  return json;
}
function toChunk(j: OmDaily): Chunk {
  const d = j.daily!;
  return { from: d.time[0]!, p: d.precipitation_sum, e: d.et0_fao_evapotranspiration, lat: j.latitude, lon: j.longitude, elevation: j.elevation };
}

/** The 10 years the "same term" comparison shows: ending this year if the day has passed, else last year. */
export function compareYears(date: Iso, today: Iso): number[] {
  const [ty] = parts(today);
  const [, m, d] = parts(date);
  const last = isoOf(ty, m, Math.min(d, lastDayOfMonth(ty, m))) <= today ? ty : ty - 1;
  return Array.from({ length: 10 }, (_, i) => last - 9 + i);
}

/** Date ranges an analysis needs: the season of the date with 60 days before, and 60 days up to each comparison date. */
export function neededRanges(date: Iso, today: Iso): [Iso, Iso][] {
  const sy = seasonYear(date);
  const seasonEnd = inSeason(date) ? date : isoOf(sy, 9, 30) > date ? date : isoOf(sy, 9, 30);
  const main: [Iso, Iso] = [addDays(isoOf(sy, 3, 21), -1), date > seasonEnd ? date : seasonEnd];
  // The daily timeline shows 45 days before the date (each needs 60 days of KBW) and 15 days after it.
  if (addDays(date, -105) < main[0]) main[0] = addDays(date, -105);
  if (addDays(date, 15) > main[1]) main[1] = addDays(date, 15);
  const [, m, d] = parts(date);
  const ranges: [Iso, Iso][] = [main, ...compareYears(date, today).map((y): [Iso, Iso] => {
    const day = isoOf(y, m, Math.min(d, lastDayOfMonth(y, m)));
    return [addDays(day, -59), isoOf(y, m, lastDayOfMonth(y, m))];
  })];
  const clipped = ranges.map(([a, b]): [Iso, Iso] => [a, b > today ? today : b]).filter(([a, b]) => a <= b).sort((x, y) => (x[0] < y[0] ? -1 : 1));
  const merged: [Iso, Iso][] = [];
  for (const r of clipped) {
    const last = merged[merged.length - 1];
    if (last && toDay(r[0]) <= toDay(last[1]) + 1) { if (r[1] > last[1]) last[1] = r[1]; } else merged.push([r[0], r[1]]);
  }
  return merged;
}

const LS = "rusalka:om:";
const mem = new Map<string, Promise<Chunk>>();
/** Archive chunks are immutable once older than the archive lag: kept in memory and localStorage. */
function archiveChunk(lat: number, lon: number, from: Iso, to: Iso): Promise<Chunk> {
  const key = `${lat.toFixed(2)},${lon.toFixed(2)},${from},${to}`;
  const hit = mem.get(key);
  if (hit) return hit;
  try {
    const raw = localStorage.getItem(LS + key);
    if (raw) { const c = Promise.resolve(JSON.parse(raw) as Chunk); mem.set(key, c); return c; }
  } catch { /* storage blocked: fetch instead */ }
  const p = getJson(`${ARCHIVE_URL}?latitude=${lat.toFixed(4)}&longitude=${lon.toFixed(4)}&daily=${DAILY}&timezone=Europe%2FWarsaw&start_date=${from}&end_date=${to}`).then(toChunk);
  mem.set(key, p);
  p.then((c) => { try { localStorage.setItem(LS + key, JSON.stringify(c)); } catch { /* quota: memory only */ } }, () => mem.delete(key));
  return p;
}
const fcMem = new Map<string, Promise<Chunk>>();
function forecastChunk(lat: number, lon: number, today: Iso): Promise<Chunk> {
  const key = `${lat.toFixed(2)},${lon.toFixed(2)},${today}`;
  const hit = fcMem.get(key);
  if (hit) return hit;
  const p = getJson(`${FORECAST_URL}?latitude=${lat.toFixed(4)}&longitude=${lon.toFixed(4)}&daily=${DAILY}&timezone=Europe%2FWarsaw&past_days=${ARCHIVE_LAG_DAYS + 7}&forecast_days=16`).then(toChunk);
  fcMem.set(key, p);
  p.catch(() => fcMem.delete(key));
  return p;
}

const seriesMem = new Map<string, Promise<DailySeries>>();
/** Weather an analysis of `date` needs, from about 11 small archive requests (instead of one 11-year request) plus the forecast. */
export function weatherFor(lat: number, lon: number, date: Iso): Promise<DailySeries> {
  const today = todayIso();
  const key = `${lat.toFixed(2)},${lon.toFixed(2)},${date},${today}`;
  const hit = seriesMem.get(key);
  if (hit) return hit;
  const p = load(lat, lon, date, today);
  seriesMem.set(key, p);
  p.catch(() => seriesMem.delete(key));
  return p;
}

async function load(lat: number, lon: number, date: Iso, today: Iso): Promise<DailySeries> {
  const archiveEnd = addDays(today, -ARCHIVE_LAG_DAYS);
  const ranges = neededRanges(date, today);
  const needForecast = ranges.some(([, b]) => b > archiveEnd) || date >= addDays(today, -2);
  const arch = ranges.filter(([a]) => a <= archiveEnd).map(([a, b]) => archiveChunk(lat, lon, a, b > archiveEnd ? archiveEnd : b));
  const [archive, fc] = await Promise.all([Promise.all(arch), needForecast ? forecastChunk(lat, lon, today).then((c) => c, () => null) : Promise.resolve(null)]);
  const from = ranges[0]?.[0] ?? addDays(date, -59);
  const startDay = toDay(from);
  const n = toDay(addDays(today, 15)) - startDay + 1;
  const p: (number | null)[] = new Array(n).fill(null);
  const et0: (number | null)[] = new Array(n).fill(null);
  const put = (c: Chunk, overwrite: boolean) => c.p.forEach((pv, i) => {
    const k = toDay(c.from) + i - startDay;
    if (k < 0 || k >= n) return;
    if (overwrite || p[k] === null) p[k] = pv ?? null;
    if (overwrite || et0[k] === null) et0[k] = c.e[i] ?? null;
  });
  archive.forEach((c) => put(c, true));
  if (fc) put(fc, false);
  const g = archive[0] ?? fc;
  return {
    start: from, startDay, p, et0, forecastFrom: addDays(today, 1), forecastOk: !!fc || !needForecast,
    grid: { lat: g?.lat ?? lat, lon: g?.lon ?? lon, elevation: g?.elevation ?? null }, retrievedAt: new Date(),
  };
}

export function dayIndex(s: DailySeries, iso: Iso): number {
  return toDay(iso) - s.startDay;
}
export function lastDate(s: DailySeries): Iso {
  return fromDay(s.startDay + s.p.length - 1);
}
