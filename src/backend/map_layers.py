"""Map layers of the decision card (GeoJSON, WGS84), one builder per layer in ``MAP_LAYERS``.

Three views of the UI (blueprint 4.3):
* "Gdzie ucieka woda"  -> ``zones`` (water state -5..+5 per terrain cell), ``flows`` (D8 arrows),
  ``hydro_network`` (BDOT10k ditches and watercourses),
* "Wilgotność"         -> ``ndmi`` (Sentinel-2 classes),
* "Plan działań"       -> ``plan`` (pins of retention measures).
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np
from affine import Affine
from rasterio.enums import Resampling
from rasterio.features import rasterize, shapes
from rasterio.transform import from_origin
from rasterio.warp import reproject, transform_geom
from shapely.geometry import mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from backend.clients.geo import parcel_shape, to_puwg92, to_wgs84
from hydro_core.registry import Registry
from hydro_core.schemas import RetentionRecommendation
from hydro_core.topography import TerrainAnalysis, terrain_wetness_offset

logger = logging.getLogger(__name__)

PUWG1992 = "EPSG:2180"
WGS84 = "EPSG:4326"
TWI_TO_STATE = 1.5  # state points per sigma of relative wetness
MAX_FLOW_SEGMENTS = 400
NDMI_CLASSES = (-1.0, 0.0, 0.1, 0.2, 0.3, 1.0)
NDMI_LABELS = ("bardzo sucho", "sucho", "umiarkowanie", "wilgotno", "bardzo wilgotno")


@dataclass
class MapContext:
    parcel_geojson: dict[str, Any]
    water_state: float
    terrain: TerrainAnalysis | None
    hydro_geojson: dict[str, Any] | None
    recommendations: list[RetentionRecommendation]
    ndmi_grid: np.ndarray | None
    ndmi_transform: tuple[float, ...] | None
    ndmi_crs: str | None


LayerBuilder = Callable[[MapContext], dict[str, Any] | None]
MAP_LAYERS: Registry[LayerBuilder] = Registry("warstwy mapy")


def _fc(features: list[dict[str, Any]]) -> dict[str, Any]:
    return {"type": "FeatureCollection", "features": features}


def _polygonize(classes: np.ndarray, valid: np.ndarray, transform: tuple[float, ...], crs: str, prop: Callable[[int], dict[str, Any]]) -> list[dict[str, Any]]:
    aff = Affine(*transform[:6])
    out = []
    for geom, value in shapes(classes.astype(np.int16), mask=valid, transform=aff):
        out.append({"type": "Feature", "properties": prop(int(value)), "geometry": transform_geom(crs, WGS84, geom)})
    return out


@MAP_LAYERS.register("parcel")
def _parcel(ctx: MapContext) -> dict[str, Any]:
    return _fc([{"type": "Feature", "properties": {"label": "Granica działki (ULDK)"}, "geometry": ctx.parcel_geojson}])


MIN_ZONE_SHARE = 0.008


def _extract_polygonal(geom: BaseGeometry) -> BaseGeometry | None:
    if geom.is_empty:
        return None
    if not geom.is_valid:
        geom = geom.buffer(0)
    if geom.is_empty:
        return None
    if geom.geom_type in ("Polygon", "MultiPolygon"):
        return geom if geom.area > 1e-6 else None
    if geom.geom_type == "GeometryCollection":
        parts = [g for g in geom.geoms if g.geom_type in ("Polygon", "MultiPolygon") and g.area > 1e-6]
        if not parts:
            return None
        merged = unary_union(parts)
        return merged if merged.area > 1e-6 else None
    return None


def zone_base_step(water_state: float) -> int:
    """Zone class of the parcel's own water state (used for gaps and as merge target)."""
    return int(np.clip(np.rint(water_state), -5, 5))


def zone_label(value: int, water_state: float) -> str:
    """Zone label relative to the parcel's own water state."""
    if value > water_state + 0.5:
        return "strefa wilgotna (zbiera wodę)"
    if value < water_state - 0.5:
        return "strefa sucha (woda ucieka)"
    return "typowa dla działki"


def parcel_cells(parcel_2180: BaseGeometry, out_shape: tuple[int, int], transform: Affine) -> np.ndarray:
    """Cells of a grid inside the parcel (cell-centre rule; all-touched for parcels thinner than a cell)."""
    inside = rasterize([(parcel_2180, 1)], out_shape=out_shape, transform=transform, fill=0, dtype="uint8")
    if not inside.any():
        inside = rasterize(
            [(parcel_2180, 1)], out_shape=out_shape, transform=transform, fill=0, dtype="uint8", all_touched=True
        )
    return inside.astype(bool)


