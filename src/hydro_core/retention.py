"""Step 3: retention and agrotechnical measures, ranked by their simulated effect, and What-If.

Measures are data (``config/retention_measures.yaml``); applicability conditions are named
predicates in ``APPLICABILITY`` (add a condition = one decorated function, reference it by name
in the YAML). Every measure is simulated with the same FAO-56 + SCS-CN balance on the driest
crop season of the last 10 years (reference year), starting from the baseline soil state.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import numpy as np
import yaml

from hydro_core.crops import CONFIG_DIR, kc_and_p_for_dates
from hydro_core.fao_balance import WindowResult, season_summary, simulate_window
from hydro_core.features import FeatureContext
from hydro_core.registry import Registry
from hydro_core.runoff import CN_MAX, CN_MIN, parcel_cn
from hydro_core.schemas import (
    PERENNIAL_CROPS,
    CropCode,
    RetentionPlan,
    RetentionRecommendation,
    WhatIfScenarioResult,
)
from hydro_core.text import effect_text

GROUP_ORDER = ("bez_urzadzen", "wymaga_zgody")
MAX_DELTA_TAW_MM = 40.0
DEFAULT_WHAT_IF_COUNT = 2


@lru_cache(maxsize=1)
def measures_config() -> dict[str, Any]:
    with open(CONFIG_DIR / "retention_measures.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


# --------------------------------------------------------------------------------------
# Applicability predicates
# --------------------------------------------------------------------------------------
Predicate = Callable[[FeatureContext], bool]
APPLICABILITY: Registry[Predicate] = Registry("warunki stosowalności zabiegów")


@APPLICABILITY.register("not_grassland")
def _not_grassland(fx: FeatureContext) -> bool:
    return fx.inputs.crop_code != CropCode.UZYTKI_ZIELONE


@APPLICABILITY.register("annual_crop")
def _annual_crop(fx: FeatureContext) -> bool:
    """Crop sown every year (catch crop and no-till make sense): not grassland, orchard, hops or strawberries."""
    return fx.inputs.crop_code != CropCode.UZYTKI_ZIELONE and fx.inputs.crop_code not in PERENNIAL_CROPS


@APPLICABILITY.register("sloped_parcel")
def _sloped(fx: FeatureContext) -> bool:
    slope = fx.get("slope_deg")
    return slope is None or slope >= 1.5


@APPLICABILITY.register("ditch_nearby")
def _ditch_nearby(fx: FeatureContext) -> bool:
    d = fx.inputs.hydro.nearest_ditch_m
    return d is not None and d <= 150.0


@APPLICABILITY.register("ditch_touching")
def _ditch_touching(fx: FeatureContext) -> bool:
    return fx.inputs.hydro.ditches_touching_parcel > 0 or (fx.inputs.hydro.nearest_ditch_m or 1e9) <= 20.0


@APPLICABILITY.register("tile_drained")
def _tile_drained(fx: FeatureContext) -> bool:
    """The farmer's answer when given; otherwise a ditch at the parcel border (a likely drain outlet)."""
    known = fx.inputs.tile_drainage
    return _ditch_touching(fx) if known is None else known


@APPLICABILITY.register("has_depressions")
def _depressions(fx: FeatureContext) -> bool:
    return (fx.get("depression_volume_m3") or 0.0) >= 5.0


# --------------------------------------------------------------------------------------
# Measures
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class RetentionMeasure:
    id: str
    title: str
    category: str
    group: str
    delta_taw_mm: float
    cn_delta: float
    time_to_effect: str
    rationale: str
    applies_if: tuple[str, ...]
    pool: str | None = None

    def applicable(self, fx: FeatureContext) -> bool:
        return all(APPLICABILITY.get(name)(fx) for name in self.applies_if)

    @property
    def pool_key(self) -> str:
        """Water reserve the measure acts on: its pool, or the measure alone."""
        return self.pool or self.id


def load_measures() -> Registry[RetentionMeasure]:
    reg: Registry[RetentionMeasure] = Registry("zabiegi retencyjne")
    for mid, m in measures_config()["measures"].items():
        reg.add(
            mid,
            RetentionMeasure(
                id=mid, title=m["title"], category=m["category"], group=m["group"],
                delta_taw_mm=float(m["delta_taw_mm"]), cn_delta=float(m["cn_delta"]),
                time_to_effect=m.get("time_to_effect", ""), rationale=m.get("rationale", ""),
                applies_if=tuple(m.get("applies_if") or ()), pool=m.get("pool"),
            ),
        )
    return reg


RETENTION_MEASURES = load_measures()


