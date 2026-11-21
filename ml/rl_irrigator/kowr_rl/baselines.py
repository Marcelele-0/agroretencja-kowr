"""Baseline irrigation strategies, all evaluated with the same simulator and data.

1. ``none``          - no irrigation.
2. ``calendar``      - fixed dose at a fixed interval during development + mid stages; the
                       interval spreads the seasonal budget evenly (min 7, max 30 days).
3. ``fao_rule``      - FAO-56 management rule: when Dr > RAW, refill to field capacity with
                       the dose rounded to an action (max 30 mm), within the budget.
4. ``fao_forecast``  - as 3, but skip irrigation when >= X mm of rain is forecast for 3 days.
5. ``oracle``        - dynamic programming with *perfect* weather knowledge (upper bound),
                       maximising the same reward as the RL agent.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from .config import load_rl_config
from .simulator import (
    Policy,
    SeasonBatch,
    SimResult,
    SimState,
    balance_step,
    daily_reward,
    default_actions,
    simulate,
)


def no_irrigation(state: SimState) -> np.ndarray:
    return np.zeros(state.batch.size)


def make_calendar(cfg: dict[str, Any] | None = None) -> Policy:
    c = cfg or load_rl_config()["baselines"]["calendar"]
    dose = float(c["dose_mm"])
    stage_ids = [{"ini": 0, "dev": 1, "mid": 2, "late": 3}[s] for s in c["stages"]]

    def policy(state: SimState) -> np.ndarray:
        b, t = state.batch, state.t
        in_window = np.isin(b.stage[:, t], stage_ids)
        window_len = np.maximum(b.late_start - b.dev_start, 1)
        n_doses = np.floor(b.budget / dose)
        interval = np.clip(np.floor(window_len / np.maximum(n_doses, 1)), c["min_interval_days"], c["max_interval_days"])
        due = in_window & (n_doses > 0) & (((t - b.dev_start) % interval) == 0)
        return np.where(due, dose, 0.0)

    return policy


def _fao_dose(state: SimState, max_dose: float) -> np.ndarray:
    need = state.dr > state.raw_estimate()
    dose = np.clip(np.round(state.dr / 10.0) * 10.0, 10.0, max_dose)
    return np.where(need, dose, 0.0)


def make_fao_rule(cfg: dict[str, Any] | None = None) -> Policy:
    c = cfg or load_rl_config()["baselines"]["fao_rule"]
    max_dose = float(c["max_dose_mm"])

    def policy(state: SimState) -> np.ndarray:
        return _fao_dose(state, max_dose)

    return policy


def make_fao_forecast(cfg: dict[str, Any] | None = None) -> Policy:
    allc = load_rl_config()["baselines"]
    c = cfg or allc["fao_forecast"]
    max_dose = float(allc["fao_rule"]["max_dose_mm"])
    skip = float(c["skip_if_forecast_3d_mm"])

    def policy(state: SimState) -> np.ndarray:
        f3 = state.batch.forecast[:, state.t, :3].sum(axis=1)
        return np.where(f3 >= skip, 0.0, _fao_dose(state, max_dose))

    return policy


def make_fao_small(cfg: dict[str, Any] | None = None) -> Policy:
    """E6 heuristic: small doses slightly before the stress threshold, skipped before rain.

    Irrigate ``dose_mm`` when Dr > ``threshold_frac`` * RAW_est, unless the forecast for the
    next ``skip_lead_days`` days is >= ``skip_if_forecast_mm``. Small doses leave room in the
    root zone for rain (less deep percolation than refilling to field capacity).
    Parameters were fixed before any test-set evaluation (validation 2024, EXPERIMENTS.md E6).
    """
    c = cfg or load_rl_config()["baselines"]["fao_small"]
    frac, dose = float(c["threshold_frac"]), float(c["dose_mm"])
    lead, skip = int(c["skip_lead_days"]), float(c["skip_if_forecast_mm"])

    def policy(state: SimState) -> np.ndarray:
        d = np.where(state.dr > frac * state.raw_estimate(), dose, 0.0)
        f = state.batch.forecast[:, state.t, :lead].sum(axis=1)
        return np.where(f >= skip, 0.0, d)

    return policy


def baseline_policies() -> dict[str, Policy]:
    return {
        "none": no_irrigation,
        "calendar": make_calendar(),
        "fao_rule": make_fao_rule(),
        "fao_forecast": make_fao_forecast(),
        "fao_small": make_fao_small(),
    }


# ======================================================================================
# Oracle: backward dynamic programming on (Dr/TAW grid x budget grid x day)
# ======================================================================================
def _interp_v(v_next: np.ndarray, frac: np.ndarray) -> np.ndarray:
    """Linear interpolation of ``v_next[B, n_d, n_b]`` at Dr/TAW = ``frac[B, k]`` -> (B, k, n_b)."""
    n_d = v_next.shape[1]
    x = np.clip(frac, 0.0, 1.0) * (n_d - 1)
    i0 = np.clip(np.floor(x).astype(np.int64), 0, n_d - 2)
    w = (x - i0)[..., None]
    i0e = np.broadcast_to(i0[..., None], i0.shape + (v_next.shape[2],))
    lo = np.take_along_axis(v_next, i0e, axis=1)
    hi = np.take_along_axis(v_next, i0e + 1, axis=1)
    return (1.0 - w) * lo + w * hi


class OraclePolicy:
    """Perfect-foresight DP policy for one batch (computes value tables on construction)."""

    def __init__(
        self,
        batch: SeasonBatch,
        *,
        actions: np.ndarray | None = None,
        reward_cfg: dict[str, float] | None = None,
        n_grid: int = 111,
        budget_step: float = 10.0,
    ) -> None:
        self.batch = batch
        self.actions = default_actions() if actions is None else actions
        self.rc = reward_cfg or load_rl_config()["reward"]
        self.step = budget_step
        self.a_idx = np.round(self.actions / budget_step).astype(np.int64)
        if not np.allclose(self.a_idx * budget_step, self.actions):
            raise ValueError("actions must be multiples of budget_step")
        B, T = batch.size, batch.horizon
        self.n_b = int(np.floor(batch.budget.max() / budget_step)) + 1
        grid = np.linspace(0.0, 1.0, n_grid)
        self.V = np.zeros((B, T + 1, n_grid, self.n_b), dtype=np.float32)
        dr_grid = grid[None, :] * batch.taw[:, None]  # (B, n_d)
        b_idx = np.arange(self.n_b)
        for t in range(T - 1, -1, -1):
            v = batch.valid[:, t]
            if not v.any():
                continue
            q = self._q_values(t, dr_grid, b_idx)  # (B, n_d, n_b, A)
            best = q.max(axis=-1)
            self.V[:, t] = np.where(v[:, None, None], best, 0.0).astype(np.float32)

    def _q_values(self, t: int, dr: np.ndarray, b_idx: np.ndarray) -> np.ndarray:
        """Q(t, dr[B,k], budget_idx[n_b], a) -> (B, k, n_b, A); infeasible actions = -inf."""
        b = self.batch
        k = dr.shape[1]
        out = np.full((b.size, k, len(b_idx), len(self.actions)), -np.inf)
        col = lambda x: x[:, None]  # noqa: E731
        for ai, a in enumerate(self.actions):
            irr = np.full_like(dr, a)
            dr_new, eta, etc, dp, _, _ = balance_step(
                dr, col(b.precip[:, t]), irr, col(b.et0[:, t]), col(b.kc[:, t]), col(b.taw), col(b.p_tab)
            )
            r = daily_reward(eta, etc, irr, dp, col(b.ky), col(b.etc_season_est), self.rc["c_w"], self.rc["c_p"])
            v_next = _interp_v(self.V[:, t + 1].astype(np.float64), dr_new / col(b.taw))  # (B, k, n_b)
            nb = b_idx - self.a_idx[ai]
            feasible = nb >= 0
            vn = np.take(v_next, np.clip(nb, 0, None), axis=2)
            q = r[:, :, None] + vn
            q[:, :, ~feasible] = -np.inf
            out[..., ai] = q
        return out

    def q_row(self, state: SimState) -> np.ndarray:
        """(B, A) perfect-foresight action values in the current state (-inf = infeasible)."""
        b_idx = np.clip(np.floor(state.budget_left / self.step + 1e-9).astype(np.int64), 0, self.n_b - 1)
        q = self._q_values(state.t, state.dr[:, None], np.arange(self.n_b))[:, 0]  # (B, n_b, A)
        return q[np.arange(len(b_idx)), b_idx]

    def __call__(self, state: SimState) -> np.ndarray:
        return self.actions[np.argmax(self.q_row(state), axis=1)]


def run_oracle(batch: SeasonBatch, *, chunk: int = 32, record: bool = False, **kw: Any) -> SimResult:
    """Oracle results for a batch, computed in chunks to bound memory."""
    parts = []
    for s in range(0, batch.size, chunk):
        sub = batch.subset(np.arange(s, min(s + chunk, batch.size)))
        parts.append(simulate(sub, OraclePolicy(sub, **kw), record=record))
    fields = {k: np.concatenate([getattr(p, k) for p in parts]) for k in parts[0].table()}
    traces = None
    if record:
        traces = {k: np.concatenate([p.traces[k] for p in parts]) for k in parts[0].traces}
    return SimResult(**fields, traces=traces)
