"""DAgger warm start with a privileged teacher: the perfect-foresight DP oracle (E6).

The oracle knows the future weather of a season, so its decisions are not reachable from
the agent's observations. Plain imitation (cross-entropy on the oracle's action) fails: the
oracle irrigates on days the student cannot tell apart from days it does not (rain the
student cannot foresee), so "irrigate" rarely has > 50 % probability given the observation
and the argmax student never irrigates (first E6 attempt: 0.6 % recall on irrigation days).

Instead the student minimises the oracle's *regret* (AggreVaTe, Ross & Bagnell 2014):
every visited state is labelled with the oracle's action values Q*(s, a) and the actor
loss is E_pi[max_a Q* - Q*(s, a)]. Given an observation, the best student action is the one
with the smallest regret averaged over the futures it cannot see (hindsight optimisation).
This teaches small, well-timed doses that leave room in the soil for rain; PPO fine-tuning
afterwards optimises the true objective under forecast uncertainty.

Round k: roll out the mixture policy (oracle with probability beta_k, otherwise the current
student) on fresh training seasons, label the visited states, aggregate all rounds and refit
the actor. The critic is fitted to the returns of the last round (beta = 0, i.e. the
student's own behaviour).

Rollouts and oracle tables run in a spawn ``multiprocessing`` pool (Windows-safe; call
from under ``if __name__ == "__main__"``).
"""

from __future__ import annotations

import json
import multiprocessing as mp
import os
import time
from pathlib import Path
from typing import Any

import numpy as np

from .config import load_crops, load_rl_config
from .simulator import (
    N_OBS,
    SimState,
    action_mask,
    balance_step,
    build_batch,
    daily_reward,
    default_actions,
    legalize,
    relative_yield,
)

_W: dict[str, Any] = {}


def _init_worker() -> None:
    os.environ["OMP_NUM_THREADS"] = "1"
    from .dataset import load_points

    _W["points"] = load_points()
    _W["crops"] = load_crops()


def sample_seasons(
    n: int, rng: np.random.Generator, low_budget_prob: float | None = None
) -> list[tuple[int, int, str, int, float]]:
    """``n`` training seasons (point index, year, crop key, soil category, budget), env-like."""
    from .dataset import load_points
    from .env import sample_budget

    cfg = load_rl_config()
    ep = cfg["episode"]
    n_pts = len(load_points())
    years, crops, soils = cfg["split"]["train_years"], ep["crops"], ep["soil_categories"]
    return [
        (
            int(rng.integers(n_pts)),
            int(years[rng.integers(len(years))]),
            str(crops[rng.integers(len(crops))]),
            int(soils[rng.integers(len(soils))]),
            sample_budget(rng, ep, low_budget_prob),
        )
        for _ in range(n)
    ]


