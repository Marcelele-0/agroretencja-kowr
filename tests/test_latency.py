"""Latency rules of the data collector: deferred sources, SoilGrids no-data cache, S2 grid prediction."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

import httpx
import numpy as np
import pytest
from affine import Affine
from rasterio.crs import CRS
from rasterio.windows import transform as window_transform
from synthetic_data import PARCEL_GEOJSON

from backend.clients import sentinel2 as s2
from backend.clients.base import (
    BaseSourceClient,
    FetchOutcome,
    SourceDescriptor,
    SourceUnavailableError,
)
from backend.clients.cache import SourceCache
from backend.clients.iung_soil import classify_samples
from backend.clients.pipeline import DEFERRED_MESSAGE, ParcelDataCollector
from backend.clients.soilgrids import NO_DATA_MESSAGE, SoilGridsClient, SoilGridsProfile
from backend.config import Settings
from hydro_core.schemas import (
    AnalysisRequest,
    CropCode,
    DataMode,
    IungSoilCategory,
    ParcelGeometry,
    SourceAuditEntry,
)

REQUEST = AnalysisRequest(parcel_id="302111_5.0015.72", crop=CropCode.KUKURYDZA, analysis_date=date(2024, 7, 1))
PROFILE = SoilGridsProfile(
    layers=[], awc_mm_0_100=150.0, estimated_category=IungSoilCategory.II, topsoil_soc_g_kg=12.0, topsoil_sand_pct=60.0
)
PARCEL = ParcelGeometry(
    parcel_id="302111_5.0015.72", centroid_lat=52.6015, centroid_lon=16.9925, area_ha=12.0,
    bbox_wgs84=(16.99, 52.60, 16.995, 52.603), geojson=PARCEL_GEOJSON,
)


class _FakeClient(BaseSourceClient[Any]):
    """Source that answers after ``delay_s`` with ``data`` (None = 'brak danych')."""

    def __init__(self, key: str, delay_s: float, data: Any, settings: Settings) -> None:
        super().__init__(None, None, settings)  # type: ignore[arg-type]
        self.descriptor = SourceDescriptor(key, "glebowe", key, "test", "-", "-")  # type: ignore[misc]
        self.delay_s, self.value = delay_s, data

    @property
    def timeout_s(self) -> float:
        return 10.0

    async def _fetch(self, **kwargs: Any) -> FetchOutcome[Any]:
        await asyncio.sleep(self.delay_s)
        if self.value is None:
            raise SourceUnavailableError("brak")
        return FetchOutcome(data=self.value)


def _collector(tmp_path: Path, iung_ok: bool) -> ParcelDataCollector:
    """Collector with fake sources: SoilGrids answers after 1.5 s, IUNG fast (or without data)."""
    settings = Settings(cache_dir=tmp_path)
    col = ParcelDataCollector(httpx.AsyncClient(), settings)
    audit = SourceAuditEntry(
        category="przestrzenne", source_name="ULDK", endpoint_or_ref="test", mode=DataMode.LIVE,
        retrieved_at=datetime.now(UTC), observation_date="-", spatial_resolution="-", license="-",
    )

    async def resolve(*_: Any, **__: Any) -> tuple[ParcelGeometry, SourceAuditEntry]:
        return PARCEL, audit

    col.resolve_parcel = resolve  # type: ignore[method-assign]
    col.clients = {key: _FakeClient(key, 0.01, None, settings) for key in col.clients}
    col.clients["soilgrids"] = _FakeClient("soilgrids", 1.5, PROFILE, settings)
    iung = classify_samples([2, 2, 2]) if iung_ok else None
    col.clients["iung"] = _FakeClient("iung", 0.05, iung, settings)
    return col


async def test_soilgrids_is_deferred_when_iung_gives_the_category(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("backend.clients.registry.load_hydrofusion", lambda: None)
    col = _collector(tmp_path, iung_ok=True)
    started = perf_counter()
    bundle = await col.collect(REQUEST)
    assert perf_counter() - started < 1.0
    res = bundle.results["soilgrids"]
    assert res.mode == DataMode.MISSING and res.message == DEFERRED_MESSAGE
    assert bundle.soil_category == IungSoilCategory.II and bundle.soil_grids is None


async def test_soilgrids_is_awaited_when_iung_has_no_data(tmp_path: Path) -> None:
    col = _collector(tmp_path, iung_ok=False)
    started = perf_counter()
    bundle = await col.collect(REQUEST)
    assert perf_counter() - started >= 1.4
    assert bundle.soil_grids == PROFILE and bundle.soil_category == IungSoilCategory.II


async def test_soilgrids_no_data_point_is_not_requested_again(tmp_path: Path) -> None:
    calls = 0
    empty = {"properties": {"layers": [
        {"name": p, "unit_measure": {"d_factor": 10}, "depths": [{"label": "0-5cm", "values": {"mean": None}}]}
        for p in ("sand", "clay", "soc")
    ]}}

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=empty)

    settings = Settings(cache_dir=tmp_path)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = SoilGridsClient(http, SourceCache(settings.cache_db_path), settings)
        for _ in range(2):
            res = await client.run(lat=52.6, lon=16.99)
            assert res.mode == DataMode.MISSING and res.message == NO_DATA_MESSAGE
    assert calls == 1


def test_sentinel2_grid_from_stac_matches_raster_header_math() -> None:
    transform = Affine(20.0, 0.0, 600000.0, 0.0, -20.0, 5900000.0)
    item = {"properties": {"proj:epsg": 32633}, "assets": {"scl": {"proj:transform": list(transform)[:6]}}}
    predicted = s2._grid_from_stac(item, PARCEL_GEOJSON)
    assert predicted is not None
    _, window = s2._parcel_window(CRS.from_epsg(32633), transform, PARCEL_GEOJSON)
    header = s2._Grid(CRS.from_epsg(32633), window_transform(window, transform), window.height, window.width, np.ones((1, 1), bool))
    assert s2._same_grid(predicted, header)
    assert s2._grid_from_stac({"properties": {}, "assets": {}}, PARCEL_GEOJSON) is None


@pytest.mark.parametrize("shift", [1e-9, 20.0])
def test_sentinel2_grid_mismatch_is_detected(shift: float) -> None:
    t = Affine(20.0, 0.0, 600000.0, 0.0, -20.0, 5900000.0)
    a = s2._Grid(CRS.from_epsg(32633), t, 10, 10, np.zeros((0, 0), bool))
    b = s2._Grid(CRS.from_epsg(32633), Affine(20.0, 0.0, 600000.0 + shift, 0.0, -20.0, 5900000.0), 10, 10, np.zeros((0, 0), bool))
    assert not s2._same_grid(a, b)


async def test_analysis_service_reuses_bundle_and_terrain_on_crop_change() -> None:
    from synthetic_data import ANALYSIS_DATE, TODAY, make_bundle

    from backend.analysis import AnalysisService

    calls = 0

    class _CountingCollector:
        def __init__(self) -> None:
            self.clients: dict[str, Any] = {}

        async def collect(self, request: AnalysisRequest, today: date | None = None):
            nonlocal calls
            calls += 1
            return make_bundle(request.analysis_date)

    svc = AnalysisService(_CountingCollector())  # type: ignore[arg-type]
    r1 = await svc.analyze(
        AnalysisRequest(parcel_id="302111_5.0015.72", crop=CropCode.KUKURYDZA, analysis_date=ANALYSIS_DATE),
        today=TODAY,
    )
    r2 = await svc.analyze(
        AnalysisRequest(parcel_id="302111_5.0015.72", crop=CropCode.ZIEMNIAK, analysis_date=ANALYSIS_DATE),
        today=TODAY,
    )
    assert calls == 1
    assert r1.response.assessment.crop == CropCode.KUKURYDZA
    assert r2.response.assessment.crop == CropCode.ZIEMNIAK
    assert r1.response.assessment.kbw_periods != r2.response.assessment.kbw_periods
