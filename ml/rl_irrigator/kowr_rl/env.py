"""Gymnasium environment ``KowrIrrigationEnv``: one crop season at one point in one year.

Each episode samples a point, a year (from the split), a crop, an IUNG soil category and
a seasonal water budget. The agent decides every morning how much to irrigate
(0/10/20/30 mm); doses above the remaining budget are masked (``action_masks``).

Reward (dense, sums approximately to the seasonal objective):
    r_t = -Ky (ETc_t - ETa_t) / ETc_season_est - c_w I_t - c_p DP_t
At episode end ``info`` holds the true FAO-33 relative yield and season totals.
"""

from __future__ import annotations

from typing import Any

import gymnasium as gym
import numpy as np
from gymnasium import spaces

from .config import load_crops, load_rl_config
from .dataset import PointData, load_points, make_spec
from .forecast import noisy_forecast
from .simulator import (
    N_OBS,
    SeasonBatch,
    SeasonSpec,
    SimState,
    action_mask,
    balance_step,
    build_batch,
    daily_reward,
    default_actions,
    legalize,
    observation,
    relative_yield,
)

OBS_LOW, OBS_HIGH = -10.0, 10.0


class KowrIrrigationEnv(gym.Env):
    metadata = {"render_modes": []}

    def __init__(
        self,
        split: str = "train",
        *,
        points: tuple[PointData, ...] | list[PointData] | None = None,
        specs: list[SeasonSpec] | None = None,
        forecast_mode: str = "noisy",
        reward_cfg: dict[str, float] | None = None,
    ) -> None:
        """
        Args:
            split: "train" | "val" | "test" -> years from ``config/rl.yaml``.
            points: dataset points (default: all points from ``data/MANIFEST.json``).
            specs: fixed list of seasons, played in order (overrides random sampling).
            forecast_mode: "noisy" (synthetic, training) or "spec" (use ``spec.forecast``).
        """
        super().__init__()
        cfg = load_rl_config()
        self.cfg = cfg
        self.actions = default_actions()
        self.reward_cfg = reward_cfg or cfg["reward"]
        self.specs = specs
        self._spec_i = 0
        self.forecast_mode = forecast_mode
        if specs is None:
            self.points = tuple(points) if points is not None else load_points()
            self.years = list(cfg["split"][f"{split}_years"])
            self.crops = [load_crops()[c] for c in cfg["episode"]["crops"]]
            self.soils = list(cfg["episode"]["soil_categories"])
            self.budget_range = cfg["episode"]["budget_mm"]
            self.budget_step = cfg["episode"]["budget_step_mm"]
        self.observation_space = spaces.Box(OBS_LOW, OBS_HIGH, shape=(N_OBS,), dtype=np.float32)
        self.action_space = spaces.Discrete(len(self.actions))
        self.batch: SeasonBatch | None = None

    # ------------------------------------------------------------------------------
    def _sample_spec(self) -> SeasonSpec:
        rng = self.np_random
        point = self.points[rng.integers(len(self.points))]
        year = int(self.years[rng.integers(len(self.years))])
        crop = self.crops[rng.integers(len(self.crops))]
        soil = int(self.soils[rng.integers(len(self.soils))])
        lo, hi = self.budget_range
        budget = float(np.floor(rng.uniform(lo, hi + self.budget_step) / self.budget_step) * self.budget_step)
        return make_spec(point, year, crop, soil, min(budget, hi))

    def reset(self, *, seed: int | None = None, options: dict[str, Any] | None = None):
        super().reset(seed=seed)
        if self.specs is not None:
            idx = (options or {}).get("spec_index", self._spec_i)
            spec = self.specs[idx % len(self.specs)]
            self._spec_i = idx + 1
        else:
            spec = self._sample_spec()
        self.batch = build_batch([spec])
        if self.forecast_mode == "noisy" or spec.forecast is None:
            self.batch.forecast = noisy_forecast(self.batch.fut, self.np_random)
        self.spec = spec
        self.t = 0
        self.L = int(self.batch.length[0])
        self.dr = self.batch.dr0.copy()
        self.bl = self.batch.budget.copy()
        self.sums = dict.fromkeys(("eta", "etc", "irr", "dp", "p", "stress", "reward"), 0.0)
        return self._obs(), {"meta": self.batch.meta[0]}

    def _obs(self) -> np.ndarray:
        t = min(self.t, self.L - 1)
        return np.clip(observation(self.batch, t, self.dr, self.bl)[0], OBS_LOW, OBS_HIGH)

    def action_masks(self) -> np.ndarray:
        return action_mask(self.bl, self.actions)[0]

    def sim_state(self) -> SimState:
        return SimState(self.t, self.dr, self.bl, self.batch)

    def step(self, action: int):
        b, t = self.batch, self.t
        requested = float(self.actions[int(action)])
        irr = legalize(np.array([requested]), self.bl, self.actions)
        dr_new, eta, etc, dp, ks, _ = balance_step(
            self.dr, b.precip[:, t], irr, b.et0[:, t], b.kc[:, t], b.taw, b.p_tab
        )
        r = float(
            daily_reward(eta, etc, irr, dp, b.ky, b.etc_season_est, self.reward_cfg["c_w"], self.reward_cfg["c_p"])[0]
        )
        self.dr = dr_new
        self.bl = self.bl - irr
        s = self.sums
        s["eta"] += float(eta[0])
        s["etc"] += float(etc[0])
        s["irr"] += float(irr[0])
        s["dp"] += float(dp[0])
        s["p"] += float(b.precip[0, t])
        s["stress"] += float(ks[0] < 1.0 - 1e-9)
        s["reward"] += r
        self.t += 1
        terminated = self.t >= self.L
        info: dict[str, Any] = {"irrigation_mm": float(irr[0]), "masked_violation": irr[0] < requested}
        if terminated:
            y = float(relative_yield(np.array([s["eta"]]), np.array([s["etc"]]), b.ky)[0])
            info.update(
                {
                    "yield_rel": y,
                    "water_mm": s["irr"],
                    "dp_mm": s["dp"],
                    "eta_mm": s["eta"],
                    "etc_mm": s["etc"],
                    "stress_days": s["stress"],
                    "season_reward": s["reward"],
                    "meta": b.meta[0],
                }
            )
        return self._obs(), r, terminated, False, info
