"""Shared settings of the HydroFusion-PL training pipeline (paths, points, splits, feature list).

Pipeline (run from the repository root):

    uv run python ml/hydrofusion/fetch_s2_samples.py   # Sentinel-2 NDMI at 100 points, 2018-2026 (~1 h)
    uv run python ml/hydrofusion/fetch_soilgrids.py    # SoilGrids texture / carbon at the points (~20 min)
    uv run python ml/hydrofusion/build_dataset.py      # features through hydro_core.features (no skew)
    uv run python ml/hydrofusion/train.py              # quantile LightGBM + conformal + AoA + validation

Points: the 100 agricultural points of the RL module (``ml/rl_irrigator/data``): 50 training
points + 50 hold-out points (>= 25 km away, all 16 voivodeships), with Open-Meteo weather
2016-2026 already in the repository (no new Open-Meteo requests needed).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_DIR = Path(__file__).resolve().parent
DATA_DIR = MODULE_DIR / "data"
REPORTS_DIR = MODULE_DIR / "reports"
MODELS_DIR = MODULE_DIR / "models"
CACHE_DIR = REPO_ROOT / "data" / "cache" / "hydrofusion"
RL_DATA = REPO_ROOT / "ml" / "rl_irrigator" / "data"
ARTIFACT_MODELS = REPO_ROOT / "artifacts" / "models"
ARTIFACT_VALIDATION = REPO_ROOT / "artifacts" / "validation"

S2_FILE = DATA_DIR / "s2_samples.parquet"
SOIL_FILE = DATA_DIR / "soilgrids_points.json"
DATASET_FILE = DATA_DIR / "dataset.parquet"

# Earth Search C1 has Sentinel-2 over Poland from December 2017; growing season only.
S2_YEARS = list(range(2018, 2027))
SEASON_MONTHS = (3, 10)  # March .. October
MIN_CLEAR_PIXELS = 6  # of the 3x3 window at 20 m (runtime uses >= 60 % clear parcel pixels)

# Splits (blueprint 3.3, adapted: spatial hold-out = RL hold-out points, temporal = drought years)
TEMPORAL_HOLDOUT_YEARS = (2018, 2023)
CALIBRATION_FRACTION = 0.2  # of training points, for split-conformal and AoA threshold
QUANTILES = {"q05": 0.05, "q50": 0.50, "q95": 0.95}
COVERAGE = 0.90

# Model inputs = names of hydro_core.features.FEATURES. Crop-dependent features (Kc, stage,
# root-zone deficit) are excluded: the crop at training points is unknown (decision 4).
# Terrain features are excluded: training points have no parcel DEM comparable to NMT 1 m.
# Forecast features are excluded: they would leak the future for historical samples.
MODEL_FEATURES = [
    "p_15", "p_30", "p_60", "et0_30", "kbw_30", "kbw_60", "kbw_60_normal", "kbw_60_anomaly", "cdd",
    "taw_mm", "iung_category", "sand_pct", "soc_g_kg", "awc_mm_0_100",
    "doy",
    "ndmi_history_mean",
    # last clear scene 16-60 days back (backend: S2ParcelSeries.previous). Experiment 22.11.2026 on
    # the spatial hold-out: R2 0.28 -> 0.62, RMSE 0.180 -> 0.132 (persistence alone: R2 0.45).
    "ndmi_prev",
    "ndmi_prev_lag",
]
CURRENT_WINDOW_DAYS = 15   # backend Settings.s2_current_window_days
PREVIOUS_WINDOW_DAYS = 60  # backend Settings.s2_previous_window_days


@dataclass(frozen=True)
class Point:
    id: str
    lat: float
    lon: float
    iung_code: int
    voivodeship: str
    weather_file: Path
    split: str  # "train" | "holdout"


def load_points() -> list[Point]:
    out: list[Point] = []
    for split, base in (("train", RL_DATA), ("holdout", RL_DATA / "holdout")):
        manifest = json.loads((base / "MANIFEST.json").read_text(encoding="utf-8"))
        for p in manifest["points"]:
            out.append(Point(
                id=p["id"], lat=float(p["lat"]), lon=float(p["lon"]), iung_code=int(p["iung_code"]),
                voivodeship=p.get("voivodeship", ""), weather_file=base / p["weather_file"], split=split,
            ))
    return out