def rollout_chunk(job: tuple) -> dict[str, np.ndarray]:
    """Roll out the oracle/student mixture on a chunk of seasons; label states with the oracle."""
    seasons, seed, student_path, beta, reward_cfg, gamma = job
    from .baselines import OraclePolicy
    from .dataset import load_points, make_spec
    from .forecast import noisy_forecast
    from .policy import OBS_HIGH, OBS_LOW, NumpyPolicy

    pts = _W.get("points") or load_points()
    crops = _W.get("crops") or load_crops()
    rng = np.random.default_rng(seed)
    batch = build_batch([make_spec(pts[i], y, crops[c], s, b) for i, y, c, s, b in seasons])
    batch.forecast = noisy_forecast(batch.fut, rng)
    oracle = OraclePolicy(batch, reward_cfg=reward_cfg)
    student = NumpyPolicy.load(student_path) if (student_path is not None and beta < 1.0) else None
    actions = default_actions()
    B, T = batch.size, batch.horizon
    obs = np.zeros((B, T, N_OBS), dtype=np.float32)
    lab = np.zeros((B, T), dtype=np.int8)
    reg = np.zeros((B, T, len(actions)), dtype=np.float32)
    msk = np.zeros((B, T, len(actions)), dtype=bool)
    rew = np.zeros((B, T))
    s_eta, s_etc, s_irr = np.zeros(B), np.zeros(B), np.zeros(B)
    dr, bl = batch.dr0.copy(), batch.budget.copy()
    for t in range(T):
        v = batch.valid[:, t]
        if not v.any():
            break
        st = SimState(t, dr, bl, batch)
        o = np.clip(st.obs(), OBS_LOW, OBS_HIGH)
        m = action_mask(bl, actions)
        q = oracle.q_row(st)  # (B, A), -inf where infeasible
        a_idx = np.argmax(q, axis=1)
        a_or = actions[a_idx]
        obs[:, t], msk[:, t] = o, m
        lab[:, t] = a_idx
        reg[:, t] = np.where(np.isfinite(q), q.max(axis=1, keepdims=True) - q, 0.0)
        if student is None:
            a = a_or
        else:
            a_st = actions[student.act(o, m)]
            a = np.where(rng.random(B) < beta, a_or, a_st)
        irr = legalize(a, bl, actions) * v
        dr_new, eta, etc, dp, _, _ = balance_step(
            dr, batch.precip[:, t], irr, batch.et0[:, t], batch.kc[:, t], batch.taw, batch.p_tab
        )
        rew[:, t] = (
            daily_reward(
                eta,
                etc,
                irr,
                dp,
                batch.ky,
                batch.etc_season_est,
                reward_cfg["c_w"],
                reward_cfg["c_p"],
            )
            * v
        )
        s_eta += eta * v
        s_etc += etc * v
        s_irr += irr
        dr = np.where(v, dr_new, dr)
        bl = bl - irr
    ret = np.zeros((B, T))
    g = np.zeros(B)
    for t in range(T - 1, -1, -1):
        g = rew[:, t] + gamma * g * batch.valid[:, t]
        ret[:, t] = g
    v = batch.valid
    return {
        "obs": obs[v],
        "label": lab[v].astype(np.int64),
        "regret": reg[v],
        "mask": msk[v],
        "ret": ret[v].astype(np.float32),
        "yield": relative_yield(s_eta, s_etc, batch.ky),
        "water": s_irr,
        "reward": rew.sum(axis=1),
    }


def fit_actor_critic(
    policy: Any,
    x: Any,
    regret: Any,
    mk: Any,
    y: Any | None = None,
    *,
    label: Any | None = None,
    epochs: int,
    lr: float = 1e-3,
    batch_size: int = 8192,
    ent_coef: float = 0.01,
    tag: str = "fit",
) -> dict[str, float]:
    """Actor: expected (scaled) oracle regret under pi, minus a small entropy bonus.

    Optional critic MSE on ``y`` (returns). ``regret`` is pre-scaled to O(1).
    """
    import torch as th

    opt = th.optim.Adam(policy.parameters(), lr=lr)
    n = len(x)
    stats: dict[str, float] = {}
    for ep in range(epochs):
        perm = th.randperm(n)
        tot_pi = tot_v = 0.0
        for i in range(0, n, batch_size):
            idx = perm[i : i + batch_size]
            dist = policy.get_distribution(x[idx], action_masks=mk[idx].numpy())
            probs = dist.distribution.probs
            loss_pi = (probs * regret[idx]).sum(dim=1).mean() - ent_coef * dist.entropy().mean()
            loss = loss_pi
            if y is not None:
                loss_v = th.nn.functional.mse_loss(policy.predict_values(x[idx]).flatten(), y[idx])
                loss = loss + 0.5 * loss_v
                tot_v += loss_v.item() * len(idx)
            opt.zero_grad()
            loss.backward()
            th.nn.utils.clip_grad_norm_(policy.parameters(), 0.5)
            opt.step()
            tot_pi += loss_pi.item() * len(idx)
        with th.no_grad():
            sub = th.randperm(n)[:200_000]
            pred = policy.get_distribution(x[sub], action_masks=mk[sub].numpy()).distribution.probs.argmax(dim=1)
            r_sub = regret[sub]
            stats = {
                "epoch": ep + 1,
                "loss_pi": tot_pi / n,
                "regret_student_argmax": float(r_sub.gather(1, pred[:, None]).mean()),
                "regret_never_irrigate": float(r_sub[:, 0].mean()),
                "student_irrigation_rate": float((pred > 0).float().mean()),
                "samples": n,
            }
            if label is not None:
                stats["oracle_irrigation_rate"] = float((label[sub] > 0).float().mean())
                stats["agreement"] = float((pred == label[sub]).float().mean())
        if y is not None:
            stats["value_mse"] = tot_v / n
        print(f"[{tag}] {json.dumps(stats)}", flush=True)
    return stats


