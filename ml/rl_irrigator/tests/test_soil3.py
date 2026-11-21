"""Three-zone soil-water model (E9)."""

from __future__ import annotations

import numpy as np

from kowr_rl.config import load_crops
from kowr_rl.soil3 import (
    canopy_share,
    relative_water,
    root_depth_curve,
    root_fractions,
    spin_up3,
    step3,
    water_class,
    zone_awc_from_profile,
)


def test_awc_scaling_and_profile_shape() -> None:
    sandy_over_loam = {
        "wv0033": {"by_depth": {"0-5cm": 15, "5-15cm": 15, "15-30cm": 16, "30-60cm": 28, "60-100cm": 32, "100-200cm": 30}},
        "wv1500": {"by_depth": {"0-5cm": 5, "5-15cm": 5, "15-30cm": 6, "30-60cm": 12, "60-100cm": 14, "100-200cm": 14}},
    }
    awc = zone_awc_from_profile(sandy_over_loam, 150.0)
    assert np.isclose(awc[0] + awc[1], 150.0)
    assert awc[0] / 280 < awc[1] / 720  # sand on top holds less water per cm than the loam below
    flat = zone_awc_from_profile(None, 150.0)
    assert np.allclose(flat / np.array([280, 720, 1000]), flat[0] / 280)


def test_mass_balance_and_bounds() -> None:
    rng = np.random.default_rng(0)
    B = 500
    awc = np.column_stack([rng.uniform(20, 60, B), rng.uniform(60, 160, B), rng.uniform(80, 200, B)])
    d = awc * rng.uniform(0, 1, (B, 3))
    for _ in range(200):
        frac = root_fractions(rng.uniform(0.2, 1.6, B))
        p = np.where(rng.random(B) < 0.4, rng.exponential(6, B), 0.0)
        irr = np.where(rng.random(B) < 0.1, 10.0, 0.0)
        et0, kc, ft = rng.uniform(0.5, 6, B), rng.uniform(0.3, 1.2, B), rng.uniform(0.1, 1, B)
        st = step3(d, awc, frac, p, irr, et0, kc, ft, np.full(B, 0.5))
        storage_before = (awc - d).sum(axis=1)
        storage_after = (awc - st.d).sum(axis=1)
        np.testing.assert_allclose(storage_after - storage_before, p + irr - st.e - st.t - st.dp, atol=1e-9)
        assert (st.d >= -1e-12).all() and (st.d <= awc + 1e-9).all()
        assert (st.ks >= 0).all() and (st.ks <= 1).all() and (st.t <= st.tp + 1e-12).all()
        d = st.d


def test_roots_canopy_and_classes() -> None:
    crops = load_crops()
    z = root_depth_curve(crops["ziemniak"])
    assert z[0] == 0.2 and z[-1] <= 0.6 and np.all(np.diff(z) >= 0)
    assert np.all(root_depth_curve(crops["uzytki_zielone"]) == root_depth_curve(crops["uzytki_zielone"])[0])
    assert np.allclose(root_fractions(np.array(0.28)), [1, 0, 0]) and np.allclose(root_fractions(np.array(2.0)), [1, 1, 1])
    f = canopy_share(crops["kukurydza"])
    assert f[0] == 0.1 and f.max() == 1.0
    assert list(water_class(np.array([0.1, 0.3, 0.5, 0.7, 0.9]))) == [0, 1, 2, 3, 4]
    assert np.allclose(relative_water(np.array([0.0, 50.0]), np.array([50.0, 50.0])), [1.0, 0.0])


def test_spin_up_remembers_a_dry_autumn() -> None:
    awc = np.array([[40.0, 100.0, 120.0], [40.0, 100.0, 120.0]])
    n = 240
    wet = np.full(n, 2.5)
    dry = np.full(n, 0.2)
    et0 = np.full(n, 2.0)
    d = spin_up3(np.vstack([wet, dry]), np.vstack([et0, et0]), awc)
    assert d[1, 2] > d[0, 2] + 10  # the deep zone of the dry case stays depleted
