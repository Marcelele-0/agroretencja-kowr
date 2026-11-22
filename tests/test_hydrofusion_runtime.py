"""The backend loads artifacts in the exact format written by ml/hydrofusion/train.py."""

from __future__ import annotations

import json
from pathlib import Path

import lightgbm as lgb
import numpy as np

from backend.inference.hydrofusion import load_hydrofusion
from hydro_core.features import FeatureContext

FEATURES = ["p_30", "kbw_60", "taw_mm", "doy", "ndmi_history_mean"]


def _write_artifacts(models_dir: Path, threshold: float) -> None:
    rng = np.random.default_rng(0)
    X = rng.normal(size=(400, len(FEATURES)))
    y = 0.2 + 0.05 * X[:, 0] + rng.normal(scale=0.02, size=400)
    files = {}
    for name, alpha in (("q05", 0.05), ("q50", 0.5), ("q95", 0.95)):
        b = lgb.train({"objective": "quantile", "alpha": alpha, "verbose": -1}, lgb.Dataset(X, y), num_boost_round=20)
        files[name] = f"hf_{name}.txt"
        b.save_model(str(models_dir / files[name]))
    np.save(models_dir / "aoa.npy", np.zeros((10, len(FEATURES)), dtype=np.float32))
    meta = {
        "version": "test", "features": FEATURES, "models": files, "conformal_delta": 0.01,
        "aoa": {"train_points": "aoa.npy", "feature_weights": [0.2] * 5, "mean": [0.0] * 5, "std": [1.0] * 5,
                "mean_train_distance": 1.0, "threshold": threshold},
    }
    (models_dir / "hydrofusion_meta.json").write_text(json.dumps(meta), encoding="utf-8")


def test_runtime_predicts_interval_shap_and_aoa(tmp_path: Path, fx: FeatureContext) -> None:
    _write_artifacts(tmp_path, threshold=1e9)
    load_hydrofusion.cache_clear()
    hf = load_hydrofusion(tmp_path)
    assert hf is not None and hf.features == FEATURES
    pred = hf.predict(fx)
    assert pred.interval[0] <= pred.median <= pred.interval[1]
    assert pred.shap.shape == (len(FEATURES),)
    assert pred.inside_aoa and pred.dissimilarity_index is not None


def test_runtime_flags_outside_aoa_and_missing_artifacts(tmp_path: Path, fx: FeatureContext) -> None:
    _write_artifacts(tmp_path, threshold=0.0)
    load_hydrofusion.cache_clear()
    assert load_hydrofusion(tmp_path).predict(fx).inside_aoa is False  # type: ignore[union-attr]
    load_hydrofusion.cache_clear()
    assert load_hydrofusion(tmp_path / "brak") is None
    load_hydrofusion.cache_clear()
