"""IMGW-PIB telemetric station network: nearest station with a current air temperature reading.

Endpoint ``/api/data/meteo`` returns all stations in one JSON list (strings or null), e.g.::

    {"kod_stacji": "352220385", "nazwa_stacji": "SIEDLCE", "lon": "22.244722", "lat": "52.181111",
     "wysokosc_npm": "152", "temperatura_powietrza": "1.1",
     "temperatura_powietrza_data": "2026-11-21 17:10:00", ...}

Used only as a ground check of the weather model grid for analyses of the current day
(the 10-year history comes from Open-Meteo). The station list is cached for 10 minutes.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from pydantic import BaseModel

from backend.clients.base import (
    BaseSourceClient,
    FetchOutcome,
    SourceDescriptor,
    SourceUnavailableError,
)
from backend.clients.geo import haversine_km
from hydro_core.schemas import DataMode

_CACHE_KEY = "imgw:meteo:stations"
_CACHE_TTL_S = 600


class ImgwStationObservation(BaseModel):
    """Nearest IMGW-PIB station with a valid air temperature."""

    station_code: str
    station_name: str
    lat: float
    lon: float
    distance_km: float
    elevation_m: float | None
    air_temp_c: float | None
    air_temp_time: str | None
    relative_humidity_pct: float | None = None
    is_far: bool


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def nearest_station(
    stations: list[dict[str, Any]], lat: float, lon: float, far_km: float
) -> ImgwStationObservation | None:
    """Nearest station that reports air temperature; ``is_far`` when beyond ``far_km``."""
    best: tuple[float, dict[str, Any]] | None = None
    for st in stations:
        s_lat, s_lon = _num(st.get("lat")), _num(st.get("lon"))
        if s_lat is None or s_lon is None or _num(st.get("temperatura_powietrza")) is None:
            continue
        dist = haversine_km(lat, lon, s_lat, s_lon)
        if best is None or dist < best[0]:
            best = (dist, st)
    if best is None:
        return None
    dist, st = best
    return ImgwStationObservation(
        station_code=str(st.get("kod_stacji", "")),
        station_name=str(st.get("nazwa_stacji", "")).title(),
        lat=float(st["lat"]),
        lon=float(st["lon"]),
        distance_km=round(dist, 1),
        elevation_m=_num(st.get("wysokosc_npm")),
        air_temp_c=_num(st.get("temperatura_powietrza")),
        air_temp_time=st.get("temperatura_powietrza_data"),
        relative_humidity_pct=_num(st.get("wilgotnosc_wzgledna")),
        is_far=dist > far_km,
    )


class ImgwStationClient(BaseSourceClient[ImgwStationObservation]):
    """Current observation from the nearest IMGW-PIB station (only for recent analysis dates)."""

    descriptor = SourceDescriptor(
        key="imgw",
        category="meteorologiczne",
        source_name="IMGW-PIB – dane telemetryczne stacji (API danych publicznych)",
        endpoint="https://danepubliczne.imgw.pl/api/data/meteo",
        spatial_resolution="stacja punktowa",
        license="Dane publiczne IMGW-PIB (regulamin serwisu danepubliczne.imgw.pl)",
        attribution="Źródło: Instytut Meteorologii i Gospodarki Wodnej – Państwowy Instytut Badawczy",
    )

    @property
    def timeout_s(self) -> float:
        return self.settings.imgw_timeout_s

    async def _fetch(self, **kwargs: Any) -> FetchOutcome[ImgwStationObservation]:
        lat: float = kwargs["lat"]
        lon: float = kwargs["lon"]
        analysis_date: date = kwargs["analysis_date"]
        if analysis_date < date.today() - timedelta(days=1):
            raise SourceUnavailableError(
                "IMGW udostępnia tylko bieżące pomiary – dla daty historycznej pominięto."
            )
        stations = await self.cache.get(_CACHE_KEY)
        mode = DataMode.PRE_FETCHED
        if not stations:
            resp = await self.http.get(self.settings.imgw_meteo_url, timeout=self.settings.imgw_timeout_s)
            resp.raise_for_status()
            stations = resp.json()
            if not isinstance(stations, list):
                raise SourceUnavailableError("IMGW zwrócił nieoczekiwany format danych.")
            await self.cache.set(_CACHE_KEY, stations, _CACHE_TTL_S)
            mode = DataMode.LIVE
        obs = nearest_station(stations, lat, lon, self.settings.imgw_far_station_km)
        if obs is None:
            raise SourceUnavailableError("Żadna stacja IMGW nie raportuje teraz temperatury powietrza.")
        message = None
        if obs.is_far:
            message = (
                f"Najbliższa stacja IMGW ({obs.station_name}) jest {obs.distance_km:.0f} km od działki – "
                "pomiar ma charakter orientacyjny."
            )
        return FetchOutcome(
            data=obs,
            mode=mode,
            observation_date=obs.air_temp_time or "pomiar bieżący",
            message=message,
        )
