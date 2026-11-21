"""Train MaskablePPO on KowrIrrigationEnv.

* ``SubprocVecEnv`` with N environments, ``VecNormalize`` on observations.
* Deterministic validation callback: the current policy is run through the batch simulator
  on ALL validation seasons (2024, 50 points x 4 crops x 4 soils x 4 budgets) with REAL
  archived ECMWF forecasts; the best checkpoint (mean validation reward) is kept.
  (Replaces SB3 EvalCallback, whose episode subset would shift between evaluations.)
* TensorBoard logs in ``ml/rl_irrigator/runs``.

Usage (Windows needs the __main__ guard for SubprocVecEnv):
    uv run --group train python -m kowr_rl.train --run E1_s1 --seed 1 --timesteps 3000000
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import numpy as np

from . import MODELS_DIR, MODULE_ROOT
from .config import load_rl_config
from .simulator import SeasonBatch, build_batch, simulate


def make_env(rank: int, seed: int, reward_cfg: dict[str, float]):
    def _init():
        from stable_baselines3.common.monitor import Monitor

        from .env import KowrIrrigationEnv

        env = KowrIrrigationEnv("train", reward_cfg=reward_cfg)
        env = Monitor(env, info_keywords=("yield_rel", "water_mm", "dp_mm"))
        env.reset(seed=seed * 10_000 + rank)
        return env

    return _init


def validation_batch(years: list[int] | None = None, budgets: list[float] | None = None) -> SeasonBatch:
    from .dataset import factorial_specs, load_points

    cfg = load_rl_config()
    years = years or cfg["split"]["val_years"]
    budgets = budgets or cfg["evaluation"]["budget_levels_mm"]
    specs = factorial_specs(load_points(), years, cfg["episode"]["crops"], cfg["episode"]["soil_categories"], budgets, forecast="real")
    return build_batch(specs)


def summarize(res: Any) -> dict[str, float]:
    return {
        "reward": float(res.reward.mean()),
        "yield": float(res.yield_rel.mean()),
        "yield_p05": float(np.percentile(res.yield_rel, 5)),
        "water_mm": float(res.water_mm.mean()),
        "dp_mm": float(res.dp_mm.mean()),
        "stress_days": float(res.stress_days.mean()),
    }


def make_validation_callback(val_batch: SeasonBatch, eval_freq: int, out_dir: Path, reward_cfg: dict[str, float], reference: dict[str, dict[str, float]]):
    from stable_baselines3.common.callbacks import BaseCallback

    from .policy import SB3BatchPolicy

    class ValidationCallback(BaseCallback):
        def __init__(self) -> None:
            super().__init__()
            self.best = -np.inf
            self.last = 0
            self.history: list[dict[str, Any]] = []

        def _evaluate(self) -> None:
            t0 = time.perf_counter()
            pol = SB3BatchPolicy(self.model, self.model.get_vec_normalize_env())
            m = summarize(simulate(val_batch, pol, reward_cfg=reward_cfg))
            m["timesteps"] = int(self.num_timesteps)
            m["eval_s"] = round(time.perf_counter() - t0, 2)
            for k, v in m.items():
                self.logger.record(f"val/{k}", v)
            for name, ref in reference.items():
                self.logger.record(f"val/d_yield_vs_{name}", m["yield"] - ref["yield"])
                self.logger.record(f"val/d_water_vs_{name}", m["water_mm"] - ref["water_mm"])
            improved = m["reward"] > self.best
            if improved:
                self.best = m["reward"]
                self.model.save(out_dir / "best_model.zip")
                self.model.get_vec_normalize_env().save(str(out_dir / "best_vecnormalize.pkl"))
            m["best"] = bool(improved)
            self.history.append(m)
            (out_dir / "val_history.json").write_text(json.dumps({"reference": reference, "history": self.history}, indent=1), encoding="utf-8")
            fao = reference.get("fao_rule", {})
            print(
                f"[val] {m['timesteps']:>9,d} steps  reward={m['reward']:+.4f}  yield={m['yield']:.4f} "
                f"(FAO {fao.get('yield', float('nan')):.4f})  water={m['water_mm']:.1f} mm (FAO {fao.get('water_mm', float('nan')):.1f})"
                f"  dp={m['dp_mm']:.1f}{'  *best*' if improved else ''}",
                flush=True,
            )

        def _on_step(self) -> bool:
            if self.num_timesteps - self.last >= eval_freq:
                self.last = self.num_timesteps
                self._evaluate()
            return True

        def _on_training_end(self) -> None:
            self._evaluate()

    return ValidationCallback()


def behavior_cloning(
    model: Any,
    venv: Any,
    reward_cfg: dict[str, float],
    gamma: float,
    *,
    n_seasons: int = 8000,
    epochs: int = 6,
    seed: int = 0,
    teacher: str = "fao_rule",
) -> dict[str, float]:
    """Warm start: imitate a baseline (actor, masked cross-entropy) and fit its returns (critic).

    Data: ``n_seasons`` training seasons sampled like the environment (point, train year, crop,
    soil, budget) with the calibrated synthetic forecast. Also initialises the VecNormalize
    observation statistics from the same data, so PPO starts from the teacher's behaviour.
    """
    import torch as th

    from .baselines import baseline_policies
    from .config import load_crops
    from .dataset import load_points, make_spec
    from .forecast import noisy_forecast
    from .policy import OBS_HIGH, OBS_LOW
    from .simulator import SimState, action_mask, balance_step, daily_reward, default_actions, legalize

    cfg = load_rl_config()
    rng = np.random.default_rng(seed)
    pts = load_points()
    crops = [load_crops()[c] for c in cfg["episode"]["crops"]]
    years = cfg["split"]["train_years"]
    lo, hi = cfg["episode"]["budget_mm"]
    step = cfg["episode"]["budget_step_mm"]
    specs = []
    for _ in range(n_seasons):
        b = float(min(np.floor(rng.uniform(lo, hi + step) / step) * step, hi))
        specs.append(make_spec(pts[rng.integers(len(pts))], int(years[rng.integers(len(years))]), crops[rng.integers(len(crops))], int(rng.integers(1, 5)), b))
    batch = build_batch(specs)
    batch.forecast = noisy_forecast(batch.fut, rng)
    actions = default_actions()
    pol = baseline_policies()[teacher]
    B, T = batch.size, batch.horizon
    obs_buf = np.zeros((B, T, 13), dtype=np.float32)
    act_buf = np.zeros((B, T), dtype=np.int64)
    mask_buf = np.zeros((B, T, len(actions)), dtype=bool)
    rew = np.zeros((B, T))
    dr, bl = batch.dr0.copy(), batch.budget.copy()
    for t in range(T):
        v = batch.valid[:, t]
        if not v.any():
            break
        st = SimState(t, dr, bl, batch)
        obs_buf[:, t] = np.clip(st.obs(), OBS_LOW, OBS_HIGH)
        mask_buf[:, t] = action_mask(bl, actions)
        irr = legalize(pol(st), bl, actions) * v
        act_buf[:, t] = np.searchsorted(actions, irr)
        dr_new, eta, etc, dp, _, _ = balance_step(dr, batch.precip[:, t], irr, batch.et0[:, t], batch.kc[:, t], batch.taw, batch.p_tab)
        rew[:, t] = daily_reward(eta, etc, irr, dp, batch.ky, batch.etc_season_est, reward_cfg["c_w"], reward_cfg["c_p"]) * v
        dr = np.where(v, dr_new, dr)
        bl = bl - irr
    ret = np.zeros((B, T))
    g = np.zeros(B)
    for t in range(T - 1, -1, -1):
        g = rew[:, t] + gamma * g * batch.valid[:, t]
        ret[:, t] = g
    m = batch.valid
    obs, act, msk, tgt = obs_buf[m], act_buf[m], mask_buf[m], ret[m].astype(np.float32)
    venv.obs_rms.mean = obs.mean(axis=0).astype(np.float64)
    venv.obs_rms.var = obs.var(axis=0).astype(np.float64)
    venv.obs_rms.count = float(len(obs))
    x = th.as_tensor(venv.normalize_obs(obs), dtype=th.float32)
    a = th.as_tensor(act)
    mk = th.as_tensor(msk)
    y = th.as_tensor(tgt)
    policy = model.policy
    opt = th.optim.Adam(policy.parameters(), lr=1e-3)
    n = len(x)
    bs = 8192
    stats: dict[str, float] = {}
    for ep in range(epochs):
        perm = th.randperm(n)
        tot_pi = tot_v = 0.0
        for i in range(0, n, bs):
            idx = perm[i : i + bs]
            dist = policy.get_distribution(x[idx], action_masks=mk[idx].numpy())
            loss_pi = -dist.log_prob(a[idx]).mean()
            loss_v = th.nn.functional.mse_loss(policy.predict_values(x[idx]).flatten(), y[idx])
            loss = loss_pi + 0.5 * loss_v
            opt.zero_grad()
            loss.backward()
            th.nn.utils.clip_grad_norm_(policy.parameters(), 0.5)
            opt.step()
            tot_pi += loss_pi.item() * len(idx)
            tot_v += loss_v.item() * len(idx)
        with th.no_grad():
            sub = th.randperm(n)[:200_000]
            pred = policy.get_distribution(x[sub], action_masks=mk[sub].numpy()).distribution.probs.argmax(dim=1)
            acc = float((pred == a[sub]).float().mean())
            irr_rows = a[sub] > 0
            acc_irr = float((pred[irr_rows] == a[sub][irr_rows]).float().mean()) if irr_rows.any() else float("nan")
        stats = {"epoch": ep + 1, "ce": tot_pi / n, "value_mse": tot_v / n, "acc": acc, "acc_on_irrigation_days": acc_irr, "samples": n}
        print(f"[bc] {json.dumps(stats)}", flush=True)
    return stats


def main() -> None:
    import torch
    from sb3_contrib import MaskablePPO
    from stable_baselines3.common.vec_env import SubprocVecEnv, VecNormalize

    from .baselines import baseline_policies

    cfg = load_rl_config()
    ppo = cfg["ppo"]
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--timesteps", type=int, default=ppo["total_timesteps"])
    ap.add_argument("--n-envs", type=int, default=ppo["n_envs"])
    ap.add_argument("--eval-freq", type=int, default=ppo["eval_freq_steps"])
    ap.add_argument("--c-w", type=float, default=cfg["reward"]["c_w"])
    ap.add_argument("--c-p", type=float, default=cfg["reward"]["c_p"])
    ap.add_argument("--ent-coef", type=float, default=ppo["ent_coef"])
    ap.add_argument("--lr", type=float, default=ppo["learning_rate"])
    ap.add_argument("--gamma", type=float, default=ppo["gamma"])
    ap.add_argument("--net", type=int, nargs="+", default=ppo["net_arch"])
    ap.add_argument("--n-steps", type=int, default=ppo["n_steps"])
    ap.add_argument("--batch-size", type=int, default=ppo["batch_size"])
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--bc-seasons", type=int, default=0, help="behaviour-cloning warm start from the FAO rule (0 = off)")
    ap.add_argument("--bc-epochs", type=int, default=6)
    args = ap.parse_args()

    torch.set_num_threads(args.threads)
    reward_cfg = {"c_w": args.c_w, "c_p": args.c_p}
    out_dir = MODELS_DIR / args.run
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "run_config.json").write_text(json.dumps({**vars(args), "reward": reward_cfg}, indent=1), encoding="utf-8")

    t0 = time.perf_counter()
    val = validation_batch()
    reference = {name: summarize(simulate(val, pol, reward_cfg=reward_cfg)) for name, pol in baseline_policies().items()}
    print(f"validation batch: {val.size} seasons ({time.perf_counter() - t0:.1f}s); reference:", json.dumps(reference), flush=True)

    venv = SubprocVecEnv([make_env(i, args.seed, reward_cfg) for i in range(args.n_envs)], start_method="spawn")
    venv = VecNormalize(venv, norm_obs=True, norm_reward=False, clip_obs=10.0, gamma=args.gamma)
    model = MaskablePPO(
        "MlpPolicy",
        venv,
        policy_kwargs={"net_arch": list(args.net)},
        gamma=args.gamma,
        gae_lambda=ppo["gae_lambda"],
        n_steps=args.n_steps,
        batch_size=args.batch_size,
        learning_rate=args.lr,
        ent_coef=args.ent_coef,
        n_epochs=ppo["n_epochs"],
        clip_range=ppo["clip_range"],
        seed=args.seed,
        device="cpu",
        verbose=0,
        tensorboard_log=str(MODULE_ROOT / "runs"),
    )
    cb = make_validation_callback(val, args.eval_freq, out_dir, reward_cfg, reference)
    if args.bc_seasons > 0:
        t_bc = time.perf_counter()
        bc = behavior_cloning(model, venv, reward_cfg, args.gamma, n_seasons=args.bc_seasons, epochs=args.bc_epochs, seed=args.seed)
        from .policy import SB3BatchPolicy

        m0 = summarize(simulate(val, SB3BatchPolicy(model, venv), reward_cfg=reward_cfg))
        print(f"[bc] done in {time.perf_counter() - t_bc:.0f}s; validation after BC: {json.dumps(m0)}", flush=True)
        (out_dir / "bc_summary.json").write_text(json.dumps({"bc": bc, "val_after_bc": m0}, indent=1), encoding="utf-8")
    t1 = time.perf_counter()
    model.learn(total_timesteps=args.timesteps, callback=cb, tb_log_name=args.run, progress_bar=False)
    train_s = time.perf_counter() - t1
    model.save(out_dir / "last_model.zip")
    venv.save(str(out_dir / "last_vecnormalize.pkl"))
    venv.close()
    summary = {"run": args.run, "train_seconds": round(train_s, 1), "steps_per_s": round(args.timesteps / train_s), "best_val_reward": cb.best}
    (out_dir / "train_summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print("done:", json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
