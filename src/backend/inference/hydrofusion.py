"""CPU runtime of HydroFusion-PL (LightGBM multi-quantile + conformal interval + TreeSHAP + AoA).

Artifacts expected in ``artifacts/models/`` (written by ``ml/hydrofusion`` training)::

    hydrofusion_meta.json      {"version": "...", "features": [...registered feature names...],
                                "models": {"q05": "hydrofusion_q05.txt", "q50": "...", "q95": "..."},
                                "conformal_delta": 0.012,
                                "aoa": {"train_points": "hydrofusion_aoa_train.npy",
                                        "feature_weights": [...], "mean": [...], "std": [...],
                                        "mean_train_distance": 1.7, "threshold": 0.45}}
    hydrofusion_q05.txt / _q50.txt / _q95.txt   (LightGBM native text models)
    hydrofusion_aoa_train.npy                    (standardised training feature matrix)

Feature names refer to ``hydro_core.features.FEATURES`` - the backend builds the input vector
with ``FeatureContext.vector(names)``, the same functions used for the score (no skew).
When the artifacts are missing, ``load_hydrofusion()`` returns None and the analysis uses the
physical path (cause attribution by counterfactuals, no cloud-gap reconstruction).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

from backend.config import PROJECT_ROOT
from hydro_core.features import FeatureContext

logger = logging.getLogger(__name__)

MODELS_DIR = PROJECT_ROOT / "artifacts" / "models"
META_FILE = "hydrofusion_meta.json"
FALLBACK_LABEL = "brak wag HydroFusion-PL – ścieżka fizyczna (FAO-56)"


@dataclass(frozen=True)
class NdmiPrediction:
    median: float
    interval: tuple[float, float]
    shap: np.ndarray  # per-feature contributions of the median model (without bias)
    dissimilarity_index: float | None
    inside_aoa: bool


class HydroFusionRuntime:
    def __init__(self, meta: dict, models: dict, aoa_train: np.ndarray | None) -> None:
        self.meta = meta
        self.version = str(meta.get("version", "?"))
        self.features: list[str] = list(meta["features"])
        self.models = models
        self.delta = float(meta.get("conformal_delta", 0.0))
        self.aoa_train = aoa_train
        aoa = meta.get("aoa") or {}
        self.aoa_mean = np.asarray(aoa.get("mean", np.zeros(len(self.features))), dtype=float)
        self.aoa_std = np.asarray(aoa.get("std", np.ones(len(self.features))), dtype=float)
        self.aoa_w = np.asarray(aoa.get("feature_weights", np.ones(len(self.features))), dtype=float)
        self.aoa_mean_dist = float(aoa.get("mean_train_distance", 1.0)) or 1.0
        self.aoa_threshold = float(aoa.get("threshold", np.inf))

    def _dissimilarity(self, x: np.ndarray) -> float | None:
        """Meyer & Pebesma (2021) DI: weighted distance to the nearest training point / mean distance."""
        if self.aoa_train is None:
            return None
        z = np.nan_to_num((x - self.aoa_mean) / np.where(self.aoa_std > 0, self.aoa_std, 1.0)) * self.aoa_w
        d = np.sqrt(((self.aoa_train - z) ** 2).sum(axis=1)).min()
        return float(d / self.aoa_mean_dist)

    def predict(self, fx: FeatureContext) -> NdmiPrediction:
        x = fx.vector(self.features)
        row = x[None, :]
        q05 = float(self.models["q05"].predict(row)[0])
        q50 = float(self.models["q50"].predict(row)[0])
        q95 = float(self.models["q95"].predict(row)[0])
        contrib = np.asarray(self.models["q50"].predict(row, pred_contrib=True))[0][:-1]
        di = self._dissimilarity(x)
        return NdmiPrediction(
            median=q50,
            interval=(min(q05, q50) - self.delta, max(q95, q50) + self.delta),
            shap=contrib,
            dissimilarity_index=None if di is None else round(di, 3),
            inside_aoa=di is None or di <= self.aoa_threshold,
        )


@lru_cache(maxsize=1)
def load_hydrofusion(models_dir: Path = MODELS_DIR) -> HydroFusionRuntime | None:
    meta_path = models_dir / META_FILE
    if not meta_path.exists():
        return None
    try:
        import lightgbm as lgb

        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        models = {q: lgb.Booster(model_file=str(models_dir / f)) for q, f in meta["models"].items()}
        aoa_file = (meta.get("aoa") or {}).get("train_points")
        aoa_train = np.load(models_dir / aoa_file) if aoa_file and (models_dir / aoa_file).exists() else None
        return HydroFusionRuntime(meta, models, aoa_train)
    except Exception:
        logger.exception("HydroFusion-PL artifacts could not be loaded")
        return None
