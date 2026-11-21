"""FAO-56 / FAO-33 simulator tests: mass balance, bounds, edge cases, Kc curve."""

from __future__ import annotations

import numpy as np
import pytest

from conftest import synthetic_weather
from kowr_rl.baselines import make_fao_rule, no_irrigation
from kowr_rl.simulator import (
    SeasonSpec,
    action_mask,
    adjusted_p,
    balance_step,
    build_batch,
    legalize,
    relative_yield,
    simulate,
)

ACTIONS = np.array([0.0, 10.0, 20.0, 30.0])


def _random_inputs(n: int, seed: int = 0):
    rng = np.random.default_rng(seed)
    taw = rng.uniform(1.0, 250.0, n)
    dr = rng.uniform(0.0, 1.0, n) * taw
    precip = np.where(rng.random(n) < 0.4, rng.exponential(10.0, n), 0.0)
    irr = rng.choice(ACTIONS, n)
    et0 = rng.uniform(0.0, 8.0, n)
    kc = rng.uniform(0.2, 1.3, n)
    p = rng.uniform(0.2, 0.7, n)
    return dr, precip, irr, et0, kc, taw, p


def test_step_mass_balance_and_bounds() -> None:
    dr, precip, irr, et0, kc, taw, p = _random_inputs(200_000)
    dr_new, eta, etc, dp, ks, raw = balance_step(dr, precip, irr, et0, kc, taw, p)
    storage_change = (taw - dr_new) - (taw - dr)
    np.testing.assert_allclose(storage_change, precip + irr - eta - dp, atol=1e-9)
    assert np.all((dr_new >= 0) & (dr_new <= taw + 1e-9))
    assert np.all((ks >= 0) & (ks <= 1))
    assert np.all(eta >= 0) and np.all(eta <= etc + 1e-12) and np.all(dp >= 0)
    assert np.all((raw >= 0.1 * taw - 1e-9) & (raw <= 0.8 * taw + 1e-9))


def test_ks_definition() -> None:
    taw = np.array([100.0, 100.0, 100.0])
    p = np.array([0.5, 0.5, 0.5])
    et0 = np.full(3, 5.0)
    kc = np.ones(3)  # ETc = 5 -> p unchanged = 0.5 -> RAW = 50
    dr = np.array([40.0, 75.0, 100.0])
    _, eta, etc, _, ks, raw = balance_step(dr, np.zeros(3), np.zeros(3), et0, kc, taw, p)
    np.testing.assert_allclose(raw, 50.0)
    np.testing.assert_allclose(ks, [1.0, 0.5, 0.0])
    np.testing.assert_allclose(eta, [5.0, 2.5, 0.0])


def test_adjusted_p_fao56_footnote() -> None:
    assert adjusted_p(0.55, 5.0) == pytest.approx(0.55)
    assert adjusted_p(0.55, 2.0) == pytest.approx(0.67)
    assert adjusted_p(0.35, 12.0) == pytest.approx(0.1)  # clipped


def test_edge_cases() -> None:
    one = np.ones(1)
    # ET0 = 0: no consumption, nothing changes without inputs
    dr_new, eta, etc, dp, ks, _ = balance_step(30 * one, 0 * one, 0 * one, 0 * one, one, 100 * one, 0.5 * one)
    assert dr_new[0] == 30 and eta[0] == 0 and etc[0] == 0 and dp[0] == 0
    # Cloudburst 200 mm on a dry soil: refills to field capacity, the rest percolates
    dr_new, eta, etc, dp, ks, _ = balance_step(60 * one, 200 * one, 0 * one, 4 * one, one, 110 * one, 0.55 * one)
    assert dr_new[0] == 0 and dp[0] == pytest.approx(200 - 60 - eta[0])
    # Soil at wilting point (Dr = TAW): Ks = 0, no ET without water input
    dr_new, eta, *_ = balance_step(110 * one, 0 * one, 0 * one, 5 * one, one, 110 * one, 0.55 * one)
    assert eta[0] == 0 and dr_new[0] == 110
    # Tiny TAW: ETa is capped by available water, Dr never exceeds TAW
    dr_new, eta, etc, dp, *_ = balance_step(0.5 * one, 0.2 * one, 0 * one, 8 * one, 1.2 * one, 1.0 * one, 0.1 * one)
    assert dr_new[0] <= 1.0 + 1e-12 and eta[0] <= 0.7 + 1e-12


def test_relative_yield_bounds() -> None:
    assert relative_yield(np.array([100.0]), np.array([100.0]), np.array([1.25]))[0] == 1.0
    assert relative_yield(np.array([0.0]), np.array([100.0]), np.array([1.25]))[0] == 0.0
    assert relative_yield(np.array([80.0]), np.array([100.0]), np.array([1.0]))[0] == pytest.approx(0.8)
    assert relative_yield(np.array([0.0]), np.array([0.0]), np.array([1.0]))[0] == 1.0


def test_kc_curve(crops) -> None:
    for c in crops.values():
        kc = c.kc_curve()
        b = c.stage_bounds
        assert len(kc) == c.length
        assert kc[0] == pytest.approx(c.kc_ini)
        assert kc[b[2] - 1] == pytest.approx(c.kc_mid)  # end of development
        assert kc[b[3] - 1] == pytest.approx(c.kc_mid)
        assert kc[-1] == pytest.approx(c.kc_end)
        assert np.all(np.diff(kc[b[1] : b[2]]) * np.sign(c.kc_mid - c.kc_ini) >= -1e-12)
        st = c.stage_index()
        assert st[0] == 0 and st[-1] == 3 and st[b[1]] == 1 and st[b[2]] == 2


def test_legalize_and_mask() -> None:
    bl = np.array([0.0, 15.0, 30.0, 100.0])
    req = np.array([30.0, 30.0, 25.0, 30.0])
    np.testing.assert_allclose(legalize(req, bl, ACTIONS), [0.0, 10.0, 20.0, 30.0])
    m = action_mask(bl, ACTIONS)
    assert m[0].tolist() == [True, False, False, False]
    assert m[1].tolist() == [True, True, False, False]


def test_season_mass_balance(small_batch) -> None:
    res = simulate(small_batch, make_fao_rule(), record=True)
    storage_change = small_batch.dr0 - res.dr_end
    np.testing.assert_allclose(storage_change, res.precip_mm + res.water_mm - res.eta_mm - res.dp_mm, atol=1e-6)
    assert np.all(res.water_mm <= small_batch.budget + 1e-9)
    assert np.all((res.yield_rel >= 0) & (res.yield_rel <= 1))
    assert res.water_mm[3] == 0.0  # zero budget
    # irrigation only on season days and only in action units
    irr = res.traces["irr"][small_batch.valid]
    assert set(np.unique(irr)).issubset(set(ACTIONS))


def test_irrigation_helps_in_dry_season(dry_batch) -> None:
    none = simulate(dry_batch, no_irrigation)
    fao = simulate(dry_batch, make_fao_rule())
    assert fao.yield_rel[0] > none.yield_rel[0] + 0.05
    assert fao.stress_days[0] < none.stress_days[0]


def test_missing_weather_raises(crops) -> None:
    w = synthetic_weather()
    w.precip[150] = np.nan  # inside the maize season
    with pytest.raises(ValueError, match="missing weather"):
        build_batch([SeasonSpec(w, 2020, crops["kukurydza"], 150.0, 100.0)])


def test_spin_up_dr0_within_bounds(small_batch) -> None:
    assert np.all((small_batch.dr0 >= 0) & (small_batch.dr0 <= small_batch.taw))