def centred_offset(offset: np.ndarray, inside: np.ndarray) -> np.ndarray:
    """Terrain offset minus its mean over the parcel cells (zero mean inside the parcel).

    Zones redistribute the parcel's water state inside the parcel; they must not move it. A raw
    TPI offset is positive over a whole parcel lying in a broad hollow, which would shift every zone
    one class wetter than the parcel's own state.
    """
    vals = offset[inside & np.isfinite(offset)]
    return offset - float(vals.mean()) if vals.size else offset


def class_polygons(
    classes: np.ndarray, valid: np.ndarray, transform: Affine
) -> list[tuple[BaseGeometry, int]]:
    """Polygonize an integer class grid (EPSG:2180) into (polygon, class) pairs."""
    out: list[tuple[BaseGeometry, int]] = []
    if not valid.any():
        return out
    for raw_geom, raw_val in shapes(classes.astype(np.int16), mask=valid, transform=transform):
        poly = _extract_polygonal(shape(raw_geom))
        if poly is not None:
            out.append((poly, int(raw_val)))
    return out


def group_zone_polygons(
    polygons: list[tuple[BaseGeometry, int]], parcel_2180: BaseGeometry
) -> dict[int, BaseGeometry]:
    """Clip class polygons to the parcel and merge them per class value."""
    by_val: dict[int, list[BaseGeometry]] = {}
    for poly, val in polygons:
        clipped = _extract_polygonal(poly.intersection(parcel_2180))
        if clipped is not None:
            by_val.setdefault(val, []).append(clipped)
    return {
        v: merged
        for v, geoms in by_val.items()
        if (merged := _extract_polygonal(unary_union(geoms))) is not None
    }


def zone_collection(
    grouped: dict[int, BaseGeometry],
    parcel_2180: BaseGeometry,
    water_state: float,
    fill_value: int | None = None,
) -> dict[str, Any]:
    """Zones FeatureCollection of one parcel: fill uncovered area, merge tiny zones, label.

    Shared by the per-parcel map layer and the joint multi-parcel endpoint (``backend.zones``).
    ``fill_value``: class of uncovered area (default: the parcel's own state class).
    """
    grouped = dict(grouped)
    parcel_area = max(float(parcel_2180.area), 1e-6)
    base_step = zone_base_step(water_state) if fill_value is None else int(np.clip(fill_value, -5, 5))

    if grouped:
        covered = unary_union(list(grouped.values()))
        missing = _extract_polygonal(parcel_2180.difference(covered))
        if missing is not None and missing.area > 1.0:
            if base_step in grouped:
                merged_missing = _extract_polygonal(unary_union([grouped[base_step], missing]))
                if merged_missing is not None:
                    grouped[base_step] = merged_missing
            else:
                grouped[base_step] = missing
    else:
        grouped = {base_step: parcel_2180}

    if len(grouped) > 1:
        small_vals = [v for v, g in grouped.items() if g.area < MIN_ZONE_SHARE * parcel_area]
        if small_vals and len(small_vals) < len(grouped):
            major_vals = [v for v in grouped if v not in small_vals]
            target_v = base_step if base_step in major_vals else max(major_vals, key=lambda v: grouped[v].area)
            merged_small = _extract_polygonal(unary_union([grouped[target_v], *(grouped.pop(v) for v in small_vals)]))
            if merged_small is not None:
                grouped[target_v] = merged_small

    features: list[dict[str, Any]] = []
    for v in sorted(grouped):
        geom_2180 = grouped[v]
        geom_wgs = _extract_polygonal(to_wgs84(geom_2180)) or to_wgs84(geom_2180)
        features.append({
            "type": "Feature",
            "properties": {
                "value": v,
                "label": zone_label(v, water_state),
                "area_pct": round(100.0 * geom_2180.area / parcel_area, 1),
                "area_ha": round(geom_2180.area / 10_000.0, 2),
            },
            "geometry": mapping(geom_wgs),
        })
    return _fc(features)


