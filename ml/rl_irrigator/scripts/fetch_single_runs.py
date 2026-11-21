"""E8: archived full-horizon ECMWF forecasts (Open-Meteo Single Runs API) for the 14-day plan study.

Open-Meteo's Previous Runs API (used for E1-E7) stores only leads 1-7. The Single Runs API returns
the complete horizon of an individual model run; for ECMWF IFS HRES 9 km the archive starts on
2024-03-14 (HRES ran to 240 h until July 2024 and to 360 h = 15 days afterwards).

Design (weekly sample, respects the free-tier limits):
* plan start day t (morning) uses the 12 UTC run of day t-1 (published ~18-19 UTC, i.e. known in
  the evening before), so lead k = 1..14 covers the local days t .. t+13 (full days only);
* calibration set: 20 training points x 2024 (validation year) x weekly t from 21 III to 24 X;
* test set: 20 hold-out points x 2025 and 2026 x weekly t (locations never used in training).

Output: data/single_runs/single_runs.parquet (point, set, run, t, lead, date, precip, et0) and a
MANIFEST.json with request URLs and retrieval times. Daily sums in Europe/Warsaw (like the archive).

Usage:  uv run --group train python ml/rl_irrigator/scripts/fetch_single_runs.py
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kowr_rl import DATA_DIR  # noqa: E402
from kowr_rl.data import TIMEZONE, DataUnavailableError, HttpClient  # noqa: E402

SINGLE_RUNS_URL = "https://single-runs-api.open-meteo.com/v1/forecast"
MODEL = "ecmwf_ifs"  # ECMWF IFS HRES 9 km, archived from 2024-03-14
OUT_DIR = DATA_DIR / "single_runs"
N_LEADS = 14
log = logging.getLogger("single_runs")


def farthest_points(points: list[dict], n: int) -> list[dict]:
    xy = np.array([[p["lon"] * np.cos(np.radians(52.0)) * 111.0, p["lat"] * 111.0] for p in points])
    chosen = [int(np.argmin(((xy - xy.mean(axis=0)) ** 2).sum(axis=1)))]
    dist = ((xy - xy[chosen[0]]) ** 2).sum(axis=1)
    while len(chosen) < n:
        nxt = int(np.argmax(dist))
        chosen.append(nxt)
        dist = np.minimum(dist, ((xy - xy[nxt]) ** 2).sum(axis=1))
    return [points[i] for i in sorted(chosen)]


def plan_days(year: int) -> list[dt.date]:
    d, end = dt.date(year, 3, 21), dt.date(year, 10, 24)
    out = []
    while d <= end:
        out.append(d)
        d += dt.timedelta(days=7)
    return out


def daily_from_hourly(payload: dict, t: dt.date) -> tuple[np.ndarray, np.ndarray]:
    """Daily sums for local days t .. t+13; NaN where the day is not fully covered."""
    h = payload["hourly"]
    times = pd.to_datetime(h["time"])
    df = pd.DataFrame({"p": pd.to_numeric(pd.Series(h["precipitation"]), errors="coerce"),
                       "e": pd.to_numeric(pd.Series(h["et0_fao_evapotranspiration"]), errors="coerce")}, index=times)
    out_p, out_e = np.full(N_LEADS, np.nan), np.full(N_LEADS, np.nan)
    for k in range(N_LEADS):
        day = pd.Timestamp(t + dt.timedelta(days=k))
        sl = df.loc[day : day + pd.Timedelta(hours=23)]
        if len(sl) == 24 and sl["p"].notna().all():
            out_p[k] = float(sl["p"].sum())
        if len(sl) == 24 and sl["e"].notna().all():
            out_e[k] = float(sl["e"].sum())
    return out_p, out_e


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    client = HttpClient(timeout=60.0, retries=5, min_interval_s={"single-runs-api.open-meteo.com": 0.6})
    train_pts = json.loads((DATA_DIR / "MANIFEST.json").read_text(encoding="utf-8"))["points"]
    hold_pts = json.loads((DATA_DIR / "holdout" / "MANIFEST.json").read_text(encoding="utf-8"))["points"]
    jobs = [("calibration_2024", p, t) for p in farthest_points(train_pts, 20) for t in plan_days(2024)]
    jobs += [("test_holdout", p, t) for p in farthest_points(hold_pts, 20) for y in (2025, 2026) for t in plan_days(y)]
    started = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    rows, failed = [], 0
    for i, (set_name, p, t) in enumerate(jobs):
        run = dt.datetime.combine(t - dt.timedelta(days=1), dt.time(12, 0))
        params = {
            "latitude": p["lat"], "longitude": p["lon"], "run": run.strftime("%Y-%m-%dT%H:%M"), "models": MODEL,
            "hourly": "precipitation,et0_fao_evapotranspiration", "timezone": TIMEZONE, "forecast_days": 16,
        }
        try:
            res = client.get(SINGLE_RUNS_URL, params)
        except DataUnavailableError as err:
            failed += 1
            log.warning("%s %s %s: %s", set_name, p["id"], run, err)
            continue
        fp, fe = daily_from_hourly(res.payload, t)
        for k in range(N_LEADS):
            rows.append({"set": set_name, "point": p["id"], "run": run.isoformat(), "t": t.isoformat(), "lead": k + 1,
                         "date": (t + dt.timedelta(days=k)).isoformat(), "precip": fp[k], "et0": fe[k], "retrieved_at": res.retrieved_at})
        if (i + 1) % 100 == 0:
            log.info("%d/%d runs (failed %d)", i + 1, len(jobs), failed)
    df = pd.DataFrame(rows)
    df.to_parquet(OUT_DIR / "single_runs.parquet", index=False)
    finished = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    cov = df.groupby(["set", "lead"])["precip"].apply(lambda s: float(s.notna().mean())).unstack("lead").round(3)
    manifest = {
        "description": "Archived ECMWF IFS HRES 9 km runs (12 UTC of day t-1) for the 14-day plan study (E8)",
        "url": SINGLE_RUNS_URL, "model": MODEL, "variables": ["precipitation", "et0_fao_evapotranspiration"],
        "license": "Open-Meteo CC BY 4.0 (ECMWF data)", "fetch_started_utc": started, "fetch_finished_utc": finished,
        "n_runs_requested": len(jobs), "n_runs_failed": failed, "daily_timezone": TIMEZONE,
        "coverage_by_lead": {s: {str(k): v for k, v in r.items()} for s, r in cov.iterrows()},
        "points": {s: sorted(df[df.set == s].point.unique().tolist()) for s in df.set.unique()},
    }
    (OUT_DIR / "MANIFEST.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    log.info("saved %d rows; coverage by lead:\n%s", len(df), cov.to_string())
    client.close()


if __name__ == "__main__":
    main()