# --------------------------------------------------------------------------------------
# Reference (driest) season and simulation
# --------------------------------------------------------------------------------------
def reference_dry_year(fx: FeatureContext) -> int:
    """Completed crop season with the largest FAO-33 yield loss before the analysis date."""
    crop, ad = fx.inputs.crop, fx.inputs.analysis_date
    best: tuple[float, int] | None = None
    for year in fx.timeline.years():
        if crop.season_end(year) >= ad:
            continue
        s = season_summary(fx.trace, crop, year)
        if s is not None and not s.is_partial and (best is None or s.yield_loss_pct > best[0]):
            best = (s.yield_loss_pct, year)
    return best[1] if best else ad.year


def applicable_ids(fx: FeatureContext, measure_ids: list[str]) -> list[str]:
    """Known measures that apply to this parcel, each once, in the given order."""
    out: list[str] = []
    for mid in measure_ids:
        if mid in RETENTION_MEASURES and mid not in out and RETENTION_MEASURES.get(mid).applicable(fx):
            out.append(mid)
    return out


def combined_deltas(measure_ids: list[str]) -> tuple[float, float]:
    """(ΔTAW, ΔCN) of measures applied together, without counting one water reserve twice.

    Measures of one pool act on the same reserve (e.g. a ditch dam and a closed drain outlet raise
    the same groundwater table): only the strongest of the pool counts. Pools add up; the
    root-zone gain is capped at ``MAX_DELTA_TAW_MM`` and the curve number is clamped by ``parcel_cn``.
    """
    best: dict[str, tuple[float, float]] = {}
    for mid in dict.fromkeys(measure_ids):
        m = RETENTION_MEASURES.get(mid)
        taw, cn = best.get(m.pool_key, (m.delta_taw_mm, m.cn_delta))
        best[m.pool_key] = (max(taw, m.delta_taw_mm), min(cn, m.cn_delta))
    d_taw = sum(t for t, _ in best.values())
    d_cn = sum(c for _, c in best.values())
    return min(MAX_DELTA_TAW_MM, d_taw), d_cn


def _scenario_params(fx: FeatureContext, measure_ids: list[str]) -> tuple[float, float]:
    """(TAW, CN) of the parcel with the given measures applied together.

    The CN shift is applied to the parcel's own baseline CN, so no measures (or measures without a
    CN change) give exactly the baseline row and never invent a runoff change.
    """
    d_taw, d_cn = combined_deltas(measure_ids)
    taw = fx.inputs.taw_mm + d_taw
    if not d_cn:
        return taw, fx.inputs.cn
    args = (fx.inputs.soil_category, fx.get("slope_deg") or 0.0)
    grass = fx.inputs.crop_code == CropCode.UZYTKI_ZIELONE
    shift = parcel_cn(*args, grassland=grass, delta=d_cn) - parcel_cn(*args, grassland=grass)
    return taw, min(CN_MAX, max(CN_MIN, fx.inputs.cn + shift))


def simulate_scenarios(fx: FeatureContext, year: int, combos: list[list[str]]) -> WindowResult:
    """Season ``year`` (1 March .. season end) for baseline (row 0) + each measure combination."""
    tl, crop = fx.timeline, fx.inputs.crop
    start = tl.index_of(dt.date(year, 3, 1)) or 0
    end_date = crop.season_end(year)
    end = tl.index_of(end_date)
    end = len(tl) - 1 if end is None else end
    sl = slice(start, end + 1)
    kc, p_tab, in_season = kc_and_p_for_dates(crop, tl.dates[sl])
    dr_start = float(fx.trace.dr[0, start - 1]) if start > 0 else 0.0
    params = [_scenario_params(fx, [])] + [_scenario_params(fx, c) for c in combos]
    taw = np.array([p[0] for p in params])
    cn = np.array([p[1] for p in params])
    dr0 = np.minimum(dr_start, taw)
    return simulate_window(tl.precip[sl], tl.et0[sl], kc, p_tab, taw, cn, dr0, ky=crop.ky, season_mask=in_season)


def _row_effect(res: WindowResult, i: int) -> dict[str, float]:
    # Rounded first, so a measure's gain equals the difference of the losses shown with and without it.
    loss_b = round(100.0 * (1.0 - float(res.yield_rel[0])), 1)
    loss_s = round(100.0 * (1.0 - float(res.yield_rel[i])), 1)
    ro_b, ro_s = float(res.ro_mm[0]), float(res.ro_mm[i])
    return {
        "loss_base": loss_b,
        "loss_scen": loss_s,
        "yield_pp": round(loss_b - loss_s, 1),
        "reserve_days": round(float(res.stress_days[0] - res.stress_days[i])),
        "retained_runoff_mm": round(max(0.0, ro_b - ro_s), 1),
        "runoff_reduction_pct": round(100.0 * max(0.0, ro_b - ro_s) / ro_b, 1) if ro_b > 0.5 else 0.0,
        "runoff_base": round(ro_b, 1),
        "runoff_scen": round(ro_s, 1),
        # Signed: extra water the crop used (ETa) with the measures; negative = less.
        "saved_water_mm": round(float(res.eta_mm[i] - res.eta_mm[0]), 1),
        "stress_base": int(res.stress_days[0]),
        "stress_scen": int(res.stress_days[i]),
    }


