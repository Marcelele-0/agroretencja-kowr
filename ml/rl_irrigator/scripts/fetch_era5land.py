"""E9: ERA5-Land soil moisture (4 layers, daily) at a subset of the dataset points.

Open-Meteo Historical Weather API, model ``era5_land`` (~9 km): daily means of the volumetric soil
water in 0-7, 7-28, 28-100 and 100-255 cm [m3/m3], 2016-01-01 .. today-6.

Used as (1) the initial state of the soil-water forecast (CDF-matched to the model, see
``kowr_rl.soil3``) and (2) an independent reference for verifying the 14-day forecasts.

A 10-year request with 4 variables weighs ~113 Open-Meteo "calls", so the subset is 25 training +
25 hold-out points (farthest-point sampling) = ~5 700 calls, paced to stay well below the free
per-minute limit. Output: data/era5land/<point>.parquet + MANIFEST.json.

    uv run --group train python ml/rl_irrigator/scripts/fetch_era5land.py
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
from kowr_rl.data import ARCHIVE_URL, TIMEZONE, DataUnavailableError, HttpClient  # noqa: E402

LAYERS = ["soil_moisture_0_to_7cm_mean", "soil_moisture_7_to_28cm_mean", "soil_moisture_28_to_100cm_mean", "soil_moisture_100_to_255cm_mean"]
OUT = DATA_DIR / "era5land"
log = logging.getLogger("era5land")


def farthest(points: list[dict], n: int) -> list[dict]:
    xy = np.array([[p["lon"] * np.cos(np.radians(52.0)) * 111.0, p["lat"] * 111.0] for p in points])
    chosen = [int(np.argmin(((xy - xy.mean(axis=0)) ** 2).sum(axis=1)))]
    dist = ((xy - xy[chosen[0]]) ** 2).sum(axis=1)
    while len(chosen) < n:
        nxt = int(np.argmax(dist))
        chosen.append(nxt)
        dist = np.minimum(dist, ((xy - xy[nxt]) ** 2).sum(axis=1))
    return [points[i] for i in sorted(chosen)]


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    OUT.mkdir(parents=True, exist_ok=True)
    client = HttpClient(timeout=90.0, retries=3, backoff_s=30.0, min_interval_s={"archive-api.open-meteo.com": 15.0})
    sel = []
    for set_name, d in (("train", DATA_DIR), ("holdout", DATA_DIR / "holdout")):
        pts = json.loads((d / "MANIFEST.json").read_text(encoding="utf-8"))["points"]
        sel += [(set_name, p) for p in farthest(pts, 25)]
    end = dt.date.today() - dt.timedelta(days=6)
    rows = []
    started = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    for i, (set_name, p) in enumerate(sel):
        params = {"latitude": p["lat"], "longitude": p["lon"], "start_date": "2016-01-01", "end_date": end.isoformat(),
                  "daily": ",".join(LAYERS), "models": "era5_land", "timezone": TIMEZONE}
        try:
            res = client.get(ARCHIVE_URL, params)
        except DataUnavailableError as err:
            log.warning("%s: %s", p["id"], err)
            if "Daily" in str(err) or "Hourly" in str(err):
                break
            continue
        d = res.payload["daily"]
        df = pd.DataFrame({"date": pd.to_datetime(d["time"]), **{k.replace("soil_moisture_", "sm_").replace("_mean", ""): d[k] for k in LAYERS}})
        df.to_parquet(OUT / f"{p['id']}.parquet", index=False)
        rows.append({"id": p["id"], "set": set_name, "lat": p["lat"], "lon": p["lon"], "grid": [res.payload.get("latitude"), res.payload.get("longitude")],
                     "retrieved_at": res.retrieved_at, "n_days": int(len(df)), "missing": int(df.drop(columns="date").isna().sum().sum())})
        log.info("[%d/%d] %s %s: %d days", i + 1, len(sel), set_name, p["id"], len(df))
    (OUT / "MANIFEST.json").write_text(json.dumps({
        "source": ARCHIVE_URL, "model": "era5_land", "variables": LAYERS, "units": "m3/m3", "daily_timezone": TIMEZONE,
        "date_range": ["2016-01-01", end.isoformat()], "license": "Open-Meteo CC BY 4.0 (Copernicus ERA5-Land)",
        "fetch_started_utc": started, "fetch_finished_utc": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "points": rows,
    }, indent=1), encoding="utf-8")
    client.close()


if __name__ == "__main__":
    main()
