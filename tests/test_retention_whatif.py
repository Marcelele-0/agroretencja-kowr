"""What-If of retention measures: combination without double counting, true baseline, signs of the effects."""

from __future__ import annotations

import re

import numpy as np
import pytest
from fastapi.testclient import TestClient
from synthetic_data import ANALYSIS_DATE, make_inputs, synthetic_weather
from test_api import BODY, FakeCollector

from backend.analysis import AnalysisService
from backend.main import app
from hydro_core.features import FeatureContext, HydroContext
from hydro_core.retention import (
    MAX_DELTA_TAW_MM,
    RETENTION_MEASURES,
    applicable_ids,
    build_retention_plan,
    combined_deltas,
    what_if,
)
from hydro_core.schemas import IungSoilCategory


def _stormy_fx() -> FeatureContext:
    """Heavy soil and a 45 mm storm every 12 days: the parcel has real surface runoff to retain."""
    w = synthetic_weather(ANALYSIS_DATE)
    p = np.array(w.precipitation_mm)
    p[::12] = 45.0
    w = w.model_copy(update={"precipitation_mm": p.tolist()})
    return make_inputs(category=IungSoilCategory.IV, weather=w)


def _with_drains(fx: FeatureContext) -> FeatureContext:
    """Ditch at the border and tile drainage: both groundwater measures apply."""
    inputs = fx.inputs
    inputs.hydro = HydroContext(nearest_ditch_m=10.0, ditches_touching_parcel=1)
    inputs.tile_drainage = True
    return FeatureContext(inputs)


# --- combination of measures ---------------------------------------------------------------
def test_measures_on_one_groundwater_table_are_not_added_up() -> None:
    dam, drain = RETENTION_MEASURES.get("zastawka_na_rowie"), RETENTION_MEASURES.get("sterowany_odplyw")
    assert dam.pool == drain.pool == "wody_gruntowe"
    # 20 + 15 mm would count one raised water table twice: the stronger structure counts
    assert combined_deltas(["zastawka_na_rowie", "sterowany_odplyw"]) == (20.0, 0.0)
    assert combined_deltas(["sterowany_odplyw", "zastawka_na_rowie"]) == (20.0, 0.0)


def test_measures_on_different_reserves_add_up_to_the_cap() -> None:
    assert combined_deltas(["mulcz", "materia_organiczna"]) == (18.0, -6.0)
    assert combined_deltas(["mulcz", "mulcz"]) == combined_deltas(["mulcz"]) == (8.0, -4.0)
    d_taw, d_cn = combined_deltas(RETENTION_MEASURES.names())
    assert d_taw == MAX_DELTA_TAW_MM
    assert d_cn == sum(m.cn_delta for m in RETENTION_MEASURES.values())  # no CN measure shares a pool
    assert combined_deltas([]) == (0.0, 0.0)


def test_what_if_counts_only_applicable_measures_once(fx: FeatureContext) -> None:
    # synthetic parcel: ditch 40 m away (dam applies), no tile drainage (controlled drainage does not)
    assert applicable_ids(fx, ["sterowany_odplyw", "mulcz", "mulcz", "nie_ma_takiego", "zastawka_na_rowie"]) == [
        "mulcz", "zastawka_na_rowie",
    ]
    picked = what_if(fx, ["mulcz", "mulcz", "sterowany_odplyw", "nie_ma_takiego"])
    alone = what_if(fx, ["mulcz"])
    assert picked.enabled_measures == ["mulcz"]
    assert picked.model_dump() == alone.model_dump()


def test_what_if_with_both_groundwater_measures_equals_the_stronger_one(fx: FeatureContext) -> None:
    fx = _with_drains(fx)
    both = what_if(fx, ["zastawka_na_rowie", "sterowany_odplyw"])
    dam = what_if(fx, ["zastawka_na_rowie"])
    drain = what_if(fx, ["sterowany_odplyw"])
    assert both.enabled_measures == ["zastawka_na_rowie", "sterowany_odplyw"]
    assert both.delta_taw_mm == dam.delta_taw_mm == 20.0
    assert both.saved_water_mm == dam.saved_water_mm >= drain.saved_water_mm
    assert both.scenario_yield_loss_pct == dam.scenario_yield_loss_pct


def test_order_of_picks_does_not_change_the_result(fx: FeatureContext) -> None:
    a = what_if(fx, ["materia_organiczna", "mulcz", "zastawka_na_rowie"])
    b = what_if(fx, ["zastawka_na_rowie", "mulcz", "materia_organiczna"])
    assert {k: v for k, v in a.model_dump().items() if k != "enabled_measures"} == {
        k: v for k, v in b.model_dump().items() if k != "enabled_measures"
    }


