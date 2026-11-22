export type Position = [number, number];
export type Ring = Position[];
export interface PolygonGeometry { type: "Polygon"; coordinates: Ring[] }
export interface MultiPolygonGeometry { type: "MultiPolygon"; coordinates: Ring[][] }
export type AreaGeometry = PolygonGeometry | MultiPolygonGeometry;
export type BBox = [number, number, number, number];

/** Parses POLYGON / MULTIPOLYGON WKT (optionally prefixed with "SRID=n;"). Returns polygons as rings of [x, y]. */
export function parseWkt(wkt: string): Ring[][] {
  const body = wkt.replace(/^SRID=\d+;/i, "").trim();
  const kind = /^(MULTIPOLYGON|POLYGON)\s*/i.exec(body);
  if (!kind) throw new Error("Nieobsługiwany typ geometrii WKT");
  const ringOf = (s: string): Ring =>
    s.split(",").map((pt) => {
      const [x, y] = pt.trim().split(/\s+/).map(Number);
      if (!Number.isFinite(x) || !Number.isFinite(y)) throw new Error("Zła współrzędna WKT");
      return [x as number, y as number];
    });
  const polygonOf = (s: string): Ring[] => [...s.matchAll(/\(([^()]+)\)/g)].map((m) => ringOf(m[1] ?? ""));
  if (kind[1]!.toUpperCase() === "POLYGON") return [polygonOf(body)];
  const inner = body.slice(body.indexOf("(") + 1, body.lastIndexOf(")"));
  return [...inner.matchAll(/\(\s*(\([^()]+\)(?:\s*,\s*\([^()]+\))*)\s*\)/g)].map((m) => polygonOf(m[1] ?? ""));
}

export function ringArea(r: Ring): number {
  let s = 0;
  for (let i = 0, j = r.length - 1; i < r.length; j = i++) s += (r[j]![0] + r[i]![0]) * (r[j]![1] - r[i]![1]);
  return Math.abs(s) / 2;
}
/** Planar area of polygons in a metric system (holes subtracted). */
export function polygonsArea(polys: Ring[][]): number {
  return polys.reduce((acc, rings) => acc + rings.reduce((a, r, i) => a + (i === 0 ? ringArea(r) : -ringArea(r)), 0), 0);
}

// GRS80 ellipsoid.
const A = 6378137, E2 = 0.00669438002290;
/**
 * Projects WGS84 lon/lat to local metres around lat0 with the ellipsoid's meridional (M) and prime-vertical (N) radii.
 * For a field-sized polygon the area error is below 0.01%.
 */
export function toLocalMetres(polys: Ring[][]): Ring[][] {
  const pts = polys.flatMap((p) => p[0] ?? []);
  const lat0 = pts.reduce((a, p) => a + p[1], 0) / Math.max(1, pts.length);
  const lon0 = pts.reduce((a, p) => a + p[0], 0) / Math.max(1, pts.length);
  const phi = (lat0 * Math.PI) / 180, s2 = Math.sin(phi) ** 2;
  const N = A / Math.sqrt(1 - E2 * s2), M = (A * (1 - E2)) / (1 - E2 * s2) ** 1.5;
  const k = Math.PI / 180;
  return polys.map((rings) => rings.map((r) => r.map(([x, y]) => [(x - lon0) * k * N * Math.cos(phi), (y - lat0) * k * M] as Position)));
}
export function areaWgs84(polys: Ring[][]): number {
  return polygonsArea(toLocalMetres(polys));
}
/** Area-weighted centroid of the outer ring of the largest polygon (lon/lat in, lon/lat out). */
export function centroid(polys: Ring[][]): Position {
  const outer = polys.map((p) => p[0]!).sort((a, b) => ringArea(b) - ringArea(a))[0]!;
  let a = 0, cx = 0, cy = 0;
  for (let i = 0, j = outer.length - 1; i < outer.length; j = i++) {
    const f = outer[j]![0] * outer[i]![1] - outer[i]![0] * outer[j]![1];
    a += f; cx += (outer[j]![0] + outer[i]![0]) * f; cy += (outer[j]![1] + outer[i]![1]) * f;
  }
  if (a === 0) return outer[0]!;
  return [cx / (3 * a), cy / (3 * a)];
}
export function toGeometry(polys: Ring[][]): AreaGeometry {
  return polys.length === 1 ? { type: "Polygon", coordinates: polys[0]! } : { type: "MultiPolygon", coordinates: polys };
}
export function bboxOf(g: AreaGeometry): BBox {
  const polys = g.type === "Polygon" ? [g.coordinates] : g.coordinates;
  let w = Infinity, s = Infinity, e = -Infinity, n = -Infinity;
  for (const rings of polys) for (const [x, y] of rings[0] ?? []) { w = Math.min(w, x); e = Math.max(e, x); s = Math.min(s, y); n = Math.max(n, y); }
  return [w, s, e, n];
}
/** Smallest box covering all boxes; null for an empty list. */
export function unionBbox(boxes: BBox[]): BBox | null {
  if (!boxes.length) return null;
  return boxes.reduce((a, b) => [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[2], b[2]), Math.max(a[3], b[3])]);
}

