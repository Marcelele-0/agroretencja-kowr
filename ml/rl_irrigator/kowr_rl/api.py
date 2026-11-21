"""FastAPI router for the irrigation module (step 4 of the KOWR workflow).

Mount in the main backend (``src/backend/main.py``)::

    from kowr_rl.api import router as rl_router
    app.include_router(rl_router)          # -> /rl/recommend, /rl/compare, /rl/health

Standalone (tests / demo)::

    uv run uvicorn kowr_rl.api:app --port 8001
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel, Field, model_validator

from . import __version__
from .config import accepted_crop_codes, load_crops, load_rl_config
from .data import DataUnavailableError
from .inference import RL_WEIGHTS, compare_strategies, load_rl_policy, recommend, to_irrigation_advice

# Unknown codes are rejected by recommend()/compare_strategies() with status invalid_input -> HTTP 422.
CROP_HELP = "Kod uprawy: klucz z config/crops.yaml albo alias (grupy SMSR frontendu, schemas.CropCode), patrz GET /rl/health"


class _Location(BaseModel):
    lat: float | None = Field(default=None, ge=48.0, le=56.0, description="Szerokosc geograficzna (WGS84)")
    lon: float | None = Field(default=None, ge=13.0, le=25.0, description="Dlugosc geograficzna (WGS84)")
    parcel_id: str | None = Field(default=None, description="Identyfikator dzialki ewidencyjnej (TERYT), np. 141201_1.0001.6509")

    @model_validator(mode="after")
    def _need_location(self) -> _Location:
        if not self.parcel_id and (self.lat is None or self.lon is None):
            raise ValueError("Podaj parcel_id albo lat i lon.")
        return self


class RecommendRequest(_Location):
    crop: str = Field(description=CROP_HELP)
    date: dt.date = Field(description="Data analizy (ostatnie 10 lat, nie pozniej niz dzis)")
    budget_mm: float = Field(default=150.0, ge=0, le=1000, description="Budzet wody na sezon [mm] (np. pojemnosc zbiornika / limit poboru)")
    budget_used_mm: float = Field(default=0.0, ge=0, le=1000, description="Woda juz zuzyta w tym sezonie [mm]")
    policy: Literal["default", "rl", "fao_rule", "fao_forecast", "fao_small", "calendar", "none"] = "default"
    irrigation_history: list[tuple[dt.date, float]] | None = Field(default=None, description="Opcjonalnie: [(data, mm), ...] dotychczasowych nawodnien")
    include_history: bool = Field(default=False, description="Dolacz 10-letnie porownanie strategii")
    plan_days: int = Field(default=7, ge=7, le=14, description="Dlugosc planu: 7 albo do 14 dni (plan_14d z prawdopodobienstwem podlewania)")


class CompareRequest(_Location):
    crop: str = Field(description=CROP_HELP)
    budget_mm: float = Field(default=150.0, ge=0, le=1000)
    n_seasons: int = Field(default=10, ge=1, le=10)
    include_oracle: bool = True


router = APIRouter(prefix="/rl", tags=["nawadnianie (RL)"])


def _raise_if_failed(out: dict[str, Any]) -> dict[str, Any]:
    if out.get("status") == "invalid_input":
        raise HTTPException(status_code=422, detail=out.get("message"))
    return out


@router.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "version": __version__,
        "crops": list(load_crops()),
        "crop_codes_accepted": accepted_crop_codes(),
        "default_policy": load_rl_config()["default_policy"],
        "rl_weights_loaded": load_rl_policy() is not None,
        "rl_weights": RL_WEIGHTS.name,
    }


@router.post("/recommend")
def post_recommend(req: RecommendRequest) -> dict[str, Any]:
    try:
        out = recommend(
            req.lat,
            req.lon,
            req.crop,
            req.date,
            req.budget_mm,
            req.budget_used_mm,
            policy=req.policy,
            parcel_id=req.parcel_id,
            irrigation_history=[(d.isoformat(), mm) for d, mm in req.irrigation_history] if req.irrigation_history else None,
            include_history=req.include_history,
            plan_days=req.plan_days,
        )
    except DataUnavailableError as err:
        raise HTTPException(status_code=503, detail=f"Zrodlo danych niedostepne: {err}") from err
    out = _raise_if_failed(out)
    if out.get("status") == "ok":
        out["irrigation_advice"] = to_irrigation_advice(out)
    return out


@router.get("/recommend")
def get_recommend(
    crop: str,
    date: dt.date,
    lat: float | None = None,
    lon: float | None = None,
    parcel_id: str | None = None,
    budget_mm: float = 150.0,
    budget_used_mm: float = 0.0,
    policy: Literal["default", "rl", "fao_rule"] = "default",
) -> dict[str, Any]:
    return post_recommend(RecommendRequest(lat=lat, lon=lon, parcel_id=parcel_id, crop=crop, date=date, budget_mm=budget_mm, budget_used_mm=budget_used_mm, policy=policy))


@router.post("/compare")
def post_compare(req: CompareRequest) -> dict[str, Any]:
    try:
        out = compare_strategies(req.lat, req.lon, req.crop, req.budget_mm, parcel_id=req.parcel_id, n_seasons=req.n_seasons, include_oracle=req.include_oracle)
    except DataUnavailableError as err:
        raise HTTPException(status_code=503, detail=f"Zrodlo danych niedostepne: {err}") from err
    return _raise_if_failed(out)


@router.get("/compare")
def get_compare(crop: str, lat: float | None = None, lon: float | None = None, parcel_id: str | None = None, budget_mm: float = 150.0) -> dict[str, Any]:
    return post_compare(CompareRequest(lat=lat, lon=lon, parcel_id=parcel_id, crop=crop, budget_mm=budget_mm))


app = FastAPI(title="KOWR - modul nawadniania (RL)", version=__version__, description="Krok 4 analizy KOWR: rekomendacje nawadniania z porownaniem strategii.")
app.include_router(router)
