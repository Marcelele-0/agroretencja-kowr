"""Joint intra-parcel wetness zones for a group of parcels (POST /api/zones).

The per-parcel map layer (``map_layers.zones``) classifies every parcel alone: the base water
state and the DEM grid (source, origin, resampling) differ per parcel, so the classes cannot line
up at shared borders. Here all requested parcels share:

* one DEM source (GUGiK 1 m only when cached for every parcel, else Copernicus GLO-30 for all),
* one DEM mosaic on a lattice aligned to a global origin -> one TPI wetness offset,
* one class grid (global lattice, 10/20 m or the terrain cell) on which each parcel's level is
  rasterized and blended only near shared borders (Gaussian, within groups of touching parcels).

Zones redistribute a parcel's water state inside the parcel; they must not move it. The level of a
parcel is its water state minus the mean terrain offset over its cells, so the area-weighted mean of
the continuous field over every parcel equals its water state (a residual left by the border blend
is corrected per parcel).

``rint`` classification and polygonization run once per group of touching parcels; the polygons
are clipped per parcel and finished with the same gap-fill / small-zone logic as the map layer
(``map_layers.zone_collection``). A single parcel gives the map-layer result.
"""

from __future__ import annotations

import asyncio
import logging
import math
from collections import OrderedDict
from dataclasses import dataclass
from time import perf_counter
from typing import Any

import numpy as np
import shapely
from affine import Affine
from rasterio.enums import Resampling
from rasterio.features import rasterize
from rasterio.transform import from_origin
from rasterio.warp import reproject
from shapely import STRtree
from shapely.geometry.base import BaseGeometry

from backend.clients.geo import parcel_shape, to_puwg92
from backend.clients.nmt import DemRaster
from backend.clients.pipeline import ParcelNotFoundError
from backend.map_layers import class_polygons, group_zone_polygons, zone_collection
from hydro_core.schemas import ZonesRequest, ZonesResponse
from hydro_core.topography import _block_mean, gaussian_smooth, tpi_wetness_offset

logger = logging.getLogger(__name__)

PUWG1992 = "EPSG:2180"
ADJACENCY_TOL_M = 1.0  # parcels closer than this share a border (ULDK vertices are not exact)
BLEND_SIGMA_M = 25.0  # Gaussian blend of the base water state across shared borders
MAX_TERRAIN_CELLS = 300  # per side, as analyse_terrain: sets the terrain cell of one DEM
MAX_MOSAIC_CELLS = 400  # per side of the group mosaic (keeps the TPI well under 0.5 s)
FINE_CELL_M = 10.0
FINE_CELL_LARGE_M = 20.0
LARGE_SPAN_M = 2500.0
POLY_CELL_M = 2.5  # polygonization cell of the upsampled class field
MAX_POLY_CELLS = 2_500_000
SMOOTH_TOL_M = 3.0  # coverage simplification of the shared class polygons
RESULT_CACHE_SIZE = 256
ZONES_VERSION = 2  # in the result cache key; bump when the zone field changes
MEAN_TOL = 0.05  # max deviation of a parcel's field mean from its water state
MEAN_BLEND_PASSES = 3  # blended (continuous) corrections before the exact per-parcel shift
NO_DEM_SOURCE = "brak modelu terenu (strefy = stan wodny działki)"


@dataclass
class ZoneParcelInput:
    """One parcel of the joint computation (geometry in WGS84 GeoJSON, as from ULDK)."""

    key: str
    geojson: dict[str, Any]
    water_state: float
    crop: str = ""  # frontend crop code; "" = not given (all such parcels count as one crop)


@dataclass
class JointZones:
    zones: dict[str, dict[str, Any]]
    grid_m: float
    dem_source: str


@dataclass
class _Grid:
    """North-up EPSG:2180 grid: top-left corner and cell size."""

    left: float
    top: float
    cell: float
    height: int
    width: int

    @property
    def transform(self) -> Affine:
        return from_origin(self.left, self.top, self.cell, self.cell)


def _is_int(v: float) -> bool:
    return abs(v - round(v)) < 1e-6


def _dem_extent(d: DemRaster) -> tuple[float, float, float, float]:
    a, _, x0, _, e, y0 = d.transform
    h, w = d.elevation.shape
    return x0, y0 + e * h, x0 + a * w, y0


