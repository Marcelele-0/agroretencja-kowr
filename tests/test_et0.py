"""FAO-56 Penman-Monteith ET0 against FAO-56 Example 18 (Brussels, 6 July, ET0 = 3.9 mm/day)."""

from __future__ import annotations

from datetime import date

import pytest

from hydro_core.et0 import extraterrestrial_radiation_mj, fao56_et0_daily


def test_extraterrestrial_radiation_example_8() -> None:
    # FAO-56 Example 8: 20°S, 3 September -> Ra = 32.2 MJ m-2 day-1
    assert extraterrestrial_radiation_mj(-20.0, date(2015, 9, 3)) == pytest.approx(32.2, abs=0.2)


def test_et0_example_18() -> None:
    et0 = fao56_et0_daily(
        day=date(2015, 7, 6),
        lat_deg=50.8,
        elevation_m=100.0,
        tmax_c=21.5,
        tmin_c=12.3,
        rh_mean_pct=73.5,
        wind_2m_ms=2.078,
        solar_rad_mj=22.07,
    )
    assert et0 == pytest.approx(3.9, abs=0.25)