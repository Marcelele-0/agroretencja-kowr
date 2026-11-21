"""E8: multi-day irrigation plans (e.g. 14 days) made in the morning of one day.

A plan for days t0 .. t0+H-1 is produced by running the same daily policy forward on the weather
*assumed* on the morning of t0:

* precipitation of day t0+k = the forecast issued on t0 (lead k+1) where it exists, otherwise the
  day-of-year climatology of the other years (``batch.clim_p``);
* ET0 of day t0+k = the forecast ET0 where given (``et0_issued``), otherwise climatology;
* the policy's forecast window on plan day k (days t0+k .. t0+k+N_FC-1) is the same issued forecast
  shifted by k, filled with climatology beyond the issued horizon; for H = 14 and a 14-day window
  this needs assumed weather for 28 days (t0 .. t0+27).

The soil-water state of the plan evolves with the assumed weather, so later plan days carry the
forecast error of all earlier days. ``closed_loop`` is the reference: the policy decides every day
with that day's fresh forecast and the true weather (what really happens when the farmer re-plans
daily).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from .simulator import N_FC, SeasonBatch, SimState, balance_step, default_actions, legalize


@dataclass
class Rollout:
    irr: np.ndarray  # (B, H) doses
    eta: np.ndarray
    etc: np.ndarray
    dp: np.ndarray
    dr_start: np.ndarray  # (B, H) depletion at the start of each day
    valid: np.ndarray  # (B, H)


def closed_loop_states(batch: SeasonBatch, policy: Any) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Run the policy over whole seasons; returns depletion and budget left at the start of every
    day (B, T) and the doses (B, T)."""
    actions = default_actions()
    B, T = batch.size, batch.horizon
    dr_s, bl_s, irr_s = np.zeros((B, T)), np.zeros((B, T)), np.zeros((B, T))
    dr, bl = batch.dr0.copy(), batch.budget.copy()
    for t in range(T):
        v = batch.valid[:, t]
        dr_s[:, t], bl_s[:, t] = dr, bl
        if not v.any():
            continue
        irr = legalize(policy(SimState(t, dr, bl, batch)), bl, actions) * v
        dr_new = balance_step(dr, batch.precip[:, t], irr, batch.et0[:, t], batch.kc[:, t], batch.taw, batch.p_tab)[0]
        dr = np.where(v, dr_new, dr)
        bl = bl - irr
        irr_s[:, t] = irr
    return dr_s, bl_s, irr_s


def rollout(batch: SeasonBatch, policy: Any | None, t0: int, h: int, dr: np.ndarray, bl: np.ndarray, doses: np.ndarray | None = None) -> Rollout:
    """Simulate days t0 .. t0+h-1 of ``batch`` from state (dr, bl): the policy decides each day, or
    the fixed ``doses`` (B, h) are applied (open-loop execution of a plan)."""
    actions = default_actions()
    B = batch.size
    out = {k: np.zeros((B, h)) for k in ("irr", "eta", "etc", "dp", "dr_start")}
    valid = np.zeros((B, h), dtype=bool)
    dr, bl = dr.copy(), bl.copy()
    for k in range(h):
        t = t0 + k
        if t >= batch.horizon:
            break
        v = batch.valid[:, t]
        valid[:, k] = v
        out["dr_start"][:, k] = dr
        want = doses[:, k] if doses is not None else policy(SimState(t, dr, bl, batch))
        irr = legalize(want, bl, actions) * v
        dr_new, eta, etc, dp, _, _ = balance_step(dr, batch.precip[:, t], irr, batch.et0[:, t], batch.kc[:, t], batch.taw, batch.p_tab)
        out["irr"][:, k], out["eta"][:, k], out["etc"][:, k], out["dp"][:, k] = irr, eta * v, etc * v, dp * v
        dr = np.where(v, dr_new, dr)
        bl = bl - irr
    return Rollout(valid=valid, **out)