def _terrain_cell(d: DemRaster) -> float:
    """Cell size analyse_terrain would use for this DEM alone."""
    return float(d.resolution_m) * math.ceil(max(d.elevation.shape) / MAX_TERRAIN_CELLS)


def _to_lattice(d: DemRaster, grid: _Grid) -> np.ndarray:
    """DEM resampled onto the mosaic grid (NaN outside the DEM).

    Aligned integer factors use the block mean of analyse_terrain (exact for one parcel);
    other cases use GDAL average/bilinear resampling.
    """
    res = float(d.resolution_m)
    x0, y0 = d.transform[2], d.transform[5]
    k, ox, oy = grid.cell / res, (x0 - grid.left) / res, (grid.top - y0) / res
    out = np.full((grid.height, grid.width), np.nan, dtype=np.float64)
    if _is_int(k) and _is_int(ox) and _is_int(oy) and ox > -0.5 and oy > -0.5:
        k, ox, oy = round(k), round(ox), round(oy)
        src = np.asarray(d.elevation, dtype=np.float64)
        px, py = ox % k, oy % k
        if px or py:
            src = np.pad(src, ((py, 0), (px, 0)), constant_values=np.nan)
        blocks = _block_mean(src, k)
        r0, c0 = oy // k, ox // k
        h = max(0, min(blocks.shape[0], grid.height - r0))
        w = max(0, min(blocks.shape[1], grid.width - c0))
        out[r0 : r0 + h, c0 : c0 + w] = blocks[:h, :w]
        return out
    dst = np.full((grid.height, grid.width), np.nan, dtype=np.float32)
    reproject(
        source=np.asarray(d.elevation, dtype=np.float32),
        destination=dst,
        src_transform=Affine(*d.transform),
        src_crs=PUWG1992,
        src_nodata=np.nan,
        dst_transform=grid.transform,
        dst_crs=PUWG1992,
        dst_nodata=np.nan,
        resampling=Resampling.average if grid.cell > res * 1.001 else Resampling.bilinear,
        ERROR_THRESHOLD=0,
    )
    return dst.astype(np.float64)


def _mosaic(dems: list[DemRaster]) -> tuple[np.ndarray, _Grid]:
    """Mean of all DEMs on one lattice aligned to the global origin (multiples of the cell)."""
    ext = [_dem_extent(d) for d in dems]
    minx, miny = min(e[0] for e in ext), min(e[1] for e in ext)
    maxx, maxy = max(e[2] for e in ext), max(e[3] for e in ext)
    cell = max(_terrain_cell(d) for d in dems)
    span = max(maxx - minx, maxy - miny)
    if span / cell > MAX_MOSAIC_CELLS:
        cell = float(math.ceil(span / MAX_MOSAIC_CELLS))
    left, top = math.floor(minx / cell) * cell, math.ceil(maxy / cell) * cell
    right, bottom = math.ceil(maxx / cell) * cell, math.floor(miny / cell) * cell
    grid = _Grid(left, top, cell, max(2, round((top - bottom) / cell)), max(2, round((right - left) / cell)))
    acc = np.zeros((grid.height, grid.width))
    cnt = np.zeros((grid.height, grid.width))
    for d in dems:
        part = _to_lattice(d, grid)
        ok = np.isfinite(part)
        acc[ok] += part[ok]
        cnt[ok] += 1.0
    elev = np.where(cnt > 0, acc / np.maximum(cnt, 1.0), np.nan)
    return elev, grid


def _sample(src: np.ndarray, src_grid: _Grid, dst: _Grid) -> np.ndarray:
    """Values of ``src`` on ``dst`` (NaN outside): slicing on the same lattice, else bilinear."""
    if _is_int(dst.cell / src_grid.cell) and round(dst.cell / src_grid.cell) == 1:
        r0, c0 = (src_grid.top - dst.top) / dst.cell, (dst.left - src_grid.left) / dst.cell
        if _is_int(r0) and _is_int(c0):
            r0, c0 = round(r0), round(c0)
            out = np.full((dst.height, dst.width), np.nan)
            rs, cs = max(0, r0), max(0, c0)
            re, ce = min(src.shape[0], r0 + dst.height), min(src.shape[1], c0 + dst.width)
            if re > rs and ce > cs:
                out[rs - r0 : re - r0, cs - c0 : ce - c0] = src[rs:re, cs:ce]
            return out
    out32 = np.full((dst.height, dst.width), np.nan, dtype=np.float32)
    reproject(
        source=np.where(np.isfinite(src), src, 0.0).astype(np.float32),
        destination=out32,
        src_transform=src_grid.transform,
        src_crs=PUWG1992,
        dst_transform=dst.transform,
        dst_crs=PUWG1992,
        resampling=Resampling.bilinear,
        dst_nodata=np.nan,
        ERROR_THRESHOLD=0,
    )
    return out32.astype(np.float64)


