"""Terrain: GUGiK NMT 1 m (WCS, LiDAR) with Copernicus DEM GLO-30 (COG) started in parallel as fallback.

The GUGiK WCS often needs 10-15 s and sometimes does not answer. Both requests start together;
the 1 m model wins when it arrives within ``nmt_gugik_timeout_s``.
Output raster is always in EPSG:2180 (metres), NaN = nodata.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from pydantic import BaseModel, ConfigDict
from rasterio.features import geometry_mask
from rasterio.io import MemoryFile
from rasterio.merge import merge
from rasterio.transform import from_origin
from rasterio.warp import Resampling, reproject

from backend.clients.base import (
    BaseSourceClient,
    FetchOutcome,
    SourceDescriptor,
    SourceUnavailableError,
)
from backend.clients.geo import PUWG1992, parcel_shape, to_puwg92
from hydro_core.schemas import DataMode

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
    url_template: str, bbox_wgs84: tuple[float, float, float, float], bounds_2180: tuple[float, ...], parcel_2180: Any
) -> DemRaster:
    """Read Copernicus DEM tiles covering the bbox and resample to a 30 m grid in EPSG:2180."""
    hrefs = [url_template.format(name=n) for n in _copernicus_tile_names(bbox_wgs84)]
    minx, miny, maxx, maxy = bbox_wgs84
    pad = 0.003
    with rasterio.Env(**_GDAL_ENV):
        datasets = [rasterio.open(h) for h in hrefs]
        try:
            src_arr, src_transform = merge(
                datasets, bounds=(minx - pad, miny - pad, maxx + pad, maxy + pad)
            )
            src_crs = datasets[0].crs
        finally:
            for ds in datasets:
                ds.close()

    left, bottom, right, top = bounds_2180
    width = max(1, math.ceil((right - left) / _FALLBACK_RES_M))
    height = max(1, math.ceil((top - bottom) / _FALLBACK_RES_M))
    dst_transform = from_origin(left, top, _FALLBACK_RES_M, _FALLBACK_RES_M)
    dst = np.full((height, width), np.nan, dtype="float32")
    reproject(
        source=src_arr[0].astype("float32"),
        destination=dst,
        src_transform=src_transform,
        src_crs=src_crs,
        dst_transform=dst_transform,
        dst_crs=PUWG1992,
        resampling=Resampling.bilinear,
        dst_nodata=np.nan,
    )
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
) -> DemRaster:
    dem = _read_copernicus(url_template, bbox_wgs84, bounds_2180, parcel_2180)
    save_dem(path, dem)
    return dem


_BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()
_INFLIGHT: dict[str, asyncio.Task[Any]] = {}
_GUGIK_SLOW_UNTIL = 0.0  # monotonic time; while in the future, GUGiK gets only a short wait


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


def _gugik_wait_s(normal_s: float, short_s: float) -> float:
    return short_s if time.monotonic() < _GUGIK_SLOW_UNTIL else normal_s


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

    async def _gugik(
        self, bounds: tuple[float, float, float, float], parcel_2180: Any, store_path: Path
    ) -> DemRaster:
        """Download NMT 1 m; the result is always stored, also when it arrives after the deadline."""
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
        _mark_gugik_ok()
        return dem

    async def _fetch(self, **kwargs: Any) -> FetchOutcome[DemRaster]:
        geojson: dict[str, Any] = kwargs["geojson"]
        bbox_wgs84: tuple[float, float, float, float] = tuple(kwargs["bbox_wgs84"])  # type: ignore[assignment]
        parcel_id: str = kwargs["parcel_id"]
        s = self.settings
        gugik_path = self._cache_path(parcel_id, "gugik1m")
        cop_path = self._cache_path(parcel_id, "cop30m")

        if gugik_path.exists():
            dem = await asyncio.to_thread(load_dem, gugik_path)
            return FetchOutcome(
                data=dem, mode=DataMode.PRE_FETCHED, observation_date="NMT GUGiK 1 m (bufor działki)"
            )

        parcel_2180 = to_puwg92(parcel_shape(geojson))
        minx, miny, maxx, maxy = parcel_2180.bounds
        b = s.nmt_buffer_m
        bounds = (math.floor(minx - b), math.floor(miny - b), math.ceil(maxx + b), math.ceil(maxy + b))

        gugik_task = _start_background(str(gugik_path), self._gugik(bounds, parcel_2180, gugik_path))
        gugik_wait = _gugik_wait_s(s.nmt_gugik_timeout_s, s.nmt_gugik_short_wait_s)
        if cop_path.exists():
            cop_task: asyncio.Task[DemRaster] = asyncio.create_task(asyncio.to_thread(load_dem, cop_path))
        else:
            cop_task = asyncio.create_task(
                asyncio.to_thread(
                    _read_and_store_copernicus,
                    s.copernicus_dem_url_template,
                    bbox_wgs84,
                    bounds,
                    parcel_2180,
                    cop_path,
                )
            )
        try:
            try:
                dem = await asyncio.wait_for(asyncio.shield(gugik_task), timeout=gugik_wait)
                return FetchOutcome(
                    data=dem, mode=DataMode.LIVE, observation_date="NMT GUGiK (aktualny zasób PZGiK)"
                )
            except TimeoutError:
                _mark_gugik_slow(s.nmt_gugik_cooldown_s)
                gugik_problem = (
                    f"nie odpowiedziała w {gugik_wait:.0f} s (pobieranie trwa w tle; "
                    "kolejna analiza tej działki użyje NMT 1 m)"
                )
            except Exception as exc:  # noqa: BLE001 - any failure switches to the fallback model
                _mark_gugik_slow(s.nmt_gugik_cooldown_s)
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


def pending_background_tasks() -> list[asyncio.Task[Any]]:
    """Background NMT downloads still running (used by scripts to let them finish)."""
    return [t for t in _BACKGROUND_TASKS if not t.done()]