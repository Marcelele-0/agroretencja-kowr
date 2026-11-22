"""Shared Pydantic v2 data contracts for AgroRetencja KOWR."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class CropCode(str, Enum):
    """Supported agricultural crops mapped to FAO-56/33 and IUNG-PIB parameters."""

    PSZENICA_OZIMA = "pszenica_ozima"
    PSZENICA_JARA = "pszenica_jara"
    KUKURYDZA = "kukurydza"
    RZEPAK_OZIMY = "rzepak_ozimy"
    BURAK_CUKROWY = "burak_cukrowy"
    ZIEMNIAK = "ziemniak"
    UZYTKI_ZIELONE = "uzytki_zielone"
    # the other IUNG-PIB SMSR crops (parameters in ml/rl_irrigator/config/crops.yaml)
    KUKURYDZA_KISZONKA = "kukurydza_kiszonka"
    STRACZKOWE = "straczkowe"
    WARZYWA = "warzywa"
    CHMIEL = "chmiel"
    TYTON = "tyton"
    DRZEWA_OWOCOWE = "drzewa_owocowe"
    KRZEWY_OWOCOWE = "krzewy_owocowe"
    TRUSKAWKI = "truskawki"


# Perennial crops: no ploughing and no catch crop between seasons
PERENNIAL_CROPS = frozenset({CropCode.CHMIEL, CropCode.DRZEWA_OWOCOWE, CropCode.KRZEWY_OWOCOWE, CropCode.TRUSKAWKI})


class RiskLevel(str, Enum):
    """Drought and runoff risk levels with veto escalation."""

    NISKIE = "niskie"
    UMIARKOWANE = "umiarkowane"
    WYSOKIE = "wysokie"
    KRYTYCZNE = "krytyczne"


class ConfidenceLevel(str, Enum):
    """Separated data confidence badge communicating uncertainty without averaging."""

    WYSOKA = "wysoka"
    SREDNIA = "srednia"
    OGRANICZONA = "ograniczona"


class IungSoilCategory(str, Enum):
    """IUNG-PIB soil drought susceptibility categories tied to TAW thresholds."""

    I = "I"
    II = "II"
    III = "III"
    IV = "IV"


class DataMode(str, Enum):
    """Provenance mode of an external dataset at query time."""

    LIVE = "live"
    PRE_FETCHED = "pre_fetched"
    FALLBACK = "fallback"
    MISSING = "brak_danych"


class ParcelGeometry(BaseModel):
    """Resolved Polish cadastral parcel geometry and administrative metadata."""

    parcel_id: str = Field(..., description="Cadastral TERYT parcel identifier.")
    centroid_lat: float = Field(..., ge=48.0, le=56.0)
    centroid_lon: float = Field(..., ge=13.0, le=25.0)
    area_ha: float = Field(..., gt=0.0)
    bbox_wgs84: tuple[float, float, float, float]
    geojson: dict[str, Any]
    voivodeship: str | None = None
    county: str | None = None
    commune: str | None = None
    region: str | None = None
    parcel_number: str | None = None


class ParcelCandidate(BaseModel):
    """One match returned when searching parcels by precinct name and parcel number."""

    parcel_id: str
    voivodeship: str | None = None
    county: str | None = None
    commune: str | None = None
    region: str | None = None
    parcel_number: str | None = None


class SourceAuditEntry(BaseModel):
    """Audit record for one open data source used in the parcel analysis."""

    category: str
    source_name: str
    endpoint_or_ref: str
    mode: DataMode
    retrieved_at: datetime
    observation_date: str
    spatial_resolution: str
    license: str
    fetch_time_s: float | None = Field(default=None, ge=0.0)
    attribution: str | None = None
    message: str | None = None


class WeatherStationContext(BaseModel):
    """Nearest IMGW-PIB station used to check the model grid for the parcel."""

    station_code: str
    station_name: str
    distance_km: float = Field(..., ge=0.0)
    station_elevation_m: float | None = None
    parcel_elevation_m: float | None = None
    elevation_diff_m: float | None = None
    observed_air_temp_c: float | None = None
    observed_at: str | None = None
    lapse_corrected_temp_c: float | None = None
    is_far: bool = False


class KbwPeriodResult(BaseModel):
    """Climatic water balance for one IUNG six-dekad period compared with the legal threshold."""

    period_index: int = Field(..., ge=0, le=13)
    label: str
    start: date
    end: date
    kbw_mm: float | None = None
    threshold_mm: float | None = None
    margin_mm: float | None = None
    is_drought: bool | None = None
    is_complete: bool
    missing_days: int = Field(default=0, ge=0)
    note: str | None = None


class DataConfidence(BaseModel):
    """Explicit uncertainty and Area of Applicability report shown next to scores."""

    level: ConfidenceLevel
    sentinel_age_days: int | None = None
    cloud_cover_pct: float | None = Field(default=None, ge=0.0, le=100.0)
    aoa_dissimilarity_index: float | None = Field(default=None, ge=0.0)
    inside_aoa: bool = True
    resolution_warnings: list[str] = Field(default_factory=list)
    consistency_warnings: list[str] = Field(default_factory=list)
    missing_factors: list[str] = Field(default_factory=list)


class ScoreComponentValue(BaseModel):
    """One registered WaterScore component (see hydro_core.scoring.SCORE_COMPONENTS)."""

    name: str
    label: str
    pillar: str
    value: float | None = Field(default=None, ge=0.0, le=100.0)
    weight_nominal: float = Field(..., ge=0.0)
    weight_used: float = Field(..., ge=0.0)
    explanation: str | None = None


class WaterScoreBreakdown(BaseModel):
    """Decomposed deterministic WaterScore on a 0-100 scale."""

    total: float = Field(..., ge=0.0, le=100.0)
    balance_score: float = Field(..., ge=0.0, le=100.0)
    satellite_score: float | None = Field(default=None, ge=0.0, le=100.0)
    soil_score: float = Field(..., ge=0.0, le=100.0)
    topography_score: float = Field(..., ge=0.0, le=100.0)
    weights_used: dict[str, float]
    components: list[ScoreComponentValue] = Field(default_factory=list)


class DayKind(str, Enum):
    """Provenance of one day in the unified weather/water timeline."""

    OBSERVED = "observed"
    RECENT = "recent"
    FORECAST = "forecast"
    CLIMATOLOGY = "climatology"


class WaterStatePoint(BaseModel):
    """Daily water state -5..+5 around the analysis date (history and projection alike)."""

    date: date
    value: float = Field(..., ge=-5.0, le=5.0)
    forecast: bool
    kind: DayKind
    storage_pct_taw: float | None = None
    precipitation_mm: float | None = None
    et0_mm: float | None = None


class WaterRiskAssessment(BaseModel):
    """Categorical risk assessment supporting absolute veto escalation."""

    level: RiskLevel
    veto_triggered: bool = False
    veto_reasons: list[str] = Field(default_factory=list)


class CauseAttribution(BaseModel):
    """Percentage breakdown of water deficit drivers across KOWR's four factors."""

    climate_pct: float = Field(..., ge=0.0, le=100.0)
    soil_pct: float = Field(..., ge=0.0, le=100.0)
    runoff_pct: float = Field(..., ge=0.0, le=100.0)
    crop_pct: float = Field(..., ge=0.0, le=100.0)
    method: str
    explanations: dict[str, str]
    # False = no significant water deficit: shares are not computed (all 0) instead of a fake 25/25/25/25
    has_deficit: bool = True


