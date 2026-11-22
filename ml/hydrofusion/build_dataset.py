"""Build the HydroFusion-PL training table: one row per clear Sentinel-2 NDMI observation.

Features are computed with the SAME code as the service: ``hydro_core.timeline.build_timeline``
(as-of the observation date, no look-ahead) + ``hydro_core.features.FeatureContext.vector``.
``ndmi_history_mean`` reproduces the runtime Sentinel-2 client: for each of the 9 previous years
the least cloudy clear scene within +-10 days of the same calendar day, then the mean.

    uv run python ml/hydrofusion/build_dataset.py
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (
    CURRENT_WINDOW_DAYS,
    DATASET_FILE,
    MODEL_FEATURES,
    PREVIOUS_WINDOW_DAYS,
    S2_FILE,
    SOIL_FILE,
    Point,
    load_points,
)

from hydro_core.crops import crop_params, taw_for
from hydro_core.features import AnalysisInputs, FeatureContext, SatelliteObservation
from hydro_core.schemas import CropCode, IungSoilCategory
from hydro_core.timeline import build_timeline

log = logging.getLogger("build_dataset")
HISTORY_YEARS = 9          # backend Settings.s2_history_years
HISTORY_WINDOW_DAYS = 10   # backend Settings.s2_history_window_days
CATEGORY = {1: IungSoilCategory.I, 2: IungSoilCategory.II, 3: IungSoilCategory.III, 4: IungSoilCategory.IV}
# Crop-independent features only; a placeholder crop satisfies the AnalysisInputs contract.
PLACEHOLDER_CROP = CropCode.UZYTKI_ZIELONE


def same_day(day: dt.date, year: int) -> dt.date:
    try:
        return day.replace(year=year)
    except ValueError:
        return day.replace(year=year, day=28)


def history_mean(obs: pd.DataFrame, day: dt.date) -> float | None:
    """Runtime-equivalent NDMI of the same season in the previous 9 years."""
    vals = []
    for k in range(1, HISTORY_YEARS + 1):
        center = same_day(day, day.year - k)
        lo, hi = center - dt.timedelta(days=HISTORY_WINDOW_DAYS), center + dt.timedelta(days=HISTORY_WINDOW_DAYS)
        win = obs[(obs["day"] >= lo) & (obs["day"] <= hi)]
        if len(win):
            vals.append(float(win.sort_values("scene_cloud")["ndmi"].iloc[0]))
    return float(np.mean(vals)) if vals else None


def previous_clear(obs: pd.DataFrame, day: dt.date) -> tuple[float | None, dt.date | None]:
    """Runtime-equivalent last clear scene 16..60 days before ``day`` (most recent first)."""
    lo = day - dt.timedelta(days=PREVIOUS_WINDOW_DAYS)
    hi = day - dt.timedelta(days=CURRENT_WINDOW_DAYS + 1)
    win = obs[(obs["day"] >= lo) & (obs["day"] <= hi)]
    if not len(win):
        return None, None
    last = win.sort_values("day").iloc[-1]
    return float(last["ndmi"]), last["day"]


def point_rows(p: Point, s2: pd.DataFrame, soil: dict) -> list[dict]:
    weather = pd.read_parquet(p.weather_file)
    wdates = [d.date() for d in pd.to_datetime(weather["date"])]
    precip = weather["precip"].tolist()
    et0 = weather["et0"].tolist()
    obs = s2[(s2["point_id"] == p.id) & s2["ndmi"].notna()].copy()
    obs["day"] = obs["date"].dt.date
    category = CATEGORY[p.iung_code]
    crop = crop_params(PLACEHOLDER_CROP)
    rows = []
    for rec in obs.itertuples():
        day: dt.date = rec.day
        start = dt.date(day.year - 10, 1, 1)
        i0 = next(i for i, d in enumerate(wdates) if d >= start)
        i1 = next((i for i, d in enumerate(wdates) if d > day), len(wdates))
        if i1 - i0 < 90:
            continue
        prev_ndmi, prev_day = previous_clear(obs, day)
        tl = build_timeline(
            dates=wdates[i0:i1], precipitation_mm=precip[i0:i1], et0_mm=et0[i0:i1], tmax_c=None,
            analysis_date=day, today=day + dt.timedelta(days=365),
        )
        inputs = AnalysisInputs(
            analysis_date=day, today=day, crop_code=PLACEHOLDER_CROP, crop=crop, soil_category=category,
            soil_source="IUNG-PIB", taw_mm=taw_for(category), cn=70.0, timeline=tl,
            trace=None,  # type: ignore[arg-type]  - no crop-dependent feature is used
            kbw_periods=[], parcel_area_ha=1.0,
            satellite=SatelliteObservation(
                ndmi_history_mean=history_mean(obs, day),
                previous_ndmi=prev_ndmi, previous_acquired=prev_day,
            ),
            soil_sand_pct=soil.get("sand_pct"), soil_soc_g_kg=soil.get("soc_g_kg"), soil_awc_mm=soil.get("awc_mm_0_100"),
        )
        vec = FeatureContext(inputs).vector(MODEL_FEATURES)
        rows.append({
            "point_id": p.id, "split": p.split, "voivodeship": p.voivodeship, "date": pd.Timestamp(day),
            "year": day.year, "ndmi": rec.ndmi, "scene_cloud": rec.scene_cloud, "clear_px": rec.clear_px,
            **dict(zip(MODEL_FEATURES, vec, strict=True)),
        })
    return rows


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    s2 = pd.read_parquet(S2_FILE)
    soils = json.loads(SOIL_FILE.read_text(encoding="utf-8")) if SOIL_FILE.exists() else {}
    started = time.perf_counter()
    rows: list[dict] = []
    points = load_points()
    for k, p in enumerate(points, start=1):
        rows += point_rows(p, s2, soils.get(p.id, {}))
        if k % 10 == 0:
            log.info("%d/%d points, %d rows, %.0f s", k, len(points), len(rows), time.perf_counter() - started)
    df = pd.DataFrame(rows)
    DATASET_FILE.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(DATASET_FILE, index=False)
    missing = {f: round(float(df[f].isna().mean()), 3) for f in MODEL_FEATURES}
    log.info("saved %s: %d rows, %d points; share missing per feature: %s", DATASET_FILE, len(df), df["point_id"].nunique(), missing)


if __name__ == "__main__":
    main()
