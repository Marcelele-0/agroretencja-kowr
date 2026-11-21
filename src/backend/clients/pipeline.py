"""Data collection for one parcel and date: ULDK first, then all sources in parallel (hard 25 s limit).

Sources come from ``backend.clients.registry.SOURCES``; a source that does not finish in time is
reported as 'brak danych' - the analysis continues with what is available and the report
shows the gap explicitly.
"""

from __future__ import annotations

import asyncio
from datetime import date
from time import perf_counter
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict

from backend.clients.base import BaseSourceClient, SourceResult
from backend.clients.bdot10k import HydroNetworkContext
from backend.clients.cache import SourceCache
from backend.clients.imgw import ImgwStationObservation
from backend.clients.iung_soil import IungSoilResult, NonAgriculturalParcelError
from backend.clients.meteo import DailyWeatherSeries, WeatherForecast
from backend.clients.nmt import DemRaster
from backend.clients.registry import (
    SOIL_LAYER_IUNG,
    SOIL_LAYER_NONE,
    SOIL_LAYER_SOILGRIDS,
    SOURCES,
    SourceRequest,
    resolve_soil,
)
from backend.clients.sentinel2 import S2ParcelSeries
from backend.clients.soilgrids import SoilGridsProfile
from backend.clients.uldk import UldkClient, UldkTransientError
from backend.config import Settings, get_settings
from hydro_core.schemas import (
    AnalysisRequest,
    IungSoilCategory,
    ParcelCandidate,
    ParcelGeometry,
    SourceAuditEntry,
    WeatherStationContext,
)

__all__ = [
    "SOIL_LAYER_IUNG",
    "SOIL_LAYER_NONE",
    "SOIL_LAYER_SOILGRIDS",
    "ParcelDataBundle",
    "ParcelDataCollector",
    "ParcelNotFoundError",
    "build_station_context",
    "choose_soil",
    "new_http_client",
]

LAPSE_RATE_C_PER_M = 0.0065


class ParcelNotFoundError(Exception):
    """The parcel could not be resolved; the analysis cannot start.

    ``retryable`` = ULDK failed (timeout, HTTP error, corrupt answer), not "no such parcel".
    """

    def __init__(self, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


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
    non_agricultural: list[str] | None = None
    results: dict[str, Any] = {}

    def data(self, key: str) -> Any | None:
        """Data of any registered source (also ones added after this class was written)."""
        res = self.results.get(key)
        return res.data if res is not None else None

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


class _Stub:
    def __init__(self, data: Any) -> None:
        self.data = data


def choose_soil(
    iung: IungSoilResult | None, grids: SoilGridsProfile | None
) -> tuple[IungSoilCategory | None, str]:
    """Layered soil choice (kept for callers/tests): delegates to the SOIL_PROVIDERS chain."""
    return resolve_soil({"iung": _Stub(iung), "soilgrids": _Stub(grids)})  # type: ignore[dict-item]


class ParcelDataCollector:
    """Runs all registered data sources for one analysis request."""

    def __init__(self, http: httpx.AsyncClient, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.http = http
        self.cache = SourceCache(self.settings.cache_db_path)
        args = (self.http, self.cache, self.settings)
        self.uldk = UldkClient(*args)
        self.clients: dict[str, BaseSourceClient[Any]] = {
            key: spec.client_cls(*args) for key, spec in SOURCES.items()
        }

    async def search_parcels(self, query: str) -> list[ParcelCandidate]:
        """Precinct name + number search (may return parcels from several counties)."""
        return await self.uldk.search(query)

    async def resolve_parcel(
        self, parcel_id: str | None = None, lat: float | None = None, lon: float | None = None
    ) -> tuple[ParcelGeometry, SourceAuditEntry]:
        """Fast first step (<= 2 s): parcel geometry and administrative data only."""
        res = await self.uldk.run(parcel_id=parcel_id, lat=lat, lon=lon)
        if res.data is None:
            retryable = res.error is None or isinstance(res.error, UldkTransientError)
            raise ParcelNotFoundError(res.message or "Nie znaleziono działki.", retryable=retryable)
        return res.data, res.audit

    async def collect(self, request: AnalysisRequest, today: date | None = None) -> ParcelDataBundle:
        """Resolve the parcel and fetch all sources within the global deadline."""
        started = perf_counter()
        deadline = started + self.settings.analysis_deadline_s
        parcel, parcel_audit = await self.resolve_parcel(request.parcel_id, request.lat, request.lon)
        src_req = SourceRequest(parcel=parcel, analysis_date=request.analysis_date, today=today or date.today())

        jobs: dict[str, dict[str, Any]] = {}
        for key, spec in SOURCES.items():
            kwargs = spec.build_kwargs(src_req)
            if kwargs is not None:
                jobs[key] = kwargs
        tasks = {key: asyncio.create_task(self.clients[key].run(**kw)) for key, kw in jobs.items()}
        remaining = max(0.5, deadline - perf_counter())
        if tasks:
            await asyncio.wait(tasks.values(), timeout=remaining)

        results: dict[str, SourceResult[Any]] = {}
        for key, task in tasks.items():
            if task.done() and not task.cancelled():
                results[key] = task.result()
            else:
                task.cancel()
                results[key] = self.clients[key].missing(
                    f"Przekroczono łączny limit analizy {self.settings.analysis_deadline_s:.0f} s.",
                    perf_counter() - started,
                )

        def data(key: str) -> Any | None:
            res = results.get(key)
            return res.data if res is not None else None

        dem: DemRaster | None = data("nmt")
        history: DailyWeatherSeries | None = data("weather_history")
        parcel_elevation = dem.mean_elevation_m if dem else (history.grid_elevation_m if history else None)
        soil_category, soil_layer = resolve_soil(results)
        iung_res = results.get("iung")
        non_ag = (
            iung_res.error.land_cover
            if iung_res is not None and isinstance(iung_res.error, NonAgriculturalParcelError)
            else None
        )

        return ParcelDataBundle(
            parcel=parcel,
            analysis_date=request.analysis_date,
            weather_history=history,
            weather_forecast=data("weather_forecast"),
            weather_station=build_station_context(data("imgw"), parcel_elevation),
            sentinel2=data("sentinel2"),
            dem=dem,
            soil_iung=data("iung"),
            soil_grids=data("soilgrids"),
            soil_category=soil_category,
            soil_source_layer=soil_layer,
            hydro_network=data("bdot10k"),
            audit=[parcel_audit] + [results[key].audit for key in jobs],
            total_time_s=round(perf_counter() - started, 2),
            non_agricultural=non_ag,
            results=results,
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