class YearlyDroughtPoint(BaseModel):
    """Single-year summary from the 10-year parcel hydrological simulation."""

    year: int
    drought_days: int = Field(..., ge=0)
    min_kbw_margin_mm: float | None = None
    kbw_drought_periods: int = Field(default=0, ge=0)
    yield_loss_pct: float = Field(..., ge=0.0, le=100.0)
    same_day_state: float | None = Field(default=None, ge=-5.0, le=5.0)
    same_day_storage_pct_taw: float | None = None
    season_precip_mm: float | None = None
    season_etc_mm: float | None = None
    is_partial: bool = False


class WaterAssessmentSummary(BaseModel):
    """Step 1 KOWR output: current moisture status, risk veto, confidence, and 10y history."""

    analysis_date: date
    crop: CropCode
    iung_category: IungSoilCategory
    soil_source_layer: str
    taw_mm: float = Field(..., gt=0.0)
    raw_mm: float = Field(..., ge=0.0)
    dr_mm: float = Field(..., ge=0.0)
    kbw_mm: float | None = None
    kbw_threshold_mm: float | None = None
    kbw_period_label: str | None = None
    kbw_periods: list[KbwPeriodResult] = Field(default_factory=list)
    weather_station: WeatherStationContext | None = None
    mean_slope_deg: float = Field(..., ge=0.0)
    mean_twi: float
    ndmi_value: float | None = None
    ndmi_interval: tuple[float, float] | None = None
    ndmi_is_reconstructed: bool = False
    water_score: WaterScoreBreakdown
    water_risk: WaterRiskAssessment
    confidence: DataConfidence
    history_10y: list[YearlyDroughtPoint]
    summary_text: str
    water_state: float = Field(default=0.0, ge=-5.0, le=5.0)
    water_state_timeline: list[WaterStatePoint] = Field(default_factory=list)
    storage_pct_taw: float | None = None
    days_to_stress: float | None = Field(default=None, ge=0.0)
    kbw_is_drought: bool | None = None
    in_season: bool = True
    crop_stage: str | None = None
    kc_today: float | None = None
    runoff_60d_mm: float | None = Field(default=None, ge=0.0)


