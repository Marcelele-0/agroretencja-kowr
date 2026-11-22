"""Offline tests of the hydro_core engine: registry, timeline, balance, terrain, scoring, steps 2-3, advice."""

from __future__ import annotations

import datetime as dt

import numpy as np
import pytest
from kowr_rl.simulator import balance_step
from synthetic_data import ANALYSIS_DATE, TODAY, make_inputs, synthetic_dem, synthetic_weather

from hydro_core.advisor import AdviceContext, build_advice
from hydro_core.causes import attribute_causes
from hydro_core.features import FEATURES, FeatureContext, feature
from hydro_core.history import build_history
from hydro_core.registry import Registry, RegistryError
from hydro_core.retention import (
    RETENTION_MEASURES,
    build_retention_plan,
    reference_dry_year,
    what_if,
)
from hydro_core.runoff import parcel_cn, scs_runoff
from hydro_core.schemas import (
    PERENNIAL_CROPS,
    AdviceKind,
    CropCode,
    DayKind,
    IungSoilCategory,
    RiskLevel,
)
from hydro_core.scoring import (
    SCORE_COMPONENTS,
    VETO_RULES,
    assess_confidence,
    assess_risk,
    compute_water_score,
    physical_water_state,
    risk_floor_from_state,
    storage_score,
    water_state,
)
from hydro_core.text import days, num
from hydro_core.timeline import build_timeline
from hydro_core.topography import analyse_terrain


# --- registry ---------------------------------------------------------------------------
def test_registry_order_duplicates_and_errors() -> None:
    reg: Registry[int] = Registry("test")
    reg.add("a", 1)

    @reg.register("b")
    def _b() -> int:
        return 2

    assert reg.names() == ["a", "b"]
    with pytest.raises(RegistryError):
        reg.add("a", 3)
    reg.add("a", 3, replace=True)
    assert reg.get("a") == 3
    with pytest.raises(RegistryError, match="Dostępne"):
        reg.get("zzz")


def test_new_feature_is_available_to_ml_vector(fx: FeatureContext) -> None:
    @feature("test_double_p15", group="klimat", unit="mm")
    def _f(ctx: FeatureContext) -> float | None:
        p = ctx.get("p_15")
        return None if p is None else 2 * p

    try:
        assert fx.get("test_double_p15") == pytest.approx(2 * fx.get("p_15"))
        assert fx.vector(["p_15", "test_double_p15", "ndmi"])[2] != fx.vector(["ndmi"])[0] or np.isnan(fx.vector(["ndmi"])[0])
    finally:
        FEATURES.remove("test_double_p15")


# --- timeline -----------------------------------------------------------------------------
def test_timeline_as_of_has_no_look_ahead_and_projects_climatology() -> None:
    w = synthetic_weather(dt.date(2021, 12, 31))  # data AFTER the analysis date exists...
    tl = build_timeline(dates=w.dates, precipitation_mm=w.precipitation_mm, et0_mm=w.et0_mm, tmax_c=w.tmax_c,
                        analysis_date=ANALYSIS_DATE, today=TODAY, projection_days=14)
    i0 = tl.analysis_index
    assert tl.kind_of(i0) == DayKind.OBSERVED
    after = [tl.kind_of(i) for i in range(i0 + 1, len(tl))]
    assert len(after) == 14 and all(k == DayKind.CLIMATOLOGY for k in after)  # ...but is not used


def test_timeline_uses_forecast_after_today() -> None:
    today = dt.date(2025, 6, 1)
    w = synthetic_weather(today)
    fdates = [today + dt.timedelta(days=k) for k in range(15)]
    tl = build_timeline(dates=w.dates, precipitation_mm=w.precipitation_mm, et0_mm=w.et0_mm, tmax_c=w.tmax_c,
                        analysis_date=today, today=today, forecast_dates=fdates, forecast_precip_mm=[9.0] * 15,
                        forecast_et0_mm=[3.0] * 15, forecast_tmax_c=[25.0] * 15)
    i0 = tl.analysis_index
    assert tl.kind_of(i0 - 1) == DayKind.RECENT
    assert tl.kind_of(i0 + 1) == DayKind.FORECAST and tl.precip[i0 + 1] == 9.0


# --- balance & runoff -----------------------------------------------------------------------
def test_shared_balance_step_closes_mass_balance() -> None:
    dr = np.array([40.0])
    taw = np.array([150.0])
    p, irr = np.array([12.0]), np.array([10.0])
    new, eta, _etc, dp, _ks, _raw = balance_step(dr, p, irr, np.array([4.0]), np.array([1.1]), taw, np.array([0.55]))
    assert (taw - new) - (taw - dr) == pytest.approx(p + irr - eta - dp)


