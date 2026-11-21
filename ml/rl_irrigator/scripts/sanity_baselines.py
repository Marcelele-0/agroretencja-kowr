"""M2 sanity check on TRAINING years: baseline yields per year and simulator throughput.

Usage: uv run --group train python ml/rl_irrigator/scripts/sanity_baselines.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kowr_rl.baselines import baseline_policies, run_oracle  # noqa: E402
from kowr_rl.config import load_rl_config  # noqa: E402
from kowr_rl.dataset import factorial_specs, load_points  # noqa: E402
from kowr_rl.forecast import noisy_forecast  # noqa: E402
from kowr_rl.simulator import build_batch, simulate  # noqa: E402


def main() -> None:
    cfg = load_rl_config()
    years = cfg["split"]["train_years"]
    crops = cfg["episode"]["crops"]
    pts = load_points()
    t0 = time.perf_counter()
    specs = factorial_specs(pts, years, crops, [2], [150.0])
    batch = build_batch(specs)
    batch.forecast = noisy_forecast(batch.fut, np.random.default_rng(0))
    print(f"{batch.size} seasons built in {time.perf_counter() - t0:.1f}s")
    years_arr = np.array([m["year"] for m in batch.meta])
    crops_arr = np.array([m["crop"] for m in batch.meta])
    results = {}
    for name, pol in baseline_policies().items():
        t0 = time.perf_counter()
        res = simulate(batch, pol)
        dt_s = time.perf_counter() - t0
        steps = int(batch.valid.sum())
        results[name] = res
        print(f"{name:13s} {steps / dt_s:,.0f} season-days/s  yield={res.yield_rel.mean():.3f} water={res.water_mm.mean():.0f} mm dp={res.dp_mm.mean():.0f} mm stress={res.stress_days.mean():.0f} d")
    sub = np.flatnonzero(crops_arr == "kukurydza")[:64]
    t0 = time.perf_counter()
    orc = run_oracle(batch.subset(sub))
    print(f"oracle (maize, {len(sub)} seasons) {time.perf_counter() - t0:.1f}s  yield={orc.yield_rel.mean():.3f} water={orc.water_mm.mean():.0f}  fao on same={results['fao_rule'].yield_rel[sub].mean():.3f}/{results['fao_rule'].water_mm[sub].mean():.0f}")
    print("\nNo-irrigation relative yield by year (all crops, soil cat. II):")
    for y in years:
        m = years_arr == y
        print(f"  {y}: none={results['none'].yield_rel[m].mean():.3f}  fao_rule={results['fao_rule'].yield_rel[m].mean():.3f}  P_season={results['none'].precip_mm[m].mean():.0f} mm  ETc={results['none'].etc_mm[m].mean():.0f} mm")
    print("\nBy crop (none / fao_rule yield, fao water):")
    for c in crops:
        m = crops_arr == c
        print(f"  {c:15s} {results['none'].yield_rel[m].mean():.3f} / {results['fao_rule'].yield_rel[m].mean():.3f}, {results['fao_rule'].water_mm[m].mean():.0f} mm")


if __name__ == "__main__":
    main()
