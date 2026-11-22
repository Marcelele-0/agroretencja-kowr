import maplibregl, { type GeoJSONSource, type Map as MlMap } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useEffect, useMemo, useRef, type ReactNode } from "react";
import { Button, cx } from "../ds";
import { parcelLabelPose, type AreaGeometry, type BBox, type LabelPose, type Position } from "../lib/geo";

const ORIGIN = typeof window !== "undefined" ? window.location.origin : "";
const WMS = "SERVICE=WMS&REQUEST=GetMap&VERSION=1.3.0&STYLES=&CRS=EPSG:3857&BBOX={bbox-epsg-3857}";
/** GUGiK orthophoto WMTS (z0-z19) via disk cache proxy: ~85 ms on miss, <5 ms on hit. */
export const ORTHO_WMTS = `${ORIGIN}/tiles/ortho-wmts/{z}/{x}/{y}.jpg`;
/** The WMS is slower but sharper close up; it draws over the WMTS from zoom 16 via disk cache. */
export const ORTHO_WMS = `${ORIGIN}/tiles/ortho-wms?${WMS}&WIDTH=512&HEIGHT=512&LAYERS=Raster&FORMAT=image/jpeg`;
// KIEG draws parcels only below ~1.8 m/px and returns an empty image above. 1024 px per 512 px tile gives 1.2 m/px from zoom 15.
export const KIEG_TILES = `${ORIGIN}/tiles/kieg?${WMS}&WIDTH=1024&HEIGHT=1024&LAYERS=dzialki,numery_dzialek&FORMAT=image/png&TRANSPARENT=true`;
const POLAND: BBox = [14.07, 49.0, 24.15, 54.84];
const EMPTY = { type: "FeatureCollection" as const, features: [] };

export interface MapZone { geometry: unknown; value: number; areaPct?: number; areaHa?: number }
/** A selected parcel. `value` (−5…+5) colours it by water state; without it the parcel is field yellow. `active` gets the hatch and a thicker line. */
export interface MapParcel { id: string; geometry: AreaGeometry; value?: number | null; active?: boolean; hasZones?: boolean; cropLabel?: string | null }
interface Pad { top: number; bottom: number; left: number; right: number }
interface LabeledParcel { id: string; label: string; active: boolean; pose: LabelPose }

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(`--${name}`).trim() || "#f4d03f";
}
function hatch(color: string): ImageData {
  const n = 12, c = document.createElement("canvas");
  c.width = n; c.height = n;
  const g = c.getContext("2d")!;
  g.strokeStyle = color; g.globalAlpha = 0.75; g.lineWidth = 1.4;
  for (const o of [-n, 0, n]) { g.beginPath(); g.moveTo(o, n); g.lineTo(o + n, 0); g.stroke(); }
  return g.getImageData(0, 0, n, n);
}
const NO_VALUE = -99;
/** `zonesShown` = the zones source has features; without them no parcel may drop its flat fill (hasZones hides parcel-fill). */
const parcelFc = (ps: MapParcel[], zonesShown: boolean) => ({ type: "FeatureCollection" as const, features: ps.map((p) => ({ type: "Feature" as const, properties: { id: p.id, value: p.value ?? NO_VALUE, active: p.active ? 1 : 0, hasZones: zonesShown && p.hasZones ? 1 : 0 }, geometry: p.geometry as never })) });

