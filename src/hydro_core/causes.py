"""Step 2: why is water missing? Split into the four KOWR factor groups.

Physical attribution (always available, deterministic): one-at-a-time counterfactuals over the
last 60 days, each run with the same FAO-56 + SCS-CN balance:

==========  ================================================================================
group       counterfactual ("how much smaller would the deficit be if ...")
==========  ================================================================================
klimat      ... the last 60 days had the parcel's average weather (climatology)
gleba       ... the soil held as much water as category IV (reference TAW)
splyw       ... no rain ran off the surface (CN minimum)
uprawa      ... the crop used water like a reference grass (Kc = 1)
==========  ================================================================================

Deficit metric = transpiration shortfall sum(ETc - ETa) over the window. Contributions are
normalised to 100 %. When the HydroFusion-PL model is available, the
backend replaces this with TreeSHAP values aggregated by ``FEATURES`` groups (same groups).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from hydro_core.crops import kc_and_p_for_dates
from hydro_core.fao_balance import simulate_window
from hydro_core.features import CAUSE_GROUPS, FEATURES, FeatureContext
from hydro_core.registry import Registry
from hydro_core.runoff import CN_MIN
from hydro_core.schemas import CauseAttribution
from hydro_core.scoring import scoring_config
from hydro_core.text import days, num

WINDOW_DAYS = 60
METHOD_PHYSICAL = "Bilans fizyczny FAO-56: scenariusze kontrfaktyczne (60 dni)"


@dataclass
class WindowInputs:
    """Baseline inputs of the attribution window (each counterfactual modifies a copy)."""

    precip: np.ndarray
    et0: np.ndarray
    kc: np.ndarray
    p_tab: np.ndarray
    taw: float
    cn: float
    dr0: float


Counterfactual = Callable[[FeatureContext, WindowInputs], WindowInputs]
COUNTERFACTUALS: Registry[Counterfactual] = Registry("scenariusze kontrfaktyczne przyczyn")


@COUNTERFACTUALS.register("klimat")
def _cf_climate(fx: FeatureContext, w: WindowInputs) -> WindowInputs:
    tl = fx.timeline
    sl = _window(fx)
    cp, ce, _ = tl.climatology(tl.dates[sl], exclude_year=fx.inputs.analysis_date.year)
    return WindowInputs(np.nan_to_num(cp), np.nan_to_num(ce, nan=1.0), w.kc, w.p_tab, w.taw, w.cn, w.dr0)


@COUNTERFACTUALS.register("gleba")
def _cf_soil(fx: FeatureContext, w: WindowInputs) -> WindowInputs:
    ref = max(float(scoring_config()["soil"]["taw_reference_mm"]), w.taw)
    # same relative filling at the window start, larger capacity
    return WindowInputs(w.precip, w.et0, w.kc, w.p_tab, ref, w.cn, w.dr0 * ref / w.taw)


@COUNTERFACTUALS.register("splyw")
def _cf_runoff(fx: FeatureContext, w: WindowInputs) -> WindowInputs:
    return WindowInputs(w.precip, w.et0, w.kc, w.p_tab, w.taw, CN_MIN, w.dr0)


@COUNTERFACTUALS.register("uprawa")
def _cf_crop(fx: FeatureContext, w: WindowInputs) -> WindowInputs:
    return WindowInputs(w.precip, w.et0, np.minimum(w.kc, 1.0), w.p_tab, w.taw, w.cn, w.dr0)


def _window(fx: FeatureContext) -> slice:
    i0 = fx.i0
    return slice(max(0, i0 - WINDOW_DAYS + 1), i0 + 1)


def _baseline(fx: FeatureContext) -> WindowInputs:
    sl = _window(fx)
    tl = fx.timeline
    kc, p_tab, _ = kc_and_p_for_dates(fx.inputs.crop, tl.dates[sl])
    dr0 = float(fx.trace.dr[0, sl.start - 1]) if sl.start > 0 else 0.0
    return WindowInputs(tl.precip[sl], tl.et0[sl], kc, p_tab, fx.inputs.taw_mm, fx.inputs.cn, dr0)


def _deficit(w: WindowInputs) -> float:
    """Transpiration shortfall sum(ETc - ETa) [mm]: water the crop needed and did not get."""
    res = simulate_window(w.precip, w.et0, w.kc, w.p_tab, np.array([w.taw]), np.array([w.cn]), np.array([w.dr0]))
    return float(res.shortfall_mm[0])


def _explain(fx: FeatureContext, group: str, gain_mm: float) -> str:
    g = fx.get
    if group == "klimat":
        a = g("kbw_60_anomaly")
        kbw = g("kbw_60")
        base = f"Bilans opad − parowanie z 60 dni: {num(kbw)} mm" if kbw is not None else "Brak bilansu 60 dni"
        tail = f" ({num(a, sign=True)} mm względem średniej wielolecia)." if a is not None else "."
        cdd = g("cdd") or 0.0
        last = "Ostatni opad ≥ 1 mm był dziś." if cdd == 0 else f"Ostatni opad ≥ 1 mm {days(cdd)} temu."
        return f"{base}{tail} {last}"
    if group == "gleba":
        return f"Gleba kat. {fx.inputs.soil_category.value} IUNG mieści ok. {num(fx.inputs.taw_mm)} mm wody dostępnej dla roślin."
    if group == "splyw":
        ro = g("runoff_60d_mm") or 0.0
        slope = g("slope_deg")
        s = f"Spadek {num(slope, 1)}°. " if slope is not None else ""
        if ro < 1.0:
            return f"{s}Spływ powierzchniowy z 60 dni pomijalny – opad wsiąka w glebę."
        return f"{s}Spływ powierzchniowy z 60 dni ok. {num(ro)} mm – ta woda ucieka z działki zamiast wsiąkać."
    kc = g("kc_today") or 0.0
    return f"{fx.inputs.crop.label_pl}: Kc = {num(kc, 2)}, zapotrzebowanie ETc z 30 dni {num(g('etc_30') or 0)} mm."


def attribute_causes(fx: FeatureContext) -> CauseAttribution:
    base = _baseline(fx)
    d0 = _deficit(base)
    gains = {g: max(0.0, d0 - _deficit(cf(fx, base))) for g, cf in COUNTERFACTUALS.items() if g in CAUSE_GROUPS}
    total = sum(gains.values())
    has_deficit = total >= 1.0
    if not has_deficit:
        # no deficit to explain: do not invent shares (KOWR: no apparent precision)
        pct = {g: 0.0 for g in CAUSE_GROUPS}
        method = METHOD_PHYSICAL + " – brak istotnego niedoboru wody, udziałów przyczyn nie wyznacza się"
    else:
        pct = {g: round(100.0 * gains.get(g, 0.0) / total, 1) for g in CAUSE_GROUPS}
        method = METHOD_PHYSICAL
    return CauseAttribution(
        climate_pct=pct["klimat"],
        soil_pct=pct["gleba"],
        runoff_pct=pct["splyw"],
        crop_pct=pct["uprawa"],
        method=method,
        explanations={g: _explain(fx, g, gains.get(g, 0.0)) for g in CAUSE_GROUPS},
        has_deficit=has_deficit,
    )


def attribution_from_shap(fx: FeatureContext, feature_names: list[str], shap_values: np.ndarray, method: str) -> CauseAttribution:
    """Aggregate per-feature TreeSHAP contributions (towards drier) into the KOWR groups."""
    sums = {g: 0.0 for g in CAUSE_GROUPS}
    for name, value in zip(feature_names, shap_values, strict=True):
        group = FEATURES.get(name).group if name in FEATURES else None
        if group in sums:
            sums[group] += abs(float(value))
    total = sum(sums.values()) or 1.0
    pct = {g: round(100.0 * v / total, 1) for g, v in sums.items()}
    return CauseAttribution(
        climate_pct=pct["klimat"], soil_pct=pct["gleba"], runoff_pct=pct["splyw"], crop_pct=pct["uprawa"],
        method=method, explanations={g: _explain(fx, g, 0.0) for g in CAUSE_GROUPS},
    )
