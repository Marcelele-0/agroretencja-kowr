"""Registry of data sources fetched in parallel after the parcel geometry is known.

Adding a source = one ``SOURCES.add(SourceSpec(...))`` entry: a client class (subclass of
``BaseSourceClient``) and a function that builds its keyword arguments from the parcel and
the analysis date (``None`` = skip the source for this request, e.g. forecast for old dates).
``ParcelDataCollector`` runs every registered source; results land in ``bundle.results[key]``.

Soil category uses a chain of responsibility (``SOIL_PROVIDERS``): the first provider that
returns a category wins (IUNG map first, SoilGrids texture estimate second).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from backend.clients.base import BaseSourceClient, SourceResult
from backend.clients.bdot10k import Bdot10kClient
from backend.clients.imgw import ImgwStationClient
from backend.clients.iung_soil import IungSoilClient, IungSoilResult
from backend.clients.meteo import WeatherForecastClient, WeatherHistoryClient
from backend.clients.nmt import NmtClient
from backend.clients.sentinel2 import Sentinel2Client
from backend.clients.soilgrids import SoilGridsClient, SoilGridsProfile
from hydro_core.registry import Registry
from hydro_core.schemas import IungSoilCategory, ParcelGeometry

SOIL_LAYER_IUNG = "IUNG-PIB: kategoria podatności gleby na suszę (mapa glebowo-rolnicza)"
SOIL_LAYER_SOILGRIDS = "SoilGrids 250 m: kategoria oszacowana z uziarnienia (przybliżenie)"
SOIL_LAYER_NONE = "brak danych glebowych"


@dataclass(frozen=True)
class SourceRequest:
    """What a source may use to build its query."""

    parcel: ParcelGeometry
    analysis_date: date
    today: date

    @property
    def point(self) -> dict[str, float]:
        return {"lat": self.parcel.centroid_lat, "lon": self.parcel.centroid_lon}

    @property
    def shape(self) -> dict[str, Any]:
        return {"geojson": self.parcel.geojson, "bbox_wgs84": self.parcel.bbox_wgs84}


@dataclass(frozen=True)
class SourceSpec:
    """One registered data source."""

    key: str
    client_cls: type[BaseSourceClient[Any]]
    build_kwargs: Callable[[SourceRequest], dict[str, Any] | None]


SOURCES: Registry[SourceSpec] = Registry("źródła danych")


def _register(key: str, client_cls: type[BaseSourceClient[Any]]):
    def deco(fn: Callable[[SourceRequest], dict[str, Any] | None]) -> Callable[..., Any]:
        SOURCES.add(key, SourceSpec(key, client_cls, fn))
        return fn

    return deco


@_register("weather_history", WeatherHistoryClient)
def _weather_history(r: SourceRequest) -> dict[str, Any]:
    return {**r.point, "analysis_date": r.analysis_date}


@_register("weather_forecast", WeatherForecastClient)
def _weather_forecast(r: SourceRequest) -> dict[str, Any] | None:
    # Forecast is only meaningful when the analysis date is (almost) today; for older dates the
    # unified timeline projects with climatology (as-of principle, no look-ahead).
    return r.point if r.analysis_date >= r.today - timedelta(days=1) else None


@_register("imgw", ImgwStationClient)
def _imgw(r: SourceRequest) -> dict[str, Any] | None:
    return {**r.point, "analysis_date": r.analysis_date} if r.analysis_date >= r.today - timedelta(days=1) else None


@_register("sentinel2", Sentinel2Client)
def _sentinel2(r: SourceRequest) -> dict[str, Any]:
    return {**r.shape, "analysis_date": r.analysis_date, "parcel_id": r.parcel.parcel_id}


@_register("nmt", NmtClient)
def _nmt(r: SourceRequest) -> dict[str, Any]:
    return {**r.shape, "parcel_id": r.parcel.parcel_id}


@_register("iung", IungSoilClient)
def _iung(r: SourceRequest) -> dict[str, Any]:
    return {"parcel_id": r.parcel.parcel_id, "geojson": r.parcel.geojson}


@_register("soilgrids", SoilGridsClient)
def _soilgrids(r: SourceRequest) -> dict[str, Any]:
    return r.point


@_register("bdot10k", Bdot10kClient)
def _bdot10k(r: SourceRequest) -> dict[str, Any]:
    return {"parcel_id": r.parcel.parcel_id, "geojson": r.parcel.geojson}


# --------------------------------------------------------------------------------------
# Soil category: chain of responsibility
# --------------------------------------------------------------------------------------
SoilProvider = Callable[[dict[str, SourceResult[Any]]], tuple[IungSoilCategory, str] | None]
SOIL_PROVIDERS: Registry[SoilProvider] = Registry("dostawcy kategorii gleby")


@SOIL_PROVIDERS.register("iung")
def _soil_from_iung(results: dict[str, SourceResult[Any]]) -> tuple[IungSoilCategory, str] | None:
    res = results.get("iung")
    data: IungSoilResult | None = res.data if res else None
    return (data.category, SOIL_LAYER_IUNG) if data is not None else None


@SOIL_PROVIDERS.register("soilgrids")
def _soil_from_soilgrids(results: dict[str, SourceResult[Any]]) -> tuple[IungSoilCategory, str] | None:
    res = results.get("soilgrids")
    data: SoilGridsProfile | None = res.data if res else None
    return (data.estimated_category, SOIL_LAYER_SOILGRIDS) if data is not None else None


def resolve_soil(results: dict[str, SourceResult[Any]]) -> tuple[IungSoilCategory | None, str]:
    """First provider in ``SOIL_PROVIDERS`` order that knows the category."""
    for provider in SOIL_PROVIDERS.values():
        found = provider(results)
        if found is not None:
            return found
    return None, SOIL_LAYER_NONE
