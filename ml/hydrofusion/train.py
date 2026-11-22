"""Train HydroFusion-PL: NDMI nowcast for cloudy days (LightGBM quantiles + conformal + AoA).

Splits (no leakage between points or years):
* fit / early-stop / calibration: 50 RL training points split by POINT (65 / 15 / 20 %),
  without the drought years 2018 and 2023,
* spatial hold-out:  the 50 RL hold-out points (>= 25 km away), all years,
* temporal hold-out: training points, years 2018 and 2023 (extreme droughts).

Baselines the model must beat (inclusion rule):
* ``historia``     - NDMI of the same season in previous years (what the service shows without ML),
                     DOY climatology of the training points when there is no history,
* ``klimatologia`` - DOY climatology of the training points only,
* ``ostatni_odczyt`` - persistence: NDMI of the last clear scene 16-60 days back (``historia``
                     when there is none).

Inclusion rule: on the spatial hold-out the model RMSE must be lower than BOTH the ``historia``
and the ``ostatni_odczyt`` baselines AND the 90 % interval coverage (PICP) must be within
0.85-0.95. Only then the
artifacts are copied to artifacts/models/ (loaded by backend/inference/hydrofusion.py).

    uv run python ml/hydrofusion/train.py
"""

from __future__ import annotations

import json
import logging
import math
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (
    ARTIFACT_MODELS,
    ARTIFACT_VALIDATION,
    CALIBRATION_FRACTION,
    COVERAGE,
    DATASET_FILE,
    MODEL_FEATURES,
    MODELS_DIR,
    QUANTILES,
    REPORTS_DIR,
    TEMPORAL_HOLDOUT_YEARS,
)

log = logging.getLogger("train")
VERSION = "1.0"
SEED = 2026
EARLY_STOP_FRACTION = 0.15
AOA_MAX_TRAIN_ROWS = 6000
PARAMS = {
    "learning_rate": 0.03,
    "num_leaves": 31,
    "min_data_in_leaf": 40,
    "feature_fraction": 0.85,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "lambda_l2": 1.0,
    "verbose": -1,
    "seed": SEED,
    "num_threads": 0,
}
ARTIFACT_FILES = {"q05": "hydrofusion_q05.txt", "q50": "hydrofusion_q50.txt", "q95": "hydrofusion_q95.txt"}
AOA_FILE = "hydrofusion_aoa_train.npy"
META_FILE = "hydrofusion_meta.json"


