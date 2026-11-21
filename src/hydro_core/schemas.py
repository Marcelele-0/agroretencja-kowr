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


class WaterScoreBreakdown(BaseModel):
    """Decomposed deterministic WaterScore on a 0-100 scale."""

    total: float = Field(..., ge=0.0, le=100.0)
    balance_score: float = Field(..., ge=0.0, le=100.0)
    satellite_score: float | None = Field(default=None, ge=0.0, le=100.0)
    soil_score: float = Field(..., ge=0.0, le=100.0)
    topography_score: float = Field(..., ge=0.0, le=100.0)
    weights_used: dict[str, float]


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


class YearlyDroughtPoint(BaseModel):
    """Single-year summary from the 10-year parcel hydrological simulation."""

    year: int
    drought_days: int = Field(..., ge=0)
    min_kbw_60: float
    yield_loss_pct: float = Field(..., ge=0.0, le=100.0)


class WaterAssessmentSummary(BaseModel):
    """Step 1 KOWR output: current moisture status, risk veto, confidence, and 10y history."""

    analysis_date: date
    crop: CropCode
    iung_category: IungSoilCategory
    taw_mm: float = Field(..., gt=0.0)
    raw_mm: float = Field(..., ge=0.0)
    dr_mm: float = Field(..., ge=0.0)
    kbw_60_mm: float
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


class WhatIfScenarioResult(BaseModel):
    """Comparison of baseline vs selected retention measures in the driest 10y season."""

    baseline_yield_loss_pct: float = Field(..., ge=0.0, le=100.0)
    scenario_yield_loss_pct: float = Field(..., ge=0.0, le=100.0)
    delta_reserve_days: int
    saved_water_mm: float
    reference_dry_year: int


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


class IrrigationAdvice(BaseModel):
    """Step 4 KOWR output: optional irrigation guidance using RL or FAO baseline."""

    action_now: str
    recommended_mm: float = Field(..., ge=0.0)
    rationale: str
    forecast_mode: str
    strategies_10y: list[IrrigationStrategySummary]


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