@MAP_LAYERS.register("zones")
def _zones(ctx: MapContext) -> dict[str, Any] | None:
    parcel_2180 = to_puwg92(parcel_shape(ctx.parcel_geojson))
    if not parcel_2180.is_valid:
        parcel_2180 = parcel_2180.buffer(0)
    parcel_area = max(float(parcel_2180.area), 1e-6)
    base_step = zone_base_step(ctx.water_state)

    t = ctx.terrain
    if t is None or int(t.mask.sum()) < 2:
        return _fc([{
            "type": "Feature",
            "properties": {
                "value": base_step,
                "label": zone_label(base_step, ctx.water_state),
                "area_pct": 100.0,
                "area_ha": round(parcel_area / 10_000.0, 2),
            },
            "geometry": ctx.parcel_geojson,
        }])

    offset = terrain_wetness_offset(t)
    off = np.where(np.isfinite(offset), offset, 0.0)
    aff = t.transform[:6]

    if t.resolution_m > 10.0:
        minx, miny, maxx, maxy = parcel_2180.bounds
        span = max(maxx - minx, maxy - miny)
        fine_res = 10.0 if span <= 2500.0 else 20.0
        left = math.floor(minx / fine_res) * fine_res - fine_res
        bottom = math.floor(miny / fine_res) * fine_res - fine_res
        right = math.ceil(maxx / fine_res) * fine_res + fine_res
        top = math.ceil(maxy / fine_res) * fine_res + fine_res
        width = max(2, round((right - left) / fine_res))
        height = max(2, round((top - bottom) / fine_res))
        dst_aff = from_origin(left, top, fine_res, fine_res)
        dst = np.full((height, width), np.nan, dtype=np.float32)
        # Bilinear resampling is linear: resample the offset, then add the (constant) parcel level.
        reproject(
            source=off.astype(np.float32),
            destination=dst,
            src_transform=Affine(*aff),
            src_crs=PUWG1992,
            dst_transform=dst_aff,
            dst_crs=PUWG1992,
            resampling=Resampling.bilinear,
            dst_nodata=np.nan,
            ERROR_THRESHOLD=0,
        )
        off_fine = dst.astype(np.float64)
        aff_fine = dst_aff
    else:
        off_fine = off
        aff_fine = Affine(*aff)

    # Zero-mean offset over the classified parcel cells: the parcel mean stays its water state.
    off_fine = centred_offset(off_fine, parcel_cells(parcel_2180, off_fine.shape, aff_fine))
    cont_fine = np.clip(ctx.water_state + off_fine, -5.0, 5.0)
    valid_fine = np.isfinite(cont_fine)

    classes = np.clip(np.rint(np.nan_to_num(cont_fine, nan=float(ctx.water_state))), -5, 5).astype(np.int16)
    grouped = group_zone_polygons(class_polygons(classes, valid_fine, aff_fine), parcel_2180)
    return zone_collection(grouped, parcel_2180, ctx.water_state)


@MAP_LAYERS.register("flows")
def _flows(ctx: MapContext) -> dict[str, Any] | None:
    t = ctx.terrain
    if t is None:
        return None
    acc = np.where(t.mask, t.accumulation_cells, 0.0)
    if not acc.any():
        return None
    threshold = np.percentile(acc[t.mask], 90)
    rows, cols = np.nonzero((acc >= threshold) & t.mask)
    order = np.argsort(-acc[rows, cols])[:MAX_FLOW_SEGMENTS]
    w = t.mask.shape[1]
    feats = []
    for k in order:
        r, c = int(rows[k]), int(cols[k])
        j = int(t.flow_to[r * w + c])
        if j < 0:
            continue
        x0, y0 = t.cell_center(r, c)
        x1, y1 = t.cell_center(j // w, j % w)
        geom = transform_geom(PUWG1992, WGS84, {"type": "LineString", "coordinates": [(x0, y0), (x1, y1)]})
        feats.append({"type": "Feature", "properties": {"accumulation_m2": round(float(acc[r, c]) * t.resolution_m**2, 0)}, "geometry": geom})
    return _fc(feats)


@MAP_LAYERS.register("hydro_network")
def _hydro(ctx: MapContext) -> dict[str, Any] | None:
    return ctx.hydro_geojson


@MAP_LAYERS.register("plan")
def _plan(ctx: MapContext) -> dict[str, Any] | None:
    feats = []
    for rec in ctx.recommendations:
        hint = rec.map_hint or {}
        if hint.get("type") == "point" and "x" in hint:
            geom = transform_geom(hint.get("crs", PUWG1992), WGS84, {"type": "Point", "coordinates": (hint["x"], hint["y"])})
            feats.append({
                "type": "Feature",
                "properties": {"measure_id": rec.id, "title": rec.title, "label": hint.get("label"), "rank": rec.priority_rank, "requires_permit": rec.requires_permit},
                "geometry": geom,
            })
    return _fc(feats) if feats else None


@MAP_LAYERS.register("ndmi")
def _ndmi(ctx: MapContext) -> dict[str, Any] | None:
    if ctx.ndmi_grid is None or ctx.ndmi_transform is None or not ctx.ndmi_crs:
        return None
    grid = ctx.ndmi_grid
    valid = np.isfinite(grid)
    if not valid.any():
        return None
    classes = np.clip(np.digitize(np.nan_to_num(grid), NDMI_CLASSES[1:-1]), 0, len(NDMI_LABELS) - 1)
    return _fc(_polygonize(classes, valid, ctx.ndmi_transform, ctx.ndmi_crs, lambda v: {"class": v, "label": NDMI_LABELS[v]}))


def build_map_layers(ctx: MapContext) -> dict[str, Any]:
    layers: dict[str, Any] = {}
    for name, builder in MAP_LAYERS.items():
        try:
            layer = builder(ctx)
        except Exception:
            logger.warning("Map layer %s failed", name, exc_info=True)
            layer = None
        if layer is not None:
            layers[name] = layer
    return layers