def dagger_warm_start(
    model: Any,
    venv: Any,
    reward_cfg: dict[str, float],
    gamma: float,
    out_dir: Path,
    *,
    seasons_per_round: int = 6000,
    betas: tuple[float, ...] = (1.0, 0.5, 0.2, 0.0),
    epochs: int = 4,
    seed: int = 0,
    low_budget_prob: float | None = None,
    workers: int | None = None,
    chunk: int = 32,
) -> dict[str, Any]:
    import torch as th

    from .policy import export_npz

    rng = np.random.default_rng(seed + 7919)
    workers = workers or max(1, min(24, (os.cpu_count() or 2) - 2))
    student_path = Path(out_dir) / "dagger_student.npz"
    data: list[dict[str, np.ndarray]] = []
    log: list[dict[str, Any]] = []
    ctx = mp.get_context("spawn")
    with ctx.Pool(workers, initializer=_init_worker) as pool:
        for k, beta in enumerate(betas):
            t0 = time.perf_counter()
            seasons = sample_seasons(seasons_per_round, rng, low_budget_prob)
            seasons.sort(key=lambda s: s[4])  # similar budgets per chunk -> small oracle tables
            jobs = [
                (
                    seasons[i : i + chunk],
                    int(rng.integers(2**31)),
                    str(student_path) if k > 0 else None,
                    beta,
                    reward_cfg,
                    gamma,
                )
                for i in range(0, len(seasons), chunk)
            ]
            parts = pool.map(rollout_chunk, jobs, chunksize=1)
            rnd = {key: np.concatenate([p[key] for p in parts]) for key in parts[0]}
            data.append(rnd)
            t_roll = time.perf_counter() - t0
            if k == 0:
                venv.obs_rms.mean = rnd["obs"].mean(axis=0).astype(np.float64)
                venv.obs_rms.var = rnd["obs"].var(axis=0).astype(np.float64)
                venv.obs_rms.count = float(len(rnd["obs"]))
            if k == 0:
                # one fixed scale for all rounds: mean positive regret on the oracle's own states
                r0 = rnd["regret"]
                regret_scale = float(r0[r0 > 0].mean()) if (r0 > 0).any() else 1.0
            obs = np.concatenate([d["obs"] for d in data])
            x = th.as_tensor(venv.normalize_obs(obs), dtype=th.float32)
            rg = th.as_tensor(np.concatenate([d["regret"] for d in data]) / regret_scale)
            lb = th.as_tensor(np.concatenate([d["label"] for d in data]))
            mk = th.as_tensor(np.concatenate([d["mask"] for d in data]))
            last = k == len(betas) - 1
            stats = fit_actor_critic(model.policy, x, rg, mk, label=lb, epochs=epochs, tag=f"dagger{k}")
            if last:
                # critic (and a final actor pass) on the student's own last-round trajectories
                xl = th.as_tensor(venv.normalize_obs(rnd["obs"]), dtype=th.float32)
                stats = fit_actor_critic(
                    model.policy,
                    xl,
                    th.as_tensor(rnd["regret"] / regret_scale),
                    th.as_tensor(rnd["mask"]),
                    th.as_tensor(rnd["ret"]),
                    label=th.as_tensor(rnd["label"]),
                    epochs=2,
                    lr=3e-4,
                    tag=f"dagger{k}-critic",
                )
            export_npz(model, venv, student_path, {"source": "dagger", "round": k})
            entry = {
                "round": k,
                "beta": beta,
                "rollout_s": round(t_roll, 1),
                "behaviour_yield": float(rnd["yield"].mean()),
                "behaviour_water_mm": float(rnd["water"].mean()),
                "behaviour_reward": float(rnd["reward"].mean()),
                "samples_total": len(obs),
                **stats,
            }
            log.append(entry)
            print(f"[dagger] {json.dumps(entry)}", flush=True)
    return {
        "rounds": log,
        "workers": workers,
        "seasons_per_round": seasons_per_round,
        "betas": list(betas),
        "regret_scale": regret_scale,
    }
