"""Sentinel-2 L2A Collection 1 (Earth Search STAC) - NDVI / NDMI of the parcel, now vs past years.

Only the parcel window is read from Cloud Optimized GeoTIFFs (HTTP range requests).
Reflectance = DN * scale + offset, with scale/offset read from each scene's metadata
(``raster:bands``), as required for processing baseline >= 04.00 (BOA_ADD_OFFSET = -1000).

Speed rules (COGs are hosted in AWS us-west-2):
- the cloud mask (SCL) is read first; spectral bands only for the chosen scene, in parallel,
- past years use NDMI only (2 bands), the current scene also NDVI (4 bands),
- per-scene parcel statistics are cached forever (a scene never changes),
- years that do not finish before the source deadline are reported, the rest is kept.
"""

from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass
from datetime import date, timedelta
from time import perf_counter
from typing import Any

import numpy as np
import rasterio
from affine import Affine
from pydantic import BaseModel, ConfigDict
from rasterio.enums import Resampling
from rasterio.features import geometry_mask
from rasterio.transform import array_bounds
from rasterio.warp import transform_geom
from rasterio.windows import Window, from_bounds
from shapely.geometry import shape

from backend.clients.base import (
    BaseSourceClient,
    FetchOutcome,
    SourceDescriptor,
    SourceUnavailableError,
)
from hydro_core.schemas import DataMode

logger = logging.getLogger(__name__)

SCL_CLEAR = (4, 5)  # vegetation, not vegetated (bare soil)
BANDS_CURRENT = ("red", "nir", "nir08", "swir16")
BANDS_HISTORY = ("nir08", "swir16")

_GDAL_ENV = {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "AWS_NO_SIGN_REQUEST": "YES",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.TIF,.tiff",
    "GDAL_INGESTED_BYTES_AT_OPEN": "65536",
    "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
    "GDAL_HTTP_TIMEOUT": "8",
    "GDAL_HTTP_MAX_RETRY": "1",
    "VSI_CACHE": "TRUE",
}


class S2SceneStats(BaseModel):
    """Parcel statistics for one Sentinel-2 scene."""

    scene_id: str
    acquired: date
    scene_cloud_pct: float | None
    parcel_clear_fraction: float
    ndvi_mean: float | None = None
    ndmi_mean: float | None = None
    ndmi_p10: float | None = None
    ndmi_p90: float | None = None