def _touching_groups(geoms: list[BaseGeometry], crops: list[str]) -> list[list[int]]:
    """Indices of parcels with the same crop grouped by shared borders (union-find).

    The base state blends only inside a group: a border between different crops keeps a hard
    class change (each parcel keeps its own state up to the border); the terrain offset stays shared.
    """
    parent = list(range(len(geoms)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(geoms)):
        for j in range(i + 1, len(geoms)):
            if crops[i] == crops[j] and geoms[i].distance(geoms[j]) <= ADJACENCY_TOL_M:
                parent[find(i)] = find(j)
    groups: dict[int, list[int]] = {}
    for i in range(len(geoms)):
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())


def _base_offsets(geoms: list[BaseGeometry], values: list[float], grid: _Grid) -> np.ndarray:
    """Rasterized parcel values (cell centre rule; all-touched for parcels thinner than a cell)."""
    return _values_on_ids(_parcel_ids(geoms, grid), values)


def _values_on_ids(ids: np.ndarray, values: list[float]) -> np.ndarray:
    """Per-parcel values on a parcel id grid (0 = no parcel -> NaN)."""
    lut = np.array([np.nan, *values], dtype=np.float64)
    return lut[ids]


def _parcel_ids(geoms: list[BaseGeometry], grid: _Grid) -> np.ndarray:
    """Parcel index + 1 per cell (cell centre rule; all-touched for parcels thinner than a cell)."""
    ids = rasterize(
        [(g, i + 1) for i, g in enumerate(geoms)],
        out_shape=(grid.height, grid.width),
        transform=grid.transform,
        fill=0,
        dtype="int32",
    )
    present = set(np.unique(ids).tolist())
    for i, g in enumerate(geoms):
        if i + 1 not in present:
            touched = rasterize(
                [(g, 1)], out_shape=ids.shape, transform=grid.transform, fill=0, dtype="uint8", all_touched=True
            )
            ids[touched.astype(bool)] = i + 1
    return ids


def _parcel_means(field: np.ndarray, cells: list[np.ndarray]) -> list[float]:
    """Mean of ``field`` over each parcel's cells (NaN when a parcel has no finite cell)."""
    out = []
    for c in cells:
        vals = field[c & np.isfinite(field)]
        out.append(float(vals.mean()) if vals.size else math.nan)
    return out


