"""Daily reference evapotranspiration ET0 by FAO-56 Penman-Monteith (Allen et al., 1998, eq. 6).

Used when the weather provider does not deliver ET0 directly (e.g. NASA POWER fallback).
"""

from __future__ import annotations

import math
from datetime import date

STEFAN_BOLTZMANN_MJ = 4.903e-9  # MJ K-4 m-2 day-1


def saturation_vapour_pressure_kpa(temp_c: float) -> float:
    """FAO-56 eq. 11."""
    return 0.6108 * math.exp(17.27 * temp_c / (temp_c + 237.3))


def extraterrestrial_radiation_mj(lat_deg: float, day: date) -> float:
    """FAO-56 eq. 21 (daily Ra in MJ m-2 day-1)."""
    doy = day.timetuple().tm_yday
    phi = math.radians(lat_deg)
    dr = 1.0 + 0.033 * math.cos(2.0 * math.pi * doy / 365.0)
    delta = 0.409 * math.sin(2.0 * math.pi * doy / 365.0 - 1.39)
    ws = math.acos(max(-1.0, min(1.0, -math.tan(phi) * math.tan(delta))))
    return (
        24.0 * 60.0 / math.pi * 0.0820 * dr
        * (ws * math.sin(phi) * math.sin(delta) + math.cos(phi) * math.cos(delta) * math.sin(ws))
    )


def fao56_et0_daily(
    day: date,
    lat_deg: float,
    elevation_m: float,
    tmax_c: float,
    tmin_c: float,
    rh_mean_pct: float,
    wind_2m_ms: float,
    solar_rad_mj: float,
) -> float:
    """Return daily ET0 [mm] from daily weather (FAO-56 eq. 6, actual vapour pressure from RHmean)."""
    tmean = (tmax_c + tmin_c) / 2.0
    pressure = 101.3 * ((293.0 - 0.0065 * elevation_m) / 293.0) ** 5.26
    gamma = 0.000665 * pressure
    es = (saturation_vapour_pressure_kpa(tmax_c) + saturation_vapour_pressure_kpa(tmin_c)) / 2.0
    ea = max(0.0, min(es, rh_mean_pct / 100.0 * es))
    delta = 4098.0 * saturation_vapour_pressure_kpa(tmean) / (tmean + 237.3) ** 2

    ra = extraterrestrial_radiation_mj(lat_deg, day)
    rso = (0.75 + 2e-5 * elevation_m) * ra
    rns = 0.77 * solar_rad_mj
    ratio = min(1.0, solar_rad_mj / rso) if rso > 0 else 0.5
    rnl = (
        STEFAN_BOLTZMANN_MJ
        * ((tmax_c + 273.16) ** 4 + (tmin_c + 273.16) ** 4) / 2.0
        * (0.34 - 0.14 * math.sqrt(ea))
        * (1.35 * ratio - 0.35)
    )
    rn = rns - rnl

    numerator = 0.408 * delta * rn + gamma * 900.0 / (tmean + 273.0) * wind_2m_ms * (es - ea)
    denominator = delta + gamma * (1.0 + 0.34 * wind_2m_ms)
    return max(0.0, numerator / denominator)