class RetentionRecommendation(BaseModel):
    """Prioritized retention or agrotechnical measure tailored to the parcel."""

    id: str
    category: str
    title: str
    rationale: str
    delta_taw_mm: float = Field(..., ge=0.0)
    runoff_reduction_pct: float = Field(..., ge=0.0, le=100.0)
    priority_rank: int = Field(..., ge=1)
    map_hint: dict[str, Any] | None = None
    group: str = "bez_urzadzen"
    requires_permit: bool = False
    permit_note: str | None = None
    permit_url: str | None = None
    effect_reserve_days: int = 0
    effect_yield_pp: float = 0.0
    retained_runoff_mm: float = 0.0
    time_to_effect: str | None = None


class WhatIfScenarioResult(BaseModel):
    """Comparison of baseline vs selected retention measures in the driest 10y season."""

    baseline_yield_loss_pct: float = Field(..., ge=0.0, le=100.0)
    scenario_yield_loss_pct: float = Field(..., ge=0.0, le=100.0)
    delta_reserve_days: int
    saved_water_mm: float
    reference_dry_year: int
    enabled_measures: list[str] = Field(default_factory=list)
    baseline_stress_days: int = 0
    scenario_stress_days: int = 0
    retained_runoff_mm: float = 0.0


class RetentionPlan(BaseModel):
    """Step 3 KOWR output: prioritized retention actions and default What-If simulation."""

    recommendations: list[RetentionRecommendation]
    what_if_default: WhatIfScenarioResult


class IrrigationStrategySummary(BaseModel):
    """10-year historical comparison row for irrigation strategies."""

    strategy_name: str
    avg_yield_loss_pct: float = Field(..., ge=0.0, le=100.0)
    avg_water_mm: float = Field(..., ge=0.0)
    avg_deep_percolation_mm: float = Field(..., ge=0.0)
    water_use_efficiency: float = Field(..., ge=0.0)


class IrrigationPlanDay(BaseModel):
    """One day of the 7-day irrigation plan (conditional on the assumed weather)."""

    date: date
    dose_mm: float = Field(..., ge=0.0)
    precip_assumed_mm: float | None = None
    stress_expected: bool = False


class IrrigationAdvice(BaseModel):
    """Step 4 KOWR output: optional irrigation guidance using RL or FAO baseline."""

    action_now: str
    recommended_mm: float = Field(..., ge=0.0)
    rationale: str
    forecast_mode: str
    strategies_10y: list[IrrigationStrategySummary]
    policy_used: str | None = None
    plan_7d: list[IrrigationPlanDay] = Field(default_factory=list)
    in_season: bool = True
    limitations: list[str] = Field(default_factory=list)
    # Full POST /rl/recommend answer (plan_days=14) computed from the step-1 soil-water state:
    # the frontend shows it instead of its own /rl/recommend call, so both steps show one number.
    rl_recommendation: dict[str, Any] | None = None


