"""Calibrate the synthetic forecast noise against real ECMWF forecasts of the VALIDATION year.

Only 2024 (validation) is used - test years 2025-2026 stay untouched.
Compares archived forecasts (Open-Meteo Previous Runs, ECMWF IFS 0.25, lead 1..7) with the
archive precipitation at the same points, per lead, then reports the same statistics for the
synthetic noise model from ``config/rl.yaml`` so both can be compared side by side.

Usage: uv run --group train python ml/rl_irrigator/scripts/calibrate_forecast.py [--year 2024]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kowr_rl import REPORTS_DIR  # noqa: E402
from kowr_rl.config import load_rl_config  # noqa: E402
from kowr_rl.dataset import load_points  # noqa: E402
from kowr_rl.forecast import noisy_forecast  # noqa: E402


def pairs(year: int) -> tuple[np.ndarray, np.ndarray]:
    """Arrays (N, 7): true P on target day and forecast for it at lead 1..7."""
    obs, fc = [], []
    for p in load_points():
        if p.fc is None:
            continue
        m = (p.fc_dates >= np.datetime64(f"{year}-04-01")) & (p.fc_dates <= np.datetime64(f"{year}-09-30"))
        d = p.fc_dates[m]
        f = p.fc[m]
        idx = np.searchsorted(p.weather.dates, d)
        ok = (idx < len(p.weather)) & np.all(np.isfinite(f), axis=1)
        ok &= p.weather.dates[np.clip(idx, 0, len(p.weather) - 1)] == d
        obs.append(np.repeat(p.weather.precip[idx[ok]][:, None], 7, axis=1))
        fc.append(f[ok])
    return np.concatenate(obs), np.concatenate(fc)


def stats(obs: np.ndarray, fc: np.ndarray, thr: float) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {k: [] for k in ("miss_prob", "sigma_log", "median_ratio", "false_alarm_prob", "false_alarm_mean", "corr")}
    for j in range(obs.shape[1]):
        o, f = obs[:, j], fc[:, j]
        rainy = o > thr
        miss = rainy & (f < 0.2 * o)
        hit = rainy & ~miss
        lr = np.log(f[hit] / o[hit])
        dry = o <= thr
        fa = dry & (f > thr)
        out["miss_prob"].append(float(miss.sum() / max(rainy.sum(), 1)))
        out["sigma_log"].append(float(lr.std()))
        out["median_ratio"].append(float(np.exp(np.median(lr))))
        out["false_alarm_prob"].append(float(fa.sum() / max(dry.sum(), 1)))
        out["false_alarm_mean"].append(float(f[fa].mean()) if fa.any() else 0.0)
        out["corr"].append(float(np.corrcoef(o, f)[0, 1]))
    return out


def window_sums(obs: np.ndarray, fc: np.ndarray, n: int) -> tuple[np.ndarray, np.ndarray]:
    """Correlation of n-day sums is approximated with lead-wise pairs (same target day set)."""
    return obs[:, :n].sum(axis=1), fc[:, :n].sum(axis=1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=2024)
    args = ap.parse_args()
    cfg = load_rl_config()["forecast_noise"]
    thr = cfg["rain_threshold_mm"]
    obs, fc = pairs(args.year)
    real = stats(obs, fc, thr)
    rng = np.random.default_rng(0)
    syn_fc = noisy_forecast(obs, rng, cfg)
    syn = stats(obs, syn_fc, thr)
    result = {"year": args.year, "n_pairs": int(len(obs)), "threshold_mm": thr, "real_ecmwf": real, "synthetic": syn, "noise_cfg": cfg}
    out = REPORTS_DIR / "forecast_calibration.json"
    out.write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(f"pairs: {len(obs)} (year {args.year})")
    for k in real:
        print(f"{k:17s} real: " + " ".join(f"{v:5.2f}" for v in real[k]))
        print(f"{'':17s} syn : " + " ".join(f"{v:5.2f}" for v in syn[k]))
    print("saved", out)


if __name__ == "__main__":
    main()