def assumed_batch(
    batch: SeasonBatch,
    t0: int,
    h: int = 14,
    *,
    use_forecast: bool = True,
    precip_issued: np.ndarray | None = None,
    et0_issued: np.ndarray | None = None,
) -> SeasonBatch:
    """Copy of ``batch`` where days t0 .. t0+h-1 carry the weather assumed on the morning of t0.

    ``precip_issued`` / ``et0_issued``: (B, n) forecast issued on t0 for days t0 .. t0+n-1 (NaN = not
    available). Default precipitation: ``batch.forecast[:, t0]`` (the forecast the policy sees on t0).
    ``use_forecast=False`` gives the no-forecast plan (climatology for every day).
    """
    b = batch.subset(np.arange(batch.size))
    T = b.horizon
    span = h + N_FC  # assumed weather needed for the plan days and their forecast windows
    clim_p = b.clim_p[:, t0 : t0 + span]
    clim_e = b.clim_e[:, t0 : t0 + span]
    if clim_p.shape[1] < span:  # season end: pad with the last climatology value
        clim_p = np.pad(clim_p, ((0, 0), (0, span - clim_p.shape[1])), mode="edge")
        clim_e = np.pad(clim_e, ((0, 0), (0, span - clim_e.shape[1])), mode="edge")
    ap, ae = clim_p.copy(), clim_e.copy()
    if use_forecast:
        fp = batch.forecast[:, t0] if precip_issued is None else precip_issued
        n = min(fp.shape[1], span)
        ap[:, :n] = np.where(np.isfinite(fp[:, :n]), fp[:, :n], ap[:, :n])
        if et0_issued is not None:
            n = min(et0_issued.shape[1], span)
            ae[:, :n] = np.where(np.isfinite(et0_issued[:, :n]), et0_issued[:, :n], ae[:, :n])
    hh = min(h, T - t0)
    b.precip[:, t0 : t0 + hh] = ap[:, :hh]
    b.et0[:, t0 : t0 + hh] = ae[:, :hh]
    # 7-day history for plan days: true weather before t0, assumed weather from t0 on
    for k in range(1, hh):
        t = t0 + k
        lo = max(t - 7, 0)
        b.p_hist7[:, t] = batch.p_hist7[:, t] - batch.precip[:, lo:t].sum(axis=1) + b.precip[:, lo:t].sum(axis=1)
        b.et0_hist7[:, t] = batch.et0_hist7[:, t] - batch.et0[:, lo:t].sum(axis=1) + b.et0[:, lo:t].sum(axis=1)
    # forecast window seen on plan day k = the assumed weather shifted by k
    for k in range(hh):
        b.forecast[:, t0 + k] = ap[:, k : k + N_FC]
    return b


def make_plan(batch: SeasonBatch, policy: Any, t0: int, dr: np.ndarray, bl: np.ndarray, h: int = 14, **kw: Any) -> np.ndarray:
    """(B, h) planned doses for days t0 .. t0+h-1, made with the information of the morning of t0."""
    ab = assumed_batch(batch, t0, h, **kw)
    return rollout(ab, policy, t0, h, dr, bl).irr


def analog_weather(batch: SeasonBatch, points: dict[str, Any], years: range | list[int]) -> tuple[np.ndarray, np.ndarray]:
    """Weather of the same calendar days in the other years: (Y, B, T + N_FC) precipitation and ET0
    (NaN where a year is missing). ``points`` maps point id -> PointData (``load_points``)."""
    T = batch.horizon + N_FC
    ys = list(years)
    P = np.full((len(ys), batch.size, T), np.nan)
    E = np.full((len(ys), batch.size, T), np.nan)
    for i, m in enumerate(batch.meta):
        w = points[m["point"]].weather
        dates = np.asarray(w.dates, dtype="datetime64[D]")
        start = batch.start_dates[i].astype(object)
        for yi, y in enumerate(ys):
            if y == m["year"]:
                continue
            try:
                d0 = np.datetime64(start.replace(year=y), "D")
            except ValueError:  # 29 II
                continue
            want = d0 + np.arange(T)
            idx = np.searchsorted(dates, want)
            idx_c = np.clip(idx, 0, len(dates) - 1)
            hit = dates[idx_c] == want
            P[yi, i, hit] = np.asarray(w.precip)[idx_c[hit]]
            E[yi, i, hit] = np.asarray(w.et0)[idx_c[hit]]
    return P, E


def scenario_plans(
    batch: SeasonBatch,
    policy: Any,
    t0: int,
    dr: np.ndarray,
    bl: np.ndarray,
    analog_p: np.ndarray,
    analog_e: np.ndarray,
    h: int = 14,
    forecast_days: int | None = None,
    precip_issued: np.ndarray | None = None,
) -> np.ndarray:
    """(Y, B, h) plans: forecast for the first ``forecast_days`` days (default: the 7 real leads of
    ``batch.forecast``; ``precip_issued`` overrides it), then the weather of analog year y.
    Seasons whose analog year is incomplete fall back to climatology for that scenario."""
    span = h + N_FC
    fc = batch.forecast[:, t0] if precip_issued is None else precip_issued
    nf = (fc.shape[1] if precip_issued is not None else 7) if forecast_days is None else forecast_days
    plans = []
    for y in range(analog_p.shape[0]):
        ap = analog_p[y][:, t0 : t0 + span].copy()
        ae = analog_e[y][:, t0 : t0 + span].copy()
        if nf > 0:
            ap[:, :nf] = np.where(np.isfinite(fc[:, :nf]), fc[:, :nf], ap[:, :nf])
        plans.append(make_plan(batch, policy, t0, dr, bl, h, precip_issued=ap, et0_issued=ae))
    return np.stack(plans)
