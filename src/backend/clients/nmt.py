"""Terrain: GUGiK NMT 1 m (WCS, LiDAR) with Copernicus DEM GLO-30 (COG) started in parallel as fallback.

The GUGiK WCS often needs 10-15 s and sometimes does not answer. Both requests start together;
the 1 m model wins when it arrives within ``nmt_gugik_timeout_s``.
Output raster is always in EPSG:2180 (metres), NaN = nodata.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import os
import re
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from pydantic import BaseModel, ConfigDict
from rasterio.features import geometry_mask
from rasterio.io import MemoryFile
from rasterio.transform import from_origin
from rasterio.warp import Resampling, reproject
from rasterio.windows import Window, from_bounds
from shapely.geometry import box

from backend.clients.base import (
    BaseSourceClient,
    FetchOutcome,
    SourceDescriptor,
    SourceUnavailableError,
)
from backend.clients.geo import PUWG1992, parcel_shape, to_puwg92, to_wgs84
from hydro_core.schemas import DataMode, ParcelGeometry

_GDAL_ENV = {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "AWS_NO_SIGN_REQUEST": "YES",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif",
    "GDAL_HTTP_TIMEOUT": "20",
}
_FALLBACK_RES_M = 30.0


class DemRaster(BaseModel):
    """Elevation raster around the parcel (parcel + buffer) in EPSG:2180."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    elevation: np.ndarray
    transform: tuple[float, float, float, float, float, float]
    crs: str = PUWG1992
    resolution_m: float
    source: str
    is_surface_model: bool
    parcel_mask: np.ndarray
    mean_elevation_m: float | None
    min_elevation_m: float | None
    max_elevation_m: float | None


def _finalize(
    elevation: np.ndarray, transform: Any, resolution: float, source: str, surface: bool, parcel_2180: Any
) -> DemRaster:
    elevation = elevation.astype("float32")
    elevation[(elevation < -100) | (elevation > 3000)] = np.nan
    mask = geometry_mask([parcel_2180], out_shape=elevation.shape, transform=transform, invert=True)
    if not mask.any():
        mask = geometry_mask(
            [parcel_2180], out_shape=elevation.shape, transform=transform, invert=True, all_touched=True
        )
    inside = elevation[mask & np.isfinite(elevation)]
    if inside.size == 0:
        raise SourceUnavailableError("Model terenu nie obejmuje działki (brak wartości wysokości).")
    return DemRaster(
        elevation=elevation,
        transform=tuple(transform)[:6],  # type: ignore[arg-type]
        resolution_m=resolution,
        source=source,
        is_surface_model=surface,
        parcel_mask=mask,
        mean_elevation_m=round(float(inside.mean()), 2),
        min_elevation_m=round(float(inside.min()), 2),
        max_elevation_m=round(float(inside.max()), 2),
    )


def _decode_gugik_tiff(content: bytes, parcel_2180: Any, resolution: float) -> DemRaster:
    with MemoryFile(content) as mem, mem.open() as src:
        arr = src.read(1).astype("float32")
        if src.nodata is not None:
            arr[arr == src.nodata] = np.nan
        return _finalize(arr, src.transform, resolution, "GUGiK NMT 1 m (LiDAR)", False, parcel_2180)


def _copernicus_tile_names(bbox_wgs84: tuple[float, float, float, float]) -> list[str]:
    minx, miny, maxx, maxy = bbox_wgs84
    names = []
    for lat in range(math.floor(miny), math.floor(maxy) + 1):
        for lon in range(math.floor(minx), math.floor(maxx) + 1):
            names.append(f"Copernicus_DSM_COG_10_N{lat:02d}_00_E{lon:03d}_00_DEM")
    return names


