"""KowrIrrigationEnv: SB3 env checker, equivalence with the batch simulator, masking."""

from __future__ import annotations

import numpy as np
import pytest

from conftest import synthetic_weather
from kowr_rl.baselines import make_fao_rule, make_calendar
from kowr_rl.dataset import PointData
from kowr_rl.forecast import noisy_forecast
from kowr_rl.simulator import SeasonSpec, build_batch, legalize, simulate

pytest.importorskip("gymnasium")
from kowr_rl.env import KowrIrrigationEnv  # noqa: E402


def _spec_with_forecast(crops, crop="kukurydza", taw=150.0, budget=120.0, seed=3):
    w = synthetic_weather(seed=seed, wet=0.2)
    spec = SeasonSpec(w, 2020, crops[crop], taw, budget, meta={"test": True})
    fut = build_batch([spec]).fut
    spec.forecast = noisy_forecast(fut, np.random.default_rng(seed))[0]
    return spec


def _synthetic_points(n: int = 3) -> list[PointData]:
    return [
        PointData(id=f"syn{i}", lat=52.0, lon=18.0 + i, iung_code=2, voivodeship="", weather=synthetic_weather(seed=10 + i))
        for i in range(n)
    ]


def test_sb3_check_env(crops) -> None:
    from stable_baselines3.common.env_checker import check_env

    env = KowrIrrigationEnv(specs=[_spec_with_forecast(crops)], forecast_mode="spec")
    check_env(env, warn=True)


@pytest.mark.parametrize("policy_factory", [make_fao_rule, make_calendar])
@pytest.mark.parametrize("crop", ["kukurydza", "ziemniak", "pszenica_ozima", "burak_cukrowy"])
def test_env_matches_simulator(crops, policy_factory, crop) -> None:
    spec = _spec_with_forecast(crops, crop=crop)
    policy = policy_factory()
    env = KowrIrrigationEnv(specs=[spec], forecast_mode="spec")
    env.reset(seed=0)
    done, total_r, irr_env = False, 0.0, []
    while not done:
        irr = legalize(policy(env.sim_state()), env.bl, env.actions)[0]
        a = int(np.flatnonzero(env.actions == irr)[0])
        assert env.action_masks()[a]
        _, r, done, trunc, info = env.step(a)
        total_r += r
        irr_env.append(info["irrigation_mm"])
    ref = simulate(build_batch([spec]), policy, record=True)
    assert info["yield_rel"] == pytest.approx(ref.yield_rel[0], abs=1e-12)
    assert info["water_mm"] == pytest.approx(ref.water_mm[0], abs=1e-12)
    assert info["dp_mm"] == pytest.approx(ref.dp_mm[0], abs=1e-9)
    assert info["stress_days"] == ref.stress_days[0]
    assert total_r == pytest.approx(ref.reward[0], abs=1e-9)
    np.testing.assert_allclose(irr_env, ref.traces["irr"][0, : len(irr_env)])


def test_masking_blocks_over_budget(crops) -> None:
    env = KowrIrrigationEnv(specs=[_spec_with_forecast(crops, budget=20.0)], forecast_mode="spec")
    env.reset(seed=0)
    assert env.action_masks().tolist() == [True, True, True, False]
    _, _, _, _, info = env.step(2)  # 20 mm
    assert env.action_masks().tolist() == [True, False, False, False]
    _, _, _, _, info = env.step(3)  # masked request -> legalised to 0 mm
    assert info["irrigation_mm"] == 0.0 and info["masked_violation"]


def test_random_sampling_is_seeded() -> None:
    pts = _synthetic_points()
    e1 = KowrIrrigationEnv(points=pts)
    e2 = KowrIrrigationEnv(points=pts)
    e1.years = e2.years = [2020]
    m1 = [e1.reset(seed=s)[1]["meta"] for s in range(5)]
    m2 = [e2.reset(seed=s)[1]["meta"] for s in range(5)]
    assert m1 == m2
    o1, _ = e1.reset(seed=42)
    o2, _ = e2.reset(seed=42)
    np.testing.assert_array_equal(o1, o2)
    assert e1.observation_space.contains(o1)


def test_observation_has_no_future_weather(crops) -> None:
    """Changing weather after day t must not change the observation at day t (except forecast)."""
    spec = _spec_with_forecast(crops)
    env_a = KowrIrrigationEnv(specs=[spec], forecast_mode="spec")
    obs_a, _ = env_a.reset(seed=0)
    w2 = synthetic_weather(seed=3, wet=0.2)
    start = spec.crop.season_start(2020)
    i0 = int((np.datetime64(start) - w2.dates[0]).astype(int))
    w2.precip[i0:] = 99.0  # different future rain from season day 0 on
    spec2 = SeasonSpec(w2, 2020, spec.crop, spec.taw, spec.budget, forecast=spec.forecast)
    env_b = KowrIrrigationEnv(specs=[spec2], forecast_mode="spec")
    obs_b, _ = env_b.reset(seed=0)
    np.testing.assert_array_equal(obs_a, obs_b)
