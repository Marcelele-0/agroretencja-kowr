"""FastAPI application: AgroRetencja KOWR backend.

Endpoints (frontend: frontend/src/api/backend.ts):

==========================  ========================================================================
GET  /api/health            status, versions, registered extension points
GET  /api/parcels/search    precinct name + parcel number search (ULDK)
GET  /api/parcel            fast first step (<= 2 s): parcel geometry and administrative data
POST /api/analyze           KOWR steps 1-5 (AnalysisResponse)
POST /api/what-if           What-If simulator for chosen retention measures
POST /api/zones             joint wetness zones of 1-12 parcels (fast, no full analysis)
GET  /api/weather/daily     daily weather in Open-Meteo JSON shape (cache, NASA POWER fallback)
GET  /api/report.pdf        2-page auditable report (PDF; HTML when no PDF renderer works)
GET  /api/registries        everything registered in the engine (features, components, rules ...)
/rl/*                       irrigation module of ml/rl_irrigator (router of kowr_rl)
==========================  ========================================================================

Run:  uv run uvicorn backend.main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from kowr_rl.api import router as rl_router
from kowr_rl.config import (
    crop_aliases,
    crops_raw,
    load_crops,
    load_pre_season,
    load_rl_config,
    load_soils,
)
from kowr_rl.inference import load_rl_policy

from backend.analysis import AnalysisService, AnalysisUnavailableError, ParcelOutOfScopeError
from backend.clients.base import SourceUnavailableError
from backend.clients.meteo import NASA_POWER, WeatherHistoryClient
from backend.clients.pipeline import ParcelDataCollector, ParcelNotFoundError, new_http_client
from backend.clients.registry import SOIL_PROVIDERS, SOURCES
from backend.config import get_settings
from backend.inference.hydrofusion import load_hydrofusion
from backend.irrigation import IRRIGATION_PROVIDERS
from backend.map_layers import MAP_LAYERS
from backend.pdf.report import RENDERERS, render_report
from backend.security import REPORT_CSP, SECURITY_HEADERS, RateLimiter, is_rate_limited_path
from backend.zones import ZonesService
from hydro_core.advisor import ADVICE_RULES
from hydro_core.causes import COUNTERFACTUALS
from hydro_core.crops import all_crops, pre_season
from hydro_core.features import FEATURES
from hydro_core.retention import APPLICABILITY, RETENTION_MEASURES, measures_config
from hydro_core.schemas import (
    AnalysisRefusal,
    AnalysisRequest,
    AnalysisResponse,
    CropCode,
    ParcelCandidate,
    ParcelGeometry,
    WhatIfRequest,
    WhatIfScenarioResult,
    ZonesRequest,
    ZonesResponse,
)
from hydro_core.scoring import CONFIDENCE_CHECKS, SCORE_COMPONENTS, VETO_RULES, scoring_config

logger = logging.getLogger(__name__)

API_VERSION = "1.0.0"
EARLIEST_DATE = dt.date(2016, 1, 1)


def _warm_up() -> None:
    """Models and YAML configs load once at startup, so the first analysis does not pay for them (~1 s)."""
    loaders = (
        load_hydrofusion, load_rl_policy, load_crops, crop_aliases, load_soils, load_rl_config,
        crops_raw, load_pre_season, all_crops, pre_season, measures_config, scoring_config,
    )
    for loader in loaders:
        try:
            loader()
        except Exception:  # warm-up must never stop the server; the first request loads it again
            logger.warning("Warm-up of %s failed", loader.__name__, exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    http = new_http_client(settings)
    collector = ParcelDataCollector(http, settings)
    app.state.collector = collector
    app.state.service = AnalysisService(collector, settings)
    app.state.zones = ZonesService(collector)
    await asyncio.to_thread(_warm_up)
    try:
        yield
    finally:
        await http.aclose()


settings = get_settings()
app = FastAPI(
    title="AgroRetencja KOWR – API",
    version=API_VERSION,
    description="Cyfrowy asystent retencji i bilansu wodnego działki rolnej (HackNation 2026 – KOWR).",
    lifespan=lifespan,
)
limiter = RateLimiter(settings.rate_limit_per_minute)


@app.middleware("http")
async def _security(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    """Rate limit of the heavy endpoints and security headers on every response.

    Registered before CORS, so CORS stays the outer layer and a 429 still carries its headers.
    """
    wait: int | None = None
    if is_rate_limited_path(request.method, request.url.path):
        wait = limiter.check(request.client.host if request.client else "unknown")
    if wait is not None:
        response: Response = JSONResponse(
            status_code=429,
            content={"status": "rate_limited", "message": f"Za dużo zapytań. Spróbuj ponownie za {wait} s."},
            headers={"Retry-After": str(wait)},
        )
    else:
        response = await call_next(request)
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
    expose_headers=["X-Report-Renderer", "Retry-After"],
)
app.include_router(rl_router)


def _service(request: Request) -> AnalysisService:
    return request.app.state.service


def _check_date(day: dt.date) -> None:
    today = dt.date.today()
    if day < EARLIEST_DATE or day > today:
        raise HTTPException(422, f"Data analizy musi być z zakresu {EARLIEST_DATE} – {today}.")


@app.exception_handler(ParcelNotFoundError)
async def _not_found(_: Request, exc: ParcelNotFoundError) -> JSONResponse:
    if exc.retryable:
        return JSONResponse(status_code=503, content={"status": "source_unavailable", "message": f"Usługa ULDK GUGiK: {exc} Spróbuj ponownie za chwilę."})
    return JSONResponse(status_code=404, content={"status": "not_found", "message": str(exc)})


@app.exception_handler(ParcelOutOfScopeError)
async def _out_of_scope(_: Request, exc: ParcelOutOfScopeError) -> JSONResponse:
    body = AnalysisRefusal(status=exc.status, message=str(exc), parcel=exc.parcel)
    return JSONResponse(status_code=422, content=body.model_dump(mode="json"))


@app.exception_handler(AnalysisUnavailableError)
async def _unavailable(_: Request, exc: AnalysisUnavailableError) -> JSONResponse:
    return JSONResponse(status_code=503, content={"status": "no_data", "message": str(exc)})


@app.get("/api/health")
def health() -> dict[str, Any]:
    hf = load_hydrofusion()
    return {
        "status": "ok",
        "version": API_VERSION,
        "crops": [c.value for c in CropCode],
        "hydrofusion": hf.version if hf else None,
        "sources": SOURCES.names(),
    }


@app.get("/api/registries")
def registries() -> dict[str, Any]:
    """Extension points of the engine and what is registered in each of them."""
    return {
        "features": [{"name": s.name, "group": s.group, "unit": s.unit, "label": s.label} for s in FEATURES.values()],
        "score_components": [{"name": c.name, "label": c.label} for c in SCORE_COMPONENTS.values()],
        "veto_rules": VETO_RULES.names(),
        "confidence_checks": CONFIDENCE_CHECKS.names(),
        "cause_counterfactuals": COUNTERFACTUALS.names(),
        "retention_measures": [{"id": m.id, "group": m.group, "applies_if": list(m.applies_if)} for m in RETENTION_MEASURES.values()],
        "applicability": APPLICABILITY.names(),
        "advice_rules": ADVICE_RULES.names(),
        "irrigation_providers": IRRIGATION_PROVIDERS.names(),
        "data_sources": SOURCES.names(),
        "soil_providers": SOIL_PROVIDERS.names(),
        "map_layers": MAP_LAYERS.names(),
        "report_renderers": RENDERERS.names(),
    }


@app.get("/api/parcels/search", response_model=list[ParcelCandidate])
async def search_parcels(request: Request, q: str = Query(..., min_length=2, max_length=100, description="np. 'Mińsk Mazowiecki 6509'")) -> list[ParcelCandidate]:
    return await request.app.state.collector.search_parcels(q)


@app.get("/api/parcel", response_model=ParcelGeometry)
async def parcel(
    request: Request,
    parcel_id: str | None = None,
    lat: float | None = Query(default=None, ge=48.0, le=56.0),
    lon: float | None = Query(default=None, ge=13.0, le=25.0),
) -> ParcelGeometry:
    if not parcel_id and (lat is None or lon is None):
        raise HTTPException(422, "Podaj parcel_id albo lat i lon.")
    geometry, _audit = await request.app.state.collector.resolve_parcel(parcel_id, lat, lon)
    return geometry


@app.post("/api/analyze", response_model=AnalysisResponse, responses={422: {"model": AnalysisRefusal}})
async def analyze(body: AnalysisRequest, request: Request) -> AnalysisResponse:
    _check_date(body.analysis_date)
    state = await _service(request).analyze(body)
    return state.response


@app.post("/api/zones", response_model=ZonesResponse)
async def zones(body: ZonesRequest, request: Request) -> ZonesResponse:
    """Joint wetness zones: one DEM mosaic and one class grid, so neighbours meet without jumps."""
    collector = request.app.state.collector
    svc: ZonesService | None = getattr(request.app.state, "zones", None)
    if svc is None or svc.collector is not collector:
        svc = ZonesService(collector)
        request.app.state.zones = svc
    return await svc.zones(body)


WEATHER_MAX_SPAN_DAYS = 25 * 366


@app.get("/api/weather/daily")
async def weather_daily(
    request: Request,
    start: dt.date,
    end: dt.date,
    lat: float = Query(..., ge=48.0, le=56.0),
    lon: float = Query(..., ge=13.0, le=25.0),
) -> JSONResponse:
    """Daily weather for the Open-Meteo proxy of the frontend when Open-Meteo is limited or failing.

    Same JSON shape as the Open-Meteo archive API (``daily.time``, ``daily.precipitation_sum``,
    ``daily.et0_fao_evapotranspiration`` ...). ``X-OM-Source`` names the provider.
    """
    if start > end or (end - start).days > WEATHER_MAX_SPAN_DAYS:
        raise HTTPException(422, "Nieprawidłowy zakres dat (start <= end, najwyżej 25 lat).")
    client: WeatherHistoryClient = request.app.state.collector.clients["weather_history"]
    headers = {"Cache-Control": "no-store"}
    try:
        series = await asyncio.wait_for(client.daily_range(lat, lon, start, end), timeout=client.timeout_s)
    except SourceUnavailableError as exc:
        return JSONResponse(status_code=503, content={"error": True, "reason": str(exc)}, headers=headers)
    except (TimeoutError, httpx.HTTPError) as exc:
        reason = "Źródła pogody nie odpowiedziały w limicie czasu." if isinstance(exc, TimeoutError) else (
            f"Źródła pogody są niedostępne ({type(exc).__name__})."
        )
        return JSONResponse(status_code=503, content={"error": True, "reason": reason}, headers=headers)
    provider = "+".join(series.providers) or "backend"
    headers["X-OM-Source"] = NASA_POWER if NASA_POWER in series.providers else "backend"
    body = {
        "latitude": lat,
        "longitude": lon,
        "elevation": series.grid_elevation_m,
        "timezone": "Europe/Warsaw",
        "provider": provider,
        "daily_units": {
            "time": "iso8601",
            "precipitation_sum": "mm",
            "et0_fao_evapotranspiration": "mm",
            "temperature_2m_mean": "°C",
            "temperature_2m_max": "°C",
            "temperature_2m_min": "°C",
        },
        "daily": {
            "time": [d.isoformat() for d in series.dates],
            "precipitation_sum": series.precipitation_mm,
            "et0_fao_evapotranspiration": series.et0_mm,
            "temperature_2m_mean": series.tmean_c,
            "temperature_2m_max": series.tmax_c,
            "temperature_2m_min": series.tmin_c,
        },
    }
    return JSONResponse(content=body, headers=headers)


@app.post("/api/what-if", response_model=WhatIfScenarioResult)
async def what_if(body: WhatIfRequest, request: Request) -> WhatIfScenarioResult:
    _check_date(body.analysis_date)
    return await _service(request).what_if(body)


async def _report(request: Request, body: AnalysisRequest) -> Response:
    _check_date(body.analysis_date)
    state = await _service(request).analyze(body)
    terrain = state.fx.inputs.terrain
    rendered = render_report(state.response, terrain)
    ext = "pdf" if rendered.media_type == "application/pdf" else "html"
    name = f"agroretencja_{state.response.parcel.parcel_id}_{body.analysis_date}.{ext}".replace("/", "_")
    return Response(
        content=rendered.content,
        media_type=rendered.media_type,
        headers={
            "Content-Disposition": f'inline; filename="{name}"',
            "X-Report-Renderer": rendered.renderer,
            "Content-Security-Policy": REPORT_CSP,
        },
    )


@app.get("/api/report.pdf")
async def report_get(request: Request, parcel_id: str, crop: CropCode, analysis_date: dt.date) -> Response:
    return await _report(request, AnalysisRequest(parcel_id=parcel_id, crop=crop, analysis_date=analysis_date))


@app.post("/api/report.pdf")
async def report_post(body: AnalysisRequest, request: Request) -> Response:
    return await _report(request, body)
