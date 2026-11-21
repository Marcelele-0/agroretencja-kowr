"""Continuous FAO-56 root-zone balance over the unified timeline, for several scenarios at once.

Physics is NOT re-implemented here: every daily step calls ``kowr_rl.simulator.balance_step``
(the exact function the RL agent was trained on). This module adds what the assessment needs
on top of it:

* surface runoff RO (SCS-CN with slope correction): the step receives P_eff = P - RO,
* a continuous run over 10+ years (Kc of the crop in season, pre-season Kc outside it), so the
  same trace gives the 10-year history, today's state and the projection after the analysis date,
* a scenario axis B (baseline, each retention measure, combinations) evaluated in one pass.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import numpy as np
from kowr_rl.simulator import balance_step, relative_yield

from hydro_core.crops import CropParams, kc_and_p_for_dates
from hydro_core.runoff import scs_runoff
from hydro_core.timeline import DailyTimeline


@dataclass(frozen=True)
class BalanceScenario:
    """Soil/terrain parameters of one simulated variant of the parcel."""

    name: str
    taw_mm: float
    cn: float


@dataclass
class BalanceTrace:
    """Daily results, shape (B scenarios, T days). ``dr`` is the depletion at the END of each day."""

    timeline: DailyTimeline
    scenarios: list[BalanceScenario]
    kc: np.ndarray  # (T,)
    in_season: np.ndarray  # (T,) bool
    dr: np.ndarray
    eta: np.ndarray
    etc: np.ndarray
    dp: np.ndarray
    ks: np.ndarray
    raw: np.ndarray
    ro: np.ndarray

    def index(self, name: str) -> int:
        for i, s in enumerate(self.scenarios):
            if s.name == name:
                return i
        raise KeyError(name)

    def taw(self, b: int = 0) -> float:
        return self.scenarios[b].taw_mm

    def storage_pct(self, b: int = 0) -> np.ndarray:
        """Plant-available water left, % of TAW (100 = field capacity)."""
        return 100.0 * (1.0 - self.dr[b] / self.taw(b))

    def stressed(self, b: int = 0) -> np.ndarray:
        """Days on which crop water stress occurs (Ks < 1)."""
        return self.ks[b] < 1.0 - 1e-9


def run_balance(
    timeline: DailyTimeline,
    crop: CropParams,
    scenarios: list[BalanceScenario],
    irrigation_mm: np.ndarray | None = None,
) -> BalanceTrace:
    """Run all scenarios from the first timeline day (Dr = 0: field capacity after winter)."""
    n_b, n_t = len(scenarios), len(timeline)
    kc, p_tab, in_season = kc_and_p_for_dates(crop, timeline.dates)
    taw = np.array([s.taw_mm for s in scenarios], dtype=np.float64)
    cn = np.array([s.cn for s in scenarios], dtype=np.float64)
    precip = np.nan_to_num(timeline.precip, nan=0.0)
    et0 = np.nan_to_num(timeline.et0, nan=0.0)
    ro_all = scs_runoff(precip[None, :], cn[:, None])  # (B, T)
    irr_all = np.zeros((n_b, n_t)) if irrigation_mm is None else np.broadcast_to(irrigation_mm, (n_b, n_t))

    out = {k: np.empty((n_b, n_t)) for k in ("dr", "eta", "etc", "dp", "ks", "raw")}
    dr = np.zeros(n_b)
    ones = np.ones(n_b)
    for t in range(n_t):
        p_eff = precip[t] - ro_all[:, t]
        dr, eta, etc, dp, ks, raw = balance_step(
            dr, p_eff, irr_all[:, t], et0[t] * ones, kc[t] * ones, taw, p_tab[t] * ones
        )
        out["dr"][:, t] = dr
        out["eta"][:, t] = eta
        out["etc"][:, t] = etc
        out["dp"][:, t] = dp
        out["ks"][:, t] = ks
        out["raw"][:, t] = raw
    return BalanceTrace(
        timeline=timeline,
        scenarios=list(scenarios),
        kc=kc,
        in_season=in_season,
        ro=ro_all,
        **out,
    )


@dataclass(frozen=True)
class SeasonSummary:
    """Crop-season totals of one scenario in one year (FAO-33 yield response)."""

    year: int
    yield_rel: float
    yield_loss_pct: float
    stress_days: int
    eta_mm: float
    etc_mm: float
    precip_mm: float
    runoff_mm: float
    is_partial: bool


def season_summary(trace: BalanceTrace, crop: CropParams, year: int, b: int = 0, until: dt.date | None = None) -> SeasonSummary | None:
    """Season of ``year`` (optionally cut at ``until``, e.g. the analysis date)."""
    start, end = crop.season_start(year), crop.season_end(year)
    tl = trace.timeline
    if until is not None:
        if until < start:
            return None
        end = min(end, until)
    sl = tl.window_idx(start, end)
    if sl.stop - sl.start < 7 or tl.index_of(start) is None:
        return None
    eta = float(trace.eta[b, sl].sum())
    etc = float(trace.etc[b, sl].sum())
    y_rel = float(relative_yield(np.array(eta), np.array(etc), np.array(crop.ky)))
    return SeasonSummary(
        year=year,
        yield_rel=round(y_rel, 4),
        yield_loss_pct=round(100.0 * (1.0 - y_rel), 1),
        stress_days=int(trace.stressed(b)[sl].sum()),
        eta_mm=round(eta, 1),
        etc_mm=round(etc, 1),
        precip_mm=round(float(np.nansum(tl.precip[sl])), 1),
        runoff_mm=round(float(trace.ro[b, sl].sum()), 1),
        is_partial=end < crop.season_end(year),
    )


def days_to_stress(trace: BalanceTrace, b: int = 0) -> float | None:
    """Days from the analysis date until Dr exceeds RAW in the projection (0 = stressed now).

    None = no stress within the projection horizon.
    """
    i0 = trace.timeline.analysis_index
    if trace.dr[b, i0] > trace.raw[b, i0]:
        return 0.0
    future = np.flatnonzero(trace.dr[b, i0 + 1 :] > trace.raw[b, i0 + 1 :])
    return float(future[0] + 1) if future.size else None


@dataclass(frozen=True)
class WindowResult:
    """Totals of a window simulation, one value per scenario row (B,)."""

    dr_end: np.ndarray
    shortfall_mm: np.ndarray  # sum(ETc - ETa): water the crop needed but did not get
    eta_mm: np.ndarray
    etc_mm: np.ndarray
    ro_mm: np.ndarray
    stress_days: np.ndarray
    yield_rel: np.ndarray


def simulate_window(
    precip: np.ndarray,
    et0: np.ndarray,
    kc: np.ndarray,
    p_tab: np.ndarray,
    taw: np.ndarray,
    cn: np.ndarray,
    dr0: np.ndarray,
    ky: float = 1.0,
    season_mask: np.ndarray | None = None,
) -> WindowResult:
    """Short balance run for counterfactuals and What-If. Inputs (T,) or (B, T); taw/cn/dr0 (B,).

    ``season_mask`` (T,) restricts the yield/stress totals to crop-season days.
    """
    taw = np.asarray(taw, dtype=np.float64)
    n_b = taw.shape[0]
    n_t = np.asarray(precip).shape[-1]
    precip_b = np.broadcast_to(np.nan_to_num(precip, nan=0.0), (n_b, n_t))
    et0_b = np.broadcast_to(np.nan_to_num(et0, nan=0.0), (n_b, n_t))
    kc_b = np.broadcast_to(kc, (n_b, n_t))
    p_b = np.broadcast_to(p_tab, (n_b, n_t))
    ro = scs_runoff(precip_b, np.asarray(cn, dtype=np.float64)[:, None])
    mask = np.ones(n_t, dtype=bool) if season_mask is None else np.asarray(season_mask, dtype=bool)
    dr = np.asarray(dr0, dtype=np.float64).copy()
    zero = np.zeros(n_b)
    sums = {k: np.zeros(n_b) for k in ("eta", "etc", "stress")}
    for t in range(n_t):
        dr, eta, etc, _dp, ks, _raw = balance_step(
            dr, precip_b[:, t] - ro[:, t], zero, et0_b[:, t], kc_b[:, t], taw, p_b[:, t]
        )
        if mask[t]:
            sums["eta"] += eta
            sums["etc"] += etc
            sums["stress"] += ks < 1.0 - 1e-9
    return WindowResult(
        dr_end=dr,
        shortfall_mm=sums["etc"] - sums["eta"],
        eta_mm=sums["eta"],
        etc_mm=sums["etc"],
        ro_mm=(ro * mask[None, :]).sum(axis=1),
        stress_days=sums["stress"],
        yield_rel=relative_yield(sums["eta"], sums["etc"], np.full(n_b, ky)),
    )


def runoff_sum(trace: BalanceTrace, days: int, b: int = 0) -> float:
    i0 = trace.timeline.analysis_index
    return float(trace.ro[b, max(0, i0 - days + 1) : i0 + 1].sum())