def test_runoff_grows_with_slope_and_shrinks_with_measures() -> None:
    flat = parcel_cn(IungSoilCategory.III, 0.5)
    steep = parcel_cn(IungSoilCategory.III, 8.0)
    mulch = parcel_cn(IungSoilCategory.III, 8.0, delta=-6.0)
    rain = np.array([40.0])
    assert scs_runoff(rain, steep)[0] > scs_runoff(rain, flat)[0]
    assert scs_runoff(rain, mulch)[0] < scs_runoff(rain, steep)[0]
    assert scs_runoff(np.array([2.0]), steep)[0] == 0.0


# --- terrain --------------------------------------------------------------------------------
def test_terrain_slope_flow_and_depression() -> None:
    dem = synthetic_dem(slope_x=0.05)
    t = analyse_terrain(dem.elevation, dem.transform, dem.resolution_m, dem.parcel_mask)
    expected = np.degrees(np.arctan(np.hypot(0.05 / 2.0, 0.01 / 2.0)))
    assert t.mean_slope_deg == pytest.approx(expected, abs=0.3)
    assert t.depression_volume_m3 > 0
    assert t.outlet_rc is not None and t.outlet_rc[1] > 60  # water leaves at the low (east) side


# --- scoring --------------------------------------------------------------------------------
def test_storage_score_is_continuous_at_raw() -> None:
    assert storage_score(0, 80, 150) == 100
    assert storage_score(80, 80, 150) == pytest.approx(storage_score(80.0001, 80, 150), abs=0.01)
    assert storage_score(150, 80, 150) == 0


def test_missing_satellite_weight_is_redistributed(fx: FeatureContext) -> None:
    score = compute_water_score(fx)
    used = {c.name: c.weight_used for c in score.components}
    assert used["wilgotnosc_lanu_ndmi"] == 0
    assert sum(used.values()) == pytest.approx(1.0, abs=5e-3)  # weights rounded to 3 decimals
    assert score.satellite_score is None
    assert {c.name for c in score.components} == set(SCORE_COMPONENTS.names())


def test_veto_and_state_cap() -> None:
    fx = make_inputs(category=IungSoilCategory.I, dem=synthetic_dem(slope_x=0.25))
    score = compute_water_score(fx)
    risk = assess_risk(fx, score)
    assert any("Spadek" in r for r in risk.veto_reasons)
    assert risk.level in (RiskLevel.WYSOKIE, RiskLevel.KRYTYCZNE)
    assert "stromy_stok_gleba_lekka" in VETO_RULES
    # the slope veto is about erosion, not about lack of water: it never caps the water state
    hit = VETO_RULES.get("stromy_stok_gleba_lekka")(fx)
    assert hit is not None and hit.dry is False
    assert -5.0 <= water_state(fx) <= 5.0


def test_physical_water_state_axis() -> None:
    # dry side: optimum while Dr <= 0.5 RAW, -1 at the start of stress, -5 at the wilting point
    assert physical_water_state(dr=10.0, raw=100.0, taw=200.0, kbw60=0.0) == 0.0
    assert physical_water_state(dr=100.0, raw=100.0, taw=200.0, kbw60=0.0) == -1.0
    assert physical_water_state(dr=150.0, raw=100.0, taw=200.0, kbw60=0.0) == -3.0
    assert physical_water_state(dr=200.0, raw=100.0, taw=200.0, kbw60=0.0) == -5.0
    # wet side only when the soil is close to field capacity (same steps as the browser scale)
    assert physical_water_state(dr=0.0, raw=100.0, taw=200.0, kbw60=100.0) == 2.0
    assert physical_water_state(dr=0.0, raw=100.0, taw=200.0, kbw60=300.0) == 5.0
    # a big positive KBW does not hide a depleted root zone
    assert physical_water_state(dr=150.0, raw=100.0, taw=200.0, kbw60=300.0) == -3.0


def test_stress_is_never_low_risk() -> None:
    assert risk_floor_from_state(-3.0) == RiskLevel.WYSOKIE
    assert risk_floor_from_state(-4.6) == RiskLevel.KRYTYCZNE
    assert risk_floor_from_state(0.4) == RiskLevel.NISKIE


def test_confidence_lists_missing_factors(fx: FeatureContext) -> None:
    conf = assess_confidence(fx)
    assert any("Sentinel-2" in m for m in conf.missing_factors)
    assert any("drenażu" in m for m in conf.missing_factors)


