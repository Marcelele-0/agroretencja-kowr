"""Application service: data bundle -> unified timeline -> KOWR steps 1-5 -> AnalysisResponse.

I/O (data collection) is async; the computation (terrain, balance, scoring, RL) runs in a worker
thread so the event loop stays free. Finished analyses are kept in a small in-process cache
keyed by (parcel, crop, date): the PDF report and What-If reuse them without recomputing.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from time import perf_counter
from typing import Any

import numpy as np

from backend.clients.pipeline import ParcelDataBundle, ParcelDataCollector
from backend.config import PROJECT_ROOT, Settings, get_settings
from backend.inference.hydrofusion import FALLBACK_LABEL, load_hydrofusion
from backend.irrigation import IrrigationRequest, advise_irrigation
from backend.map_layers import MapContext, build_map_layers
from hydro_core.advisor import AdviceContext, build_advice
from hydro_core.causes import attribute_causes
from hydro_core.crops import crop_params, stage_on, taw_for
from hydro_core.fao_balance import BalanceScenario, run_balance
from hydro_core.features import AnalysisInputs, FeatureContext, HydroContext, SatelliteObservation
from hydro_core.history import build_history, kbw_for_year
from hydro_core.kbw import current_period
from hydro_core.retention import build_retention_plan, what_if
from hydro_core.runoff import parcel_cn
from hydro_core.schemas import (
    AnalysisRequest,
    AnalysisResponse,
    AuditMetadata,
    CropCode,
    DataMode,
    IungSoilCategory,
    ParcelGeometry,
    RiskLevel,
    WaterAssessmentSummary,
    WaterStatePoint,
    WhatIfRequest,
    WhatIfScenarioResult,
)
from hydro_core.scoring import (
    assess_confidence,
    assess_risk,
    compute_water_score,
    daily_water_states,
    physical_water_state,
)
from hydro_core.text import days, num, state_label
from hydro_core.timeline import DailyTimeline, build_timeline, kind_is_forecast
from hydro_core.topography import TerrainAnalysis, analyse_terrain

logger = logging.getLogger(__name__)

TIMELINE_DAYS_BEFORE = 30
ENGINE_VERSION = "1.0"
_CATEGORY_CODE = {IungSoilCategory.I: 1, IungSoilCategory.II: 2, IungSoilCategory.III: 3, IungSoilCategory.IV: 4}
_RISK_PL = {
    RiskLevel.NISKIE: "niskie",
    RiskLevel.UMIARKOWANE: "umiarkowane",
    RiskLevel.WYSOKIE: "wysokie",
    RiskLevel.KRYTYCZNE: "krytyczne",
}


class ParcelOutOfScopeError(Exception):
    """The parcel is not agricultural land: the service returns a message instead of an analysis."""

    def __init__(self, message: str, parcel: ParcelGeometry | None = None, status: str = "not_arable") -> None:
        super().__init__(message)
        self.parcel = parcel
        self.status = status


class AnalysisUnavailableError(Exception):
    """A source without which no honest result exists is missing (no placeholder values)."""


@dataclass
class AnalysisState:
    """Everything needed to answer follow-up requests (PDF, What-If) for one analysis."""

    response: AnalysisResponse
    fx: FeatureContext
    bundle: ParcelDataBundle
    created: float


@dataclass
class _CachedBundle:
    """Crop-independent parcel data, terrain model, and weather timeline for one (parcel, date)."""

    bundle: ParcelDataBundle
    terrain: TerrainAnalysis | None
    timeline: DailyTimeline
    created: float


class _TtlCache:
    """Thread-safe LRU cache with monotonic TTL for items holding a ``created`` timestamp."""

    def __init__(self, max_items: int = 64, ttl_s: float = 3600.0) -> None:
        self._data: OrderedDict[tuple[str, ...], Any] = OrderedDict()
        self._lock = threading.Lock()
        self.max_items = max_items
        self.ttl_s = ttl_s

    def get(self, key: tuple[str, ...]) -> Any | None:
        with self._lock:
            item = self._data.get(key)
            if item is None or time.monotonic() - item.created > self.ttl_s:
                self._data.pop(key, None)
                return None
            self._data.move_to_end(key)
            return item

    def put(self, key: tuple[str, ...], item: Any) -> None:
        with self._lock:
            self._data[key] = item
            self._data.move_to_end(key)
            while len(self._data) > self.max_items:
                self._data.popitem(last=False)

    def pop(self, key: tuple[str, ...]) -> None:
        with self._lock:
            self._data.pop(key, None)


class AnalysisCache(_TtlCache):
    """Thread-safe LRU with TTL (L1 cache of the scaling plan; L2 = Redis/PostGIS in production)."""

    @staticmethod
    def key(parcel_id: str, crop: CropCode, day: dt.date) -> tuple[str, str, str]:
        return (parcel_id, crop.value, day.isoformat())


class _BundleCache(_TtlCache):
    """Crop-independent bundle + terrain + timeline cache keyed by (parcel_id, analysis_date, today)."""

    @staticmethod
    def key(parcel_id: str, day: dt.date, today: dt.date) -> tuple[str, str, str]:
        return (parcel_id, day.isoformat(), today.isoformat())


def _rl_card_version() -> str:
    card = PROJECT_ROOT / "artifacts" / "models" / "ppo_irrigator_card.json"
    try:
        data = json.loads(card.read_text(encoding="utf-8"))
        return str(data.get("model_id") or data.get("name") or data.get("version") or card.stem)
    except (OSError, ValueError):
        return "brak karty modelu"


def _satellite(bundle: ParcelDataBundle) -> SatelliteObservation:
    s2 = bundle.sentinel2
    if s2 is None:
        return SatelliteObservation()
    prev = s2.previous
    previous = {
        "previous_ndmi": prev.ndmi_mean if prev else None,
        "previous_acquired": prev.acquired if prev else None,
    }
    if s2.current is None:
        return SatelliteObservation(ndmi_history_mean=s2.ndmi_history_mean, **previous)
    c = s2.current
    return SatelliteObservation(
        acquired=c.acquired, ndvi=c.ndvi_mean, ndmi=c.ndmi_mean, ndmi_p10=c.ndmi_p10, ndmi_p90=c.ndmi_p90,
        ndmi_history_mean=s2.ndmi_history_mean, ndmi_anomaly_z=s2.ndmi_anomaly_z, cloud_pct=c.scene_cloud_pct,
        **previous,
    )


def _hydro(bundle: ParcelDataBundle) -> HydroContext:
    h = bundle.hydro_network
    if h is None:
        return HydroContext()
    return HydroContext(
        nearest_ditch_m=h.nearest_ditch_m, ditches_touching_parcel=h.ditches_touching_parcel,
        ditch_length_in_radius_m=h.ditch_length_in_radius_m, nearest_watercourse_m=h.nearest_watercourse_m,
        nearest_water_body_m=h.nearest_water_body_m,
    )


def _summary_text(fx: FeatureContext, total: float, state: float, risk: RiskLevel, in_season: bool, kbw_drought: bool | None) -> str:
    storage = fx.get("storage_pct")
    dts = fx.get("days_to_stress")
    parts = [
        (
            f"Stan wody: {state_label(state)} ({num(state, 1, sign=True)} w skali od −5 susza do +5 nadmiar wody), "
            f"ryzyko {_RISK_PL[risk]}. Ogólna ocena warunków wodnych działki (WaterScore): {num(total)}/100."
        )
    ]
    if storage is not None:
        parts.append(f"Zapas wody w strefie korzeniowej {num(storage)}% pojemności.")
    if kbw_drought is True:
        parts.append("Wg progów IUNG-PIB w ostatnim zakończonym oknie wystąpiła susza rolnicza.")
    elif kbw_drought is False:
        parts.append("Wg progów IUNG-PIB w ostatnim zakończonym oknie nie było suszy.")
    if in_season:
        if dts is None:
            parts.append("W horyzoncie prognozy nie przewiduje się stresu wodnego.")
        elif dts == 0:
            parts.append("Uprawa jest teraz w stresie wodnym.")
        else:
            parts.append(f"Stres wodny przewidywany za ok. {days(dts)}.")
    else:
        parts.append("Data poza sezonem uprawy – ocena dotyczy stanu gleby, bez decyzji o nawadnianiu.")
    return " ".join(parts)


class AnalysisService:
    def __init__(self, collector: ParcelDataCollector, settings: Settings | None = None) -> None:
        self.collector = collector
        self.settings = settings or get_settings()
        self.cache = AnalysisCache(ttl_s=self.settings.analysis_cache_ttl_s)
        self._bundles = _BundleCache(ttl_s=self.settings.analysis_cache_ttl_s)

    def _dem_upgraded_on_disk(self, parcel_id: str, bundle: ParcelDataBundle) -> bool:
        """True when a cached Copernicus fallback bundle now has a finished 1 m GUGiK NMT on disk."""
        if bundle.dem is None or not bundle.dem.is_surface_model:
            return False
        clients = getattr(self.collector, "clients", None)
        nmt = clients.get("nmt") if isinstance(clients, dict) else None
        if nmt is None or not hasattr(nmt, "_cache_path"):
            return False
        try:
            return bool(nmt._cache_path(parcel_id, "gugik1m").exists())
        except OSError:
            return False

    async def analyze(self, request: AnalysisRequest, today: dt.date | None = None) -> AnalysisState:
        today = today or dt.date.today()
        if request.parcel_id:
            cached = self.cache.get(AnalysisCache.key(request.parcel_id, request.crop, request.analysis_date))
            if cached is not None:
                return cached
        started = perf_counter()
        cached_bundle: _CachedBundle | None = None
        if request.parcel_id:
            bkey = _BundleCache.key(request.parcel_id, request.analysis_date, today)
            candidate = self._bundles.get(bkey)
            if candidate is not None:
                if self._dem_upgraded_on_disk(request.parcel_id, candidate.bundle):
                    self._bundles.pop(bkey)
                else:
                    cached_bundle = candidate

        if cached_bundle is not None:
            bundle = cached_bundle.bundle
            state, _, _ = await asyncio.to_thread(
                self._build_state,
                bundle,
                request,
                today,
                started,
                (cached_bundle.terrain, cached_bundle.timeline),
            )
        else:
            bundle = await self.collector.collect(request, today=today)
            state, terrain, tl = await asyncio.to_thread(self._build_state, bundle, request, today, started, None)
            self._bundles.put(
                _BundleCache.key(bundle.parcel.parcel_id, request.analysis_date, today),
                _CachedBundle(bundle=bundle, terrain=terrain, timeline=tl, created=time.monotonic()),
            )
        self.cache.put(AnalysisCache.key(bundle.parcel.parcel_id, request.crop, request.analysis_date), state)
        return state

    async def what_if(self, req: WhatIfRequest) -> WhatIfScenarioResult:
        state = await self.analyze(AnalysisRequest(parcel_id=req.parcel_id, crop=req.crop, analysis_date=req.analysis_date))
        return await asyncio.to_thread(what_if, state.fx, req.enabled_measures, state.response.retention.what_if_default.reference_dry_year)

    # ----------------------------------------------------------------------------------
    def _compute(self, bundle: ParcelDataBundle, request: AnalysisRequest, today: dt.date, started: float) -> AnalysisState:
        return self._build_state(bundle, request, today, started, None)[0]

    def _build_state(
        self,
        bundle: ParcelDataBundle,
        request: AnalysisRequest,
        today: dt.date,
        started: float,
        precomputed: tuple[TerrainAnalysis | None, DailyTimeline] | None = None,
    ) -> tuple[AnalysisState, TerrainAnalysis | None, DailyTimeline]:
        parcel = bundle.parcel
        if bundle.non_agricultural:
            raise ParcelOutOfScopeError(
                "Działka nie jest gruntem rolnym wg mapy IUNG-PIB (" + ", ".join(bundle.non_agricultural)
                + "). Analiza dotyczy gruntów ornych, łąk, pastwisk i sadów.",
                parcel,
            )
        w = bundle.weather_history
        if w is None:
            raise AnalysisUnavailableError("Brak danych pogodowych dla działki – bilansu wodnego nie da się policzyć.")
        if bundle.soil_category is None:
            raise AnalysisUnavailableError("Brak danych glebowych (IUNG-PIB i SoilGrids) – bilansu wodnego nie da się policzyć.")

        if precomputed is not None:
            terrain, tl = precomputed
        else:
            f = bundle.weather_forecast
            tl = build_timeline(
                dates=w.dates, precipitation_mm=w.precipitation_mm, et0_mm=w.et0_mm, tmax_c=w.tmax_c,
                analysis_date=request.analysis_date, today=today,
                archive_delay_days=self.settings.open_meteo_archive_delay_days,
                forecast_dates=f.dates if f else None, forecast_precip_mm=f.precipitation_mm if f else None,
                forecast_et0_mm=f.et0_mm if f else None, forecast_tmax_c=f.tmax_c if f else None,
                providers=w.providers, forecast_model=f.model if f else None,
            )

            terrain = None
            if bundle.dem is not None:
                try:
                    d = bundle.dem
                    terrain = analyse_terrain(d.elevation, d.transform, d.resolution_m, d.parcel_mask, source=d.source)
                except ValueError:
                    logger.warning("Terrain analysis failed", exc_info=True)

        crop = crop_params(request.crop)
        category = bundle.soil_category
        taw = taw_for(category)
        grass = request.crop == CropCode.UZYTKI_ZIELONE
        cn = parcel_cn(category, terrain.mean_slope_deg if terrain else 0.0, grassland=grass)
        trace = run_balance(tl, crop, [BalanceScenario("baseline", taw, cn)])

        iung = bundle.soil_iung
        organic = bool(iung and iung.organic_points * 2 > len(iung.sample_values))
        grids = bundle.soil_grids
        missing = [
            a.source_name.split(" (")[0] for a in bundle.audit
            if a.mode == DataMode.MISSING and a.category not in ("meteorologiczne",)
        ]
        inputs = AnalysisInputs(
            analysis_date=request.analysis_date, today=today, crop_code=request.crop, crop=crop,
            soil_category=category, soil_source=bundle.soil_source_layer, taw_mm=taw, cn=cn,
            timeline=tl, trace=trace, kbw_periods=[], parcel_area_ha=parcel.area_ha, terrain=terrain,
            satellite=_satellite(bundle), hydro=_hydro(bundle),
            soil_sand_pct=grids.topsoil_sand_pct if grids else None,
            soil_soc_g_kg=grids.topsoil_soc_g_kg if grids else None,
            soil_awc_mm=grids.awc_mm_0_100 if grids else None,
            soil_organic=organic,
            terrain_is_fallback=bool(bundle.dem and bundle.dem.is_surface_model),
            weather_is_fallback="nasa-power" in w.providers,
            missing_sources=missing,
        )
        fx = FeatureContext(inputs)
        inputs.kbw_periods = kbw_for_year(fx, request.analysis_date.year)

        # Optional ML: cloud-gap reconstruction + TreeSHAP causes (only inside the AoA)
        hf = load_hydrofusion()
        prediction = None
        if hf is not None:
            try:
                prediction = hf.predict(fx)
            except Exception:
                logger.warning("HydroFusion prediction failed", exc_info=True)
        # Reconstruct only when there is no cloud-free scene; outside the Area of Applicability
        # the model is not used (physical path, confidence lowered by the AoA check).
        if prediction is not None and prediction.inside_aoa and inputs.satellite.ndmi is None:
            inputs.satellite = SatelliteObservation(
                ndmi=round(prediction.median, 4), interval=prediction.interval, reconstructed=True,
                acquired=request.analysis_date, ndmi_history_mean=inputs.satellite.ndmi_history_mean,
                previous_ndmi=inputs.satellite.previous_ndmi, previous_acquired=inputs.satellite.previous_acquired,
            )
            fx = FeatureContext(inputs)

        score = compute_water_score(fx)
        risk = assess_risk(fx, score)
        aoa = (prediction.dissimilarity_index, prediction.inside_aoa) if prediction else (None, True)
        outside_aoa = prediction is not None and not prediction.inside_aoa and inputs.satellite.ndmi is None
        confidence = assess_confidence(fx, extra_penalty=1 if outside_aoa else 0, aoa=aoa)
        if outside_aoa:
            confidence.missing_factors.append(
                "Warunki działki wykraczają poza dane treningowe HydroFusion-PL (AoA) – wilgotności pod chmurami nie odtworzono."
            )
        state_value = physical_water_state(fx, risk=risk.level)

        i0 = tl.analysis_index
        idx = np.arange(max(0, i0 - TIMELINE_DAYS_BEFORE), len(tl))
        daily = daily_water_states(fx, score, idx)
        storage = trace.storage_pct(0)
        points = []
        for k, i in enumerate(idx):
            kind = tl.kind_of(int(i))
            value = state_value if i == i0 else float(daily[k])
            points.append(WaterStatePoint(
                # projection days (forecast or climatology) are never presented as measurements
                date=tl.dates[i].astype(dt.date), value=value, forecast=bool(i > i0 or kind_is_forecast(kind)),
                kind=kind, storage_pct_taw=round(float(storage[i]), 1),
                precipitation_mm=round(float(tl.precip[i]), 1), et0_mm=round(float(tl.et0[i]), 2),
            ))

        # Causes stay physical (crop-aware counterfactuals): HydroFusion-PL is trained without crop
        # information, so its TreeSHAP cannot attribute the crop factor (team decision 21.11.2026).
        # hydro_core.causes.attribution_from_shap remains available for a crop-aware model.
        causes = attribute_causes(fx)

        retention = build_retention_plan(fx)
        irrigation, irr_notes = advise_irrigation(
            IrrigationRequest(fx=fx, lat=parcel.centroid_lat, lon=parcel.centroid_lon, iung_code=_CATEGORY_CODE[category], today=today)
        )
        history = build_history(fx, score)
        cur = current_period(inputs.kbw_periods)
        complete = [p for p in inputs.kbw_periods if p.is_complete and p.is_drought is not None]
        kbw_verdict = complete[-1].is_drought if complete else None
        in_season = stage_on(crop, request.analysis_date) is not None
        projection_kind = "prognoza ECMWF" if (tl.kind[i0 + 1 :] == 2).any() else "klimatologia działki"
        advice = build_advice(fx, AdviceContext(
            risk=risk, retention=retention, irrigation=irrigation, history=history,
            current_kbw=complete[-1] if complete else None, projection_kind=projection_kind,
        ))

        s2 = bundle.sentinel2
        layers = build_map_layers(MapContext(
            parcel_geojson=parcel.geojson, water_state=state_value, terrain=terrain,
            hydro_geojson=bundle.hydro_network.features_geojson if bundle.hydro_network else None,
            recommendations=retention.recommendations,
            ndmi_grid=s2.current_ndmi_grid if s2 else None, ndmi_transform=s2.grid_transform if s2 else None,
            ndmi_crs=s2.grid_crs if s2 else None,
        ))

        sat = inputs.satellite
        assessment = WaterAssessmentSummary(
            analysis_date=request.analysis_date, crop=request.crop, iung_category=category,
            soil_source_layer=bundle.soil_source_layer, taw_mm=taw,
            raw_mm=round(fx.get("raw_mm") or 0.0, 1), dr_mm=round(fx.get("dr_mm") or 0.0, 1),
            kbw_mm=cur.kbw_mm if cur else None, kbw_threshold_mm=cur.threshold_mm if cur else None,
            kbw_period_label=cur.label if cur else None, kbw_periods=inputs.kbw_periods,
            weather_station=bundle.weather_station,
            mean_slope_deg=terrain.mean_slope_deg if terrain else 0.0, mean_twi=terrain.mean_twi if terrain else 0.0,
            ndmi_value=sat.ndmi, ndmi_interval=sat.interval, ndmi_is_reconstructed=sat.reconstructed,
            water_score=score, water_risk=risk, confidence=confidence, history_10y=history,
            summary_text=_summary_text(fx, score.total, state_value, risk.level, in_season, kbw_verdict),
            water_state=state_value, water_state_timeline=points,
            storage_pct_taw=round(fx.get("storage_pct") or 0.0, 1), days_to_stress=fx.get("days_to_stress"),
            kbw_is_drought=kbw_verdict, in_season=in_season,
            crop_stage=stage_on(crop, request.analysis_date), kc_today=round(fx.get("kc_today") or 0.0, 2),
            runoff_60d_mm=round(fx.get("runoff_60d_mm") or 0.0, 1),
        )
        audit = AuditMetadata(
            sources=list(bundle.audit),
            execution_time_s=round(perf_counter() - started, 2),
            model_versions={
                "silnik": f"AgroRetencja {ENGINE_VERSION}",
                "bilans": "FAO-56 (kowr_rl.simulator.balance_step) + SCS-CN + FAO-33",
                "nawadnianie": f"{irrigation.policy_used or '-'} (PPO: {_rl_card_version()})"
                + (f"; pominięto: {'; '.join(irr_notes)}" if irr_notes else ""),
                "hydrofusion": f"HydroFusion-PL {hf.version}" if hf else FALLBACK_LABEL,
                "os_czasu": f"{len(tl)} dni, projekcja: {projection_kind}",
            },
        )
        response = AnalysisResponse(
            parcel=parcel, assessment=assessment, causes=causes, retention=retention,
            irrigation=irrigation, map_layers=layers, audit=audit, advice=advice,
        )
        return AnalysisState(response=response, fx=fx, bundle=bundle, created=time.monotonic()), terrain, tl


def response_json(state: AnalysisState) -> dict[str, Any]:
    return state.response.model_dump(mode="json")
