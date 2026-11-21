"""Data collection for one parcel and date: ULDK first, then all sources in parallel (hard 25 s limit).

Sources that do not finish in time are reported as 'brak danych' - the analysis continues
with what is available and the report shows the gap explicitly.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from time import perf_counter
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict

from backend.clients.base import BaseSourceClient, SourceResult
from backend.clients.bdot10k import Bdot10kClient, HydroNetworkContext
from backend.clients.cache import SourceCache
from backend.clients.imgw import ImgwStationClient, ImgwStationObservation
from backend.clients.iung_soil import IungSoilClient, IungSoilResult
from backend.clients.meteo import (
    DailyWeatherSeries,
    WeatherForecast,
    WeatherForecastClient,
    WeatherHistoryClient,
)
from backend.clients.nmt import DemRaster, NmtClient
from backend.clients.sentinel2 import S2ParcelSeries, Sentinel2Client
from backend.clients.soilgrids import SoilGridsClient, SoilGridsProfile
from backend.clients.uldk import UldkClient
from backend.config import Settings, get_settings
from hydro_core.schemas import (
    AnalysisRequest,
    IungSoilCategory,
    ParcelCandidate,
    ParcelGeometry,
    SourceAuditEntry,
    WeatherStationContext,
)

LAPSE_RATE_C_PER_M = 0.0065
SOIL_LAYER_IUNG = "IUNG-PIB: kategoria podatności gleby na suszę (mapa glebowo-rolnicza)"
SOIL_LAYER_SOILGRIDS = "SoilGrids 250 m: kategoria oszacowana z uziarnienia (przybliżenie)"
SOIL_LAYER_NONE = "brak danych glebowych"


class ParcelNotFoundError(Exception):
    """The parcel could not be resolved; the analysis cannot start."""


class ParcelDataBundle(BaseModel):
    """All source data for one parcel and analysis date, with audit entries and timings."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    parcel: ParcelGeometry
    analysis_date: date
    weather_history: DailyWeatherSeries | None
    weather_forecast: WeatherForecast | None
    weather_station: WeatherStationContext | None
    sentinel2: S2ParcelSeries | None
    dem: DemRaster | None
    soil_iung: IungSoilResult | None
    soil_grids: SoilGridsProfile | None
    soil_category: IungSoilCategory | None
    soil_source_layer: str
    hydro_network: HydroNetworkContext | None
    audit: list[SourceAuditEntry]
    total_time_s: float

    def timing_footer(self) -> str:
        """One-line footer for the report, e.g. 'ULDK 0,8 s · Sentinel-2 9,1 s'."""
        parts = []
        for entry in self.audit:
            if entry.fetch_time_s is None:
                continue
            parts.append(f"{entry.source_name.split(' (')[0]} {entry.fetch_time_s:.1f} s".replace(".", ","))
        return " · ".join(parts) + f" · razem {self.total_time_s:.1f} s".replace(".", ",")


def build_station_context(
    obs: ImgwStationObservation | None,
    parcel_elevation_m: float | None,
) -> WeatherStationContext | None:
    """Station distance, elevation difference and temperature corrected to the parcel elevation."""
    if obs is None:
        return None
    diff = None
    corrected = None
    if obs.elevation_m is not None and parcel_elevation_m is not None:
        diff = round(parcel_elevation_m - obs.elevation_m, 1)
        if obs.air_temp_c is not None:
            corrected = round(obs.air_temp_c - LAPSE_RATE_C_PER_M * diff, 1)
    return WeatherStationContext(
        station_code=obs.station_code,
        station_name=obs.station_name,
        distance_km=obs.distance_km,
        station_elevation_m=obs.elevation_m,
        parcel_elevation_m=parcel_elevation_m,
        elevation_diff_m=diff,
        observed_air_temp_c=obs.air_temp_c,
        observed_at=obs.air_temp_time,
        lapse_corrected_temp_c=corrected,
        is_far=obs.is_far,
    )


def choose_soil(
    iung: IungSoilResult | None, grids: SoilGridsProfile | None
) -> tuple[IungSoilCategory | None, str]:
    """Layered soil choice: IUNG category first, SoilGrids estimate second."""
    if iung is not None:
        return iung.category, SOIL_LAYER_IUNG
    if grids is not None:
        return grids.estimated_category, SOIL_LAYER_SOILGRIDS
    return None, SOIL_LAYER_NONE