class S2ParcelSeries(BaseModel):
    """Current state of the parcel and the same season in previous years."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    current: S2SceneStats | None
    # last clear scene older than the current window (16-60 days before the analysis date):
    # input of HydroFusion-PL when the current window is cloudy (NDMI changes slowly)
    previous: S2SceneStats | None = None
    current_ndmi_grid: np.ndarray | None = None
    grid_transform: tuple[float, float, float, float, float, float] | None = None
    grid_crs: str | None = None
    history: list[S2SceneStats]
    years_without_data: list[int]
    years_timed_out: list[int]
    ndmi_history_mean: float | None = None
    ndmi_anomaly_z: float | None = None
    note: str | None = None


@dataclass
class _Grid:
    crs: Any
    transform: Affine
    height: int
    width: int
    inside: np.ndarray


@dataclass
class _SclResult:
    item: dict[str, Any]
    grid: _Grid
    clear: np.ndarray
    clear_fraction: float


def _band_scaling(asset: dict[str, Any]) -> tuple[float, float]:
    bands = asset.get("raster:bands") or [{}]
    return float(bands[0].get("scale", 1.0)), float(bands[0].get("offset", 0.0))


def _read_scl(item: dict[str, Any], geom_wgs84: dict[str, Any]) -> _SclResult:
    """Open SCL (20 m), define the analysis grid around the parcel, compute the clear-sky mask."""
    with rasterio.Env(**_GDAL_ENV), rasterio.open(item["assets"]["scl"]["href"]) as src:
        geom = transform_geom("EPSG:4326", src.crs, geom_wgs84)
        minx, miny, maxx, maxy = shape(geom).bounds
        res = abs(src.transform.a)
        win = from_bounds(minx - res, miny - res, maxx + res, maxy + res, transform=src.transform)
        col0, row0 = math.floor(win.col_off), math.floor(win.row_off)
        width = max(1, math.ceil(win.col_off + win.width) - col0)
        height = max(1, math.ceil(win.row_off + win.height) - row0)
        window = Window(col0, row0, width, height)
        scl = src.read(1, window=window, boundless=True, fill_value=0)
        transform = src.window_transform(window)
        crs = src.crs
    inside = geometry_mask([geom], out_shape=scl.shape, transform=transform, invert=True)
    if not inside.any():
        inside = geometry_mask(
            [geom], out_shape=scl.shape, transform=transform, invert=True, all_touched=True
        )
    clear = inside & np.isin(scl, SCL_CLEAR)
    n_inside = int(inside.sum())
    fraction = float(clear.sum()) / n_inside if n_inside else 0.0
    return _SclResult(item, _Grid(crs, transform, height, width, inside), clear, fraction)


def _read_on_grid(href: str, grid: _Grid, scale: float, offset: float) -> np.ndarray:
    """Read a band onto the 20 m analysis grid as reflectance (NaN = nodata)."""
    with rasterio.Env(**_GDAL_ENV), rasterio.open(href) as src:
        left, bottom, right, top = array_bounds(grid.height, grid.width, grid.transform)
        win = from_bounds(left, bottom, right, top, transform=src.transform)
        raw = src.read(
            1,
            window=win,
            out_shape=(grid.height, grid.width),
            boundless=True,
            fill_value=0,
            resampling=Resampling.average,
        ).astype("float32")
    raw[raw <= 0] = np.nan
    return raw * scale + offset


def _normalized_difference(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    with np.errstate(divide="ignore", invalid="ignore"):
        return (a - b) / (a + b)


def _base_stats(scl: _SclResult) -> S2SceneStats:
    props = scl.item["properties"]
    return S2SceneStats(
        scene_id=scl.item["id"],
        acquired=date.fromisoformat(props["datetime"][:10]),
        scene_cloud_pct=props.get("eo:cloud_cover"),
        parcel_clear_fraction=round(scl.clear_fraction, 3),
    )


def _same_day_in_year(day: date, year: int) -> date:
    try:
        return day.replace(year=year)
    except ValueError:  # 29 February
        return day.replace(year=year, day=28)


class Sentinel2Client(BaseSourceClient[S2ParcelSeries]):
    """NDVI / NDMI now (last clear scene up to the analysis date) and NDMI in the same window of past years."""

    descriptor = SourceDescriptor(
        key="sentinel2",
        category="satelitarne",
        source_name="Copernicus Sentinel-2 L2A Collection 1 (Earth Search STAC)",
        endpoint="https://earth-search.aws.element84.com/v1 – kolekcja sentinel-2-c1-l2a",
        spatial_resolution="10–20 m (analiza na siatce 20 m)",
        license="Copernicus Open Access – dane wolne i otwarte",
        attribution="Contains modified Copernicus Sentinel data, processed by ESA / Element 84",
    )

    @property
    def timeout_s(self) -> float:
        return self.settings.s2_timeout_s

    async def _search(self, bbox: tuple[float, ...], start: date, end: date) -> list[dict[str, Any]]:
        body = {
            "collections": [self.settings.s2_collection],
            "bbox": list(bbox),
            "datetime": f"{start.isoformat()}T00:00:00Z/{end.isoformat()}T23:59:59Z",
            "limit": 60,
            "query": {"eo:cloud_cover": {"lt": self.settings.s2_max_scene_cloud_pct}},
            "fields": {"include": ["id", "properties.datetime", "properties.eo:cloud_cover", "assets"]},
        }
        resp = await self.http.post(self.settings.s2_stac_search_url, json=body, timeout=8.0)
        resp.raise_for_status()
        best_per_day: dict[str, dict[str, Any]] = {}
        for item in resp.json().get("features", []):
            day = item["properties"]["datetime"][:10]
            cc = item["properties"].get("eo:cloud_cover", 100.0)
            if day not in best_per_day or cc < best_per_day[day]["properties"].get("eo:cloud_cover", 100.0):
                best_per_day[day] = item
        return list(best_per_day.values())

    async def _in_thread(self, sem: asyncio.Semaphore, func: Any, *args: Any) -> Any:
        async with sem:
            return await asyncio.to_thread(func, *args)

    async def _evaluate(
        self,
        candidates: list[dict[str, Any]],
        geom: dict[str, Any],
        parcel_key: str,
        sem: asyncio.Semaphore,
        current: bool,
    ) -> tuple[S2SceneStats | None, np.ndarray | None, _Grid | None]:
        """Pick the first candidate (given order) with enough clear pixels and compute its indices."""
        if not current:
            for item in candidates:
                cached = await self.cache.get(f"s2:{parcel_key}:{item['id']}")
                if cached and cached.get("ndmi_mean") is not None:
                    return S2SceneStats.model_validate(cached), None, None

        async def safe_scl(item: dict[str, Any]) -> _SclResult | None:
            try:
                return await self._in_thread(sem, _read_scl, item, geom)
            except Exception:  # one broken scene must not stop the others
                logger.warning("Sentinel-2 SCL %s skipped", item.get("id"), exc_info=True)
                return None

        scls = await asyncio.gather(*(safe_scl(c) for c in candidates))
        usable = [s for s in scls if s is not None and s.clear_fraction >= self.settings.s2_min_parcel_clear_fraction]
        if not usable:
            return None, None, None

        chosen = usable[0]
        names = BANDS_CURRENT if current else BANDS_HISTORY
        assets = chosen.item["assets"]
        arrays = await asyncio.gather(
            *(
                self._in_thread(sem, _read_on_grid, assets[n]["href"], chosen.grid, *_band_scaling(assets[n]))
                for n in names
            )
        )
        bands = dict(zip(names, arrays, strict=True))
        stats = _base_stats(chosen)
        ndmi = _normalized_difference(bands["nir08"], bands["swir16"])
        ndmi_vals = ndmi[chosen.clear & np.isfinite(ndmi)]
        if ndmi_vals.size:
            stats.ndmi_mean = round(float(ndmi_vals.mean()), 4)
            stats.ndmi_p10 = round(float(np.percentile(ndmi_vals, 10)), 4)
            stats.ndmi_p90 = round(float(np.percentile(ndmi_vals, 90)), 4)
        grid_out = None
        if current:
            ndvi = _normalized_difference(bands["nir"], bands["red"])
            ndvi_vals = ndvi[chosen.clear & np.isfinite(ndvi)]
            if ndvi_vals.size:
                stats.ndvi_mean = round(float(ndvi_vals.mean()), 4)
            grid_out = np.where(chosen.clear, ndmi, np.nan).astype("float32")
        else:
            await self.cache.set(f"s2:{parcel_key}:{stats.scene_id}", stats.model_dump(mode="json"), None)
        return stats, grid_out, chosen.grid

    async def _fetch(self, **kwargs: Any) -> FetchOutcome[S2ParcelSeries]:
        started = perf_counter()
        geom: dict[str, Any] = kwargs["geojson"]
        bbox: tuple[float, ...] = tuple(kwargs["bbox_wgs84"])
        analysis_date: date = kwargs["analysis_date"]
        parcel_key: str = kwargs.get("parcel_id") or ",".join(f"{v:.5f}" for v in bbox)
        s = self.settings
        sem = asyncio.Semaphore(s.s2_max_concurrent_reads)

        history_years = [analysis_date.year - k for k in range(s.s2_history_years, 0, -1)]
        windows = [(analysis_date - timedelta(days=s.s2_current_window_days), analysis_date)] + [
            (
                _same_day_in_year(analysis_date, y) - timedelta(days=s.s2_history_window_days),
                _same_day_in_year(analysis_date, y) + timedelta(days=s.s2_history_window_days),
            )
            for y in history_years
        ]

        async def run_window(idx: int) -> tuple[S2SceneStats | None, np.ndarray | None, _Grid | None]:
            items = await self._search(bbox, *windows[idx])
            if idx == 0:
                ordered = sorted(items, key=lambda it: it["properties"]["datetime"], reverse=True)[:3]
            else:
                ordered = sorted(items, key=lambda it: it["properties"].get("eo:cloud_cover", 100.0))[:2]
            return await self._evaluate(ordered, geom, parcel_key, sem, current=(idx == 0))

        previous_window = (
            analysis_date - timedelta(days=s.s2_previous_window_days),
            analysis_date - timedelta(days=s.s2_current_window_days + 1),
        )

        async def run_previous() -> S2SceneStats | None:
            items = await self._search(bbox, *previous_window)
            ordered = sorted(items, key=lambda it: it["properties"]["datetime"], reverse=True)[:4]
            stats, _, _ = await self._evaluate(ordered, geom, parcel_key, sem, current=False)
            return stats

        tasks = [asyncio.create_task(run_window(i)) for i in range(len(windows))]
        previous_task = asyncio.create_task(run_previous())
        budget = max(1.0, s.s2_timeout_s - 1.0 - (perf_counter() - started))
        await asyncio.wait([*tasks, previous_task], timeout=budget)
        previous: S2SceneStats | None = None
        if previous_task.done() and not previous_task.cancelled() and previous_task.exception() is None:
            previous = previous_task.result()
        elif not previous_task.done():
            previous_task.cancel()

        outcomes: list[tuple[S2SceneStats | None, np.ndarray | None, _Grid | None] | None] = []
        timed_out: list[int] = []
        for i, task in enumerate(tasks):
            if task.done() and not task.cancelled() and task.exception() is None:
                outcomes.append(task.result())
            else:
                if not task.done():
                    task.cancel()
                    if i > 0:
                        timed_out.append(history_years[i - 1])
                outcomes.append(None)

        current, grid_arr, grid = outcomes[0] if outcomes[0] else (None, None, None)
        history: list[S2SceneStats] = []
        years_without: list[int] = []
        for year, res in zip(history_years, outcomes[1:], strict=True):
            if res is not None and res[0] is not None:
                history.append(res[0])
            elif year not in timed_out:
                years_without.append(year)

        series = S2ParcelSeries(
            current=current,
            previous=previous,
            current_ndmi_grid=grid_arr,
            grid_transform=tuple(grid.transform)[:6] if grid else None,  # type: ignore[arg-type]
            grid_crs=str(grid.crs) if grid else None,
            history=history,
            years_without_data=years_without,
            years_timed_out=timed_out,
        )
        ndmi_hist = [h.ndmi_mean for h in history if h.ndmi_mean is not None]
        if ndmi_hist:
            series.ndmi_history_mean = round(float(np.mean(ndmi_hist)), 4)
        if current and current.ndmi_mean is not None and len(ndmi_hist) >= 3:
            std = float(np.std(ndmi_hist, ddof=1))
            if std > 0.01:
                series.ndmi_anomaly_z = round((current.ndmi_mean - float(np.mean(ndmi_hist))) / std, 2)

        notes: list[str] = []
        if current is None:
            notes.append(
                f"Brak bezchmurnego zdjęcia działki w ostatnich {s.s2_current_window_days} dniach przed datą analizy."
            )
        if years_without:
            notes.append(
                "Brak bezchmurnych zdjęć z tego samego okresu w latach: "
                + ", ".join(str(y) for y in years_without)
                + " (w katalogu Earth Search dane dla Polski są od grudnia 2017 r.)."
            )
        if timed_out:
            notes.append(
                "Nie zdążono pobrać zdjęć z lat: " + ", ".join(str(y) for y in timed_out)
                + " – kolejne zapytanie użyje bufora."
            )
        series.note = " ".join(notes) or None

        if current is None and not history and previous is None:
            raise SourceUnavailableError(series.note or "Brak bezchmurnych zdjęć Sentinel-2.")
        observation = current.acquired.isoformat() if current else "brak aktualnego zdjęcia"
        return FetchOutcome(data=series, mode=DataMode.LIVE, observation_date=observation, message=series.note)