class AdviceKind(str, Enum):
    """Type of farmer advice produced by hydro_core.advisor.ADVICE_RULES."""

    NAWADNIAJ_TERAZ = "nawadniaj_teraz"
    PRZYGOTUJ_NAWADNIANIE = "przygotuj_nawadnianie"
    POCZEKAJ_NA_OPAD = "poczekaj_na_opad"
    SUSZA_IUNG = "susza_iung"
    OSTRZEZENIE = "ostrzezenie"
    RETENCJA = "retencja"
    POZA_SEZONEM = "poza_sezonem"
    MONITORUJ = "monitoruj"


class FarmerAdvice(BaseModel):
    """Plain-language suggestion with its evidence, ordered by priority (1 = most urgent)."""

    id: str
    kind: AdviceKind
    priority: int = Field(..., ge=1, le=5)
    title: str
    text: str
    horizon_start: date | None = None
    horizon_end: date | None = None
    evidence: dict[str, float | str | None] = Field(default_factory=dict)
    based_on: list[str] = Field(default_factory=list)


class AuditMetadata(BaseModel):
    """Step 5 KOWR transparency metadata: sources, timing, and privacy compliance."""

    sources: list[SourceAuditEntry]
    execution_time_s: float = Field(..., ge=0.0)
    model_versions: dict[str, str] = Field(default_factory=dict)
    rodo_notice: str = (
        "Raport oparty wylacznie na danych przedmiotowych dzialki ewidencyjnej bez danych osobowych."
    )


class AnalysisRequest(BaseModel):
    """Input payload for running a full parcel water decision analysis."""

    parcel_id: str | None = Field(default=None, description="Cadastral TERYT parcel ID.")
    lat: float | None = Field(default=None, ge=48.0, le=56.0)
    lon: float | None = Field(default=None, ge=13.0, le=25.0)
    crop: CropCode
    analysis_date: date

    @model_validator(mode="after")
    def validate_location_input(self) -> AnalysisRequest:
        """Require either a TERYT parcel ID or a latitude/longitude coordinate pair."""
        has_id = bool(self.parcel_id and self.parcel_id.strip())
        has_xy = self.lat is not None and self.lon is not None
        if not has_id and not has_xy:
            raise ValueError("Provide either parcel_id or both lat and lon.")
        return self


class WhatIfRequest(BaseModel):
    """Input payload for interactive What-If retention simulation."""

    parcel_id: str
    crop: CropCode
    analysis_date: date
    enabled_measures: list[str] = Field(default_factory=list)


class AnalysisResponse(BaseModel):
    """Canonical end-to-end API response strictly ordered per KOWR workflow."""

    parcel: ParcelGeometry
    assessment: WaterAssessmentSummary
    causes: CauseAttribution
    retention: RetentionPlan
    irrigation: IrrigationAdvice
    map_layers: dict[str, Any] = Field(default_factory=dict)
    audit: AuditMetadata
    advice: list[FarmerAdvice] = Field(default_factory=list)


class AnalysisRefusal(BaseModel):
    """Returned instead of an analysis when the parcel is out of scope (e.g. forest, built-up)."""

    status: str
    message: str
    parcel: ParcelGeometry | None = None


class ZonesParcel(BaseModel):
    """One parcel of a joint wetness-zones request."""

    parcel_id: str = Field(min_length=3, max_length=60, description="Cadastral TERYT parcel ID.")
    water_state: float = Field(ge=-5.0, le=5.0, description="Parcel water state on the -5..+5 scale.")
    crop: str | None = Field(
        default=None,
        max_length=40,
        description="Frontend crop code; neighbours with different crops keep a hard border (no blending).",
    )


class ZonesRequest(BaseModel):
    """Joint intra-parcel wetness zones for 1-12 parcels (adjacent parcels meet without jumps)."""

    parcels: list[ZonesParcel] = Field(min_length=1, max_length=12)


class ZonesResponse(BaseModel):
    """Zones per parcel (GeoJSON FeatureCollection, EPSG:4326; same features as map_layers.zones)."""

    zones: dict[str, dict[str, Any]] = Field(default_factory=dict)
    errors: dict[str, str] = Field(default_factory=dict)
    grid_m: float
    dem_source: str
    elapsed_s: float