# --- baseline ("Teraz") -------------------------------------------------------------------------
def test_no_measures_is_exactly_the_baseline() -> None:
    fx = _stormy_fx()
    w = what_if(fx, [])
    assert w.enabled_measures == []
    assert w.scenario_yield_loss_pct == w.baseline_yield_loss_pct
    assert w.scenario_stress_days == w.baseline_stress_days
    assert w.saved_water_mm == 0.0 and w.retained_runoff_mm == 0.0 and w.delta_reserve_days == 0
    assert w.scenario_runoff_mm == w.baseline_runoff_mm > 0.0
    assert w.delta_taw_mm == 0.0


def test_a_measure_without_curve_number_change_keeps_the_runoff() -> None:
    w = what_if(_stormy_fx(), ["zastawka_na_rowie"])
    assert w.baseline_runoff_mm > 0.0
    assert w.scenario_runoff_mm == w.baseline_runoff_mm and w.retained_runoff_mm == 0.0


def test_baseline_does_not_depend_on_the_picked_measures() -> None:
    fx = _stormy_fx()
    rows = [what_if(fx, ids) for ids in ([], ["mulcz"], ["pas_buforowy", "zbiornik_w_zaglebieniu"], RETENTION_MEASURES.names())]
    assert len({(r.baseline_yield_loss_pct, r.baseline_stress_days, r.baseline_runoff_mm) for r in rows}) == 1


# --- physical limits and signs ---------------------------------------------------------------------
def test_retained_runoff_never_exceeds_the_parcel_runoff() -> None:
    fx = _stormy_fx()
    every = what_if(fx, RETENTION_MEASURES.names())
    assert 0.0 < every.retained_runoff_mm <= every.baseline_runoff_mm
    assert every.retained_runoff_mm == pytest.approx(every.baseline_runoff_mm - every.scenario_runoff_mm, abs=0.11)
    buffer, pond = what_if(fx, ["pas_buforowy"]), what_if(fx, ["zbiornik_w_zaglebieniu"])
    both = what_if(fx, ["pas_buforowy", "zbiornik_w_zaglebieniu"])
    assert max(buffer.retained_runoff_mm, pond.retained_runoff_mm) <= both.retained_runoff_mm <= both.baseline_runoff_mm
    for r in build_retention_plan(fx).recommendations:
        assert 0.0 <= r.runoff_reduction_pct <= 100.0
        assert 0.0 <= r.retained_runoff_mm <= every.baseline_runoff_mm


def test_more_water_kept_means_signed_gains(fx: FeatureContext) -> None:
    w = what_if(fx, ["materia_organiczna", "mulcz", "zastawka_na_rowie"])
    assert w.saved_water_mm > 0.0  # the crop gets more water: shown as "+"
    assert w.scenario_yield_loss_pct <= w.baseline_yield_loss_pct
    assert w.delta_reserve_days == w.baseline_stress_days - w.scenario_stress_days >= 0
    assert 0.0 < w.delta_taw_mm <= MAX_DELTA_TAW_MM


def test_a_measure_card_matches_the_summary_of_that_measure_alone(fx: FeatureContext) -> None:
    for r in build_retention_plan(fx).recommendations:
        w = what_if(fx, [r.id])
        assert r.effect_yield_pp == pytest.approx(max(0.0, w.baseline_yield_loss_pct - w.scenario_yield_loss_pct), abs=1e-9)
        assert r.effect_reserve_days == w.delta_reserve_days
        assert r.retained_runoff_mm == w.retained_runoff_mm


def test_rationale_states_only_gains_with_polish_numbers() -> None:
    plan = build_retention_plan(_stormy_fx())
    assert any("spływ mniejszy o" in r.rationale for r in plan.recommendations)
    for r in plan.recommendations:
        text = r.rationale
        assert "−-" not in text and "--" not in text and "+-" not in text and ".." not in text
        assert text.endswith(".")
        assert not re.search(r"\b0 dni\b|−0[,.]0|o 0 mm", text), text
        assert not re.search(r"\d\.\d", text), text  # decimal comma
        assert ("strata plonu mniejsza" in text) == (r.effect_yield_pp >= 0.05)
        assert ("spływ mniejszy" in text) == (r.retained_runoff_mm >= 0.5)
        assert r.pool == RETENTION_MEASURES.get(r.id).pool


# --- API ------------------------------------------------------------------------------------------
def test_api_what_if_reports_the_measures_it_used() -> None:
    with TestClient(app) as client:
        fake = FakeCollector()
        app.state.collector = fake
        app.state.service = AnalysisService(fake)  # type: ignore[arg-type]
        w = client.post("/api/what-if", json={**BODY, "enabled_measures": ["mulcz", "mulcz", "sterowany_odplyw", "zastawka_na_rowie"]})
    assert w.status_code == 200, w.text
    d = w.json()
    assert d["enabled_measures"] == ["mulcz", "zastawka_na_rowie"]  # no drainage known: controlled outflow does not apply
    assert d["delta_taw_mm"] == 28.0
    assert d["scenario_runoff_mm"] <= d["baseline_runoff_mm"]
