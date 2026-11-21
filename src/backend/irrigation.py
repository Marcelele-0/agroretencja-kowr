"""Step 4: irrigation advice through a chain of providers (first one that succeeds wins).

``IRRIGATION_PROVIDERS`` order:

1. ``rl`` - the trained MaskablePPO policy of ``kowr_rl`` (crops it was trained on). It receives
   the backend's unified timeline through ``recommend(_prefetched=...)``: no second weather
   download, the same numbers as in step 1.
2. ``fao`` - deterministic FAO-56 rule (Dr > RAW, rain forecast considered) for every crop, using
   the same balance trace as the assessment.

Both return ``IrrigationAdvice`` incl. a 10-year comparison of strategies run through
``kowr_rl.simulator`` (the same seasons and weather for every strategy).
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from kowr_rl.baselines import baseline_policies
from kowr_rl.data import DailySeries
from kowr_rl.forecast import noisy_forecast
from kowr_rl.inference import (
    STATIC_LIMITATIONS,
    STRATEGY_LABELS,
    compare_strategies,
    load_rl_policy,
    recommend,
    to_irrigation_advice,
)
from kowr_rl.simulator import N_LEADS, SeasonSpec, balance_step, build_batch, simulate

from hydro_core.crops import rl_crop_keys, stage_on
from hydro_core.features import FeatureContext
from hydro_core.registry import Registry
from hydro_core.schemas import IrrigationAdvice, IrrigationPlanDay, IrrigationStrategySummary
from hydro_core.timeline import DailyTimeline

logger = logging.getLogger(__name__)

DEFAULT_BUDGET_MM = 150.0
ACTIONS_MM = (0.0, 10.0, 20.0, 30.0)
OFF_SEASON_ACTION = "Poza sezonem uprawy – brak decyzji o nawadnianiu"


@dataclass
class IrrigationRequest:
    fx: FeatureContext
    lat: float
    lon: float
    iung_code: int
    today: dt.date
    budget_mm: float = DEFAULT_BUDGET_MM


class ProviderSkipped(Exception):
    """The provider cannot handle this request; the chain tries the next one."""


Provider = Callable[[IrrigationRequest], IrrigationAdvice]
IRRIGATION_PROVIDERS: Registry[Provider] = Registry("dostawcy porad nawadniania")


# --------------------------------------------------------------------------------------
# Timeline -> kowr_rl.DailySeries
# --------------------------------------------------------------------------------------
def observed_series(tl: DailyTimeline) -> DailySeries:
    """Days that really happened (observed + recent), up to the analysis date."""
    m = tl.known_mask()
    return DailySeries(tl.dates[m], tl.precip[m], tl.et0[m])


def forecast_series(tl: DailyTimeline) -> DailySeries | None:
    m = tl.kind == 2  # DayKind.FORECAST
    return DailySeries(tl.dates[m], tl.precip[m], tl.et0[m]) if m.any() else None


def _strategy_rows(strategies: list[dict]) -> list[IrrigationStrategySummary]:
    return [IrrigationStrategySummary(**row) for row in to_irrigation_advice({}, {"strategies": strategies})["strategies_10y"]]


def _plan(rows: list[dict], start: dt.date) -> list[IrrigationPlanDay]:
    out = []
    for r in rows:
        day = dt.date.fromisoformat(r["date"]) if "date" in r else start + dt.timedelta(days=int(r["day"]))
        out.append(IrrigationPlanDay(date=day, dose_mm=float(r["dose_mm"]), precip_assumed_mm=r.get("precip_assumed_mm"), stress_expected=bool(r.get("stress_expected"))))
    return out


# --------------------------------------------------------------------------------------
# 10-year strategy comparison for any crop (same simulator as RL evaluation)
# --------------------------------------------------------------------------------------
def compare_baselines(req: IrrigationRequest) -> list[IrrigationStrategySummary]:
    fx = req.fx
    crop, ad = fx.inputs.crop, fx.inputs.analysis_date
    obs = observed_series(fx.timeline)
    years = [y for y in fx.timeline.years() if crop.season_end(y) + dt.timedelta(days=N_LEADS) < ad][-10:]
    specs = []
    for y in years:
        spec = SeasonSpec(obs, y, crop, fx.inputs.taw_mm, req.budget_mm, meta={"year": y})
        try:
            build_batch([spec])  # validates that the season has complete weather
        except ValueError:
            continue
        specs.append(spec)
    if not specs:
        return []
    batch = build_batch(specs)
    batch.forecast = noisy_forecast(batch.fut, np.random.default_rng(12345))
    pols = {k: v for k, v in baseline_policies().items() if k in ("none", "fao_rule", "fao_forecast")}
    res = {name: simulate(batch, p) for name, p in pols.items()}
    none_y = res["none"].yield_rel
    rows = []
    for name, r in res.items():
        water = float(r.water_mm.mean())
        gain = float((r.yield_rel - none_y).mean())
        rows.append({
            "label": STRATEGY_LABELS[name],
            "avg_yield_loss_pct": round(100 * (1 - float(r.yield_rel.mean())), 2),
            "avg_water_mm": round(water, 1),
            "avg_deep_percolation_mm": round(float(r.dp_mm.mean()), 1),
            "yield_gain_pp_per_100mm": round(100 * gain / water * 100, 2) if water > 0 else None,
        })
    return _strategy_rows(rows)


# --------------------------------------------------------------------------------------
# Provider 1: RL agent (kowr_rl)
# --------------------------------------------------------------------------------------
@IRRIGATION_PROVIDERS.register("rl")
def rl_provider(req: IrrigationRequest) -> IrrigationAdvice:
    fx = req.fx
    crop_key = fx.inputs.crop_code.value
    if crop_key not in rl_crop_keys():
        raise ProviderSkipped(f"Agent RL nie był trenowany dla uprawy '{crop_key}'.")
    obs = observed_series(fx.timeline)
    ad = fx.inputs.analysis_date
    try:
        comparison = compare_strategies(
            req.lat, req.lon, crop_key, req.budget_mm, include_oracle=False,
            today=ad, _prefetched=(req.iung_code, obs, []),
        )
        strategies = comparison.get("strategies") or []
    except Exception:
        logger.warning("RL strategy comparison failed", exc_info=True)
        strategies = []

    if stage_on(fx.inputs.crop, ad) is None:
        return IrrigationAdvice(
            action_now=OFF_SEASON_ACTION, recommended_mm=0.0,
            rationale=f"Data {ad} jest poza sezonem uprawy '{fx.inputs.crop.label_pl}'. Poniżej porównanie strategii z ostatnich sezonów.",
            forecast_mode="brak (poza sezonem)", strategies_10y=_strategy_rows(strategies),
            policy_used="rl" if load_rl_policy() else "fao_rule", in_season=False, limitations=list(STATIC_LIMITATIONS),
        )

    rec = recommend(
        req.lat, req.lon, crop_key, ad, req.budget_mm, 0.0,
        today=req.today, _prefetched=(req.iung_code, obs, [], forecast_series(fx.timeline)),
    )
    if rec.get("status") != "ok":
        raise ProviderSkipped(rec.get("message", "Agent RL nie zwrócił rekomendacji."))
    base = to_irrigation_advice(rec, {"strategies": strategies})
    recd = rec.get("recommendation") or {}
    return IrrigationAdvice(
        **{**base, "strategies_10y": _strategy_rows(strategies)},
        policy_used=(rec.get("policy") or {}).get("used"),
        plan_7d=_plan(recd.get("plan_7d") or [], ad),
        in_season=True,
        limitations=rec.get("limitations") or [],
    )


# --------------------------------------------------------------------------------------
# Provider 2: FAO-56 rule on the backend's own balance
# --------------------------------------------------------------------------------------
def _legal_dose(mm: float) -> float:
    allowed = [a for a in ACTIONS_MM if a <= mm + 1e-9]
    return max(allowed) if allowed else 0.0


@IRRIGATION_PROVIDERS.register("fao")
def fao_provider(req: IrrigationRequest) -> IrrigationAdvice:
    fx = req.fx
    ad = fx.inputs.analysis_date
    strategies = []
    try:
        strategies = compare_baselines(req)
    except Exception:
        logger.warning("Baseline comparison failed", exc_info=True)
    limitations = list(STATIC_LIMITATIONS)
    if stage_on(fx.inputs.crop, ad) is None:
        return IrrigationAdvice(
            action_now=OFF_SEASON_ACTION, recommended_mm=0.0,
            rationale=f"Data {ad} jest poza sezonem uprawy '{fx.inputs.crop.label_pl}'.",
            forecast_mode="brak (poza sezonem)", strategies_10y=strategies, policy_used="fao_rule",
            in_season=False, limitations=limitations,
        )

    tl, tr = fx.timeline, fx.trace
    i0 = tl.analysis_index
    taw = fx.inputs.taw_mm
    dr = np.array([float(tr.dr[0, i0 - 1]) if i0 > 0 else 0.0])
    plan: list[IrrigationPlanDay] = []
    for k in range(7):
        i = i0 + k
        if i >= len(tl):
            break
        raw_now = float(tr.raw[0, i])
        p3 = float(np.nansum(tl.precip[i : i + 3]))
        need = float(dr[0]) - raw_now
        dose = _legal_dose(need + 10.0) if need > 0 and p3 < need else 0.0
        p_eff = np.array([tl.precip[i] - tr.ro[0, i]])
        dr, _eta, _etc, _dp, ks, _raw = balance_step(
            dr, p_eff, np.array([dose]), np.array([tl.et0[i]]), np.array([tr.kc[i]]), np.array([taw]),
            np.array([fx.inputs.crop.p]),
        )
        plan.append(IrrigationPlanDay(date=ad + dt.timedelta(days=k), dose_mm=dose, precip_assumed_mm=round(float(tl.precip[i]), 1), stress_expected=bool(ks[0] < 1 - 1e-9)))

    dose_today = plan[0].dose_mm if plan else 0.0
    storage = fx.get("storage_pct") or 0.0
    p3 = fx.get("forecast_p_3") or 0.0
    if dose_today > 0:
        action = f"Nawadniaj dziś: {dose_today:.0f} mm"
        why = f"zapas wody {storage:.0f}% TAW jest poniżej progu stresu, a prognoza opadu na 3 dni to {p3:.0f} mm"
    else:
        action = "Nie nawadniaj dziś"
        dts = fx.get("days_to_stress")
        why = (f"zapas wody {storage:.0f}% TAW" + (f"; stres przewidywany za ok. {dts:.0f} dni" if dts else "; brak stresu w horyzoncie prognozy"))
    later = [d for d in plan[1:] if d.dose_mm > 0]
    text = f"{action} – {why}."
    if later:
        text += " Plan warunkowy: " + ", ".join(f"{d.date:%d.%m}: {d.dose_mm:.0f} mm" for d in later) + "."
    mode = "prognoza ECMWF (Open-Meteo)" if (tl.kind[i0 + 1 : i0 + 8] == 2).any() else "klimatologia działki (bez zaglądania w przyszłość)"
    return IrrigationAdvice(
        action_now=action, recommended_mm=dose_today, rationale=text, forecast_mode=mode,
        strategies_10y=strategies, policy_used="fao_rule", plan_7d=plan, in_season=True, limitations=limitations,
    )


def advise_irrigation(req: IrrigationRequest) -> tuple[IrrigationAdvice, list[str]]:
    """Run the provider chain; returns the advice and notes about skipped providers."""
    notes: list[str] = []
    for name, provider in IRRIGATION_PROVIDERS.items():
        try:
            return provider(req), notes
        except ProviderSkipped as exc:
            notes.append(f"{name}: {exc}")
        except Exception as exc:
            logger.warning("Irrigation provider %s failed", name, exc_info=True)
            notes.append(f"{name}: błąd ({type(exc).__name__})")
    raise RuntimeError("Żaden dostawca porad nawadniania nie zadziałał: " + "; ".join(notes))