/** Point-in-polygon (even–odd rule, holes included) for lon/lat geometry. */
export function containsPoint(g: AreaGeometry, [x, y]: Position): boolean {
  const polys = g.type === "Polygon" ? [g.coordinates] : g.coordinates;
  let inside = false;
  for (const rings of polys) for (const r of rings) {
    for (let i = 0, j = r.length - 1; i < r.length; j = i++) {
      const [xi, yi] = r[i]!, [xj, yj] = r[j]!;
      if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
    }
  }
  return inside;
}

export interface LabelPose {
  /** Interior anchor point centered horizontally along the longest-edge chord (lon/lat). */
  center: Position;
  /** Endpoints of the interior chord parallel to the longest edge (lon/lat). */
  chord: [Position, Position];
  /** Perpendicular interior width at the anchor in metres. */
  widthM: number;
  /** Interior chord length along the longest edge in metres. */
  lengthM: number;
}

/**
 * Computes an interior label anchor and fitted chord aligned with the parcel's longest edge.
 * Collinear boundary segments (within 6 deg) are summed so subdivided strip borders still win.
 */
export function parcelLabelPose(g: AreaGeometry): LabelPose {
  const polys = g.type === "Polygon" ? [g.coordinates] : g.coordinates;
  const rings = [...polys].sort((a, b) => ringArea(b[0] ?? []) - ringArea(a[0] ?? []))[0] ?? [];
  const outer = rings[0] ?? [];
  if (outer.length < 3) {
    const pt: Position = outer[0] ?? [0, 0];
    return { center: pt, chord: [pt, pt], widthM: 1, lengthM: 1 };
  }

  const lat0 = outer.reduce((a, p) => a + p[1], 0) / outer.length;
  const lon0 = outer.reduce((a, p) => a + p[0], 0) / outer.length;
  const phi = (lat0 * Math.PI) / 180, s2 = Math.sin(phi) ** 2;
  const N = A / Math.sqrt(1 - E2 * s2), M = (A * (1 - E2)) / (1 - E2 * s2) ** 1.5;
  const kx = (Math.PI / 180) * N * Math.cos(phi);
  const ky = (Math.PI / 180) * M;
  const toM = ([lon, lat]: Position): Position => [(lon - lon0) * kx, (lat - lat0) * ky];
  const toWgs = ([x, y]: Position): Position => [lon0 + x / kx, lat0 + y / ky];

  const mRings = rings.map((r) => r.map(toM));
  const mOuter = mRings[0]!;

  interface Seg { ux: number; uy: number; len: number }
  const segs: Seg[] = [];
  for (let i = 0; i < mOuter.length; i++) {
    const p1 = mOuter[i]!, p2 = mOuter[(i + 1) % mOuter.length]!;
    let dx = p2[0] - p1[0], dy = p2[1] - p1[1];
    const len = Math.hypot(dx, dy);
    if (len < 1e-3) continue;
    if (dx < 0 || (dx === 0 && dy < 0)) { dx = -dx; dy = -dy; }
    segs.push({ ux: dx / len, uy: dy / len, len });
  }

  let ux = 1, uy = 0;
  if (segs.length) {
    const cosTol = Math.cos((6 * Math.PI) / 180);
    let bestGroupLen = -1, bestSingle = -1;
    for (const s of segs) {
      let groupLen = 0, maxSingle = 0, sx = 0, sy = 0;
      for (const o of segs) {
        const dot = s.ux * o.ux + s.uy * o.uy;
        if (Math.abs(dot) >= cosTol) {
          groupLen += o.len;
          if (o.len > maxSingle) maxSingle = o.len;
          const sign = dot >= 0 ? 1 : -1;
          sx += sign * o.ux * o.len;
          sy += sign * o.uy * o.len;
        }
      }
      if (groupLen > bestGroupLen + 1e-6 || (Math.abs(groupLen - bestGroupLen) <= 1e-6 && maxSingle > bestSingle)) {
        bestGroupLen = groupLen;
        bestSingle = maxSingle;
        const norm = Math.hypot(sx, sy) || 1;
        ux = sx / norm;
        uy = sy / norm;
        if (ux < 0 || (ux === 0 && uy < 0)) { ux = -ux; uy = -uy; }
      }
    }
  }

  const vx = -uy, vy = ux;
  const uvRings = mRings.map((r) => r.map(([x, y]): Position => [x * ux + y * uy, x * vx + y * vy]));
  const uvOuter = uvRings[0]!;
  let uMin = Infinity, uMax = -Infinity, vMin = Infinity, vMax = -Infinity;
  for (const [u, v] of uvOuter) {
    if (u < uMin) uMin = u;
    if (u > uMax) uMax = u;
    if (v < vMin) vMin = v;
    if (v > vMax) vMax = v;
  }
  const uSpan = Math.max(1, uMax - uMin);
  const vSpan = Math.max(1, vMax - vMin);
  const [uCent, vCent] = centroid([[uvOuter]]);

  const intervalsAt = (coordIdx: 0 | 1, level: number): [number, number][] => {
    const other = (1 - coordIdx) as 0 | 1;
    const hits: number[] = [];
    for (const r of uvRings) {
      for (let i = 0, j = r.length - 1; i < r.length; j = i++) {
        const p1 = r[i]!, p2 = r[j]!;
        const c1 = p1[coordIdx], c2 = p2[coordIdx];
        if ((c1 > level) !== (c2 > level)) {
          hits.push(p1[other] + ((p2[other] - p1[other]) * (level - c1)) / (c2 - c1));
        }
      }
    }
    hits.sort((a, b) => a - b);
    const out: [number, number][] = [];
    for (let k = 0; k + 1 < hits.length; k += 2) {
      if (hits[k + 1]! > hits[k]!) out.push([hits[k]!, hits[k + 1]!]);
    }
    return out;
  };

  const vLevels: number[] = [];
  for (let k = 0; k < 15; k++) vLevels.push(vMin + ((k + 0.5) / 15) * vSpan);
  if (vCent > vMin && vCent < vMax) vLevels.push(vCent);

  let best = { uA: uCent - uSpan * 0.35, uB: uCent + uSpan * 0.35, v: vCent, widthM: vSpan * 0.5, lengthM: uSpan * 0.7, score: -1 };
  for (const v of vLevels) {
    for (const [uA, uB] of intervalsAt(1, v)) {
      const len = uB - uA;
      if (len < 1) continue;
      const uMid = (uA + uB) / 2;
      const vInts = intervalsAt(0, uMid);
      const hitV = vInts.find(([vLo, vHi]) => vLo <= v + 1e-4 && v <= vHi + 1e-4);
      const clearance = hitV ? Math.max(1, 2 * Math.min(v - hitV[0], hitV[1] - v)) : vSpan * 0.25;
      const centrality = 1 - 0.25 * (Math.abs(v - vCent) / vSpan);
      const widthFactor = Math.min(1, clearance / Math.max(6, 0.28 * vSpan));
      const score = len * widthFactor * centrality;
      if (score > best.score) best = { uA, uB, v, widthM: clearance, lengthM: len, score };
    }
  }

  const fromUv = (u: number, v: number): Position => toWgs([u * ux + v * vx, u * uy + v * vy]);
  const uMid = (best.uA + best.uB) / 2;
  return {
    center: fromUv(uMid, best.v),
    chord: [fromUv(best.uA, best.v), fromUv(best.uB, best.v)],
    widthM: best.widthM,
    lengthM: best.lengthM,
  };
}
