"""Shared test helpers. Synthetic weather is used ONLY in unit tests."""

from __future__ import annotations

import datetime as dt

import numpy as np
import pytest

from kowr_rl.config import load_crops
from kowr_rl.data import DailySeries
from kowr_rl.simulator import SeasonSpec, build_batch


def synthetic_weather(year: int = 2020, seed: int = 0, wet: float = 0.3, scale: float = 6.0) -> DailySeries:
    rng = np.random.default_rng(seed)
    start = np.datetime64(f"{year}-01-01")
    n = 366 + 31
    dates = start + np.arange(n)
    doy = np.arange(n)
    et0 = np.clip(0.5 + 4.0 * np.sin(np.pi * (doy - 60) / 245.0), 0.3, None)
    rain = np.where(rng.random(n) < wet, rng.exponential(scale, n), 0.0)
    return DailySeries(dates, rain, et0)


@pytest.fixture
def crops():
    return load_crops()


@pytest.fixture
def small_batch(crops):
    specs = []
    for i, (crop, taw, budget) in enumerate(
        [("kukurydza", 110.0, 100.0), ("ziemniak", 150.0, 250.0), ("pszenica_ozima", 220.0, 50.0), ("burak_cukrowy", 185.0, 0.0)]
    ):
        w = synthetic_weather(seed=i, wet=0.25)
        specs.append(SeasonSpec(w, 2020, crops[crop], taw, budget, meta={"i": i}))
    return build_batch(specs)


@pytest.fixture
def dry_batch(crops):
    w = synthetic_weather(seed=7, wet=0.08, scale=4.0)
    return build_batch([SeasonSpec(w, 2020, crops["kukurydza"], 150.0, 250.0)])


def date_range(start: dt.date, n: int) -> np.ndarray:
    return np.datetime64(start, "D") + np.arange(n)