export function ParcelMap({ parcels, bbox, flyTo, onPick, popover, popoverAt, zones, padding, overlay, legend, className }: {
  parcels?: MapParcel[] | null; bbox?: BBox | null; flyTo?: { center: Position; zoom?: number; seq: number } | null; legend?: ReactNode; onPick?: ((lon: number, lat: number) => void) | null; popover?: ReactNode; popoverAt?: Position | null;
  zones?: MapZone[] | null; padding?: Partial<Pad>; overlay?: ReactNode; className?: string;
}) {
  const box = useRef<HTMLDivElement>(null);
  const popRef = useRef<HTMLDivElement>(null);
  const tagRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const mapRef = useRef<MlMap | null>(null);
  const readyRef = useRef(false);
  const pickRef = useRef(onPick);
  pickRef.current = onPick;
  const fitted = useRef(false);
  const data = useRef({ parcels, zones });
  data.current = { parcels, zones };

  const labeled = useMemo<LabeledParcel[]>(
    () =>
      (parcels ?? []).flatMap((p) => {
        const lbl = p.cropLabel?.trim();
        return lbl ? [{ id: p.id, label: lbl, active: !!p.active, pose: parcelLabelPose(p.geometry) }] : [];
      }),
    [parcels],
  );
  const labeledRef = useRef(labeled);
  labeledRef.current = labeled;

  const updateTags = () => {
    const map = mapRef.current;
    if (!map) return;
    for (const item of labeledRef.current) {
      const el = tagRefs.current[item.id];
      if (!el) continue;
      const s0 = map.project(item.pose.chord[0]);
      const s1 = map.project(item.pose.chord[1]);
      const sc = map.project(item.pose.center);
      const dx = s1.x - s0.x;
      const dy = s1.y - s0.y;
      const lenPx = Math.hypot(dx, dy);
      const widthPx = lenPx * (item.pose.widthM / Math.max(1, item.pose.lengthM));
      if (lenPx < 34 || widthPx < 7) {
        el.style.display = "none";
        continue;
      }
      let angle = (Math.atan2(dy, dx) * 180) / Math.PI;
      if (angle > 90) angle -= 180;
      if (angle <= -90) angle += 180;
      const scale = Math.max(0.74, Math.min(1, Math.min(lenPx / 105, widthPx / 21)));
      const maxW = Math.max(34, Math.floor((lenPx * 0.9) / scale));
      el.style.display = "inline-flex";
      el.style.maxWidth = `${maxW}px`;
      el.style.transform = `translate(${sc.x.toFixed(1)}px, ${sc.y.toFixed(1)}px) translate(-50%, -50%) rotate(${angle.toFixed(2)}deg) scale(${scale.toFixed(3)})`;
    }
  };

  const sync = () => {
    const map = mapRef.current;
    if (!map || !readyRef.current) return;
    const d = data.current;
    (map.getSource("parcel") as GeoJSONSource).setData(parcelFc(d.parcels ?? [], !!d.zones?.length));
    (map.getSource("zones") as GeoJSONSource).setData({ type: "FeatureCollection", features: (d.zones ?? []).map((z) => ({ type: "Feature", properties: { value: z.value }, geometry: z.geometry as never })) });
    // Feature count of the zones source, for diagnostics and browser tests.
    if (box.current) box.current.dataset.zones = String(d.zones?.length ?? 0);
    updateTags();
  };

  useEffect(() => {
    const map = new maplibregl.Map({
      container: box.current!,
      style: {
        version: 8,
        sources: {
          wmts: { type: "raster", tiles: [ORTHO_WMTS], tileSize: 256, minzoom: 0, maxzoom: 19 },
          wms: { type: "raster", tiles: [ORTHO_WMS], tileSize: 512, minzoom: 15, maxzoom: 19 },
          kieg: { type: "raster", tiles: [KIEG_TILES], tileSize: 512, minzoom: 15, maxzoom: 20 },
          parcel: { type: "geojson", data: EMPTY },
          zones: { type: "geojson", data: EMPTY },
        },
        layers: [
          { id: "bg", type: "background", paint: { "background-color": cssVar("night") } },
          { id: "wmts", type: "raster", source: "wmts" },
          { id: "wms", type: "raster", source: "wms", minzoom: 16 },
          // KIEG draws parcel lines in colour; brightness-min 1 turns them into thin white lines, as in the design system.
          { id: "kieg", type: "raster", source: "kieg", minzoom: 15, paint: { "raster-opacity": 0.85, "raster-brightness-min": 1 } },
        ],
      },
      bounds: bbox ? [[bbox[0], bbox[1]], [bbox[2], bbox[3]]] : POLAND,
      fitBoundsOptions: { padding: 40, maxZoom: 17.2 },
      attributionControl: false,
      dragRotate: false,
      pitchWithRotate: false,
      maxZoom: 19.5,
    });
    if (bbox) fitted.current = true;
    map.touchZoomRotate.disableRotation();
    // "style.load" fires when the style is parsed, without waiting for the tiles of the first view.
    map.once("style.load", () => {
      const field = cssVar("field");
      map.addImage("hatch", hatch(field));
      const steps = ["dry-5", "dry-4", "dry-3", "dry-2", "dry-1", "optimum", "wet-1", "wet-2", "wet-3", "wet-4", "wet-5"].map(cssVar);
      const byStep = ["step", ["round", ["get", "value"]], steps[0]!, -4, steps[1]!, -3, steps[2]!, -2, steps[3]!, -1, steps[4]!, 0, steps[5]!, 1, steps[6]!, 2, steps[7]!, 3, steps[8]!, 4, steps[9]!, 5, steps[10]!] as never;
      const noValue = ["==", ["get", "value"], NO_VALUE] as never;
      map.addLayer({ id: "parcel-fill", type: "fill", source: "parcel", filter: ["!=", ["get", "hasZones"], 1], paint: { "fill-color": ["case", noValue, field, byStep] as never, "fill-opacity": ["case", noValue, 0.3, 0.56] as never } });
      map.addLayer({ id: "parcel-hatch", type: "fill", source: "parcel", filter: ["all", ["==", ["get", "active"], 1], ["!=", ["get", "hasZones"], 1]], paint: { "fill-pattern": "hatch" } });
      map.addLayer({ id: "zones", type: "fill", source: "zones", paint: { "fill-color": byStep, "fill-opacity": 0.56 } });
      map.addLayer({ id: "parcel-line", type: "line", source: "parcel", paint: { "line-color": field, "line-width": ["case", ["==", ["get", "active"], 1], 3, 1.8] as never }, layout: { "line-join": "round" } });
      readyRef.current = true;
      sync();
    });
    map.on("click", (e) => pickRef.current?.(e.lngLat.lng, e.lngLat.lat));
    map.on("move", updateTags);
    map.on("resize", updateTags);
    // Backdrop blur of the glass overlays is expensive while the map moves: switch it off for the duration.
    map.on("movestart", () => document.documentElement.classList.add("rs-moving"));
    map.on("moveend", () => document.documentElement.classList.remove("rs-moving"));
    map.addControl(new maplibregl.ScaleControl({ maxWidth: 90, unit: "metric" }), "bottom-left");
    mapRef.current = map;
    return () => { readyRef.current = false; map.remove(); mapRef.current = null; document.documentElement.classList.remove("rs-moving"); };
    // The map is created once; props reach it through the effects below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(sync, [parcels, zones]);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { updateTags(); }, [labeled]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !bbox) return;
    map.fitBounds([[bbox[0], bbox[1]], [bbox[2], bbox[3]]], { padding: { top: 80, bottom: 80, left: 40, right: 40, ...padding }, maxZoom: 17.2, duration: fitted.current ? 900 : 0 });
    fitted.current = true;
  }, [bbox?.join(","), padding?.top, padding?.bottom, padding?.left, padding?.right]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !flyTo) return;
    map.flyTo({ center: flyTo.center, zoom: flyTo.zoom ?? 15.2, duration: 900 });
  }, [flyTo?.seq]);

  // The popover follows the map through a direct style write, not a React render per frame.
  useEffect(() => {
    const map = mapRef.current, el = popRef.current;
    if (!map || !el || !popoverAt) return;
    const upd = () => { const p = map.project(popoverAt); el.style.transform = `translate(${p.x}px, ${p.y}px)`; };
    upd();
    map.on("move", upd);
    return () => { map.off("move", upd); };
  }, [popoverAt?.[0], popoverAt?.[1], !!popover]);

  const locate = () => navigator.geolocation?.getCurrentPosition((p) => mapRef.current?.flyTo({ center: [p.coords.longitude, p.coords.latitude], zoom: 15.5 }));

  return (
    <div className={cx("rs-map", onPick && "rs-map--pick", className)}>
      <div ref={box} className="rs-map__canvas" role="application" aria-label="Mapa działek na ortofotomapie GUGiK" />
      {labeled.map((t) => (
        <div
          key={t.id}
          ref={(el) => {
            if (el) tagRefs.current[t.id] = el;
            else delete tagRefs.current[t.id];
          }}
          className={cx("rs-map__tag", t.active && "is-on")}
          aria-hidden="true"
        >
          <span>{t.label}</span>
        </div>
      ))}
      {popover && popoverAt ? <div ref={popRef} className="rs-map__anchor"><div className="rs-map__pop">{popover}</div></div> : null}
      {overlay ? <div className="rs-map__overlay">{overlay}</div> : null}
      {legend ? <div className="rs-map__legend">{legend}</div> : null}
      <div className="rs-map__zoom ar-zoom">
        <Button variant="icon" icon="plus" label="Przybliż" onClick={() => mapRef.current?.zoomIn()} />
        <Button variant="icon" icon="minus" label="Oddal" onClick={() => mapRef.current?.zoomOut()} />
      </div>
      <div className="rs-map__locate"><Button variant="icon" icon="locate" label="Pokaż moją lokalizację" onClick={locate} /></div>
      <span className="rs-map__credit">© GUGiK</span>
    </div>
  );
}