def _effect_sentence(year: int, eff: dict[str, float]) -> str:
    """Simulated effect in words: only the gains, each with its own correct sign."""
    text = effect_text(eff["yield_pp"], eff["reserve_days"], eff["retained_runoff_mm"])
    if not text:
        return ""
    return f" W najsuchszym sezonie {year}: {text}{'' if text.endswith('.') else '.'}"


def _map_hint(fx: FeatureContext, m: RetentionMeasure) -> dict[str, Any]:
    t = fx.inputs.terrain
    if m.id == "pas_buforowy" and t is not None and t.outlet_rc:
        x, y = t.cell_center(*t.outlet_rc)
        return {"type": "point", "crs": "EPSG:2180", "x": x, "y": y, "label": "Najniższy punkt odpływu wody z działki"}
    if m.id == "zbiornik_w_zaglebieniu" and t is not None and t.wettest_rc:
        x, y = t.cell_center(*t.wettest_rc)
        return {"type": "point", "crs": "EPSG:2180", "x": x, "y": y, "label": "Miejsce akumulacji wody (najwyższy TWI)"}
    if m.id in ("zastawka_na_rowie", "sterowany_odplyw"):
        return {"type": "ditch", "distance_m": fx.inputs.hydro.nearest_ditch_m, "label": "Najbliższy rów melioracyjny (BDOT10k)"}
    return {"type": "parcel", "label": "Cała działka"}


def build_retention_plan(fx: FeatureContext) -> RetentionPlan:
    year = reference_dry_year(fx)
    measures = [m for m in RETENTION_MEASURES.values() if m.applicable(fx)]
    res = simulate_scenarios(fx, year, [[m.id] for m in measures])
    permit = measures_config()["permit"]
    scored = []
    for i, m in enumerate(measures, start=1):
        eff = _row_effect(res, i)
        scored.append((m, eff))
    scored.sort(key=lambda me: (GROUP_ORDER.index(me[0].group), -me[1]["yield_pp"], -me[1]["reserve_days"], -me[1]["retained_runoff_mm"]))
    recs = []
    for rank, (m, eff) in enumerate(scored, start=1):
        needs = m.group == "wymaga_zgody"
        recs.append(
            RetentionRecommendation(
                id=m.id, category=m.category, title=m.title,
                rationale=f"{m.rationale}{_effect_sentence(year, eff)}",
                delta_taw_mm=m.delta_taw_mm,
                runoff_reduction_pct=min(100.0, eff["runoff_reduction_pct"]),
                priority_rank=rank,
                map_hint=_map_hint(fx, m),
                group=m.group,
                requires_permit=needs,
                permit_note=permit["note"] if needs else None,
                permit_url=permit["url"] if needs else None,
                effect_reserve_days=eff["reserve_days"],
                effect_yield_pp=max(0.0, eff["yield_pp"]),
                retained_runoff_mm=eff["retained_runoff_mm"],
                time_to_effect=m.time_to_effect,
                pool=m.pool,
            )
        )
    default = [r.id for r in recs if r.group == "bez_urzadzen"][:DEFAULT_WHAT_IF_COUNT]
    return RetentionPlan(recommendations=recs, what_if_default=what_if(fx, default, year))


def what_if(fx: FeatureContext, enabled: list[str], year: int | None = None) -> WhatIfScenarioResult:
    """Baseline vs all ``enabled`` measures applied together, in the reference dry season.

    Only measures that apply to this parcel count, each once; unknown or not applicable ids are
    dropped, so the scenario holds exactly the measures listed in ``enabled_measures``.
    """
    year = reference_dry_year(fx) if year is None else year
    known = applicable_ids(fx, enabled)
    res = simulate_scenarios(fx, year, [known])
    eff = _row_effect(res, 1)
    return WhatIfScenarioResult(
        baseline_yield_loss_pct=min(100.0, max(0.0, eff["loss_base"])),
        scenario_yield_loss_pct=min(100.0, max(0.0, eff["loss_scen"])),
        delta_reserve_days=eff["reserve_days"],
        saved_water_mm=eff["saved_water_mm"],
        reference_dry_year=year,
        enabled_measures=known,
        baseline_stress_days=eff["stress_base"],
        scenario_stress_days=eff["stress_scen"],
        retained_runoff_mm=eff["retained_runoff_mm"],
        baseline_runoff_mm=eff["runoff_base"],
        scenario_runoff_mm=eff["runoff_scen"],
        delta_taw_mm=combined_deltas(known)[0],
    )
