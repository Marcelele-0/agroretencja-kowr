"""Pre-fetched training/evaluation dataset (``data/MANIFEST.json`` + parquet files).

Train-time only (needs pandas/pyarrow from the ``train`` dependency group).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from . import DATA_DIR
from .config import CropParams, load_crops, load_rl_config, taw_for_category
from .data import DailySeries
from .forecast import align_archived_forecast
from .simulator import SeasonSpec


@dataclass
class PointData:
    id: str
    lat: float
    lon: float
    iung_code: int
    voivodeship: str
    weather: DailySeries
    fc_dates: np.ndarray | None = None
    fc: np.ndarray | None = None  # (n, 7) archived ECMWF forecasts, lead 1..7
    meta: dict[str, Any] = field(default_factory=dict)


def load_manifest(data_dir: Path = DATA_DIR) -> dict[str, Any]:
    return json.loads((Path(data_dir) / "MANIFEST.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=4)
def load_points(data_dir: str = str(DATA_DIR)) -> tuple[PointData, ...]:
    import pandas as pd

    d = Path(data_dir)
    man = load_manifest(d)
    out = []
    for p in man["points"]:
        w = pd.read_parquet(d / p["weather_file"])
        series = DailySeries(w["date"].values.astype("datetime64[D]"), w["precip"].values, w["et0"].values)
        fc_dates = fc = None
        if p.get("forecast_file") and (d / p["forecast_file"]).exists():
            f = pd.read_parquet(d / p["forecast_file"])
            fc_dates = f["date"].values.astype("datetime64[D]")
            fc = f[[f"f{k}" for k in range(1, 8)]].values.astype(np.float64)
        out.append(
            PointData(
                id=p["id"],
                lat=float(p["lat"]),
                lon=float(p["lon"]),
                iung_code=int(p["iung_code"]),
                voivodeship=p.get("voivodeship", ""),
                weather=series,
                fc_dates=fc_dates,
                fc=fc,
                meta=p,
            )
        )
    return tuple(out)


def season_etc(point: PointData, crop: CropParams, year: int) -> float:
    w = point.weather.window(crop.season_start(year), crop.season_end(year))
    if len(w) != crop.length or np.isnan(w.et0).any():
        return float("nan")
    return float((crop.kc_curve() * w.et0).sum())


def etc_climatology(point: PointData, crop: CropParams) -> float:
    """Mean seasonal ETc of (point, crop) over the TRAINING years only (no leakage).

    Cached on the point object. NaN if no training season is available.
    """
    cache = point.meta.setdefault("_cache", {})
    key = ("etc_clim", crop.key)
    if key not in cache:
        years = load_rl_config()["split"]["train_years"]
        vals = np.array([season_etc(point, crop, y) for y in years])
        cache[key] = float(vals[np.isfinite(vals)].mean()) if np.isfinite(vals).any() else float("nan")
    return cache[key]


def make_spec(
    point: PointData,
    year: int,
    crop: CropParams,
    soil_category: int,
    budget: float,
    *,
    forecast: str = "none",
) -> SeasonSpec:
    """SeasonSpec for one (point, year, crop, soil, budget).

    ``forecast``: "none" (zeros; set later, e.g. noisy) or "real" (archived ECMWF, 2024+).
    """
    fc = None
    fc_missing = None
    if forecast == "real":
        if point.fc is None:
            raise ValueError(f"no archived forecasts for {point.id}")
        fc, fc_missing = align_archived_forecast(crop.season_start(year), crop.length, point.fc_dates, point.fc)
    return SeasonSpec(
        weather=point.weather,
        year=year,
        crop=crop,
        taw=taw_for_category(soil_category),
        budget=float(budget),
        etc_season_est=etc_climatology(point, crop),
        forecast=fc,
        meta={
            "point": point.id,
            "lat": point.lat,
            "lon": point.lon,
            "soil_cat": int(soil_category),
            "true_soil_cat": point.iung_code,
            "voivodeship": point.voivodeship,
            "forecast": forecast,
            "forecast_missing_frac": fc_missing,
        },
    )


def factorial_specs(
    points: tuple[PointData, ...] | list[PointData],
    years: list[int],
    crops: list[str],
    soil_categories: list[int],
    budgets: list[float],
    *,
    forecast: str = "none",
) -> list[SeasonSpec]:
    allc = load_crops()
    return [
        make_spec(p, y, allc[c], s, b, forecast=forecast)
        for p in points
        for y in years
        for c in crops
        for s in soil_categories
        for b in budgets
    ]
