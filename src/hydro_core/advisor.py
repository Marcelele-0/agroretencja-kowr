"""Farmer advice: plain-language suggestions derived from the unified timeline projection.

Each rule in ``ADVICE_RULES`` looks at the features (state today, projection over the forecast /
climatology days, KBW verdict) and the results of steps 3-4, and returns zero or more
``FarmerAdvice`` items with the numbers they are based on. Output is sorted by priority.

The projection makes "prepare for drought" possible: the balance is continued over the forecast
days, so the engine knows on which day the root zone crosses the stress threshold (RAW) and can
suggest irrigating *before* the stress, or waiting when rain is forecast.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass

from hydro_core.crops import STAGE_LABELS_PL, stage_on
from hydro_core.features import FeatureContext
from hydro_core.registry import Registry
from hydro_core.schemas import (
    AdviceKind,
    FarmerAdvice,
    IrrigationAdvice,
    KbwPeriodResult,
    RetentionPlan,
    RiskLevel,
    WaterRiskAssessment,
    YearlyDroughtPoint,
)
from hydro_core.text import days, effect_text, num

PREPARE_HORIZON_DAYS = 7
DOSE_STEP_MM = 10.0
MAX_DOSE_MM = 30.0


@dataclass
class AdviceContext:
    """Results of the other steps that rules may refer to."""

    risk: WaterRiskAssessment
    retention: RetentionPlan
    irrigation: IrrigationAdvice | None
    history: list[YearlyDroughtPoint]
    current_kbw: KbwPeriodResult | None
    projection_kind: str  # "prognoza ECMWF" / "klimatologia"


AdviceRule = Callable[[FeatureContext, AdviceContext], list[FarmerAdvice]]
ADVICE_RULES: Registry[AdviceRule] = Registry("reguły porad dla rolnika")


def _dose(deficit_mm: float) -> float:
    steps = max(1, round(deficit_mm / DOSE_STEP_MM))
    return min(MAX_DOSE_MM, steps * DOSE_STEP_MM)


def _in_season(fx: FeatureContext) -> bool:
    return stage_on(fx.inputs.crop, fx.inputs.analysis_date) is not None


def _day(fx: FeatureContext, offset: float) -> dt.date:
    return fx.inputs.analysis_date + dt.timedelta(days=int(offset))


@ADVICE_RULES.register("susza_wg_iung")
def _r_kbw(fx: FeatureContext, ctx: AdviceContext) -> list[FarmerAdvice]:
    p = ctx.current_kbw
    if p is None or not p.is_drought:
        return []
    return [
        FarmerAdvice(
            id="susza_wg_iung", kind=AdviceKind.SUSZA_IUNG, priority=1,
            title=f"Susza rolnicza wg progów IUNG-PIB w oknie {p.label}",
            text=(
                f"KBW = {p.kbw_mm:.0f} mm przy progu {p.threshold_mm:.0f} mm dla tej uprawy i kategorii gleby "
                f"{fx.inputs.soil_category.value}. Zachowaj ten raport: przy suszy ogłoszonej w SMSR dla gminy "
                "możesz wnioskować o oszacowanie szkód przez komisję gminną i pomoc suszową."
            ),
            horizon_start=p.start, horizon_end=p.end,
            evidence={"kbw_mm": p.kbw_mm, "prog_mm": p.threshold_mm, "zapas_mm": p.margin_mm},
            based_on=["KBW SMSR IUNG-PIB (Dz.U. 2019 poz. 739)", "Open-Meteo ERA5"],
        )
    ]


@ADVICE_RULES.register("nawadnianie")
def _r_irrigation(fx: FeatureContext, ctx: AdviceContext) -> list[FarmerAdvice]:
    if not _in_season(fx):
        return []
    dr, raw, dts = fx.get("dr_mm") or 0.0, fx.get("raw_mm") or 0.0, fx.get("days_to_stress")
    p3, p7 = fx.get("forecast_p_3") or 0.0, fx.get("forecast_p_7") or 0.0
    irr = ctx.irrigation
    policy = irr.policy_used if irr else None
    evidence: dict[str, float | str | None] = {
        "niedobor_mm": round(dr, 1), "prog_stresu_mm": round(raw, 1), "opad_3d_mm": round(p3, 1), "opad_7d_mm": round(p7, 1),
    }
    based = ["bilans FAO-56", ctx.projection_kind] + ([f"polityka nawadniania: {policy}"] if policy else [])

    # 1) Step 4 (RL agent or FAO rule) already decided to irrigate today: the advice follows it.
    #    The RL agent may irrigate *before* the stress threshold when the forecast is dry -
    #    that is the "prepare for drought" behaviour learned from 10 years of seasons.
    if irr is not None and irr.recommended_mm > 0:
        doses = [d for d in irr.plan_7d if d.dose_mm > 0]
        return [FarmerAdvice(
            id="nawadniaj_teraz", kind=AdviceKind.NAWADNIAJ_TERAZ, priority=1,
            title=f"Nawadniaj dziś: {num(irr.recommended_mm)} mm",
            text=irr.rationale,
            horizon_start=fx.inputs.analysis_date, horizon_end=doses[-1].date if doses else fx.inputs.analysis_date,
            evidence={**evidence, "dawka_mm": irr.recommended_mm, "dawki_7d_mm": sum(d.dose_mm for d in irr.plan_7d)},
            based_on=based,
        )]
    # 2) Stressed, but step 4 says "not today": rain is coming (or the budget is used up).
    if dts == 0:
        rain = p3 >= max(5.0, 0.5 * (dr - raw))
        return [FarmerAdvice(
            id="poczekaj_na_opad" if rain else "stres_bez_nawadniania",
            kind=AdviceKind.POCZEKAJ_NA_OPAD if rain else AdviceKind.OSTRZEZENIE, priority=2 if rain else 1,
            title="Poczekaj z nawadnianiem – prognozowany opad" if rain else "Uprawa w stresie wodnym",
            text=(f"Niedobór {num(dr)} mm przekracza próg stresu ({num(raw)} mm). "
                  + (f"W ciągu 3 dni prognozuje się {num(p3)} mm opadu, co uzupełni większość niedoboru."
                     if rain else (irr.rationale if irr else "Rozważ nawadnianie, jeśli masz dostęp do wody."))),
            horizon_start=fx.inputs.analysis_date, horizon_end=_day(fx, 3), evidence=evidence, based_on=based,
        )]
    # 3) Not stressed yet, but the projection crosses the stress threshold soon: prepare.
    if dts is not None and dts <= PREPARE_HORIZON_DAYS and p7 < (raw - dr) + 10:
        stress_day = _day(fx, dts)
        planned = [d for d in (irr.plan_7d if irr else []) if d.dose_mm > 0]
        # on the stress day Dr ~ RAW; refilling half of RAW keeps the crop out of stress for
        # several days without pushing water below the root zone
        dose = planned[0].dose_mm if planned else _dose(0.5 * raw)
        when = planned[0].date if planned else stress_day
        return [FarmerAdvice(
            id="przygotuj_nawadnianie", kind=AdviceKind.PRZYGOTUJ_NAWADNIANIE, priority=2,
            title=f"Przygotuj nawadnianie – stres ok. {stress_day:%d.%m}",
            text=(f"Przy prognozowanej pogodzie zapas wody spadnie poniżej progu stresu za ok. {days(dts)}. "
                  f"Opad w 7 dni ({num(p7)} mm) tego nie pokryje. Zaplanuj dawkę ok. {num(dose)} mm na {when:%d.%m}; "
                  "wcześniejsze podlanie zwiększa straty na parowanie i przesiąk."),
            horizon_start=_day(fx, max(0, dts - 2)), horizon_end=stress_day,
            evidence={**evidence, "dni_do_stresu": dts, "dawka_mm": dose}, based_on=based,
        )]
    return []


@ADVICE_RULES.register("upal")
def _r_heat(fx: FeatureContext, ctx: AdviceContext) -> list[FarmerAdvice]:
    stage = stage_on(fx.inputs.crop, fx.inputs.analysis_date)
    hot = fx.get("forecast_hot_days") or 0
    if stage not in ("dev", "mid") or hot < 2:
        return []
    return [FarmerAdvice(
        id="upal", kind=AdviceKind.OSTRZEZENIE, priority=2,
        title=f"Upał w fazie {STAGE_LABELS_PL[stage]}",
        text=f"W prognozie {hot:.0f} dni z temperaturą ≥ 30 °C – zużycie wody wzrośnie; sprawdzaj stan łanu codziennie.",
        horizon_start=_day(fx, 1), horizon_end=_day(fx, 7), evidence={"dni_upalne": hot}, based_on=["prognoza ECMWF"],
    )]


@ADVICE_RULES.register("erozja_splyw")
def _r_runoff(fx: FeatureContext, ctx: AdviceContext) -> list[FarmerAdvice]:
    share = fx.get("runoff_share_60d") or 0.0
    slope = fx.get("slope_deg")
    if slope is None or (slope < 5.0 and share < 0.1):
        return []
    return [FarmerAdvice(
        id="erozja_splyw", kind=AdviceKind.OSTRZEZENIE, priority=3,
        title="Woda ucieka po powierzchni działki",
        text=(f"Średni spadek {slope:.1f}°; w 60 dni spłynęło ok. {100 * share:.0f}% opadu. "
              "Uprawa w poprzek stoku i pas zadarnienia w dolnej części działki zatrzymają tę wodę."),
        evidence={"spadek_deg": slope, "udzial_splywu": round(share, 3)}, based_on=["NMT", "SCS-CN"],
    )]


@ADVICE_RULES.register("poza_sezonem")
def _r_off_season(fx: FeatureContext, ctx: AdviceContext) -> list[FarmerAdvice]:
    if _in_season(fx):
        return []
    done = [h for h in ctx.history if not h.is_partial]
    last = done[-1] if done else None
    summary = (
        f"Ostatni sezon ({last.year}): strata plonu z niedoboru wody ok. {last.yield_loss_pct:.0f}%, "
        f"{last.drought_days} dni stresu, okna suszy KBW: {last.kbw_drought_periods}. "
        if last else ""
    )
    free = [r.title.lower() for r in ctx.retention.recommendations if r.group == "bez_urzadzen"][:2]
    permit = [r for r in ctx.retention.recommendations if r.requires_permit][:1]
    text = summary + "Przed wiosną: " + (", ".join(free) if free else "zatrzymanie wody zimowej w glebie") + "."
    if permit:
        text += f" Jeśli chcesz zatrzymać wodę z roztopów ({permit[0].title.lower()}), złóż zgłoszenie do PGW Wody Polskie już zimą."
    return [FarmerAdvice(
        id="poza_sezonem", kind=AdviceKind.POZA_SEZONEM, priority=2,
        title="Poza sezonem wegetacji – przygotuj działkę na wiosnę",
        text=text, evidence={"strata_plonu_pct": last.yield_loss_pct if last else None}, based_on=["bilans FAO-56 10 lat", "ranking retencji"],
    )]


@ADVICE_RULES.register("retencja")
def _r_retention(fx: FeatureContext, ctx: AdviceContext) -> list[FarmerAdvice]:
    if not ctx.retention.recommendations:
        return []
    top = ctx.retention.recommendations[0]
    wi = ctx.retention.what_if_default
    return [FarmerAdvice(
        id="retencja", kind=AdviceKind.RETENCJA, priority=3,
        title=f"Zatrzymaj wodę tam, gdzie spadła: {top.title.lower()}",
        text=(f"W najsuchszym sezonie {wi.reference_dry_year} ten zabieg daje: {eff}{'' if eff.endswith('.') else '.'}"
              if (eff := effect_text(top.effect_yield_pp, top.effect_reserve_days, top.retained_runoff_mm))
              else top.rationale),
        evidence={"rok_odniesienia": float(wi.reference_dry_year), "efekt_pp": top.effect_yield_pp}, based_on=["symulator Co jeśli (FAO-56/33)"],
    )]


@ADVICE_RULES.register("monitoruj")
def _r_monitor(fx: FeatureContext, ctx: AdviceContext) -> list[FarmerAdvice]:
    if not _in_season(fx) or ctx.risk.level in (RiskLevel.WYSOKIE, RiskLevel.KRYTYCZNE):
        return []
    dts = fx.get("days_to_stress")
    irrigating = ctx.irrigation is not None and ctx.irrigation.recommended_mm > 0
    if irrigating or (dts is not None and dts <= PREPARE_HORIZON_DAYS):
        return []  # an irrigation / preparation advice covers this situation
    text = (f"Zapas wody {num(fx.get('storage_pct') or 0)}% pojemności. "
            + (f"Przy prognozowanej pogodzie wystarczy na ok. {days(dts)}." if dts else "W horyzoncie prognozy nie przewiduje się stresu wodnego."))
    return [FarmerAdvice(
        id="monitoruj", kind=AdviceKind.MONITORUJ, priority=4, title="Woda w glebie wystarcza", text=text,
        evidence={"zapas_pct": fx.get("storage_pct"), "dni_do_stresu": dts}, based_on=["bilans FAO-56", ctx.projection_kind],
    )]


def build_advice(fx: FeatureContext, ctx: AdviceContext) -> list[FarmerAdvice]:
    items = [a for rule in ADVICE_RULES.values() for a in rule(fx, ctx)]
    order = {name: i for i, name in enumerate(ADVICE_RULES.names())}
    return sorted(items, key=lambda a: (a.priority, order.get(a.id, 99)))
