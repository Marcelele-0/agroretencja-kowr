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