class ParcelDataCollector:
    """Runs all data clients for one analysis request."""

    def __init__(self, http: httpx.AsyncClient, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.http = http
        self.cache = SourceCache(self.settings.cache_db_path)
        args = (self.http, self.cache, self.settings)
        self.uldk = UldkClient(*args)
        self.weather_history = WeatherHistoryClient(*args)
        self.weather_forecast = WeatherForecastClient(*args)
        self.imgw = ImgwStationClient(*args)
        self.sentinel2 = Sentinel2Client(*args)
        self.nmt = NmtClient(*args)
        self.iung = IungSoilClient(*args)
        self.soilgrids = SoilGridsClient(*args)
        self.bdot10k = Bdot10kClient(*args)

    async def search_parcels(self, query: str) -> list[ParcelCandidate]:
        """Precinct name + number search (may return parcels from several counties)."""
        return await self.uldk.search(query)

    async def collect(self, request: AnalysisRequest) -> ParcelDataBundle:
        """Resolve the parcel and fetch all sources within the global deadline."""
        started = perf_counter()
        deadline = started + self.settings.analysis_deadline_s

        parcel_res = await self.uldk.run(parcel_id=request.parcel_id, lat=request.lat, lon=request.lon)
        if parcel_res.data is None:
            raise ParcelNotFoundError(parcel_res.message or "Nie znaleziono działki.")
        parcel = parcel_res.data
        day = request.analysis_date
        point = {"lat": parcel.centroid_lat, "lon": parcel.centroid_lon}
        shape_args = {"geojson": parcel.geojson, "bbox_wgs84": parcel.bbox_wgs84}

        jobs: dict[str, tuple[BaseSourceClient[Any], dict[str, Any]]] = {
            "weather_history": (self.weather_history, {**point, "analysis_date": day}),
            "imgw": (self.imgw, {**point, "analysis_date": day}),
            "sentinel2": (
                self.sentinel2,
                {**shape_args, "analysis_date": day, "parcel_id": parcel.parcel_id},
            ),
            "nmt": (self.nmt, {**shape_args, "parcel_id": parcel.parcel_id}),
            "iung": (self.iung, {"parcel_id": parcel.parcel_id, "geojson": parcel.geojson}),
            "soilgrids": (self.soilgrids, point),
            "bdot10k": (self.bdot10k, {"parcel_id": parcel.parcel_id, "geojson": parcel.geojson}),
        }
        if day >= date.today() - timedelta(days=1):
            jobs["weather_forecast"] = (self.weather_forecast, point)

        tasks = {name: asyncio.create_task(client.run(**kw)) for name, (client, kw) in jobs.items()}
        remaining = max(0.5, deadline - perf_counter())
        await asyncio.wait(tasks.values(), timeout=remaining)

        results: dict[str, SourceResult[Any]] = {}
        for name, task in tasks.items():
            if task.done() and not task.cancelled():
                results[name] = task.result()
            else:
                task.cancel()
                results[name] = jobs[name][0].missing(
                    f"Przekroczono łączny limit analizy {self.settings.analysis_deadline_s:.0f} s.",
                    perf_counter() - started,
                )

        dem: DemRaster | None = results["nmt"].data
        history: DailyWeatherSeries | None = results["weather_history"].data
        parcel_elevation = dem.mean_elevation_m if dem else (history.grid_elevation_m if history else None)
        soil_category, soil_layer = choose_soil(results["iung"].data, results["soilgrids"].data)

        audit = [parcel_res.audit] + [results[name].audit for name in jobs]
        return ParcelDataBundle(
            parcel=parcel,
            analysis_date=day,
            weather_history=history,
            weather_forecast=results["weather_forecast"].data if "weather_forecast" in results else None,
            weather_station=build_station_context(results["imgw"].data, parcel_elevation),
            sentinel2=results["sentinel2"].data,
            dem=dem,
            soil_iung=results["iung"].data,
            soil_grids=results["soilgrids"].data,
            soil_category=soil_category,
            soil_source_layer=soil_layer,
            hydro_network=results["bdot10k"].data,
            audit=audit,
            total_time_s=round(perf_counter() - started, 2),
        )


def new_http_client(settings: Settings | None = None) -> httpx.AsyncClient:
    """Shared async HTTP client for the data collector (one per application)."""
    s = settings or get_settings()
    return httpx.AsyncClient(
        headers={"User-Agent": s.http_user_agent},
        follow_redirects=True,
        timeout=httpx.Timeout(10.0),
        limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
    )