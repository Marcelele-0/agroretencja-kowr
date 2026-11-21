"""FastAPI application: AgroRetencja KOWR backend.

Endpoints (frontend: frontend/src/api/backend.ts):

==========================  ========================================================================
GET  /api/health            status, versions, registered extension points
GET  /api/parcels/search    precinct name + parcel number search (ULDK)
GET  /api/parcel            fast first step (<= 2 s): parcel geometry and administrative data
POST /api/analyze           KOWR steps 1-5 (AnalysisResponse)
POST /api/what-if           What-If simulator for chosen retention measures
GET  /api/report.pdf        2-page auditable report (PDF; HTML when no PDF renderer works)
GET  /api/registries        everything registered in the engine (features, components, rules ...)
/rl/*                       irrigation module of ml/rl_irrigator (router of kowr_rl)
==========================  ========================================================================

Run:  uv run uvicorn backend.main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from kowr_rl.api import router as rl_router

from backend.analysis import AnalysisService, AnalysisUnavailableError, ParcelOutOfScopeError
from backend.clients.pipeline import ParcelDataCollector, ParcelNotFoundError, new_http_client
from backend.clients.registry import SOIL_PROVIDERS, SOURCES
from backend.config import get_settings
from backend.inference.hydrofusion import load_hydrofusion
from backend.irrigation import IRRIGATION_PROVIDERS
from backend.map_layers import MAP_LAYERS
from backend.pdf.report import RENDERERS, render_report
from hydro_core.advisor import ADVICE_RULES
from hydro_core.causes import COUNTERFACTUALS
from hydro_core.features import FEATURES
from hydro_core.retention import APPLICABILITY, RETENTION_MEASURES
from hydro_core.schemas import (
    AnalysisRefusal,
    AnalysisRequest,
    AnalysisResponse,
    CropCode,
    ParcelCandidate,
    ParcelGeometry,
    WhatIfRequest,
    WhatIfScenarioResult,
)
from hydro_core.scoring import CONFIDENCE_CHECKS, SCORE_COMPONENTS, VETO_RULES

logger = logging.getLogger(__name__)

API_VERSION = "1.0.0"
EARLIEST_DATE = dt.date(2016, 1, 1)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    http = new_http_client(settings)
    collector = ParcelDataCollector(http, settings)
    app.state.collector = collector
    app.state.service = AnalysisService(collector, settings)
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
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
    expose_headers=["X-Report-Renderer"],
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
async def search_parcels(request: Request, q: str = Query(..., min_length=2, description="np. 'Mińsk Mazowiecki 6509'")) -> list[ParcelCandidate]:
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
        headers={"Content-Disposition": f'inline; filename="{name}"', "X-Report-Renderer": rendered.renderer},
    )


@app.get("/api/report.pdf")
async def report_get(request: Request, parcel_id: str, crop: CropCode, analysis_date: dt.date) -> Response:
    return await _report(request, AnalysisRequest(parcel_id=parcel_id, crop=crop, analysis_date=analysis_date))


@app.post("/api/report.pdf")
async def report_post(body: AnalysisRequest, request: Request) -> Response:
    return await _report(request, body)
