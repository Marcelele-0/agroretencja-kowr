"""Offline tests of the hydro_core engine: registry, timeline, balance, terrain, scoring, steps 2-3, advice."""

from __future__ import annotations

import datetime as dt

import numpy as np
import pytest
from kowr_rl.simulator import balance_step
from synthetic_data import (
    ANALYSIS_DATE,
    PARCEL_GEOJSON,
    TODAY,
    make_inputs,
    synthetic_dem,
    synthetic_weather,
)

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
from hydro_core.schemas import AdviceKind, CropCode, DayKind, IungSoilCategory, RiskLevel
from hydro_core.scoring import (
    SCORE_COMPONENTS,
    VETO_RULES,
    assess_confidence,
    assess_risk,
    compute_water_score,
    physical_water_state,
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
    assert water_state(95.0, RiskLevel.KRYTYCZNE) == -3.0
    assert water_state(95.0, RiskLevel.NISKIE) == 4.5
    assert "stromy_stok_gleba_lekka" in VETO_RULES


def test_confidence_lists_missing_factors(fx: FeatureContext) -> None:
    conf = assess_confidence(fx)
    assert any("Sentinel-2" in m for m in conf.missing_factors)
    assert any("drenażu" in m for m in conf.missing_factors)


# --- steps 2-3, history, advice -----------------------------------------------------------
def test_causes_sum_to_100(fx: FeatureContext) -> None:
    c = attribute_causes(fx)
    assert c.climate_pct + c.soil_pct + c.runoff_pct + c.crop_pct == pytest.approx(100.0, abs=0.5)
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


def test_physical_water_state_three_regimes() -> None:
    fx = make_inputs()
    tr, tl, i0 = fx.trace, fx.timeline, fx.i0

    # 1) Full field capacity with normal dry/moderate weather -> Optimum (~0.0, NOT false wet +3.5)
    tr.dr[0, i0] = 0.0
    tr.dp[0, max(0, i0 - 29) : i0 + 1] = 0.0
    tr.ro[0, max(0, i0 - 29) : i0 + 1] = 0.0
    tl.precip[max(0, i0 - 59) : i0 + 1] = 1.0
    tl.et0[max(0, i0 - 59) : i0 + 1] = 1.5
    assert physical_water_state(fx) == 0.0

    # 2) Active water stress (Dr > RAW) -> strongly negative (<= -2.0)
    raw = float(tr.raw[0, i0])
    taw = float(tr.taw(0))
    tr.dr[0, i0] = raw + 0.4 * (taw - raw)
    assert physical_water_state(fx) <= -2.0

    # 3) Saturated soil + heavy percolation / excess rainfall -> strongly positive (>= +2.0)
    tr.dr[0, i0] = 0.0
    tr.dp[0, max(0, i0 - 13) : i0 + 1] = 7.5  # 14d * 7.5 = 105 mm excess
    assert physical_water_state(fx) >= 2.0


def test_zones_layer_clipped_to_parcel_and_covers_100_pct() -> None:
    from rasterio.features import geometry_mask
    from shapely.geometry import shape
    from shapely.ops import unary_union

    from backend.clients.geo import parcel_shape, to_puwg92
    from backend.map_layers import MapContext, build_map_layers

    parcel_2180 = to_puwg92(parcel_shape(PARCEL_GEOJSON))
    minx, _miny, _maxx, maxy = parcel_2180.bounds
    res = 20.0
    cols, rows = 30, 26
    x0, y0 = minx - 40.0, maxy + 40.0
    yy, xx = np.mgrid[0:rows, 0:cols]
    elev = (120.0 + 0.15 * (cols - xx) + 0.08 * yy).astype("float32")
    tr = (res, 0.0, x0, 0.0, -res, y0)
    mask = geometry_mask([parcel_2180], out_shape=(rows, cols), transform=tr, invert=True)
    terrain = analyse_terrain(elev, tr, res, mask)
    layers = build_map_layers(
        MapContext(
            parcel_geojson=PARCEL_GEOJSON,
            water_state=-0.8,
            terrain=terrain,
            hydro_geojson=None,
            recommendations=[],
            ndmi_grid=None,
            ndmi_transform=None,
            ndmi_crs=None,
        )
    )
    zones = layers["zones"]
    feats = zones["features"]
    assert feats
    total_pct = sum(f["properties"]["area_pct"] for f in feats)
    assert 99.0 <= total_pct <= 101.0
    p_geom = shape(PARCEL_GEOJSON)
    z_union = unary_union([shape(f["geometry"]) for f in feats])
    spill_pct = 100.0 * z_union.difference(p_geom).area / max(z_union.area, 1e-12)
    cov_pct = 100.0 * z_union.intersection(p_geom).area / max(p_geom.area, 1e-12)
    assert spill_pct < 0.5
    assert cov_pct > 99.5


def test_zones_are_continuous_across_shared_parcel_border() -> None:
    import math

    from rasterio.features import geometry_mask
    from shapely.geometry import Point, box, mapping, shape
    from shapely.ops import unary_union

    from backend.clients.geo import to_wgs84
    from backend.map_layers import MapContext, build_map_layers
    from hydro_core.topography import terrain_wetness_offset

    res = 30.0
    buf = 390.0
    box_a = box(344700.0, 468300.0, 345000.0, 468900.0)
    box_b = box(345000.0, 468300.0, 345300.0, 468900.0)

    def _terrain_for(p_2180):
        minx, miny, maxx, maxy = p_2180.bounds
        left = math.floor((minx - buf) / res) * res
        bottom = math.floor((miny - buf) / res) * res
        right = math.ceil((maxx + buf) / res) * res
        top = math.ceil((maxy + buf) / res) * res
        cols = round((right - left) / res)
        rows = round((top - bottom) / res)
        yy, xx = np.mgrid[0:rows, 0:cols]
        xs = left + (xx + 0.5) * res
        ys = top - (yy + 0.5) * res
        elev = (100.0 + 3.0 * np.sin((xs - 345000.0) / 80.0) * np.cos((ys - 468600.0) / 80.0)).astype("float32")
        tr = (res, 0.0, left, 0.0, -res, top)
        mask = geometry_mask([p_2180], out_shape=(rows, cols), transform=tr, invert=True)
        return analyse_terrain(elev, tr, res, mask)

    t_a = _terrain_for(box_a)
    t_b = _terrain_for(box_b)
    off_a = terrain_wetness_offset(t_a)
    off_b = terrain_wetness_offset(t_b)

    # Identical TPI offset along shared border x=345000 (sampled at cell centers flanking x=345000)
    for y_val in np.linspace(468330.0, 468870.0, 15):
        ra = int((t_a.transform[5] - y_val) / res)
        ca = int((344985.0 - t_a.transform[2]) / res)
        rb = int((t_b.transform[5] - y_val) / res)
        cb = int((344985.0 - t_b.transform[2]) / res)
        assert abs(float(off_a[ra, ca] - off_b[rb, cb])) < 1e-4

    geo_a = mapping(to_wgs84(box_a))
    geo_b = mapping(to_wgs84(box_b))
    z_a = build_map_layers(
        MapContext(
            parcel_geojson=geo_a, water_state=0.0, terrain=t_a,
            hydro_geojson=None, recommendations=[], ndmi_grid=None, ndmi_transform=None, ndmi_crs=None,
        )
    )["zones"]
    z_b = build_map_layers(
        MapContext(
            parcel_geojson=geo_b, water_state=0.0, terrain=t_b,
            hydro_geojson=None, recommendations=[], ndmi_grid=None, ndmi_transform=None, ndmi_crs=None,
        )
    )["zones"]

    assert len(z_a["features"]) > 1 and len(z_b["features"]) > 1
    for p_geo, z_fc in ((geo_a, z_a), (geo_b, z_b)):
        pg = shape(p_geo)
        zu = unary_union([shape(f["geometry"]) for f in z_fc["features"]])
        assert 100.0 * zu.difference(pg).area / max(zu.area, 1e-12) < 0.5
        assert 100.0 * zu.intersection(pg).area / max(pg.area, 1e-12) > 99.5

    # Classes on opposite sides (+/- 3m) of shared border x=345000 match across the boundary
    def _val_at(z_fc, x_m: float, y_m: float) -> int:
        pt = to_wgs84(Point(x_m, y_m))
        for feat in z_fc["features"]:
            if shape(feat["geometry"]).covers(pt):
                return int(feat["properties"]["value"])
        raise AssertionError(f"Point ({x_m}, {y_m}) not covered")

    matches = sum(
        _val_at(z_a, 344997.0, float(yv)) == _val_at(z_b, 345003.0, float(yv))
        for yv in np.linspace(468320.0, 468880.0, 20)
    )
    assert matches >= 19
