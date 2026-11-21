"""Baseline strategies and the DP oracle."""

from __future__ import annotations

import numpy as np

from kowr_rl.baselines import baseline_policies, make_calendar, run_oracle
from kowr_rl.forecast import noisy_forecast
from kowr_rl.simulator import simulate


def test_baselines_respect_budget(small_batch) -> None:
    for name, pol in baseline_policies().items():
        res = simulate(small_batch, pol)
        assert np.all(res.water_mm <= small_batch.budget + 1e-9), name
        assert np.all((res.yield_rel >= 0) & (res.yield_rel <= 1)), name


def test_calendar_only_in_dev_and_mid(small_batch) -> None:
    res = simulate(small_batch, make_calendar(), record=True)
    irr = np.nan_to_num(res.traces["irr"])
    stage = small_batch.stage
    assert np.all(irr[(stage == 0) | (stage == 3)] == 0)
    assert res.water_mm[1] > 0  # budget 250 is used


def test_oracle_is_upper_bound_on_reward(small_batch) -> None:
    rng = np.random.default_rng(0)
    small_batch.forecast = noisy_forecast(small_batch.fut, rng)
    oracle = run_oracle(small_batch)
    for name, pol in baseline_policies().items():
        res = simulate(small_batch, pol)
        # grid interpolation can cost a hair of optimality; allow a tiny tolerance
        assert np.all(oracle.reward >= res.reward - 2e-3), (name, oracle.reward, res.reward)
    assert np.all(oracle.water_mm <= small_batch.budget + 1e-9)


def test_noisy_forecast_properties() -> None:
    rng = np.random.default_rng(1)
    fut = np.where(rng.random((2000, 50, 7)) < 0.3, rng.exponential(6, (2000, 50, 7)), 0.0)
    f = noisy_forecast(fut, rng)
    assert f.shape == fut.shape and np.all(f >= 0)
    rainy = fut > 1.0
    corr = np.corrcoef(f[rainy], fut[rainy])[0, 1]
    assert 0.3 < corr < 0.99  # informative but not clairvoyant
    assert np.mean(f[~rainy & (fut == 0)] > 0) > 0.05  # some false alarms


def test_fao_small_doses_and_forecast_skip(small_batch) -> None:
    from kowr_rl.baselines import make_fao_small
    from kowr_rl.simulator import SimState

    pol = make_fao_small({"threshold_frac": 0.9, "dose_mm": 10, "skip_lead_days": 2, "skip_if_forecast_mm": 5.0})
    res = simulate(small_batch, pol, record=True)
    irr = np.nan_to_num(res.traces["irr"])
    assert set(np.unique(irr)) <= {0.0, 10.0}
    # a 2-day forecast of >= 5 mm suppresses the dose even when the soil is dry
    b = small_batch.subset([0])
    b.forecast[:] = 0.0
    b.forecast[0, 60, :2] = [3.0, 2.0]
    dry = np.array([0.95 * b.taw[0]])
    assert pol(SimState(60, dry, b.budget.copy(), b))[0] == 0.0
    assert pol(SimState(61, dry, b.budget.copy(), b))[0] == 10.0
