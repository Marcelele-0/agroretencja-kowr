"""Map layers of the decision card (GeoJSON, WGS84), one builder per layer in ``MAP_LAYERS``.

Three views of the UI (blueprint 4.3):
* "Gdzie ucieka woda"  -> ``zones`` (water state -5..+5 per terrain cell), ``flows`` (D8 arrows),
  ``hydro_network`` (BDOT10k ditches and watercourses),
* "Wilgotność"         -> ``ndmi`` (Sentinel-2 classes),
* "Plan działań"       -> ``plan`` (pins of retention measures).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np
from affine import Affine
from rasterio.features import shapes
from rasterio.warp import transform_geom

from hydro_core.registry import Registry
from hydro_core.schemas import RetentionRecommendation
from hydro_core.topography import TerrainAnalysis, twi_relative

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


@MAP_LAYERS.register("zones")
def _zones(ctx: MapContext) -> dict[str, Any] | None:
    t = ctx.terrain
    if t is None:
        return None
    z = twi_relative(t)
    state = np.clip(np.rint(ctx.water_state + TWI_TO_STATE * z), -5, 5)
    valid = t.mask & np.isfinite(state)

    def prop(v: int) -> dict[str, Any]:
        label = "strefa wilgotna (zbiera wodę)" if v > ctx.water_state + 0.5 else (
            "strefa sucha (woda ucieka)" if v < ctx.water_state - 0.5 else "typowa dla działki"
        )
        return {"value": v, "label": label}

    return _fc(_polygonize(np.nan_to_num(state).astype(int), valid, t.transform, PUWG1992, prop))


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