# --- steps 2-3, history, advice -----------------------------------------------------------
def test_causes_sum_to_100(fx: FeatureContext) -> None:
    c = attribute_causes(fx)
    expected = 100.0 if c.has_deficit else 0.0  # no deficit -> no invented shares
    assert c.climate_pct + c.soil_pct + c.runoff_pct + c.crop_pct == pytest.approx(expected, abs=0.5)
    assert set(c.explanations) == {"klimat", "gleba", "splyw", "uprawa"}


def test_retention_plan_groups_and_what_if(fx: FeatureContext) -> None:
    plan = build_retention_plan(fx)
    groups = [r.group for r in plan.recommendations]
    assert groups == sorted(groups, key=["bez_urzadzen", "wymaga_zgody"].index)
    assert [r.priority_rank for r in plan.recommendations] == list(range(1, len(groups) + 1))
    permit = [r for r in plan.recommendations if r.requires_permit]
    assert permit and all(r.permit_url for r in permit)  # ditch 40 m away -> zastawka applicable
    year = reference_dry_year(fx)
    assert year < ANALYSIS_DATE.year
    all_free = [m.id for m in RETENTION_MEASURES.values() if m.group == "bez_urzadzen"]
    wi = what_if(fx, all_free)
    assert wi.scenario_yield_loss_pct <= wi.baseline_yield_loss_pct
    assert wi.reference_dry_year == year


@pytest.mark.parametrize("crop", list(CropCode))
def test_every_ui_crop_has_a_full_assessment(crop: CropCode) -> None:
    """All 15 crops of the frontend: score, water state, causes and retention are computed."""
    fx = make_inputs(crop=crop)
    score = compute_water_score(fx)
    assert 0.0 <= score.total <= 100.0
    assert -5.0 <= water_state(fx) <= 5.0
    attribute_causes(fx)
    ids = {r.id for r in build_retention_plan(fx).recommendations}
    if crop in PERENNIAL_CROPS or crop == CropCode.UZYTKI_ZIELONE:
        assert "miedzyplon" not in ids and "uprawa_uproszczona" not in ids


def test_history_has_ten_years_and_same_day_state(fx: FeatureContext) -> None:
    rows = build_history(fx, compute_water_score(fx))
    assert len(rows) == 11
    assert rows[-1].is_partial and rows[-1].year == ANALYSIS_DATE.year
    assert all(r.same_day_state is not None for r in rows)


def test_advice_prepares_for_drought_when_dry_forecast() -> None:
    w = synthetic_weather(ANALYSIS_DATE, dry_last_days=25)
    fx = make_inputs(crop=CropCode.ZIEMNIAK, category=IungSoilCategory.I, weather=w)
    score = compute_water_score(fx)
    risk = assess_risk(fx, score)
    plan = build_retention_plan(fx)
    advice = build_advice(fx, AdviceContext(risk=risk, retention=plan, irrigation=None, history=[], current_kbw=None, projection_kind="klimatologia"))
    kinds = {a.kind for a in advice}
    assert kinds & {AdviceKind.PRZYGOTUJ_NAWADNIANIE, AdviceKind.OSTRZEZENIE, AdviceKind.POCZEKAJ_NA_OPAD}
    assert advice == sorted(advice, key=lambda a: a.priority)


def test_off_season_advice() -> None:
    fx = make_inputs(crop=CropCode.PSZENICA_JARA, analysis_date=dt.date(2021, 11, 20))
    score = compute_water_score(fx)
    advice = build_advice(fx, AdviceContext(
        risk=assess_risk(fx, score), retention=build_retention_plan(fx), irrigation=None,
        history=build_history(fx, score), current_kbw=None, projection_kind="klimatologia",
    ))
    assert advice[0].kind == AdviceKind.POZA_SEZONEM


def test_polish_text_helpers() -> None:
    assert num(3.45, 1, sign=True) == "+3,5"
    assert num(-2.0) == "−2"
    assert days(1) == "1 dzień" and days(3) == "3 dni" and days(12) == "12 dni" and days(22) == "22 dni"


def test_backend_crops_are_the_rl_crops():
    """Every CropCode of the API is handled by the RL agent with the same crop physics as the assessment."""
    from kowr_rl.config import load_crops, resolve_crop

    from hydro_core.crops import all_crops, rl_crop_keys
    from hydro_core.schemas import CropCode

    rl = load_crops()
    for code in CropCode:
        assert code.value in rl_crop_keys()
        assert all_crops()[code.value] == rl[resolve_crop(code.value)]
