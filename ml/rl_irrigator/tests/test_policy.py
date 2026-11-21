"""The torch-free NumpyPolicy (.npz) must take exactly the same decisions as SB3."""

from __future__ import annotations

import numpy as np
import pytest

from conftest import synthetic_weather
from kowr_rl.simulator import SeasonSpec, action_mask, build_batch, simulate

sb3_contrib = pytest.importorskip("sb3_contrib")


def test_numpy_policy_matches_sb3(tmp_path, crops) -> None:
    from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize

    from kowr_rl.env import KowrIrrigationEnv
    from kowr_rl.policy import NumpyPolicy, SB3BatchPolicy, export_npz

    specs = [SeasonSpec(synthetic_weather(seed=s, wet=0.15), 2020, crops[c], 150.0, 200.0) for s, c in enumerate(crops)]
    venv = VecNormalize(DummyVecEnv([lambda: KowrIrrigationEnv(specs=specs)]), norm_obs=True, norm_reward=False)
    model = sb3_contrib.MaskablePPO(
        "MlpPolicy", venv, n_steps=256, batch_size=128, n_epochs=2, policy_kwargs={"net_arch": [32, 32]}, seed=0, device="cpu"
    )
    model.learn(512)
    path = export_npz(model, venv, tmp_path / "policy.npz", meta={"run": "test"})
    npol = NumpyPolicy.load(path)
    assert npol.meta["run"] == "test" and npol.meta["activation"] == "tanh"

    rng = np.random.default_rng(0)
    obs = rng.normal(0, 1, size=(2000, 13)).astype(np.float32) * 2
    budget_left = rng.choice([0.0, 10.0, 25.0, 100.0], size=2000)
    masks = action_mask(budget_left, npol.actions)
    a_sb3, _ = model.predict(venv.normalize_obs(np.clip(obs, -10, 10)), action_masks=masks, deterministic=True)
    a_np = npol.act(obs, masks)
    np.testing.assert_array_equal(a_sb3, a_np)
    probs = npol.probabilities(obs, masks)
    np.testing.assert_allclose(probs.sum(axis=1), 1.0)
    assert np.all(probs[~masks] == 0)

    batch = build_batch(specs)
    r1 = simulate(batch, SB3BatchPolicy(model, venv))
    r2 = simulate(batch, npol)
    np.testing.assert_allclose(r1.water_mm, r2.water_mm)
    np.testing.assert_allclose(r1.yield_rel, r2.yield_rel)
