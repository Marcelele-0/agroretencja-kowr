"""M0 smoke tests: the ML stack imports and interoperates on this machine."""

from __future__ import annotations

import numpy as np
import pytest


def test_numpy_torch_interop() -> None:
    torch = pytest.importorskip("torch")
    arr = np.arange(6, dtype=np.float32).reshape(2, 3)
    t = torch.from_numpy(arr)
    assert t.sum().item() == pytest.approx(15.0)
    np.testing.assert_allclose(t.numpy(), arr)


def test_sb3_contrib_imports() -> None:
    pytest.importorskip("sb3_contrib")
    from sb3_contrib import MaskablePPO  # noqa: F401
    from stable_baselines3.common.vec_env import SubprocVecEnv, VecNormalize  # noqa: F401


def _make_cartpole():
    import gymnasium as gym

    return gym.make("CartPole-v1")


def test_subproc_vec_env_windows() -> None:
    """SubprocVecEnv uses spawn on Windows; factories must be importable module-level callables."""
    pytest.importorskip("stable_baselines3")
    from stable_baselines3.common.vec_env import SubprocVecEnv

    venv = SubprocVecEnv([_make_cartpole for _ in range(4)], start_method="spawn")
    try:
        obs = venv.reset()
        assert obs.shape == (4, 4)
        obs, rew, done, info = venv.step(np.zeros(4, dtype=np.int64))
        assert rew.shape == (4,)
    finally:
        venv.close()