def compute_joint_zones(parcels: list[ZoneParcelInput], dems: list[DemRaster]) -> JointZones:
    """Zones of all parcels on one shared grid (CPU only; call it from a worker thread).

    ``dems``: rasters of ONE source covering the group (per-parcel or tiles); empty = flat.
    """
    geoms: list[BaseGeometry] = []
    for p in parcels:
        g = to_puwg92(parcel_shape(p.geojson))
        geoms.append(g if g.is_valid else g.buffer(0))
    if not parcels:
        return JointZones({}, FINE_CELL_M, NO_DEM_SOURCE)

    offset: np.ndarray | None = None
    dem_grid: _Grid | None = None
    if dems:
        elev, dem_grid = _mosaic(dems)
        offset = tpi_wetness_offset(elev, dem_grid.cell)

    ux0 = min(g.bounds[0] for g in geoms)
    uy0 = min(g.bounds[1] for g in geoms)
    ux1 = max(g.bounds[2] for g in geoms)
    uy1 = max(g.bounds[3] for g in geoms)
    if dem_grid is not None and dem_grid.cell <= FINE_CELL_M:
        cell = dem_grid.cell  # fine terrain model: classify on the terrain cells
    else:
        cell = FINE_CELL_M if max(ux1 - ux0, uy1 - uy0) <= LARGE_SPAN_M else FINE_CELL_LARGE_M
    margin = math.ceil(3.0 * BLEND_SIGMA_M / cell) + 2
    left = math.floor(ux0 / cell) * cell - margin * cell
    top = math.ceil(uy1 / cell) * cell + margin * cell
    right = math.ceil(ux1 / cell) * cell + margin * cell
    bottom = math.floor(uy0 / cell) * cell - margin * cell
    grid = _Grid(left, top, cell, round((top - bottom) / cell), round((right - left) / cell))
    if offset is not None and dem_grid is not None:
        off = _sample(offset, dem_grid, grid)
        off = np.where(np.isfinite(off), off, 0.0)
    else:
        off = np.zeros((grid.height, grid.width))

    zones: dict[str, dict[str, Any]] = {}
    for members in _touching_groups(geoms, [p.crop for p in parcels]):
        gx0 = min(geoms[i].bounds[0] for i in members)
        gy0 = min(geoms[i].bounds[1] for i in members)
        gx1 = max(geoms[i].bounds[2] for i in members)
        gy1 = max(geoms[i].bounds[3] for i in members)
        c0 = max(0, math.floor((gx0 - grid.left) / cell) - margin)
        c1 = min(grid.width, math.ceil((gx1 - grid.left) / cell) + margin)
        r0 = max(0, math.floor((grid.top - gy1) / cell) - margin)
        r1 = min(grid.height, math.ceil((grid.top - gy0) / cell) + margin)
        win = _Grid(grid.left + c0 * cell, grid.top - r0 * cell, cell, r1 - r0, c1 - c0)
        member_geoms = [geoms[i] for i in members]
        states = [parcels[i].water_state for i in members]
        sub_off = off[r0:r1, c0:c1]
        ids = _parcel_ids(member_geoms, win)
        cells = [ids == k + 1 for k in range(len(members))]
        # Level = state minus the parcel's mean offset: the terrain offset redistributes the state
        # inside the parcel (zero mean) and never moves the whole parcel wetter or drier.
        levels = [
            s - m if math.isfinite(m) else s for s, m in zip(states, _parcel_means(sub_off, cells), strict=True)
        ]
        ref = levels[0]
        sigma = BLEND_SIGMA_M / cell
        if len(members) > 1:
            # Blend relative to one member: a parcel interior keeps its own level exactly.
            base = _values_on_ids(ids, [lv - ref for lv in levels])
            blend = np.nan_to_num(gaussian_smooth(base, sigma), nan=0.0)
        else:
            blend = np.zeros_like(sub_off)
        raw = ref + blend + sub_off
        # The border blend moves each parcel's mean slightly: correct it, first with blended
        # (continuous) corrections, then with an exact per-parcel shift for any residual.
        for npass in range(MEAN_BLEND_PASSES + 1):
            dev = [
                s - m if math.isfinite(m) else 0.0 for s, m in zip(states, _parcel_means(raw, cells), strict=True)
            ]
            if max(abs(d) for d in dev) <= MEAN_TOL:
                break
            if len(members) > 1 and npass < MEAN_BLEND_PASSES:
                raw += np.nan_to_num(gaussian_smooth(_values_on_ids(ids, dev), sigma), nan=0.0)
            else:
                for c, d in zip(cells, dev, strict=True):
                    if abs(d) > MEAN_TOL:
                        raw[c] += d
                break
        cont = np.clip(raw, -5.0, 5.0)
        # Polygonize from a bilinear-upsampled field: zone edges follow smooth isolines, not 10 m pixels.
        up = max(1, round(cell / POLY_CELL_M))
        while up > 1 and win.height * win.width * up * up > MAX_POLY_CELLS:
            up -= 1
        fine = _Grid(win.left, win.top, cell / up, win.height * up, win.width * up)
        cont_f = _sample(cont, win, fine) if up > 1 else cont
        cont_f = np.where(np.isfinite(cont_f), cont_f, states[0])
        classes = np.clip(np.rint(cont_f), -5, 5).astype(np.int16)
        near = rasterize(
            [(g.buffer(cell), 1) for g in member_geoms],
            out_shape=classes.shape,
            transform=fine.transform,
            fill=0,
            dtype="uint8",
            all_touched=True,
        ).astype(bool)
        polys = class_polygons(classes, near, fine.transform)
        if polys and SMOOTH_TOL_M > 0:
            # Coverage simplification keeps shared edges identical, so neighbours still meet exactly.
            smooth = shapely.coverage_simplify([g for g, _ in polys], SMOOTH_TOL_M, simplify_boundary=False)
            polys = [(g, v) for g, (_, v) in zip(smooth, polys, strict=True) if not g.is_empty]
        tree = STRtree([poly for poly, _ in polys]) if polys else None
        for i in members:
            g = geoms[i]
            # Blended class at the parcel's inner point: used for gaps and for too-thin parcels.
            px, py = g.representative_point().coords[0]
            row = min(max(int((fine.top - py) / fine.cell), 0), fine.height - 1)
            col = min(max(int((px - fine.left) / fine.cell), 0), fine.width - 1)
            fill = int(classes[row, col])
            try:
                if g.area < 2.0 * fine.cell * fine.cell or tree is None:
                    grouped = {}
                else:
                    grouped = group_zone_polygons([polys[j] for j in tree.query(g)], g)
                zones[parcels[i].key] = zone_collection(grouped, g, parcels[i].water_state, fill)
            except Exception:  # never omit a parcel: one zone over the whole parcel
                logger.warning("Zones of %s fell back to one zone", parcels[i].key, exc_info=True)
                zones[parcels[i].key] = zone_collection({}, g, parcels[i].water_state, fill)

    sources = sorted({d.source for d in dems if d.source})
    dem_source = " + ".join(sources) if sources else (dems[0].source if dems else NO_DEM_SOURCE) or NO_DEM_SOURCE
    return JointZones(zones, float(cell), dem_source)


