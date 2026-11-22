"""Two-axis assessment: WaterScore (0-100) + WaterRisk (absolute veto) + DataConfidence.

Three registries, each item self-contained:

* ``SCORE_COMPONENTS`` - functions returning a 0-100 sub-score (or None = no data). Weights and
  pillars live in ``config/scoring.yaml``; missing components have their weight redistributed.
* ``VETO_RULES`` - functions returning a ``VetoHit`` (level + reason). Never averaged.
* ``CONFIDENCE_CHECKS`` - functions returning ``ConfidenceNote`` items (warning + penalty).

Add a new variable to the formula: register a feature (features.py), register a component
that reads it here, give it a weight in scoring.yaml.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import numpy as np
import yaml

from hydro_core.crops import CONFIG_DIR, stage_on
from hydro_core.features import FeatureContext
from hydro_core.registry import Registry
from hydro_core.schemas import (
    ConfidenceLevel,
    CropCode,
    DataConfidence,
    IungSoilCategory,
    RiskLevel,
    ScoreComponentValue,
    WaterRiskAssessment,
    WaterScoreBreakdown,
)
from hydro_core.text import num

_RISK_ORDER = [RiskLevel.NISKIE, RiskLevel.UMIARKOWANE, RiskLevel.WYSOKIE, RiskLevel.KRYTYCZNE]


@lru_cache(maxsize=1)
def scoring_config() -> dict[str, Any]:
    with open(CONFIG_DIR / "scoring.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _clip(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return float(min(hi, max(lo, x)))


# ======================================================================================
# WaterScore components
# ======================================================================================
ComponentResult = tuple[float | None, str | None]


@dataclass(frozen=True)
class ScoreComponent:
    name: str
    label: str
    fn: Callable[[FeatureContext], ComponentResult]


SCORE_COMPONENTS: Registry[ScoreComponent] = Registry("składniki WaterScore")


def component(name: str, label: str) -> Callable[[Callable[[FeatureContext], ComponentResult]], Any]:
    def deco(fn: Callable[[FeatureContext], ComponentResult]) -> Callable[[FeatureContext], ComponentResult]:
        SCORE_COMPONENTS.add(name, ScoreComponent(name, label, fn))
        return fn

    return deco


def storage_score(dr: float, raw: float, taw: float) -> float:
    """100 at field capacity, ``score_at_raw`` when stress starts (Dr = RAW), 0 at wilting point."""
    at_raw = float(scoring_config()["storage"]["score_at_raw"])
    if dr <= raw:
        return _clip(100.0 - (100.0 - at_raw) * dr / max(raw, 1e-6))
    return _clip(at_raw * (taw - dr) / max(taw - raw, 1e-6))


@component("zapas_wody_w_glebie", "Zapas wody w strefie korzeniowej (FAO-56)")
def _c_storage(fx: FeatureContext) -> ComponentResult:
    dr, raw = fx.get("dr_mm"), fx.get("raw_mm")
    if dr is None or raw is None:
        return None, None
    value = storage_score(dr, raw, fx.inputs.taw_mm)
    return value, f"Zapas {fx.get('storage_pct'):.0f}% pojemności (TAW {fx.inputs.taw_mm:.0f} mm), próg stresu przy Dr = {raw:.0f} mm."


@component("kbw_iung", "Klimatyczny bilans wodny (IUNG-PIB / SMSR)")
def _c_kbw(fx: FeatureContext) -> ComponentResult:
    done = [p for p in fx.inputs.kbw_periods if p.is_complete and p.margin_mm is not None and p.threshold_mm]
    if done:
        p = done[-1]
        value = _clip(50.0 + 100.0 * p.margin_mm / abs(p.threshold_mm))  # type: ignore[arg-type, operator]
        return value, f"KBW okna {p.label}: {p.kbw_mm:.0f} mm, próg suszy {p.threshold_mm:.0f} mm."
    anomaly = fx.get("kbw_60_anomaly")
    if anomaly is None:
        return None, None
    return _clip(50.0 + anomaly / 2.0), f"KBW 60 dni odbiega od średniej wielolecia o {anomaly:+.0f} mm."


@component("wilgotnosc_lanu_ndmi", "Wilgotność łanu z satelity (Sentinel-2 NDMI)")
def _c_ndmi(fx: FeatureContext) -> ComponentResult:
    cfg = scoring_config()["satellite"]
    age = fx.get("s2_age_days")
    if age is not None and age > 30:
        return None, None
    z = fx.get("ndmi_anomaly_z")
    if z is not None:
        return _clip(50.0 + cfg["ndmi_anomaly_points_per_sigma"] * z), f"NDMI odbiega od tego samego okresu poprzednich lat o {z:+.1f} σ."
    ndmi = fx.get("ndmi")
    if ndmi is None:
        return None, None
    lo, hi = cfg["ndmi_abs_low"], cfg["ndmi_abs_high"]
    sat = fx.inputs.satellite
    if sat.reconstructed and sat.interval is not None:
        text = (f"Zdjęcie zachmurzone: NDMI odtworzone modelem HydroFusion-PL = {num(ndmi, 2)} "
                f"(przedział 90%: {num(sat.interval[0], 2)} … {num(sat.interval[1], 2)}).")
    else:
        text = f"NDMI łanu = {num(ndmi, 2)} (bez porównania z latami poprzednimi)."
    return _clip(100.0 * (ndmi - lo) / (hi - lo)), text


@component("pojemnosc_wodna_gleby", "Pojemność wodna gleby (kategoria IUNG)")
def _c_soil(fx: FeatureContext) -> ComponentResult:
    ref = float(scoring_config()["soil"]["taw_reference_mm"])
    return _clip(100.0 * fx.inputs.taw_mm / ref), f"Kategoria {fx.inputs.soil_category.value}: TAW ok. {fx.inputs.taw_mm:.0f} mm."


@component("teren_splyw", "Teren: spadek, spływ i zagłębienia (NMT)")
def _c_topo(fx: FeatureContext) -> ComponentResult:
    cfg = scoring_config()["topography"]
    slope = fx.get("slope_deg")
    if slope is None:
        return None, None
    share = fx.get("runoff_share_60d") or 0.0
    value = 100.0 - min(cfg["slope_penalty_max"], cfg["slope_penalty_per_deg"] * slope)
    value -= cfg["runoff_penalty_per_pct"] * 100.0 * share
    vol, area = fx.get("depression_volume_m3"), fx.inputs.parcel_area_ha * 10_000.0
    if vol and area > 0:
        value += min(cfg["depression_bonus_max"], 0.5 * 1000.0 * vol / area)
    return _clip(value), f"Średni spadek {slope:.1f}°, spływ {100 * share:.0f}% opadu z 60 dni."


def compute_water_score(fx: FeatureContext, overrides: dict[str, float | None] | None = None) -> WaterScoreBreakdown:
    """Weighted mean of available components (weights rescaled when some are missing)."""
    cfg = scoring_config()["components"]
    values: list[tuple[ScoreComponent, float | None, str | None, float, str]] = []
    for name, comp in SCORE_COMPONENTS.items():
        meta = cfg.get(name, {"weight": 0.0, "pillar": "other"})
        if overrides and name in overrides:
            val, expl = overrides[name], None
        else:
            val, expl = comp.fn(fx)
        values.append((comp, val, expl, float(meta["weight"]), str(meta["pillar"])))
    total_w = sum(w for _, v, _, w, _ in values if v is not None)
    rows: list[ScoreComponentValue] = []
    total = 0.0
    pillars: dict[str, list[tuple[float, float]]] = {}
    for comp, val, expl, w, pillar in values:
        used = w / total_w if (val is not None and total_w > 0) else 0.0
        if val is not None:
            total += used * val
            pillars.setdefault(pillar, []).append((w, val))
        rows.append(
            ScoreComponentValue(
                name=comp.name, label=comp.label, pillar=pillar, value=None if val is None else round(val, 1),
                weight_nominal=w, weight_used=round(used, 3), explanation=expl,
            )
        )

    def pillar_value(name: str) -> float | None:
        items = pillars.get(name)
        if not items:
            return None
        return round(sum(w * v for w, v in items) / sum(w for w, _ in items), 1)

    weights_used: dict[str, float] = {}
    for r in rows:
        weights_used[r.pillar] = round(weights_used.get(r.pillar, 0.0) + r.weight_used, 3)
    return WaterScoreBreakdown(
        total=round(_clip(total if total_w > 0 else 50.0), 1),
        balance_score=pillar_value("balance") or 50.0,
        satellite_score=pillar_value("satellite"),
        soil_score=pillar_value("soil") or 50.0,
        topography_score=pillar_value("topography") or 50.0,
        weights_used=weights_used,
        components=rows,
    )


# ======================================================================================
# Veto rules
# ======================================================================================
@dataclass(frozen=True)
class VetoHit:
    level: RiskLevel
    reason: str


VetoRule = Callable[[FeatureContext], VetoHit | None]
VETO_RULES: Registry[VetoRule] = Registry("reguły weta WaterRisk")


@VETO_RULES.register("kbw_ponizej_progu_smsr")
def _veto_kbw(fx: FeatureContext) -> VetoHit | None:
    done = [p for p in fx.inputs.kbw_periods if p.is_complete and p.is_drought is not None]
    if not done or not done[-1].is_drought:
        return None
    p = done[-1]
    if (fx.inputs.analysis_date - p.end).days > 30:  # verdict of a closed season, not the current state
        return None
    return VetoHit(
        RiskLevel.KRYTYCZNE,
        f"Susza wg progów IUNG-PIB: KBW okna {p.label} = {p.kbw_mm:.0f} mm, próg {p.threshold_mm:.0f} mm "
        f"(kat. gleby {fx.inputs.soil_category.value}).",
    )


@VETO_RULES.register("deficyt_w_fazie_wrazliwej")
def _veto_dr(fx: FeatureContext) -> VetoHit | None:
    cfg = scoring_config()["veto"]
    stage = stage_on(fx.inputs.crop, fx.inputs.analysis_date)
    ratio = fx.get("dr_over_taw")
    if stage in cfg["sensitive_stages"] and ratio is not None and ratio > cfg["dr_critical_fraction"]:
        return VetoHit(RiskLevel.KRYTYCZNE, f"Niedobór wody {100 * ratio:.0f}% TAW w fazie wrażliwej uprawy.")
    return None


@VETO_RULES.register("stromy_stok_gleba_lekka")
def _veto_slope(fx: FeatureContext) -> VetoHit | None:
    cfg = scoring_config()["veto"]
    slope = fx.get("slope_deg")
    if slope is not None and slope > cfg["steep_slope_deg"] and fx.inputs.soil_category.value in cfg["steep_light_soil_categories"]:
        return VetoHit(RiskLevel.WYSOKIE, f"Spadek {slope:.1f}° na glebie lekkiej: duży spływ i erozja przy ulewach.")
    return None


@VETO_RULES.register("stres_w_prognozie")
def _veto_projection(fx: FeatureContext) -> VetoHit | None:
    if stage_on(fx.inputs.crop, fx.inputs.analysis_date) is None:
        return None
    dts, p7 = fx.get("days_to_stress"), fx.get("forecast_p_7")
    if dts is not None and dts <= 3 and (p7 or 0.0) < 5.0:
        return VetoHit(RiskLevel.WYSOKIE, f"Stres wodny w ciągu {dts:.0f} dni, w prognozie tylko {p7 or 0:.0f} mm opadu.")
    return None


def risk_from_score(score: float) -> RiskLevel:
    cfg = scoring_config()["risk_from_score"]
    if score >= cfg["niskie"]:
        return RiskLevel.NISKIE
    if score >= cfg["umiarkowane"]:
        return RiskLevel.UMIARKOWANE
    return RiskLevel.WYSOKIE


def assess_risk(fx: FeatureContext, score: WaterScoreBreakdown) -> WaterRiskAssessment:
    hits = [h for rule in VETO_RULES.values() if (h := rule(fx)) is not None]
    level = risk_from_score(score.total)
    for h in hits:
        if _RISK_ORDER.index(h.level) > _RISK_ORDER.index(level):
            level = h.level
    return WaterRiskAssessment(
        level=level,
        veto_triggered=any(_RISK_ORDER.index(h.level) > _RISK_ORDER.index(risk_from_score(score.total)) for h in hits),
        veto_reasons=[h.reason for h in hits],
    )


def water_state(score: float, risk: RiskLevel | None = None) -> float:
    """UI scale -5..+5 = (score - 50) / 10, capped by the risk level (veto never averaged)."""
    value = (score - 50.0) / 10.0
    caps = scoring_config()["water_state"]["cap_by_risk"]
    if risk is not None and risk.value in caps:
        value = min(value, float(caps[risk.value]))
    return round(max(-5.0, min(5.0, value)), 1)


DAILY_COMPONENT = "zapas_wody_w_glebie"


def daily_water_states(
    fx: FeatureContext, score: WaterScoreBreakdown, indices: np.ndarray, b: int = 0
) -> np.ndarray:
    """Water state on the given timeline days: the storage component follows the daily balance,
    the other components are held at their analysis-date values (same weights as WaterScore)."""
    tr = fx.trace
    taw = tr.taw(b)
    others = [(r.weight_nominal, r.value) for r in score.components if r.name != DAILY_COMPONENT and r.value is not None]
    w_storage = next((r.weight_nominal for r in score.components if r.name == DAILY_COMPONENT), 0.0)
    w_total = w_storage + sum(w for w, _ in others)
    fixed_part = sum(w * v for w, v in others)
    out = np.empty(len(indices))
    for k, i in enumerate(indices):
        s = storage_score(float(tr.dr[b, i]), float(tr.raw[b, i]), taw)
        out[k] = water_state((w_storage * s + fixed_part) / w_total if w_total > 0 else 50.0)
    return out


# ======================================================================================
# Data confidence
# ======================================================================================
@dataclass(frozen=True)
class ConfidenceNote:
    kind: str  # "resolution" | "consistency" | "missing"
    message: str
    penalty: int = 0


ConfidenceCheck = Callable[[FeatureContext], list[ConfidenceNote]]
CONFIDENCE_CHECKS: Registry[ConfidenceCheck] = Registry("kontrole pewności danych")


@CONFIDENCE_CHECKS.register("skala_przestrzenna")
def _cc_scale(fx: FeatureContext) -> list[ConfidenceNote]:
    notes = [
        ConfidenceNote("resolution", "Pogoda z siatki modelu ok. 9–11 km, a nie z pomiaru na działce."),
    ]
    if fx.inputs.parcel_area_ha < 0.2:
        notes.append(ConfidenceNote("resolution", f"Działka {fx.inputs.parcel_area_ha:.2f} ha: tylko kilka pikseli Sentinel-2 (10–20 m).", 1))
    if "SoilGrids" in fx.inputs.soil_source:
        notes.append(ConfidenceNote("resolution", "Kategoria gleby oszacowana z SoilGrids 250 m (brak odczytu mapy IUNG).", 1))
    return notes


@CONFIDENCE_CHECKS.register("satelita")
def _cc_sat(fx: FeatureContext) -> list[ConfidenceNote]:
    sat = fx.inputs.satellite
    if sat.ndmi is None:
        return [ConfidenceNote("missing", "Brak bezchmurnego zdjęcia Sentinel-2 – ocena bez stanu łanu.", 1)]
    age = fx.get("s2_age_days") or 0
    notes = []
    if age > 10:
        notes.append(ConfidenceNote("resolution", f"Ostatnie bezchmurne zdjęcie sprzed {age:.0f} dni ({sat.acquired}).", 1 if age > 20 else 0))
    if sat.reconstructed:
        notes.append(ConfidenceNote("resolution", "NDMI odtworzone modelem HydroFusion-PL (zdjęcie zachmurzone).", 1))
    return notes


@CONFIDENCE_CHECKS.register("teren")
def _cc_terrain(fx: FeatureContext) -> list[ConfidenceNote]:
    if fx.inputs.terrain is None:
        return [ConfidenceNote("missing", "Brak modelu terenu – spływ i strefy wilgotne nieocenione.", 1)]
    if fx.inputs.terrain_is_fallback:
        return [ConfidenceNote("resolution", "Model terenu 30 m (Copernicus DEM) zamiast NMT 1 m – spadki przybliżone.", 1)]
    return []


@CONFIDENCE_CHECKS.register("pogoda_i_gleba")
def _cc_weather(fx: FeatureContext) -> list[ConfidenceNote]:
    notes = []
    if fx.inputs.weather_is_fallback:
        notes.append(ConfidenceNote("resolution", "Historia pogody częściowo z NASA POWER (siatka 0,5°).", 1))
    if fx.inputs.soil_organic:
        notes.append(ConfidenceNote("consistency", "Gleby organiczne: bilans dla gleb mineralnych jest przybliżeniem.", 1))
    if fx.inputs.crop_code == CropCode.UZYTKI_ZIELONE:
        notes.append(ConfidenceNote("consistency", "Dla użytków zielonych SMSR nie wyznacza progów KBW – ocena tylko z bilansu FAO-56.", 1))
    if fx.inputs.soil_category == IungSoilCategory.IV and fx.inputs.soil_organic:
        notes.append(ConfidenceNote("consistency", "Kategorię IV przyjęto umownie dla gleby organicznej."))
    return notes


@CONFIDENCE_CHECKS.register("spojnosc_zrodel")
def _cc_consistency(fx: FeatureContext) -> list[ConfidenceNote]:
    anomaly, z = fx.get("kbw_60_anomaly"), fx.get("ndmi_anomaly_z")
    if anomaly is not None and z is not None and anomaly < -60 and z > 1.0:
        return [
            ConfidenceNote(
                "consistency",
                "Susza wg bilansu meteo, a łan wilgotniejszy niż zwykle: możliwe nawadnianie lub podsiąk wód gruntowych.",
                1,
            )
        ]
    return []


@CONFIDENCE_CHECKS.register("brakujace_zrodla")
def _cc_missing(fx: FeatureContext) -> list[ConfidenceNote]:
    notes = [ConfidenceNote("missing", f"Brak danych: {name}.", 1) for name in fx.inputs.missing_sources]
    notes += [
        ConfidenceNote("missing", "Nie uwzględniono drenażu rurkowego (brak danych publicznych)."),
        ConfidenceNote("missing", "Nie uwzględniono poziomu wód gruntowych i podsiąku kapilarnego."),
    ]
    return notes


def assess_confidence(fx: FeatureContext, extra_penalty: int = 0, aoa: tuple[float | None, bool] = (None, True)) -> DataConfidence:
    notes = [n for check in CONFIDENCE_CHECKS.values() for n in check(fx)]
    penalty = sum(n.penalty for n in notes) + extra_penalty
    level = ConfidenceLevel.WYSOKA if penalty == 0 else ConfidenceLevel.SREDNIA if penalty <= 2 else ConfidenceLevel.OGRANICZONA
    sat = fx.inputs.satellite
    age = fx.get("s2_age_days")
    return DataConfidence(
        level=level,
        sentinel_age_days=None if age is None else int(age),
        cloud_cover_pct=sat.cloud_pct,
        aoa_dissimilarity_index=aoa[0],
        inside_aoa=aoa[1],
        resolution_warnings=[n.message for n in notes if n.kind == "resolution"],
        consistency_warnings=[n.message for n in notes if n.kind == "consistency"],
        missing_factors=[n.message for n in notes if n.kind == "missing"],
    )
