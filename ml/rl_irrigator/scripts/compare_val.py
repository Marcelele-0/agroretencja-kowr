"""Compare candidate policies with the FAO rule on VALIDATION 2024 (E6 model selection).

Per budget level: mean paired difference vs FAO (yield in pp, water in mm) with a 95%
bootstrap CI over points (one validation year), the validation reward, and the crop x soil
cells at the tightest budget. The test years are never touched here.

    uv run --group train python ml/rl_irrigator/scripts/compare_val.py E5_s3 E6b_s1 ens:E6b_s1+E6b_s2+E6b_s3
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kowr_rl import REPORTS_DIR  # noqa: E402
from kowr_rl.baselines import baseline_policies  # noqa: E402
from kowr_rl.evaluate import ensure_npz  # noqa: E402
from kowr_rl.policy import EnsemblePolicy, NumpyPolicy, load_policy  # noqa: E402
from kowr_rl.simulator import simulate  # noqa: E402
from kowr_rl.train import validation_batch  # noqa: E402

REWARD = {"c_w": 5e-4, "c_p": 3e-4}  # E5/E6 training objective (selection metric)


def resolve(spec: str):
    if spec.startswith("ens:"):
        return EnsemblePolicy([NumpyPolicy.load(ensure_npz(r)) for r in spec[4:].split("+")])
    if spec.endswith(".npz"):
        return load_policy(spec)
    return NumpyPolicy.load(ensure_npz(spec))


def boot_ci(d: np.ndarray, pts: np.ndarray, rng: np.random.Generator, reps: int = 2000) -> tuple[float, float, float]:
    u, inv = np.unique(pts, return_inverse=True)
    per = np.bincount(inv, weights=d) / np.bincount(inv)
    b = per[rng.integers(0, len(u), size=(reps, len(u)))].mean(axis=1)
    return float(d.mean()), float(np.percentile(b, 2.5)), float(np.percentile(b, 97.5))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("policies", nargs="+")
    ap.add_argument("--out", default=str(REPORTS_DIR / "e6_val_selection.json"))
    ap.add_argument("--synthetic-forecast", type=int, default=None, metavar="SEED",
                    help="E8: replace the real forecasts by the calibrated synthetic ones (all 14 leads) with this noise seed")
    args = ap.parse_args()
    val = validation_batch()
    if args.synthetic_forecast is not None:
        from kowr_rl.forecast import noisy_forecast

        val.forecast = noisy_forecast(val.fut, np.random.default_rng(args.synthetic_forecast))
    pts = np.array([m["point"] for m in val.meta])
    crop = np.array([m["crop"] for m in val.meta])
    soil = np.array([m["soil_cat"] for m in val.meta])
    fao = simulate(val, baseline_policies()["fao_rule"], reward_cfg=REWARD)
    rng = np.random.default_rng(0)
    out = {}
    budgets = sorted(set(val.budget.tolist()))
    print(f"FAO: reward {fao.reward.mean():+.4f} yield {fao.yield_rel.mean():.4f} water {fao.water_mm.mean():.1f}")
    for spec in args.policies:
        r = simulate(val, resolve(spec), reward_cfg=REWARD)
        dy = 100 * (r.yield_rel - fao.yield_rel)
        dw = r.water_mm - fao.water_mm
        row = {"reward": float(r.reward.mean()), "yield": float(r.yield_rel.mean()), "water_mm": float(r.water_mm.mean()), "per_budget": {}}
        line = [f"{spec:28s} reward {row['reward']:+.4f} yield {row['yield']:.4f} water {row['water_mm']:5.1f} |"]
        n_win = 0
        for b in budgets:
            k = val.budget == b
            cy, cw = boot_ci(dy[k], pts[k], rng), boot_ci(dw[k], pts[k], rng)
            win = (cy[0] >= -1.0 and cw[2] < 0) or (cy[1] > 0 and cw[0] <= 0)
            n_win += win
            row["per_budget"][str(b)] = {"d_yield_pp": cy, "d_water_mm": cw, "pass": bool(win)}
            line.append(f" {b:.0f}: {cy[0]:+.2f}pp[{cy[1]:+.2f};{cy[2]:+.2f}] {cw[0]:+.1f}mm{'*' if win else ' '}")
        k50 = val.budget == budgets[0]
        cells = {f"{c}/{s}": float(dy[k50 & (crop == c) & (soil == s)].mean()) for c in np.unique(crop) for s in np.unique(soil)}
        row["cells_tightest_budget_dy_pp"] = cells
        row["worst_cell"] = min(cells.items(), key=lambda kv: kv[1])
        row["n_budget_pass"] = int(n_win)
        out[spec] = row
        print("".join(line), f"| worst cell @{budgets[0]:.0f}mm {row['worst_cell'][0]} {row['worst_cell'][1]:+.2f}pp", flush=True)
    Path(args.out).write_text(json.dumps({"fao": {"reward": float(fao.reward.mean())}, "candidates": out}, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
