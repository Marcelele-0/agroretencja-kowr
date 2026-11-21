"""Throughput of KowrIrrigationEnv: single process and SubprocVecEnv (random masked actions).

Usage: uv run --group train python ml/rl_irrigator/scripts/bench_env.py [--n-envs 16] [--steps 20000]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def make_env(rank: int):
    def _init():
        from kowr_rl.env import KowrIrrigationEnv

        env = KowrIrrigationEnv("train")
        env.reset(seed=rank)
        return env

    return _init


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-envs", type=int, default=16)
    ap.add_argument("--steps", type=int, default=20000)
    args = ap.parse_args()

    env = make_env(0)()
    rng = np.random.default_rng(0)
    env.reset(seed=0)
    t0 = time.perf_counter()
    for _ in range(args.steps):
        m = env.action_masks()
        _, _, done, _, _ = env.step(int(rng.choice(np.flatnonzero(m))))
        if done:
            env.reset()
    single = args.steps / (time.perf_counter() - t0)
    print(f"single process: {single:,.0f} steps/s")

    from stable_baselines3.common.vec_env import SubprocVecEnv

    venv = SubprocVecEnv([make_env(i) for i in range(args.n_envs)], start_method="spawn")
    venv.reset()
    n_iter = args.steps // args.n_envs * 4
    t0 = time.perf_counter()
    for _ in range(n_iter):
        masks = np.stack(venv.env_method("action_masks"))
        acts = np.array([rng.choice(np.flatnonzero(m)) for m in masks])
        venv.step(acts)
    vec = n_iter * args.n_envs / (time.perf_counter() - t0)
    venv.close()
    print(f"SubprocVecEnv x{args.n_envs}: {vec:,.0f} steps/s (incl. action-mask round trip)")


if __name__ == "__main__":
    main()