class ZonesService:
    """POST /api/zones: geometry (ULDK cache), group DEMs (NMT cache), joint zones, result cache."""

    def __init__(self, collector: Any) -> None:
        self.collector = collector
        self._cache: OrderedDict[tuple[Any, ...], JointZones] = OrderedDict()

    async def zones(self, request: ZonesRequest) -> ZonesResponse:
        started = perf_counter()
        states: dict[str, float] = {}
        crops: dict[str, str] = {}
        for p in request.parcels:
            pid = p.parcel_id.strip()
            if pid and pid not in states:
                states[pid] = round(float(p.water_state), 1)
                crops[pid] = (p.crop or "").strip()
        ids = list(states)
        resolved = await asyncio.gather(
            *(self.collector.resolve_parcel(pid) for pid in ids), return_exceptions=True
        )
        errors: dict[str, str] = {}
        parcels: dict[str, Any] = {}
        for pid, res in zip(ids, resolved, strict=True):
            if isinstance(res, ParcelNotFoundError):
                errors[pid] = str(res) or "Nie znaleziono działki w ULDK."
            elif isinstance(res, BaseException):
                logger.warning("Zones: parcel %s not resolved", pid, exc_info=res)
                errors[pid] = "Nie udało się pobrać granic działki z ULDK. Spróbuj ponownie za chwilę."
            else:
                parcels[pid] = res[0]

        nmt = getattr(self.collector, "clients", {}).get("nmt")
        source = nmt.group_source([g.parcel_id for g in parcels.values()]) if nmt is not None else "none"
        key = (ZONES_VERSION, tuple(sorted((pid, states[pid], crops[pid]) for pid in parcels)), source)
        joint = self._cache.get(key)
        if joint is not None:
            self._cache.move_to_end(key)
        elif parcels:
            geoms = list(parcels.values())
            dems: list[DemRaster] = await nmt.group_dems(geoms) if nmt is not None else []
            inputs = [ZoneParcelInput(pid, g.geojson, states[pid], crops[pid]) for pid, g in parcels.items()]
            try:
                joint = await asyncio.to_thread(compute_joint_zones, inputs, dems)
            except Exception:
                logger.exception("Joint zones failed for %s", list(parcels))
                for pid in parcels:
                    errors[pid] = "Nie udało się wyznaczyć stref wilgotności działki."
                joint = None
            if joint is not None:
                self._cache[key] = joint
                while len(self._cache) > RESULT_CACHE_SIZE:
                    self._cache.popitem(last=False)

        return ZonesResponse(
            zones=dict(joint.zones) if joint is not None else {},
            errors=errors,
            grid_m=joint.grid_m if joint is not None else FINE_CELL_M,
            dem_source=joint.dem_source if joint is not None else NO_DEM_SOURCE,
            elapsed_s=round(perf_counter() - started, 3),
        )