def _read_copernicus(
    url_template: str,
    bbox_wgs84: tuple[float, float, float, float],
    bounds_2180: tuple[float, ...],
    parcel_2180: Any,
    local_dir: Path | None = None,
) -> DemRaster:
    """Read Copernicus DEM tiles (local first, S3 fallback) and resample to a snapped 30 m grid in EPSG:2180."""
    left, bottom, right, top = bounds_2180
    b_wgs = to_wgs84(box(left, bottom, right, top)).bounds
    pad_lon, pad_lat = 0.003, 0.002
    read_bbox = (
        min(bbox_wgs84[0], b_wgs[0]) - pad_lon,
        min(bbox_wgs84[1], b_wgs[1]) - pad_lat,
        max(bbox_wgs84[2], b_wgs[2]) + pad_lon,
        max(bbox_wgs84[3], b_wgs[3]) + pad_lat,
    )
    hrefs: list[str] = []
    for name in _copernicus_tile_names(read_bbox):
        local_tile = None if local_dir is None else local_dir / f"{name}.tif"
        if local_tile is not None and local_tile.exists():
            hrefs.append(str(local_tile))
        else:
            hrefs.append(url_template.format(name=name))

    width = max(1, round((right - left) / _FALLBACK_RES_M))
    height = max(1, round((top - bottom) / _FALLBACK_RES_M))
    dst_transform = from_origin(left, top, _FALLBACK_RES_M, _FALLBACK_RES_M)
    dst = np.full((height, width), np.nan, dtype="float32")
    with rasterio.Env(**_GDAL_ENV):
        for h in hrefs:
            with rasterio.open(h) as ds:
                win = from_bounds(*read_bbox, transform=ds.transform).round_offsets().round_lengths()
                win = win.intersection(Window(0, 0, ds.width, ds.height))
                if win.width <= 0 or win.height <= 0:
                    continue
                arr = ds.read(1, window=win).astype("float32")
                if ds.nodata is not None:
                    arr[arr == ds.nodata] = np.nan
                tmp = np.full((height, width), np.nan, dtype="float32")
                reproject(
                    source=arr,
                    destination=tmp,
                    src_transform=ds.window_transform(win),
                    src_crs=ds.crs,
                    dst_transform=dst_transform,
                    dst_crs=PUWG1992,
                    resampling=Resampling.bilinear,
                    dst_nodata=np.nan,
                    ERROR_THRESHOLD=0,
                )
                fill = np.isnan(dst) & np.isfinite(tmp)
                dst[fill] = tmp[fill]
    return _finalize(
        dst, dst_transform, _FALLBACK_RES_M, "Copernicus DEM GLO-30 (model pokrycia terenu)", True, parcel_2180
    )


