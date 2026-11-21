"""FAO-56 daily root-zone water balance ("bucket" model) and FAO-33 yield response.

Vectorised over a batch of seasons (B) and looped over days (T). The same
:func:`balance_step` and :func:`observation` are used by the batch simulator, the
Gymnasium environment and live inference, so there is no training/serving skew.

Daily step (FAO-56 ch. 8, eq. 84-88; RO and capillary rise are not modelled):

    ETc = Kc * ET0
    p   = clip(p_tab + 0.04 (5 - ETc), 0.1, 0.8)          (FAO-56 Tab. 22, fn. 2)
    RAW = p * TAW
    Ks  = 1                        if Dr <= RAW
        = (TAW - Dr) / (TAW - RAW) otherwise               (Ks evaluated on Dr at day start)
    ETa = min(Ks * ETc, TAW - Dr + P + I)                  (cannot use water that is not there)
    net = Dr - P - I + ETa
    DP  = max(0, -net)                                     (water above field capacity drains)
    Dr' = max(0, net)                                      (Dr' <= TAW by construction)

Mass balance closes exactly: (TAW - Dr') - (TAW - Dr) = P + I - ETa - DP.
Yield (FAO-33): Ya/Ym = clip(1 - Ky (1 - sum ETa / sum ETc), 0, 1).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .config import CropParams, PreSeason, load_pre_season, load_rl_config
from .data import DailySeries

N_LEADS = 7
N_OBS = 13
OBS_NAMES = (
    "season_progress",
    "kc",
    "dr_over_taw",
    "precip_past7_scaled",
    "et0_past7_scaled",
    "forecast_3d_scaled",
    "forecast_7d_scaled",
    "budget_left_frac",
    "budget_left_mm_scaled",
    "taw_scaled",
    "p_table",
    "ky",
    "raw_minus_dr_over_taw",
)


def default_actions() -> np.ndarray:
    return np.asarray(load_rl_config()["actions_mm"], dtype=np.float64)


# ======================================================================================
# Physics
# ======================================================================================
def adjusted_p(p_tab: np.ndarray | float, etc: np.ndarray | float) -> np.ndarray:
    """Depletion fraction adjusted for evaporative demand (FAO-56 Tab. 22, footnote 2)."""
    return np.clip(np.asarray(p_tab) + 0.04 * (5.0 - np.asarray(etc)), 0.1, 0.8)


def balance_step(
    dr: np.ndarray,
    precip: np.ndarray,
    irr: np.ndarray,
    et0: np.ndarray,
    kc: np.ndarray,
    taw: np.ndarray,
    p_tab: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """One day of the root-zone balance. Returns ``(dr_new, eta, etc, dp, ks, raw)``."""
    etc = kc * et0
    raw = adjusted_p(p_tab, etc) * taw
    ks = np.where(dr <= raw, 1.0, (taw - dr) / np.maximum(taw - raw, 1e-9))
    ks = np.clip(ks, 0.0, 1.0)
    eta = np.minimum(ks * etc, taw - dr + precip + irr)
    net = dr - precip - irr + eta
    dp = np.maximum(-net, 0.0)
    dr_new = np.maximum(net, 0.0)
    return dr_new, eta, etc, dp, ks, raw


def relative_yield(sum_eta: np.ndarray, sum_etc: np.ndarray, ky: np.ndarray) -> np.ndarray:
    """FAO-33 seasonal yield response, clipped to [0, 1]."""
    sum_etc = np.asarray(sum_etc, dtype=np.float64)
    ratio = np.where(sum_etc > 0, np.asarray(sum_eta) / np.maximum(sum_etc, 1e-12), 1.0)
    return np.clip(1.0 - np.asarray(ky) * (1.0 - ratio), 0.0, 1.0)


def spin_up(precip: np.ndarray, et0: np.ndarray, taw: np.ndarray, pre: PreSeason) -> np.ndarray:
    """Depletion at season start: run the balance from Dr=0 (field capacity) with pre-season Kc."""
    dr = np.zeros(precip.shape[0])
    kc = np.full_like(dr, pre.kc)
    p = np.full_like(dr, pre.p)
    zero = np.zeros_like(dr)
    for t in range(precip.shape[1]):
        dr = balance_step(dr, precip[:, t], zero, et0[:, t], kc, taw, p)[0]
    return dr


def legalize(irr: np.ndarray, budget_left: np.ndarray, actions: np.ndarray) -> np.ndarray:
    """Largest action that is <= the request and <= the remaining budget."""
    irr = np.asarray(irr, dtype=np.float64)
    ok = (actions[None, :] <= budget_left[:, None] + 1e-9) & (actions[None, :] <= irr[:, None] + 1e-9)
    idx = ok.shape[1] - 1 - np.argmax(ok[:, ::-1], axis=1)
    return actions[idx]


def action_mask(budget_left: np.ndarray, actions: np.ndarray) -> np.ndarray:
    """(B, A) boolean mask of doses that fit in the remaining budget."""
    return actions[None, :] <= np.asarray(budget_left)[:, None] + 1e-9


# ======================================================================================
# Season batches
# ======================================================================================
@dataclass
class SeasonSpec:
    """One season to simulate. ``weather`` must cover 1 March .. season end (+7 days if possible)."""

    weather: DailySeries
    year: int
    crop: CropParams
    taw: float
    budget: float
    etc_season_est: float | None = None
    forecast: np.ndarray | None = None  # (L, 7) forecast known on the morning of each day
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class SeasonBatch:
    precip: np.ndarray  # (B, T)
    et0: np.ndarray  # (B, T)
    kc: np.ndarray  # (B, T)
    stage: np.ndarray  # (B, T) int, -1 on padding
    valid: np.ndarray  # (B, T) bool
    length: np.ndarray  # (B,)
    p_hist7: np.ndarray  # (B, T) precipitation of the 7 days before day t
    et0_hist7: np.ndarray  # (B, T)
    fut: np.ndarray  # (B, T, 7) true precipitation on days t..t+6 (0 where unknown)
    forecast: np.ndarray  # (B, T, 7) forecast for days t..t+6 known on the morning of day t
    taw: np.ndarray
    p_tab: np.ndarray
    ky: np.ndarray
    dr0: np.ndarray
    budget: np.ndarray
    etc_season_est: np.ndarray
    dev_start: np.ndarray  # (B,) first day of the development stage
    late_start: np.ndarray  # (B,) first day of the late stage
    start_dates: np.ndarray  # (B,) datetime64[D]
    meta: list[dict[str, Any]]

    @property
    def size(self) -> int:
        return self.precip.shape[0]

    @property
    def horizon(self) -> int:
        return self.precip.shape[1]

    def subset(self, idx: np.ndarray | list[int]) -> SeasonBatch:
        idx = np.asarray(idx)
        kw = {}
        for name, val in self.__dict__.items():
            if name == "meta":
                kw[name] = [self.meta[i] for i in idx]
            else:
                kw[name] = val[idx]
        return SeasonBatch(**kw)


def _daily_on_range(weather: DailySeries, start: dt.date, n: int) -> tuple[np.ndarray, np.ndarray]:
    dates = np.datetime64(start, "D") + np.arange(n)
    p = np.full(n, np.nan)
    e = np.full(n, np.nan)
    idx = np.searchsorted(weather.dates, dates)
    idx = np.clip(idx, 0, max(len(weather) - 1, 0))
    hit = (len(weather) > 0) & (weather.dates[idx] == dates)
    p[hit] = weather.precip[idx[hit]]
    e[hit] = weather.et0[idx[hit]]
    return p, e


def build_batch(specs: list[SeasonSpec], pre: PreSeason | None = None, horizon: int | None = None) -> SeasonBatch:
    """Stack seasons into padded arrays. Missing season weather raises ``ValueError``."""
    pre = pre or load_pre_season()
    B = len(specs)
    T = horizon or max(s.crop.length for s in specs)
    shape = (B, T)
    precip = np.zeros(shape)
    et0 = np.zeros(shape)
    kc = np.zeros(shape)
    stage = np.full(shape, -1, dtype=np.int8)
    valid = np.zeros(shape, dtype=bool)
    p7 = np.zeros(shape)
    e7 = np.zeros(shape)
    fut = np.zeros((B, T, N_LEADS))
    fc = np.zeros((B, T, N_LEADS))
    length = np.zeros(B, dtype=np.int64)
    dr0 = np.zeros(B)
    etc_est = np.zeros(B)
    dev_start = np.zeros(B, dtype=np.int64)
    late_start = np.zeros(B, dtype=np.int64)
    starts = np.empty(B, dtype="datetime64[D]")
    for b, s in enumerate(specs):
        crop = s.crop
        L = crop.length
        start = crop.season_start(s.year)
        pre_start = dt.date(s.year, *pre.start_month_day)
        n_pre = (start - pre_start).days
        if n_pre < 7:
            raise ValueError("pre-season must be at least 7 days (for the 7-day history)")
        p_all, e_all = _daily_on_range(s.weather, pre_start, n_pre + L + N_LEADS)
        core = slice(n_pre, n_pre + L)
        if np.isnan(p_all[: n_pre + L]).any() or np.isnan(e_all[: n_pre + L]).any():
            raise ValueError(f"missing weather for {crop.key} {s.year} ({s.meta})")
        dr0[b] = spin_up(p_all[None, :n_pre], e_all[None, :n_pre], np.array([s.taw]), pre)[0]
        precip[b, :L] = p_all[core]
        et0[b, :L] = e_all[core]
        kc[b, :L] = crop.kc_curve()
        stage[b, :L] = crop.stage_index()
        valid[b, :L] = True
        cs = np.concatenate([[0.0], np.cumsum(p_all[: n_pre + L])])
        ce = np.concatenate([[0.0], np.cumsum(e_all[: n_pre + L])])
        absi = n_pre + np.arange(L)
        p7[b, :L] = cs[absi] - cs[absi - 7]
        e7[b, :L] = ce[absi] - ce[absi - 7]
        p_future = np.nan_to_num(p_all, nan=0.0)
        for j in range(N_LEADS):
            fut[b, :L, j] = p_future[absi + j]
        if s.forecast is not None:
            fc[b, :L] = s.forecast[:L]
        length[b] = L
        own_etc = float((kc[b, :L] * et0[b, :L]).sum())
        est = s.etc_season_est
        etc_est[b] = est if (est is not None and np.isfinite(est) and est > 0) else own_etc
        dev_start[b] = crop.stage_bounds[1]
        late_start[b] = crop.stage_bounds[3]
        starts[b] = np.datetime64(start, "D")
    return SeasonBatch(
        precip=precip,
        et0=et0,
        kc=kc,
        stage=stage,
        valid=valid,
        length=length,
        p_hist7=p7,
        et0_hist7=e7,
        fut=fut,
        forecast=fc,
        taw=np.array([s.taw for s in specs], dtype=np.float64),
        p_tab=np.array([s.crop.p for s in specs], dtype=np.float64),
        ky=np.array([s.crop.ky for s in specs], dtype=np.float64),
        dr0=dr0,
        budget=np.array([s.budget for s in specs], dtype=np.float64),
        etc_season_est=etc_est,
        dev_start=dev_start,
        late_start=late_start,
        start_dates=starts,
        meta=[dict(s.meta, crop=s.crop.key, year=s.year, taw=s.taw, budget=s.budget) for s in specs],
    )


# ======================================================================================
# Observation (shared by env, batch simulation and inference)
# ======================================================================================
def observation(
    batch: SeasonBatch,
    t: int | np.ndarray,
    dr: np.ndarray,
    budget_left: np.ndarray,
    rows: np.ndarray | None = None,
) -> np.ndarray:
    """(B, N_OBS) float32 observation on the morning of day ``t`` (before irrigation).

    Uses only information available at that moment: past weather, the forecast, the
    current soil-water state, budget and crop/soil parameters.
    """
    cfg = load_rl_config()["observation"]
    r = np.arange(batch.size) if rows is None else np.asarray(rows)
    t_arr = np.broadcast_to(np.asarray(t), r.shape)
    taw = batch.taw[r]
    kc = batch.kc[r, t_arr]
    et0_7 = batch.et0_hist7[r, t_arr]
    f = batch.forecast[r, t_arr]
    f3 = f[:, :3].sum(axis=1)
    f7 = f.sum(axis=1)
    budget = batch.budget[r]
    p_tab = batch.p_tab[r]
    raw = adjusted_p(p_tab, kc * et0_7 / 7.0) * taw
    obs = np.stack(
        [
            t_arr / batch.length[r],
            kc,
            dr / taw,
            batch.p_hist7[r, t_arr] / cfg["precip_7d_scale_mm"],
            et0_7 / cfg["et0_7d_scale_mm"],
            f3 / cfg["forecast_scale_mm"],
            f7 / cfg["forecast_scale_mm"],
            np.where(budget > 0, budget_left / np.maximum(budget, 1e-9), 0.0),
            budget_left / cfg["budget_scale_mm"],
            taw / cfg["taw_scale_mm"],
            p_tab,
            batch.ky[r],
            (raw - dr) / taw,
        ],
        axis=1,
    )
    return obs.astype(np.float32)


# ======================================================================================
# Batch simulation with a policy
# ======================================================================================
@dataclass
class SimState:
    """What a policy may look at on the morning of day ``t``."""

    t: int
    dr: np.ndarray
    budget_left: np.ndarray
    batch: SeasonBatch

    def obs(self) -> np.ndarray:
        return observation(self.batch, self.t, self.dr, self.budget_left)

    def raw_estimate(self) -> np.ndarray:
        """RAW from information known in the morning (ETc from the last 7 days' ET0)."""
        b = self.batch
        etc_est = b.kc[:, self.t] * b.et0_hist7[:, self.t] / 7.0
        return adjusted_p(b.p_tab, etc_est) * b.taw


Policy = Callable[[SimState], np.ndarray]


@dataclass
class SimResult:
    yield_rel: np.ndarray
    water_mm: np.ndarray
    dp_mm: np.ndarray
    eta_mm: np.ndarray
    etc_mm: np.ndarray
    precip_mm: np.ndarray
    stress_days: np.ndarray
    dr_end: np.ndarray
    reward: np.ndarray
    traces: dict[str, np.ndarray] | None = None

    def table(self) -> dict[str, np.ndarray]:
        return {k: v for k, v in self.__dict__.items() if k != "traces"}


def daily_reward(
    eta: np.ndarray, etc: np.ndarray, irr: np.ndarray, dp: np.ndarray, ky: np.ndarray, etc_est: np.ndarray, c_w: float, c_p: float
) -> np.ndarray:
    return -ky * (etc - eta) / np.maximum(etc_est, 1e-9) - c_w * irr - c_p * dp


def simulate(
    batch: SeasonBatch,
    policy: Policy,
    *,
    actions: np.ndarray | None = None,
    record: bool = False,
    reward_cfg: dict[str, float] | None = None,
) -> SimResult:
    """Run ``policy`` over all seasons of the batch (aligned at season start)."""
    actions = default_actions() if actions is None else actions
    rc = reward_cfg or load_rl_config()["reward"]
    B, T = batch.size, batch.horizon
    dr = batch.dr0.copy()
    bl = batch.budget.copy()
    sums = {k: np.zeros(B) for k in ("eta", "etc", "irr", "dp", "p", "stress", "reward")}
    tr = {k: np.full((B, T), np.nan) for k in ("dr", "irr", "ks", "raw", "dp", "eta", "etc")} if record else None
    for t in range(T):
        v = batch.valid[:, t]
        if not v.any():
            break
        state = SimState(t, dr, bl, batch)
        irr = legalize(policy(state), bl, actions) * v
        dr_new, eta, etc, dp, ks, raw = balance_step(
            dr, batch.precip[:, t], irr, batch.et0[:, t], batch.kc[:, t], batch.taw, batch.p_tab
        )
        if record:
            tr["dr"][:, t] = np.where(v, dr, np.nan)  # depletion at the start of day t
            for k, val in (("irr", irr), ("ks", ks), ("raw", raw), ("dp", dp), ("eta", eta), ("etc", etc)):
                tr[k][:, t] = np.where(v, val, np.nan)
        r = daily_reward(eta, etc, irr, dp, batch.ky, batch.etc_season_est, rc["c_w"], rc["c_p"])
        dr = np.where(v, dr_new, dr)
        bl = bl - irr
        sums["eta"] += eta * v
        sums["etc"] += etc * v
        sums["irr"] += irr
        sums["dp"] += dp * v
        sums["p"] += batch.precip[:, t] * v
        sums["stress"] += (ks < 1.0 - 1e-9) & v
        sums["reward"] += r * v
    return SimResult(
        yield_rel=relative_yield(sums["eta"], sums["etc"], batch.ky),
        water_mm=sums["irr"],
        dp_mm=sums["dp"],
        eta_mm=sums["eta"],
        etc_mm=sums["etc"],
        precip_mm=sums["p"],
        stress_days=sums["stress"],
        dr_end=dr,
        reward=sums["reward"],
        traces=tr,
    )
