import { request, sourceFailure } from "./http";
import type { Iso } from "./dates";

/**
 * NASA POWER daily point API: backup for the Open-Meteo archive when it refuses requests (HTTP 429 limit).
 * Same method as the backend (src/backend/clients/meteo.py power_to_block): ET₀ computed by FAO-56
 * Penman-Monteith from daily Tmax/Tmin, RH, wind at 2 m and solar radiation. Grid 0.5° × 0.625°. CORS: *.
 */
export const POWER_URL = "https://power.larc.nasa.gov/api/temporal/daily/point";
const PARAMS = "PRECTOTCORR,T2M_MAX,T2M_MIN,RH2M,WS2M,ALLSKY_SFC_SW_DWN";
const STEFAN_BOLTZMANN_MJ = 4.903e-9;

const svp = (t: number) => 0.6108 * Math.exp((17.27 * t) / (t + 237.3));

function dayOfYear(iso: Iso): number {
  const [y, m, d] = iso.split("-").map(Number) as [number, number, number];
  return Math.round((Date.UTC(y, m - 1, d) - Date.UTC(y, 0, 0)) / 86_400_000);
}

/** FAO-56 eq. 21: extraterrestrial radiation Ra [MJ m⁻² day⁻¹]. */
function extraterrestrialRadiation(latDeg: number, iso: Iso): number {
  const doy = dayOfYear(iso);
  const phi = (latDeg * Math.PI) / 180;
  const dr = 1 + 0.033 * Math.cos((2 * Math.PI * doy) / 365);
  const delta = 0.409 * Math.sin((2 * Math.PI * doy) / 365 - 1.39);
  const ws = Math.acos(Math.max(-1, Math.min(1, -Math.tan(phi) * Math.tan(delta))));
  return ((24 * 60) / Math.PI) * 0.082 * dr * (ws * Math.sin(phi) * Math.sin(delta) + Math.cos(phi) * Math.cos(delta) * Math.sin(ws));
}

/** Daily reference evapotranspiration ET₀ [mm], FAO-56 eq. 6 (port of src/hydro_core/et0.py). */
export function fao56Et0Daily(iso: Iso, latDeg: number, elevationM: number, tmax: number, tmin: number, rhMean: number, wind2m: number, solarMj: number): number {
  const tmean = (tmax + tmin) / 2;
  const pressure = 101.3 * ((293 - 0.0065 * elevationM) / 293) ** 5.26;
  const gamma = 0.000665 * pressure;
  const es = (svp(tmax) + svp(tmin)) / 2;
  const ea = Math.max(0, Math.min(es, (rhMean / 100) * es));
  const delta = (4098 * svp(tmean)) / (tmean + 237.3) ** 2;
  const ra = extraterrestrialRadiation(latDeg, iso);
  const rso = (0.75 + 2e-5 * elevationM) * ra;
  const rns = 0.77 * solarMj;
  const ratio = rso > 0 ? Math.min(1, solarMj / rso) : 0.5;
  const rnl = STEFAN_BOLTZMANN_MJ * (((tmax + 273.16) ** 4 + (tmin + 273.16) ** 4) / 2) * (0.34 - 0.14 * Math.sqrt(ea)) * (1.35 * ratio - 0.35);
  const rn = rns - rnl;
  const num = 0.408 * delta * rn + ((gamma * 900) / (tmean + 273)) * wind2m * (es - ea);
  return Math.max(0, num / (delta + gamma * (1 + 0.34 * wind2m)));
}

interface PowerJson {
  geometry?: { coordinates?: [number, number, number?] };
  header?: { fill_value?: number };
  properties?: { parameter?: Record<string, Record<string, number>> };
}
export interface PowerChunk { from: Iso; p: (number | null)[]; e: (number | null)[]; lat: number; lon: number; elevation: number | null }

/** Parses the POWER answer into daily precipitation and FAO-56 ET₀ (null where a variable is missing). */
export function parsePower(j: PowerJson, lat: number, lon: number): PowerChunk {
  const par = j.properties?.parameter;
  if (!par?.PRECTOTCORR) throw sourceFailure("NASA POWER", null, 502);
  const fill = j.header?.fill_value ?? -999;
  const elevation = j.geometry?.coordinates?.[2] ?? null;
  const v = (name: string, key: string): number | null => {
    const x = par[name]?.[key];
    return x === undefined || x === null || x === fill ? null : x;
  };
  const keys = Object.keys(par.PRECTOTCORR).sort();
  const iso = (k: string) => `${k.slice(0, 4)}-${k.slice(4, 6)}-${k.slice(6, 8)}` as Iso;
  const p = keys.map((k) => v("PRECTOTCORR", k));
  const e = keys.map((k) => {
    const tx = v("T2M_MAX", k), tn = v("T2M_MIN", k), rh = v("RH2M", k), ws = v("WS2M", k), rs = v("ALLSKY_SFC_SW_DWN", k);
    if (tx === null || tn === null || rh === null || ws === null || rs === null) return null;
    return Math.round(fao56Et0Daily(iso(k), lat, elevation ?? 100, tx, tn, rh, ws, rs) * 100) / 100;
  });
  return { from: iso(keys[0]!), p, e, lat, lon, elevation };
}

/** Daily precipitation and ET₀ for [from, to] from NASA POWER. */
export async function powerChunk(lat: number, lon: number, from: Iso, to: Iso): Promise<PowerChunk> {
  const compact = (d: Iso) => d.replace(/-/g, "");
  const url = `${POWER_URL}?parameters=${PARAMS}&community=AG&latitude=${lat.toFixed(4)}&longitude=${lon.toFixed(4)}&start=${compact(from)}&end=${compact(to)}&format=JSON`;
  let res;
  try {
    res = await request(url, 25000);
  } catch (e) {
    throw sourceFailure("NASA POWER", e);
  }
  if (!res.ok) throw sourceFailure("NASA POWER", null, res.status);
  return parsePower(JSON.parse(res.text) as PowerJson, lat, lon);
}