def save_dem(path: Path, dem: DemRaster) -> None:
    """Store a DEM raster in the local cache (compressed NumPy archive)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = dem.model_dump(exclude={"elevation", "parcel_mask"})
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".npz")
    with os.fdopen(fd, "wb") as fh:
        np.savez_compressed(
            fh, elevation=dem.elevation, parcel_mask=dem.parcel_mask, meta=np.array(json.dumps(meta))
        )
    os.replace(tmp, path)


def load_dem(path: Path) -> DemRaster:
    """Load a DEM raster stored by ``save_dem``."""
    with np.load(path) as data:
        meta = json.loads(str(data["meta"]))
        return DemRaster(elevation=data["elevation"], parcel_mask=data["parcel_mask"], **meta)


def _read_and_store_copernicus(
    url_template: str,
    bbox_wgs84: tuple[float, float, float, float],
    bounds_2180: tuple[float, ...],
    parcel_2180: Any,
    path: Path,
    local_dir: Path | None = None,
) -> DemRaster:
    dem = _read_copernicus(url_template, bbox_wgs84, bounds_2180, parcel_2180, local_dir=local_dir)
    save_dem(path, dem)
    return dem


_COP_TILE_M = 3000.0  # Copernicus tile cache for parcel groups (multiple of the 30 m lattice)


def _read_and_store_copernicus_tile(
    url_template: str, bounds_2180: tuple[float, float, float, float], path: Path, local_dir: Path | None
) -> DemRaster:
    """Read one fixed Copernicus tile (EPSG:2180, 30 m lattice) and store it in the cache."""
    tile = box(*bounds_2180)
    bbox_wgs84: tuple[float, float, float, float] = to_wgs84(tile).bounds  # type: ignore[assignment]
    return _read_and_store_copernicus(url_template, bbox_wgs84, bounds_2180, tile, path, local_dir)


_BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()
_INFLIGHT: dict[str, asyncio.Task[Any]] = {}
_GUGIK_SLOW_UNTIL = 0.0  # monotonic time; while in the future, GUGiK gets only a short wait
_GUGIK_STATE_KEY = "nmt:gugik_slow"  # same state in the SQLite cache: survives a server restart
# The stored state may live longer than the in-process cooldown: every request still starts a
# background download, and one that answers within nmt_gugik_timeout_s clears the state at once.
_GUGIK_STATE_TTL_S = 6 * 3600.0


def _start_background(key: str, coro: Any) -> asyncio.Task[Any]:
    """Start (or reuse) a download that may outlive the request; one task per parcel."""
    running = _INFLIGHT.get(key)
    if running is not None and not running.done():
        coro.close()
        return running
    task = asyncio.create_task(coro)
    _INFLIGHT[key] = task
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    task.add_done_callback(lambda t: _INFLIGHT.pop(key, None) if _INFLIGHT.get(key) is t else None)
    task.add_done_callback(lambda t: t.cancelled() or t.exception())
    return task


def _dem_bounds(
    parcel_2180: Any, buffer_m: float, max_pixels_per_side: int
) -> tuple[tuple[float, float, float, float], tuple[float, float, float, float]]:
    """Snapped EPSG:2180 bounds (parcel + buffer) of the GUGiK 1 m request and the Copernicus grid."""
    minx, miny, maxx, maxy = parcel_2180.bounds
    g_buf = max(buffer_m, 120.0)
    raw_span = max(maxx - minx + 2 * g_buf, maxy - miny + 2 * g_buf)
    g_res = float(max(1, math.ceil(raw_span / max_pixels_per_side)))
    gugik_bounds = (
        math.floor((minx - g_buf) / g_res) * g_res,
        math.floor((miny - g_buf) / g_res) * g_res,
        math.ceil((maxx + g_buf) / g_res) * g_res,
        math.ceil((maxy + g_buf) / g_res) * g_res,
    )
    cop_buf = 390.0
    cop_bounds = (
        math.floor((minx - cop_buf) / _FALLBACK_RES_M) * _FALLBACK_RES_M,
        math.floor((miny - cop_buf) / _FALLBACK_RES_M) * _FALLBACK_RES_M,
        math.ceil((maxx + cop_buf) / _FALLBACK_RES_M) * _FALLBACK_RES_M,
        math.ceil((maxy + cop_buf) / _FALLBACK_RES_M) * _FALLBACK_RES_M,
    )
    return gugik_bounds, cop_bounds


def _mark_gugik_slow(cooldown_s: float) -> None:
    global _GUGIK_SLOW_UNTIL
    _GUGIK_SLOW_UNTIL = time.monotonic() + cooldown_s


def _mark_gugik_ok() -> None:
    global _GUGIK_SLOW_UNTIL
    _GUGIK_SLOW_UNTIL = 0.0


class NmtClient(BaseSourceClient[DemRaster]):
    """Elevation for slope, D8 flow, TWI and depressions."""

    descriptor = SourceDescriptor(
        key="nmt",
        category="przestrzenne",
        source_name="GUGiK Numeryczny Model Terenu 1 m (WCS)",
        endpoint="https://mapy.geoportal.gov.pl/wss/service/PZGIK/NMT/GRID1/WCS/DigitalTerrainModelFormatTIFF",
        spatial_resolution="1 m (LiDAR)",
        license="Dane GUGiK udostępniane bez opłat i bez warunków użytkowania",
        attribution="Źródło: GUGiK, NMT",
    )

    @property
    def timeout_s(self) -> float:
        return self.settings.nmt_timeout_s

    def _cache_path(self, parcel_id: str, kind: str) -> Path:
        safe = re.sub(r"[^0-9A-Za-z_.-]", "_", parcel_id)
        return self.settings.cache_dir / "nmt" / f"{safe}_{kind}.npz"

    async def _set_gugik_slow(self, slow: bool) -> None:
        """Record whether GUGiK is slow, in this process and in the cache (for the cooldown period)."""
        cooldown = self.settings.nmt_gugik_cooldown_s
        if slow:
            _mark_gugik_slow(cooldown)
        else:
            _mark_gugik_ok()
        with contextlib.suppress(Exception):  # the state is an optimisation only
            await self.cache.set(_GUGIK_STATE_KEY, {"slow": slow}, max(cooldown, _GUGIK_STATE_TTL_S))

    async def _gugik_is_slow(self) -> bool:
        if time.monotonic() < _GUGIK_SLOW_UNTIL:
            return True
        with contextlib.suppress(Exception):
            state = await self.cache.get(_GUGIK_STATE_KEY)
            return bool(state and state.get("slow"))
        return False

    async def _gugik(
        self, bounds: tuple[float, float, float, float], parcel_2180: Any, store_path: Path
    ) -> DemRaster:
        """Download NMT 1 m; the result is always stored, also when it arrives after the deadline.

        Every background download also refreshes the slow/ok state: only an answer within
        ``nmt_gugik_timeout_s`` ends the cooldown, so a slow service is never probed in the foreground.
        """
        started = time.monotonic()
        try:
            dem = await self._gugik_download(bounds, parcel_2180, store_path)
        except Exception:
            await self._set_gugik_slow(True)
            raise
        await self._set_gugik_slow(time.monotonic() - started > self.settings.nmt_gugik_timeout_s)
        return dem

    async def _gugik_download(
        self, bounds: tuple[float, float, float, float], parcel_2180: Any, store_path: Path
    ) -> DemRaster:
        s = self.settings
        left, bottom, right, top = bounds
        side = max(right - left, top - bottom)
        resolution = float(max(1, math.ceil(side / s.nmt_max_pixels_per_side)))
        width = max(1, round((right - left) / resolution))
        height = max(1, round((top - bottom) / resolution))
        params = {
            "SERVICE": "WCS",
            "VERSION": "1.0.0",
            "REQUEST": "GetCoverage",
            "COVERAGE": s.nmt_coverage_id,
            "CRS": "EPSG:2180",
            "BBOX": f"{left:.0f},{bottom:.0f},{right:.0f},{top:.0f}",
            "WIDTH": str(width),
            "HEIGHT": str(height),
            "FORMAT": "image/tiff",
        }
        resp = await self.http.get(s.nmt_wcs_url, params=params, timeout=s.nmt_background_timeout_s)
        resp.raise_for_status()
        if "tiff" not in resp.headers.get("content-type", ""):
            raise SourceUnavailableError("Usługa NMT GUGiK zwróciła błąd zamiast rastra.")
        dem = await asyncio.to_thread(_decode_gugik_tiff, resp.content, parcel_2180, resolution)
        await asyncio.to_thread(save_dem, store_path, dem)
        return dem

    async def _fetch(self, **kwargs: Any) -> FetchOutcome[DemRaster]:
        geojson: dict[str, Any] = kwargs["geojson"]
        bbox_wgs84: tuple[float, float, float, float] = tuple(kwargs["bbox_wgs84"])  # type: ignore[assignment]
        parcel_id: str = kwargs["parcel_id"]
        s = self.settings
        gugik_path = self._cache_path(parcel_id, "gugik1m")
        cop_path = self._cache_path(parcel_id, "cop30m_g2")

        if gugik_path.exists():
            dem = await asyncio.to_thread(load_dem, gugik_path)
            return FetchOutcome(
                data=dem, mode=DataMode.PRE_FETCHED, observation_date="NMT GUGiK 1 m (bufor działki)"
            )

        parcel_2180 = to_puwg92(parcel_shape(geojson))
        gugik_bounds, cop_bounds = _dem_bounds(parcel_2180, s.nmt_buffer_m, s.nmt_max_pixels_per_side)

        gugik_task = _start_background(str(gugik_path), self._gugik(gugik_bounds, parcel_2180, gugik_path))
        slow = await self._gugik_is_slow()
        gugik_wait = s.nmt_gugik_short_wait_s if slow else s.nmt_gugik_timeout_s
        if cop_path.exists():
            cop_task: asyncio.Task[DemRaster] = asyncio.create_task(asyncio.to_thread(load_dem, cop_path))
        else:
            cop_task = asyncio.create_task(
                asyncio.to_thread(
                    _read_and_store_copernicus,
                    s.copernicus_dem_url_template,
                    bbox_wgs84,
                    cop_bounds,
                    parcel_2180,
                    cop_path,
                    s.copernicus_dem_dir,
                )
            )
        try:
            try:
                if slow:
                    # Known slow: GUGiK gets only the time the fallback needs (at most the short wait).
                    await asyncio.wait(
                        {gugik_task, cop_task}, timeout=gugik_wait, return_when=asyncio.FIRST_COMPLETED
                    )
                    if not gugik_task.done():
                        raise TimeoutError
                    dem = gugik_task.result()
                else:
                    dem = await asyncio.wait_for(asyncio.shield(gugik_task), timeout=gugik_wait)
                return FetchOutcome(
                    data=dem, mode=DataMode.LIVE, observation_date="NMT GUGiK (aktualny zasób PZGiK)"
                )
            except TimeoutError:
                await self._set_gugik_slow(True)
                gugik_problem = (
                    "nie odpowiada w ostatnich minutach" if slow else f"nie odpowiedziała w {gugik_wait:.0f} s"
                ) + " (pobieranie trwa w tle; kolejna analiza tej działki użyje NMT 1 m)"
            except Exception as exc:  # noqa: BLE001 - any failure switches to the fallback model
                await self._set_gugik_slow(True)
                gugik_problem = f"zwróciła błąd ({type(exc).__name__})"

            try:
                dem = await cop_task
            except Exception as exc:
                raise SourceUnavailableError(
                    f"Usługa NMT 1 m GUGiK {gugik_problem}, a zapasowy model Copernicus DEM "
                    "jest niedostępny."
                ) from exc
        finally:
            if not cop_task.done():
                cop_task.cancel()
        return FetchOutcome(
            data=dem,
            mode=DataMode.FALLBACK,
            observation_date="Copernicus DEM GLO-30 (2011–2015)",
            message=(
                f"Usługa NMT 1 m GUGiK {gugik_problem} – użyto modelu Copernicus DEM 30 m. "
                "To model pokrycia terenu (z drzewami i budynkami): spadki i spływ są przybliżone."
            ),
            source_name="Copernicus DEM GLO-30 (zapas za NMT GUGiK)",
            endpoint="https://copernicus-dem-30m.s3.amazonaws.com",
            spatial_resolution="30 m",
            license="Copernicus DEM – licencja ESA/Airbus: bezpłatnie, z podaniem źródła",
            attribution="Copernicus DEM GLO-30 © DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH "
            "2014-2018 provided under COPERNICUS by the European Union and ESA",
        )

    def download_state(self, parcel_id: str) -> str:
        """'ready' (NMT 1 m on disk), 'downloading' (background download running) or 'none'."""
        path = self._cache_path(parcel_id, "gugik1m")
        if path.exists():
            return "ready"
        task = _INFLIGHT.get(str(path))
        return "downloading" if task is not None and not task.done() else "none"

    def group_source(self, parcel_ids: Sequence[str]) -> str:
        """'gugik' when the 1 m model of every parcel is cached, else 'copernicus'."""
        if parcel_ids and all(self._cache_path(pid, "gugik1m").exists() for pid in parcel_ids):
            return "gugik"
        return "copernicus"

    def _cop_tile_path(self, ix: int, iy: int) -> Path:
        return self.settings.cache_dir / "nmt" / "tiles" / f"cop30_{_COP_TILE_M:.0f}_{ix}_{iy}.npz"

    async def group_dems(self, parcels: Sequence[ParcelGeometry]) -> list[DemRaster]:
        """DEM rasters covering a parcel group from ONE source (no terrain jumps at shared borders).

        GUGiK 1 m (one raster per parcel) only when it is cached for every parcel; otherwise
        Copernicus GLO-30 tiles of a fixed 3 km EPSG:2180 grid around all parcels, cached on disk
        and shared by every later group in the area. Never waits for the GUGiK WCS: missing 1 m
        models download in the background (same task as the analysis). Empty list = no elevation.
        """
        s = self.settings
        ids = [p.parcel_id for p in parcels]
        if self.group_source(ids) == "gugik":
            loaded = await asyncio.gather(
                *(asyncio.to_thread(load_dem, self._cache_path(pid, "gugik1m")) for pid in ids),
                return_exceptions=True,
            )
            if not any(isinstance(d, BaseException) for d in loaded):
                return [d for d in loaded if isinstance(d, DemRaster)]

        tiles: set[tuple[int, int]] = set()
        for p in parcels:
            parcel_2180 = to_puwg92(parcel_shape(p.geojson))
            gugik_bounds, cop_bounds = _dem_bounds(parcel_2180, s.nmt_buffer_m, s.nmt_max_pixels_per_side)
            gugik_path = self._cache_path(p.parcel_id, "gugik1m")
            if not gugik_path.exists():
                _start_background(str(gugik_path), self._gugik(gugik_bounds, parcel_2180, gugik_path))
            left, bottom, right, top = cop_bounds
            for ix in range(math.floor(left / _COP_TILE_M), math.ceil(right / _COP_TILE_M)):
                for iy in range(math.floor(bottom / _COP_TILE_M), math.ceil(top / _COP_TILE_M)):
                    tiles.add((ix, iy))

        async def tile(ix: int, iy: int) -> DemRaster | None:
            path = self._cop_tile_path(ix, iy)
            try:
                if path.exists():
                    return await asyncio.to_thread(load_dem, path)
                task = _start_background(
                    str(path),
                    asyncio.to_thread(
                        _read_and_store_copernicus_tile,
                        s.copernicus_dem_url_template,
                        (ix * _COP_TILE_M, iy * _COP_TILE_M, (ix + 1) * _COP_TILE_M, (iy + 1) * _COP_TILE_M),
                        path,
                        s.copernicus_dem_dir,
                    ),
                )
                return await asyncio.wait_for(asyncio.shield(task), timeout=s.nmt_timeout_s)
            except Exception:  # noqa: BLE001 - a tile without elevation leaves a gap (offset 0)
                return None

        loaded_tiles = await asyncio.gather(*(tile(ix, iy) for ix, iy in sorted(tiles)))
        return [d for d in loaded_tiles if d is not None]


def pending_background_tasks() -> list[asyncio.Task[Any]]:
    """Background NMT downloads still running (used by scripts to let them finish)."""
    return [t for t in _BACKGROUND_TASKS if not t.done()]