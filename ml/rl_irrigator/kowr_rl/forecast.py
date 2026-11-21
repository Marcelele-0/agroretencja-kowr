"""Precipitation forecasts seen by policies.

* :func:`noisy_forecast` - synthetic forecast for training (true rain + noise), so the agent
  cannot become clairvoyant. Parameters in ``config/rl.yaml`` (calibrated on 2024 ECMWF
  archived forecasts, see EXPERIMENTS.md).
* :func:`align_archived_forecast` - real ECMWF IFS forecasts (Open-Meteo Previous Runs API)
  arranged as "forecast for days t..t+6 known on the morning of day t" (lead 1..7).
* :func:`climatology_forecast` - for historical dates without archived forecasts (< 2024):
  mean precipitation of the same calendar days in the other years at the point.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import numpy as np

from .config import load_rl_config
from .data import DailySeries
from .simulator import N_LEADS


def _by_lead(value: float | list[float], n: int) -> np.ndarray:
    """Config value ``x`` or ``[x_lead1, increment_per_lead]`` -> array over leads 1..n."""
    if isinstance(value, (list, tuple)):
        return float(value[0]) + float(value[1]) * np.arange(n)
    return np.full(n, float(value))


def noisy_forecast(fut: np.ndarray, rng: np.random.Generator, cfg: dict[str, Any] | None = None) -> np.ndarray:
    """Noisy version of true future precipitation ``fut[..., j]`` (day t+j, lead j+1).

    Per lead: multiplicative log-normal error around a median bias, "missed" rain days and
    false alarms on dry days, all growing with lead time (calibrated on 2024 ECMWF).
    """
    c = cfg or load_rl_config()["forecast_noise"]
    n = fut.shape[-1]
    sigma = _by_lead(c["sigma"], n)
    miss_p = np.clip(_by_lead(c["miss_prob"], n), 0.0, 1.0)
    fa_p = np.clip(_by_lead(c["false_alarm_prob"], n), 0.0, 1.0)
    fa_mean = np.maximum(_by_lead(c["false_alarm_mean_mm"], n), 1e-6)
    mult = float(c.get("median_ratio", 1.0)) * np.exp(rng.normal(0.0, 1.0, size=fut.shape) * sigma)
    out = fut * mult
    rainy = fut > c["rain_threshold_mm"]
    u = rng.random(fut.shape)
    miss = rainy & (u < miss_p)
    out = np.where(miss, fut * rng.uniform(0.0, c["miss_factor_max"], size=fut.shape), out)
    false_alarm = (~rainy) & (u < fa_p)
    out = np.where(false_alarm, out + rng.exponential(1.0, size=fut.shape) * fa_mean, out)
    return out


def align_archived_forecast(
    season_start: dt.date, length: int, f_dates: np.ndarray, f: np.ndarray
) -> tuple[np.ndarray, float]:
    """(L, 7) matrix: row t = forecast for days t..t+6 issued before the morning of day t.

    ``f[d, k-1]`` is the forecast for date ``f_dates[d]`` issued ``k`` days earlier, so the
    forecast for day t+j known on day t uses lead ``j+1``. Returns the matrix (NaN -> 0)
    and the fraction of cells that were missing.
    """
    f_dates = np.asarray(f_dates, dtype="datetime64[D]")
    out = np.full((length, N_LEADS), np.nan)
    day0 = np.datetime64(season_start, "D")
    for j in range(N_LEADS):
        target = day0 + np.arange(length) + j
        idx = np.searchsorted(f_dates, target)
        idx_c = np.clip(idx, 0, len(f_dates) - 1)
        hit = f_dates[idx_c] == target
        out[hit, j] = f[idx_c[hit], j]
    missing = float(np.isnan(out).mean())
    return np.nan_to_num(out, nan=0.0), missing


def climatology_forecast(weather: DailySeries, season_start: dt.date, length: int, exclude_year: int) -> np.ndarray:
    """(L, 7) climatological 'forecast': mean P of the same day-of-year in other years."""
    years = sorted({int(str(d)[:4]) for d in weather.dates} - {exclude_year})
    out = np.zeros((length, N_LEADS))
    n = np.zeros((length, N_LEADS))
    for y in years:
        try:
            start_y = season_start.replace(year=y)
        except ValueError:  # 29 Feb
            start_y = season_start.replace(year=y, day=28)
        w = weather.window(start_y, start_y + dt.timedelta(days=length + N_LEADS))
        if len(w) < length + N_LEADS:
            continue
        p = np.nan_to_num(w.precip, nan=0.0)
        for j in range(N_LEADS):
            out[:, j] += p[j : j + length]
            n[:, j] += 1
    return np.where(n > 0, out / np.maximum(n, 1), 0.0)
