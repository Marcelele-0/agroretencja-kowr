"""The torch-free NumpyPolicy (.npz) must take exactly the same decisions as SB3."""

from __future__ import annotations

import numpy as np
import pytest

from conftest import synthetic_weather
from kowr_rl.simulator import N_OBS, N_OBS_V1, SeasonSpec, SimState, action_mask, build_batch, simulate

def test_numpy_policy_matches_sb3(tmp_path, crops) -> None:
    # needs the training stack (sb3-contrib and kowr_rl.env, absent in the runtime-only copy on main)
    sb3_contrib = pytest.importorskip("sb3_contrib")
    pytest.importorskip("kowr_rl.env")
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
    obs = rng.normal(0, 1, size=(2000, N_OBS)).astype(np.float32) * 2
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


def test_v1_policy_reads_first_13_features(crops) -> None:
    """A policy exported before E6 (13 inputs) must act on the first 13 features of the v2 obs."""
    from kowr_rl.policy import NumpyPolicy

    rng = np.random.default_rng(1)
    arrays = {
        "W0": rng.normal(size=(8, N_OBS_V1)), "b0": np.zeros(8), "Wout": rng.normal(size=(4, 8)), "bout": np.zeros(4),
        "obs_mean": np.zeros(N_OBS_V1), "obs_var": np.ones(N_OBS_V1), "clip_obs": np.array(10.0), "epsilon": np.array(1e-8),
        "actions_mm": np.array([0.0, 10.0, 20.0, 30.0]), "meta_json": np.array('{"n_hidden": 1, "activation": "tanh"}'),
    }
    pol = NumpyPolicy(arrays)
    specs = [SeasonSpec(synthetic_weather(seed=s, wet=0.15), 2020, crops[c], 150.0, 100.0) for s, c in enumerate(crops)]
    batch = build_batch(specs)
    obs = SimState(40, batch.dr0, batch.budget, batch).obs()
    assert obs.shape[1] == N_OBS > N_OBS_V1
    masks = action_mask(batch.budget, pol.actions)
    np.testing.assert_array_equal(pol.act(obs, masks), pol.act(obs[:, :N_OBS_V1], masks))
    simulate(batch, pol)  # runs end to end


def test_ensemble_roundtrip(tmp_path, crops) -> None:
    from kowr_rl.policy import EnsemblePolicy, NumpyPolicy, load_policy, save_ensemble

    rng = np.random.default_rng(2)
    paths = []
    for k in range(3):
        arrays = {
            "W0": rng.normal(size=(8, N_OBS)), "b0": np.zeros(8), "Wout": rng.normal(size=(4, 8)), "bout": np.zeros(4),
            "actions_mm": np.array([0.0, 10.0, 20.0, 30.0]), "meta_json": np.array(f'{{"n_hidden": 1, "activation": "tanh", "run": "m{k}"}}'),
        }
        p = tmp_path / f"m{k}.npz"
        np.savez(p, **arrays)
        paths.append(p)
    ens = load_policy(save_ensemble(paths, tmp_path / "ens.npz", {"note": "t"}))
    assert isinstance(ens, EnsemblePolicy) and len(ens.members) == 3 and ens.meta["note"] == "t"
    assert isinstance(load_policy(paths[0]), NumpyPolicy)
    obs = rng.normal(size=(500, N_OBS))
    masks = action_mask(rng.choice([0.0, 10.0, 30.0], size=500), ens.actions)
    probs = ens.probabilities(obs, masks)
    ref = np.mean([NumpyPolicy.load(p).probabilities(obs, masks) for p in paths], axis=0)
    np.testing.assert_allclose(probs, ref)
    np.testing.assert_array_equal(ens.act(obs, masks), np.argmax(np.where(masks, ref, -np.inf), axis=1))
    assert np.all(probs[~masks] == 0)
