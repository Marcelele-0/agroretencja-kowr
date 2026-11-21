"""Latency of recommend(): cold (network, empty cache) and warm (cache only) on random cases.

Usage: uv run python ml/rl_irrigator/scripts/benchmark_latency.py [--n 10]
Writes ml/rl_irrigator/reports/latency.json.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kowr_rl import REPORTS_DIR  # noqa: E402
from kowr_rl.config import load_crops  # noqa: E402
from kowr_rl.data import HttpClient  # noqa: E402
from kowr_rl.inference import RUNTIME_HTTP, recommend  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    rng = np.random.default_rng(args.seed)
    crops = list(load_crops().values())
    cases = []
    while len(cases) < args.n:
        lat, lon = float(rng.uniform(50.0, 54.0)), float(rng.uniform(15.5, 23.0))
        c = crops[rng.integers(len(crops))]
        year = int(rng.integers(2016, 2027))
        d = c.season_start(year) + dt.timedelta(days=int(rng.integers(20, c.length - 10)))
        if d < dt.date.today():
            cases.append((round(lat, 4), round(lon, 4), c.key, d))
    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        client = HttpClient(Path(tmp) / "cache", **RUNTIME_HTTP)
        for lat, lon, crop, d in cases:
            t0 = time.perf_counter()
            r1 = recommend(lat, lon, crop, d, 150, client=client)
            cold = time.perf_counter() - t0
            t0 = time.perf_counter()
            r2 = recommend(lat, lon, crop, d, 150, client=client)
            warm = time.perf_counter() - t0
            rows.append({"lat": lat, "lon": lon, "crop": crop, "date": d.isoformat(), "status": r1["status"], "forecast_mode": r1.get("forecast_mode"), "cold_s": round(cold, 3), "warm_s": round(warm, 3)})
            print(f"{crop:15s} {d} ({lat:.2f},{lon:.2f}) {r1['status']:12s} cold {cold:5.2f}s  warm {warm:5.3f}s")
            time.sleep(7.0)  # ~1 cold analysis per 7 s = a single user; stays under the 600/min API limit
        client.close()
    cold = [r["cold_s"] for r in rows]
    warm = [r["warm_s"] for r in rows]
    summary = {
        "measured_at": dt.datetime.now().isoformat(timespec="seconds"),
        "n": len(rows),
        "cold_median_s": statistics.median(cold),
        "cold_max_s": max(cold),
        "warm_median_s": statistics.median(warm),
        "warm_max_s": max(warm),
        "cases": rows,
    }
    (REPORTS_DIR / "latency.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "cases"}))


if __name__ == "__main__":
    main()
