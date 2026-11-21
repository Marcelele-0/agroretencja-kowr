"""Crop parameters (Kc curve, p, Ky, season dates) for every CropCode of the API contract.

Single source of truth: the RL module's ``ml/rl_irrigator/config/crops.yaml`` for the four
crops it was trained on, plus ``hydro_core/config/crops_extra.yaml`` (same schema) for the
remaining ones. Both are parsed by ``kowr_rl.config.load_crops`` so the assessment (step 1)
and the irrigation agent (step 4) always use identical crop physics.
"""

from __future__ import annotations

import datetime as dt
from functools import lru_cache
from pathlib import Path

import numpy as np
from kowr_rl import CONFIG_DIR as RL_CONFIG_DIR
from kowr_rl.config import (
    STAGE_LABELS_PL,
    STAGES,
    CropParams,
    PreSeason,
    load_crops,
    load_pre_season,
    load_soils,
)

from hydro_core.schemas import CropCode, IungSoilCategory

CONFIG_DIR = Path(__file__).resolve().parent / "config"
RL_CROPS_FILE = RL_CONFIG_DIR / "crops.yaml"
EXTRA_CROPS_FILE = CONFIG_DIR / "crops_extra.yaml"

_CATEGORY_CODE = {IungSoilCategory.I: 1, IungSoilCategory.II: 2, IungSoilCategory.III: 3, IungSoilCategory.IV: 4}

__all__ = [
    "STAGES",
    "STAGE_LABELS_PL",
    "CropParams",
    "PreSeason",
    "all_crops",
    "crop_params",
    "kc_and_p_for_dates",
    "pre_season",
    "rl_crop_keys",
    "stage_on",
    "taw_for",
]


@lru_cache(maxsize=1)
def all_crops() -> dict[str, CropParams]:
    """RL crops + extra crops (RL wins on a duplicated key)."""
    merged = dict(load_crops(str(EXTRA_CROPS_FILE)))
    merged.update(load_crops(str(RL_CROPS_FILE)))
    return merged


def rl_crop_keys() -> set[str]:
    """Crops the RL policy was trained on."""
    return set(load_crops(str(RL_CROPS_FILE)))


def crop_params(code: CropCode) -> CropParams:
    crops = all_crops()
    if code.value not in crops:
        raise KeyError(f"Brak parametrów uprawy '{code.value}' w {RL_CROPS_FILE.name} / {EXTRA_CROPS_FILE.name}")
    return crops[code.value]


def pre_season() -> PreSeason:
    return load_pre_season(str(RL_CROPS_FILE))


def taw_for(category: IungSoilCategory) -> float:
    """Representative TAW [mm] of an IUNG category (ml/rl_irrigator/config/soils.yaml)."""
    return float(load_soils()["categories"][_CATEGORY_CODE[category]]["taw_mm"])


def soil_label(category: IungSoilCategory) -> str:
    return str(load_soils()["categories"][_CATEGORY_CODE[category]]["label"])


def stage_on(crop: CropParams, day: dt.date) -> str | None:
    """Stage key (ini/dev/mid/late) on ``day`` or None outside the crop season."""
    start = crop.season_start(day.year)
    idx = (day - start).days
    if idx < 0 or idx >= crop.length:
        return None
    return STAGES[int(crop.stage_index()[idx])]


def kc_and_p_for_dates(crop: CropParams, dates: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Daily Kc, p_table and in-season flag for arbitrary dates (outside season: pre-season Kc/p)."""
    pre = pre_season()
    dates = np.asarray(dates, dtype="datetime64[D]")
    kc = np.full(len(dates), pre.kc)
    p = np.full(len(dates), pre.p)
    in_season = np.zeros(len(dates), dtype=bool)
    curve = crop.kc_curve()
    years = dates.astype("datetime64[Y]").astype(int) + 1970
    for year in np.unique(years):
        start = np.datetime64(crop.season_start(int(year)), "D")
        idx = (dates - start).astype(int)
        m = (years == year) & (idx >= 0) & (idx < crop.length)
        kc[m] = curve[idx[m]]
        p[m] = crop.p
        in_season[m] = True
    return kc, p, in_season
