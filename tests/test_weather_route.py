"""GET /api/weather/daily: Open-Meteo JSON shape from the backend weather client (mocked, no network)."""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from backend.clients.base import SourceUnavailableError
from backend.clients.meteo import DailyWeatherSeries
from backend.main import app


class FakeWeather:
    timeout_s = 5.0

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[float, float, dt.date, dt.date]] = []

    async def daily_range(self, lat: float, lon: float, start: dt.date, end: dt.date) -> DailyWeatherSeries:
        self.calls.append((lat, lon, start, end))
        if self.fail:
            raise SourceUnavailableError("Brak danych pogodowych dla tej lokalizacji.")
        days = [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]
        return DailyWeatherSeries(
            dates=days,
            precipitation_mm=[1.5] * len(days),
            et0_mm=[2.0] * len(days),
            tmean_c=[15.0] * len(days),
            tmax_c=[20.0] * len(days),
            tmin_c=[10.0] * len(days),
            providers=["nasa-power"],
            grid_elevation_m=180.0,
        )


class FakeCollector:
    def __init__(self, weather: FakeWeather) -> None:
        self.clients = {"weather_history": weather}


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_weather_daily_returns_open_meteo_shape(client: TestClient) -> None:
    weather = FakeWeather()
    app.state.collector = FakeCollector(weather)
    r = client.get("/api/weather/daily", params={"lat": 50.75, "lon": 23.7, "start": "2019-09-01", "end": "2019-09-10"})
    assert r.status_code == 200, r.text
    assert r.headers["X-OM-Source"] == "nasa-power"
    body = r.json()
    daily = body["daily"]
    assert daily["time"][0] == "2019-09-01" and daily["time"][-1] == "2019-09-10"
    assert len(daily["precipitation_sum"]) == len(daily["et0_fao_evapotranspiration"]) == 10
    assert body["elevation"] == 180.0 and body["provider"] == "nasa-power"
    assert weather.calls == [(50.75, 23.7, dt.date(2019, 9, 1), dt.date(2019, 9, 10))]


def test_weather_daily_errors(client: TestClient) -> None:
    app.state.collector = FakeCollector(FakeWeather(fail=True))
    r = client.get("/api/weather/daily", params={"lat": 50.75, "lon": 23.7, "start": "2019-09-01", "end": "2019-09-10"})
    assert r.status_code == 503
    assert r.json()["error"] is True
    r = client.get("/api/weather/daily", params={"lat": 50.75, "lon": 23.7, "start": "2019-09-10", "end": "2019-09-01"})
    assert r.status_code == 422
