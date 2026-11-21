"""Weather: 10-year daily history (Open-Meteo, fallback NASA POWER) and ECMWF IFS forecast.

Open-Meteo counts a 10-year request as many API calls, so history is cached per grid cell
and calendar year: past years never expire, the current year expires after 6 hours.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import httpx
from pydantic import BaseModel

from backend.clients.base import (
    BaseSourceClient,
    FetchOutcome,
    SourceDescriptor,
    SourceUnavailableError,
)
from hydro_core.et0 import fao56_et0_daily
from hydro_core.schemas import DataMode

_DAILY_VARS = (
    "precipitation_sum",
    "et0_fao_evapotranspiration",
    "temperature_2m_mean",
    "temperature_2m_max",
    "temperature_2m_min",
)
_POWER_PARAMS = "PRECTOTCORR,T2M,T2M_MAX,T2M_MIN,ALLSKY_SFC_SW_DWN,RH2M,WS2M"
_CURRENT_YEAR_TTL_S = 6 * 3600
_FALLBACK_TTL_S = 24 * 3600
_FORECAST_TTL_S = 3600

OPEN_METEO = "open-meteo"
NASA_POWER = "nasa-power"


class RateLimitedError(Exception):
    """Open-Meteo returned HTTP 429 (daily limit exceeded)."""


class DailyWeatherSeries(BaseModel):
    """Daily weather series for the parcel location (oldest first)."""

    dates: list[date]
    precipitation_mm: list[float | None]
    et0_mm: list[float | None]
    tmean_c: list[float | None]
    tmax_c: list[float | None]
    tmin_c: list[float | None]
    providers: list[str]
    grid_elevation_m: float | None = None


class WeatherForecast(BaseModel):
    """ECMWF IFS forecast: daily for up to 15 days and hourly ET0 for the next 3 days."""

    model: str
    dates: list[date]
    precipitation_mm: list[float | None]
    et0_mm: list[float | None]
    tmax_c: list[float | None]
    tmin_c: list[float | None]
    hourly_time: list[str]
    hourly_et0_mm: list[float | None]
    hourly_precipitation_mm: list[float | None]


def grid_cell_key(lat: float, lon: float) -> str:
    """Round coordinates to a 0.05 degree cell (finer than the ~9-11 km model grid)."""
    return f"{round(lat * 20) / 20:.2f}_{round(lon * 20) / 20:.2f}"


def _empty_block() -> dict[str, list[Any]]:
    return {"time": [], **{v: [] for v in _DAILY_VARS}}


def split_by_year(block: dict[str, list[Any]]) -> dict[int, dict[str, list[Any]]]:
    """Split a column-oriented daily block into per-year blocks."""
    out: dict[int, dict[str, list[Any]]] = {}
    for i, day in enumerate(block["time"]):
        year = int(str(day)[:4])
        target = out.setdefault(year, _empty_block())
        target["time"].append(str(day))
        for var in _DAILY_VARS:
            target[var].append(block[var][i])
    return out


def merge_blocks(first: dict[str, list[Any]], second: dict[str, list[Any]]) -> dict[str, list[Any]]:
    """Append ``second`` to ``first`` skipping days already present."""
    seen = set(first["time"])
    merged = {k: list(v) for k, v in first.items()}
    for i, day in enumerate(second["time"]):
        if day in seen:
            continue
        merged["time"].append(day)
        for var in _DAILY_VARS:
            merged[var].append(second[var][i])
    return merged


def power_to_block(payload: dict[str, Any], lat: float) -> tuple[dict[str, list[Any]], float | None]:
    """Convert NASA POWER daily JSON into a block and compute FAO-56 ET0."""
    params = payload["properties"]["parameter"]
    fill = payload.get("header", {}).get("fill_value", -999.0)
    coords = payload.get("geometry", {}).get("coordinates", [None, None, None])
    elevation = float(coords[2]) if len(coords) > 2 and coords[2] is not None else 100.0

    def val(name: str, key: str) -> float | None:
        v = params.get(name, {}).get(key)
        return None if v is None or v == fill else float(v)

    block = _empty_block()
    for key in sorted(params["PRECTOTCORR"].keys()):
        day = date(int(key[:4]), int(key[4:6]), int(key[6:8]))
        tmax, tmin, tmean = val("T2M_MAX", key), val("T2M_MIN", key), val("T2M", key)
        rh, ws, rs = val("RH2M", key), val("WS2M", key), val("ALLSKY_SFC_SW_DWN", key)
        et0 = None
        if None not in (tmax, tmin, rh, ws, rs):
            et0 = round(fao56_et0_daily(day, lat, elevation, tmax, tmin, rh, ws, rs), 2)  # type: ignore[arg-type]
        block["time"].append(day.isoformat())
        block["precipitation_sum"].append(val("PRECTOTCORR", key))
        block["et0_fao_evapotranspiration"].append(et0)
        block["temperature_2m_mean"].append(tmean)
        block["temperature_2m_max"].append(tmax)
        block["temperature_2m_min"].append(tmin)
    return block, elevation


class WeatherHistoryClient(BaseSourceClient[DailyWeatherSeries]):
    """10 full seasons + current year up to the analysis date."""

    descriptor = SourceDescriptor(
        key="weather_history",
        category="meteorologiczne",
        source_name="Open-Meteo Historical Weather API (ERA5 / ERA5-Land, ECMWF IFS)",
        endpoint="https://archive-api.open-meteo.com/v1/archive",
        spatial_resolution="siatka ok. 9–11 km",
        license="CC BY 4.0; darmowe API do użytku niekomercyjnego (limit 10 000 zapytań/dobę)",
        attribution="Weather data by Open-Meteo.com (CC BY 4.0)",
    )

    @property
    def timeout_s(self) -> float:
        return self.settings.weather_history_timeout_s

    def _om_params(self) -> dict[str, str]:
        params = {"timezone": "Europe/Warsaw", "daily": ",".join(_DAILY_VARS)}
        if self.settings.open_meteo_api_key is not None:
            params["apikey"] = self.settings.open_meteo_api_key.get_secret_value()
        return params

    async def _open_meteo_range(
        self, lat: float, lon: float, start: date, end: date
    ) -> tuple[dict[str, list[Any]], float | None]:
        """Archive API up to (today - delay), forecast API for the most recent days."""
        today = date.today()
        archive_end = min(end, today - timedelta(days=self.settings.open_meteo_archive_delay_days))
        block = _empty_block()
        elevation: float | None = None
        base = {"latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}", **self._om_params()}

        if start <= archive_end:
            resp = await self.http.get(
                self.settings.open_meteo_archive_url,
                params={**base, "start_date": start.isoformat(), "end_date": archive_end.isoformat()},
                timeout=self.settings.open_meteo_timeout_s,
            )
            if resp.status_code == 429:
                raise RateLimitedError(resp.text[:200])
            resp.raise_for_status()
            payload = resp.json()
            elevation = payload.get("elevation")
            block = merge_blocks(block, {"time": payload["daily"]["time"], **{v: payload["daily"][v] for v in _DAILY_VARS}})

        recent_start = max(start, archive_end + timedelta(days=1))
        recent_end = min(end, today)
        if recent_start <= recent_end:
            resp = await self.http.get(
                self.settings.open_meteo_forecast_url,
                params={**base, "start_date": recent_start.isoformat(), "end_date": recent_end.isoformat()},
                timeout=self.settings.open_meteo_timeout_s,
            )
            if resp.status_code == 429:
                raise RateLimitedError(resp.text[:200])
            resp.raise_for_status()
            payload = resp.json()
            elevation = elevation if elevation is not None else payload.get("elevation")
            block = merge_blocks(block, {"time": payload["daily"]["time"], **{v: payload["daily"][v] for v in _DAILY_VARS}})
        return block, elevation

    async def _nasa_power_range(
        self, lat: float, lon: float, start: date, end: date
    ) -> tuple[dict[str, list[Any]], float | None]:
        resp = await self.http.get(
            self.settings.nasa_power_url,
            params={
                "parameters": _POWER_PARAMS,
                "community": "AG",
                "latitude": f"{lat:.4f}",
                "longitude": f"{lon:.4f}",
                "start": start.strftime("%Y%m%d"),
                "end": end.strftime("%Y%m%d"),
                "format": "JSON",
            },
            timeout=self.settings.nasa_power_timeout_s,
        )
        resp.raise_for_status()
        return power_to_block(resp.json(), lat)

    async def _fetch(self, **kwargs: Any) -> FetchOutcome[DailyWeatherSeries]:
        lat: float = kwargs["lat"]
        lon: float = kwargs["lon"]
        analysis_date: date = kwargs["analysis_date"]
        today = date.today()
        if analysis_date > today:
            raise SourceUnavailableError("Data analizy jest z przyszłości.")

        cell = grid_cell_key(lat, lon)
        years = list(range(analysis_date.year - 10, analysis_date.year + 1))
        blocks: dict[int, dict[str, Any]] = {}
        for year in years:
            cached = await self.cache.get(f"meteo:{cell}:{year}")
            if cached:
                blocks[year] = cached

        missing = [y for y in years if y not in blocks]
        message: str | None = None
        if missing:
            start = date(min(missing), 1, 1)
            end = min(date(max(missing), 12, 31), today)
            provider = OPEN_METEO
            try:
                block, elevation = await self._open_meteo_range(lat, lon, start, end)
            except (RateLimitedError, httpx.HTTPError) as exc:
                provider = NASA_POWER
                reason = "limit zapytań" if isinstance(exc, RateLimitedError) else "brak odpowiedzi"
                message = (
                    f"Open-Meteo: {reason} – historię pogody pobrano z NASA POWER "
                    "(siatka 0,5°, ET₀ policzone wzorem FAO-56)."
                )
                block, elevation = await self._nasa_power_range(lat, lon, start, end)

            for year, year_block in split_by_year(block).items():
                if year not in missing:
                    continue
                complete_year = year < today.year
                ttl = None if (complete_year and provider == OPEN_METEO) else (
                    _FALLBACK_TTL_S if provider == NASA_POWER else _CURRENT_YEAR_TTL_S
                )
                stored = {**year_block, "provider": provider, "elevation": elevation}
                await self.cache.set(f"meteo:{cell}:{year}", stored, ttl)
                blocks[year] = stored

        series = self._assemble(blocks, years, analysis_date)
        if not series.dates:
            raise SourceUnavailableError("Brak danych pogodowych dla tej lokalizacji.")
        mode = DataMode.FALLBACK if NASA_POWER in series.providers else (
            DataMode.LIVE if missing else DataMode.PRE_FETCHED
        )
        outcome_kwargs: dict[str, Any] = {}
        if NASA_POWER in series.providers:
            message = message or "Część historii pogody pochodzi z NASA POWER (zapas za Open-Meteo)."
            outcome_kwargs = {
                "source_name": "Open-Meteo + NASA POWER (zapas)",
                "spatial_resolution": "siatka 9–11 km / 0,5° (NASA POWER)",
                "license": "Open-Meteo CC BY 4.0; NASA POWER – dane publiczne NASA",
                "attribution": "Weather data by Open-Meteo.com (CC BY 4.0); NASA POWER Project",
            }
        return FetchOutcome(
            data=series,
            mode=mode,
            observation_date=f"{series.dates[0].isoformat()} – {series.dates[-1].isoformat()}",
            message=message,
            **outcome_kwargs,
        )

    @staticmethod
    def _assemble(
        blocks: dict[int, dict[str, Any]], years: list[int], analysis_date: date
    ) -> DailyWeatherSeries:
        dates: list[date] = []
        cols: dict[str, list[float | None]] = {v: [] for v in _DAILY_VARS}
        providers: list[str] = []
        elevation: float | None = None
        for year in years:
            block = blocks.get(year)
            if not block:
                continue
            if block.get("provider") and block["provider"] not in providers:
                providers.append(block["provider"])
            elevation = elevation if elevation is not None else block.get("elevation")
            for i, day_text in enumerate(block["time"]):
                day = date.fromisoformat(day_text)
                if day > analysis_date:
                    break
                dates.append(day)
                for var in _DAILY_VARS:
                    cols[var].append(block[var][i])
        return DailyWeatherSeries(
            dates=dates,
            precipitation_mm=cols["precipitation_sum"],
            et0_mm=cols["et0_fao_evapotranspiration"],
            tmean_c=cols["temperature_2m_mean"],
            tmax_c=cols["temperature_2m_max"],
            tmin_c=cols["temperature_2m_min"],
            providers=providers,
            grid_elevation_m=elevation,
        )


class WeatherForecastClient(BaseSourceClient[WeatherForecast]):
    """ECMWF IFS forecast from Open-Meteo for the 'Plan dnia' (only for today's analyses)."""

    descriptor = SourceDescriptor(
        key="weather_forecast",
        category="meteorologiczne",
        source_name="Open-Meteo Forecast API – model ECMWF IFS",
        endpoint="https://api.open-meteo.com/v1/forecast",
        spatial_resolution="siatka ok. 9 km",
        license="CC BY 4.0; darmowe API do użytku niekomercyjnego",
        attribution="Weather data by Open-Meteo.com (CC BY 4.0), model ECMWF IFS",
    )

    @property
    def timeout_s(self) -> float:
        return self.settings.open_meteo_timeout_s

    async def _fetch(self, **kwargs: Any) -> FetchOutcome[WeatherForecast]:
        lat: float = kwargs["lat"]
        lon: float = kwargs["lon"]
        cache_key = f"forecast:{grid_cell_key(lat, lon)}:{date.today().isoformat()}"
        cached = await self.cache.get(cache_key)
        if cached:
            data = WeatherForecast.model_validate(cached)
            return FetchOutcome(data=data, mode=DataMode.PRE_FETCHED, observation_date=f"prognoza od {data.dates[0]}")

        params = {
            "latitude": f"{lat:.4f}",
            "longitude": f"{lon:.4f}",
            "timezone": "Europe/Warsaw",
            "models": self.settings.open_meteo_forecast_model,
            "daily": "precipitation_sum,et0_fao_evapotranspiration,temperature_2m_max,temperature_2m_min",
            "hourly": "et0_fao_evapotranspiration,precipitation",
            "forecast_days": "15",
        }
        if self.settings.open_meteo_api_key is not None:
            params["apikey"] = self.settings.open_meteo_api_key.get_secret_value()
        resp = await self.http.get(
            self.settings.open_meteo_forecast_url, params=params, timeout=self.settings.open_meteo_timeout_s
        )
        if resp.status_code == 429:
            raise SourceUnavailableError("Open-Meteo: przekroczono dzienny limit zapytań.")
        resp.raise_for_status()
        payload = resp.json()
        daily, hourly = payload["daily"], payload["hourly"]
        hours = 72
        data = WeatherForecast(
            model=self.settings.open_meteo_forecast_model,
            dates=[date.fromisoformat(d) for d in daily["time"]],
            precipitation_mm=daily["precipitation_sum"],
            et0_mm=daily["et0_fao_evapotranspiration"],
            tmax_c=daily["temperature_2m_max"],
            tmin_c=daily["temperature_2m_min"],
            hourly_time=hourly["time"][:hours],
            hourly_et0_mm=hourly["et0_fao_evapotranspiration"][:hours],
            hourly_precipitation_mm=hourly["precipitation"][:hours],
        )
        await self.cache.set(cache_key, data.model_dump(mode="json"), _FORECAST_TTL_S)
        return FetchOutcome(data=data, mode=DataMode.LIVE, observation_date=f"prognoza od {data.dates[0]}")