# --------------------------------------------------------------------------------------
def split_points(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(SEED)
    train_pts = np.array(sorted(df.loc[df["split"] == "train", "point_id"].unique()))
    rng.shuffle(train_pts)
    n = len(train_pts)
    n_cal = max(1, round(CALIBRATION_FRACTION * n))
    n_es = max(1, round(EARLY_STOP_FRACTION * n))
    cal_pts, es_pts, fit_pts = set(train_pts[:n_cal]), set(train_pts[n_cal : n_cal + n_es]), set(train_pts[n_cal + n_es :])
    normal_years = ~df["year"].isin(TEMPORAL_HOLDOUT_YEARS)
    return {
        "fit": df[df["point_id"].isin(fit_pts) & normal_years],
        "early_stop": df[df["point_id"].isin(es_pts) & normal_years],
        "calibration": df[df["point_id"].isin(cal_pts) & normal_years],
        "spatial_holdout": df[df["split"] == "holdout"],
        "temporal_holdout": df[(df["split"] == "train") & df["year"].isin(TEMPORAL_HOLDOUT_YEARS)],
    }


def train_quantile(alpha: float, fit: pd.DataFrame, es: pd.DataFrame) -> lgb.Booster:
    params = {**PARAMS, "objective": "quantile", "alpha": alpha, "metric": "quantile"}
    dfit = lgb.Dataset(fit[MODEL_FEATURES], fit["ndmi"], free_raw_data=False)
    des = lgb.Dataset(es[MODEL_FEATURES], es["ndmi"], reference=dfit)
    return lgb.train(params, dfit, num_boost_round=3000, valid_sets=[des],
                     callbacks=[lgb.early_stopping(150, verbose=False)])


def doy_climatology(fit: pd.DataFrame) -> pd.Series:
    """Mean NDMI per day of year (+-7 days smoothing) over the fit points."""
    s = fit.groupby(fit["doy"].astype(int))["ndmi"].agg(["sum", "count"]).reindex(range(1, 367), fill_value=0)
    k = np.ones(15)
    total = np.convolve(np.r_[s["sum"].values[-7:], s["sum"].values, s["sum"].values[:7]], k, "valid")
    count = np.convolve(np.r_[s["count"].values[-7:], s["count"].values, s["count"].values[:7]], k, "valid")
    clim = np.where(count > 0, total / np.maximum(count, 1), np.nan)
    return pd.Series(pd.Series(clim, index=range(1, 367)).ffill().bfill().values, index=range(1, 367))


def metrics(y: np.ndarray, pred: np.ndarray, lo: np.ndarray | None = None, hi: np.ndarray | None = None) -> dict[str, float]:
    err = pred - y
    ss_res = float((err**2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum()) or 1.0
    out = {
        "n": len(y),
        "rmse": round(math.sqrt(float((err**2).mean())), 4),
        "mae": round(float(np.abs(err).mean()), 4),
        "r2": round(1.0 - ss_res / ss_tot, 3),
        "bias": round(float(err.mean()), 4),
    }
    if lo is not None and hi is not None:
        out["picp90"] = round(float(((y >= lo) & (y <= hi)).mean()), 3)
        out["mean_interval_width"] = round(float((hi - lo).mean()), 4)
    return out


class Model:
    def __init__(self, boosters: dict[str, lgb.Booster], delta: float) -> None:
        self.b = boosters
        self.delta = delta

    def predict(self, X: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        q05, q50, q95 = (self.b[k].predict(X[MODEL_FEATURES]) for k in ("q05", "q50", "q95"))
        lo = np.minimum(q05, q50) - self.delta
        hi = np.maximum(q95, q50) + self.delta
        return q50, lo, hi


def conformal_delta(boosters: dict[str, lgb.Booster], cal: pd.DataFrame) -> float:
    """Conformalized quantile regression (Romano et al. 2019): widen [q05, q95] to 90 % coverage."""
    q05 = boosters["q05"].predict(cal[MODEL_FEATURES])
    q95 = boosters["q95"].predict(cal[MODEL_FEATURES])
    scores = np.maximum(q05 - cal["ndmi"].values, cal["ndmi"].values - q95)
    n = len(scores)
    level = min(1.0, math.ceil((n + 1) * COVERAGE) / n)
    return float(np.quantile(scores, level, method="higher"))


# --------------------------------------------------------------------------------------
class AreaOfApplicability:
    """Dissimilarity index DI (Meyer & Pebesma 2021) on importance-weighted standardised features."""

    def __init__(self, fit: pd.DataFrame, importance: np.ndarray) -> None:
        X = fit[MODEL_FEATURES].to_numpy(float)
        self.mean = np.nanmean(X, axis=0)
        self.std = np.nanstd(X, axis=0)
        self.std[self.std == 0] = 1.0
        self.weights = importance / importance.sum()
        rng = np.random.default_rng(SEED)
        idx = rng.choice(len(X), size=min(AOA_MAX_TRAIN_ROWS, len(X)), replace=False)
        self.train = self.transform(X[idx])
        a = rng.integers(0, len(self.train), 4000)
        b = rng.integers(0, len(self.train), 4000)
        self.mean_dist = float(np.sqrt(((self.train[a] - self.train[b]) ** 2).sum(axis=1)).mean()) or 1.0
        self.threshold = math.inf

    def transform(self, X: np.ndarray) -> np.ndarray:
        return np.nan_to_num((X - self.mean) / self.std) * self.weights

    def di(self, df: pd.DataFrame) -> np.ndarray:
        Z = self.transform(df[MODEL_FEATURES].to_numpy(float))
        out = np.empty(len(Z))
        for s in range(0, len(Z), 512):
            chunk = Z[s : s + 512]
            d2 = (chunk**2).sum(1)[:, None] + (self.train**2).sum(1)[None, :] - 2 * chunk @ self.train.T
            out[s : s + 512] = np.sqrt(np.maximum(d2.min(axis=1), 0.0)) / self.mean_dist
        return out


# --------------------------------------------------------------------------------------
def evaluate(model: Model, aoa: AreaOfApplicability, clim: pd.Series, part: pd.DataFrame) -> dict:
    y = part["ndmi"].to_numpy()
    pred, lo, hi = model.predict(part)
    clim_pred = clim.reindex(part["doy"].astype(int)).to_numpy()
    hist_pred = np.where(part["ndmi_history_mean"].notna(), part["ndmi_history_mean"], clim_pred)
    last_pred = np.where(part["ndmi_prev"].notna(), part["ndmi_prev"], hist_pred)
    di = aoa.di(part)
    inside = di <= aoa.threshold
    res = {
        "model": metrics(y, pred, lo, hi),
        "baseline_historia": metrics(y, hist_pred),
        "baseline_klimatologia": metrics(y, clim_pred),
        "baseline_ostatni_odczyt": metrics(y, last_pred),
        "share_inside_aoa": round(float(inside.mean()), 3),
        "n_points": int(part["point_id"].nunique()),
    }
    if inside.any() and (~inside).any():
        res["model_inside_aoa"] = metrics(y[inside], pred[inside], lo[inside], hi[inside])
        res["model_outside_aoa"] = metrics(y[~inside], pred[~inside], lo[~inside], hi[~inside])
    with_hist = part["ndmi_history_mean"].notna().to_numpy()
    if with_hist.any():
        res["with_history_only"] = {
            "model": metrics(y[with_hist], pred[with_hist]),
            "baseline_historia": metrics(y[with_hist], hist_pred[with_hist]),
        }
    return res


def scatter_figure(model: Model, parts: dict[str, pd.DataFrame], path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 2, figsize=(9, 4.2))
    for ax, name in zip(axes, ("spatial_holdout", "temporal_holdout"), strict=True):
        part = parts[name]
        pred, _, _ = model.predict(part)
        ax.scatter(part["ndmi"], pred, s=3, alpha=0.25, color="#2f7d55")
        lim = (-0.4, 0.7)
        ax.plot(lim, lim, color="#999", lw=0.8)
        ax.set_xlim(lim)
        ax.set_ylim(lim)
        ax.set_xlabel("NDMI obserwowane (Sentinel-2)")
        ax.set_ylabel("NDMI z modelu (mediana)")
        ax.set_title({"spatial_holdout": "Nowe lokalizacje (50 punktów)", "temporal_holdout": "Lata suszy 2018 i 2023"}[name], fontsize=9)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=130)
    plt.close(fig)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    df = pd.read_parquet(DATASET_FILE)
    invalid = ~df["ndmi"].between(-1.0, 1.0)  # reflectance artefacts (offset-corrected DN near 0)
    if invalid.any():
        log.info("dropping %d observations with NDMI outside [-1, 1]", int(invalid.sum()))
        df = df[~invalid]
    parts = split_points(df)
    log.info("rows: %s", {k: len(v) for k, v in parts.items()})

    boosters = {name: train_quantile(alpha, parts["fit"], parts["early_stop"]) for name, alpha in QUANTILES.items()}
    log.info("trees: %s", {k: b.best_iteration for k, b in boosters.items()})
    delta = conformal_delta(boosters, parts["calibration"])
    model = Model(boosters, delta)

    gain = boosters["q50"].feature_importance("gain").astype(float)
    importance = gain + 1e-9
    aoa = AreaOfApplicability(parts["fit"], importance)
    aoa.threshold = float(np.quantile(aoa.di(parts["calibration"]), 0.95))
    clim = doy_climatology(parts["fit"])

    results = {name: evaluate(model, aoa, clim, parts[name]) for name in ("calibration", "spatial_holdout", "temporal_holdout")}
    sp = results["spatial_holdout"]
    rule = {
        "rmse_model": sp["model"]["rmse"],
        "rmse_baseline_historia": sp["baseline_historia"]["rmse"],
        "rmse_baseline_ostatni_odczyt": sp["baseline_ostatni_odczyt"]["rmse"],
        "picp90": sp["model"].get("picp90"),
        "beats_baseline": sp["model"]["rmse"] < min(sp["baseline_historia"]["rmse"], sp["baseline_ostatni_odczyt"]["rmse"]),
        "coverage_ok": 0.85 <= (sp["model"].get("picp90") or 0) <= 0.95,
    }
    rule["deployed"] = bool(rule["beats_baseline"] and rule["coverage_ok"])

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    for name, b in boosters.items():
        b.save_model(str(MODELS_DIR / ARTIFACT_FILES[name]), num_iteration=b.best_iteration)
    np.save(MODELS_DIR / AOA_FILE, aoa.train.astype(np.float32))
    meta = {
        "version": VERSION,
        "trained_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "target": "NDMI (Sentinel-2 L2A, (B8A-B11)/(B8A+B11), 20 m)",
        "features": MODEL_FEATURES,
        "models": ARTIFACT_FILES,
        "conformal_delta": round(delta, 5),
        "coverage": COVERAGE,
        "aoa": {
            "train_points": AOA_FILE,
            "feature_weights": aoa.weights.round(6).tolist(),
            "mean": np.nan_to_num(aoa.mean).round(6).tolist(),
            "std": aoa.std.round(6).tolist(),
            "mean_train_distance": round(aoa.mean_dist, 6),
            "threshold": round(aoa.threshold, 4),
        },
        "feature_importance_gain": dict(zip(MODEL_FEATURES, (gain / gain.sum()).round(4).tolist(), strict=True)),
        "data": {
            "rows": {k: len(v) for k, v in parts.items()},
            "points": {"train": int(df.loc[df["split"] == "train", "point_id"].nunique()), "holdout": int(df.loc[df["split"] == "holdout", "point_id"].nunique())},
            "years": sorted(int(y) for y in df["year"].unique()),
            "temporal_holdout_years": list(TEMPORAL_HOLDOUT_YEARS),
        },
        "inclusion_rule": rule,
    }
    (MODELS_DIR / META_FILE).write_text(json.dumps(meta, indent=1, ensure_ascii=False), encoding="utf-8")

    evaluation = {"model": "HydroFusion-PL", **{k: meta[k] for k in ("version", "trained_at", "features", "conformal_delta", "inclusion_rule", "data", "feature_importance_gain")}, "results": results}
    ARTIFACT_VALIDATION.mkdir(parents=True, exist_ok=True)
    (ARTIFACT_VALIDATION / "hydrofusion_eval.json").write_text(json.dumps(evaluation, indent=1, ensure_ascii=False), encoding="utf-8")
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    scatter_figure(model, parts, REPORTS_DIR / "figures" / "holdout_scatter.png")

    if rule["deployed"]:
        ARTIFACT_MODELS.mkdir(parents=True, exist_ok=True)
        for f in [*ARTIFACT_FILES.values(), AOA_FILE, META_FILE]:
            shutil.copy2(MODELS_DIR / f, ARTIFACT_MODELS / f)
        log.info("DEPLOYED to %s", ARTIFACT_MODELS)
    else:
        for f in [*ARTIFACT_FILES.values(), AOA_FILE, META_FILE]:
            (ARTIFACT_MODELS / f).unlink(missing_ok=True)
        log.info("NOT deployed (inclusion rule failed) - the service keeps the physical path")
    log.info("inclusion rule: %s", rule)
    for name, r in results.items():
        log.info("%-17s model %s | historia %s | ostatni odczyt %s | klimatologia %s | AoA %.0f%%", name, r["model"], r["baseline_historia"]["rmse"], r["baseline_ostatni_odczyt"]["rmse"], r["baseline_klimatologia"]["rmse"], 100 * r["share_inside_aoa"])


if __name__ == "__main__":
    main()
