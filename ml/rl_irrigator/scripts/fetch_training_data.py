"""Fetch the pre-fetched training/evaluation dataset for the RL irrigation module.

Steps (resumable: every HTTP response is cached in ``data/cache``):

1. Grid over Poland (lon 14.2-24.1, lat 49.1-54.8, step 0.7 deg).
2. IUNG-PIB WMS soil category at each node; if the node is not arable (code outside 1-4),
   try 8 neighbours at +-0.06 deg (~5 km) and keep the first arable one.
3. Region label from GUGiK ULDK (GetCommuneByXY).
4. Open-Meteo archive: daily P and ET0 2016-01-01 .. (today - 5 days) per point.
5. Open-Meteo Previous Runs (ECMWF IFS 0.25): archived precipitation forecasts with lead
   1..7 days, 15 Mar - 31 Oct of 2024, 2025, 2026 (validation + test years).
6. ``data/MANIFEST.json`` with URLs, retrieval timestamps, date ranges and quality checks.

Usage:  uv run --group train python ml/rl_irrigator/scripts/fetch_training_data.py
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kowr_rl import DATA_DIR  # noqa: E402
from kowr_rl.data import (  # noqa: E402
    ARCHIVE_URL,
    FORECAST_MODEL,
    IUNG_WMS_URL,
    LICENSES,
    PREVIOUS_RUNS_URL,
    ULDK_URL,
    DataUnavailableError,
    HttpClient,
    fetch_archive,
    fetch_iung_code,
    fetch_previous_runs,
)

log = logging.getLogger("fetch")

LON_RANGE = (14.2, 24.1)
LAT_RANGE = (49.1, 54.8)
STEP = 0.7
NEIGHBOUR_OFFSET = 0.06
ARCHIVE_START = dt.date(2016, 1, 1)
FORECAST_YEARS = (2024, 2025, 2026)
FORECAST_SEASON = ((3, 15), (10, 31))

WEATHER_DIR = DATA_DIR / "weather"
FORECAST_DIR = DATA_DIR / "forecasts"
SOILS_DIR = DATA_DIR / "soils"


def grid_nodes() -> list[tuple[float, float]]:
    lons = np.round(np.arange(LON_RANGE[0], LON_RANGE[1] + 1e-9, STEP), 2)
    lats = np.round(np.arange(LAT_RANGE[0], LAT_RANGE[1] + 1e-9, STEP), 2)
    return [(float(la), float(lo)) for la in lats for lo in lons]


def point_id(lat: float, lon: float) -> str:
    return f"pl_{lat:.2f}_{lon:.2f}".replace(".", "p")


def region_of(client: HttpClient, lat: float, lon: float) -> dict[str, str]:
    params = {"request": "GetCommuneByXY", "xy": f"{lon},{lat},4326", "result": "teryt,voivodeship,county,commune"}
    try:
        res = client.get(ULDK_URL, params, kind="text")
        lines = [ln for ln in res.payload.strip().splitlines() if ln.strip()]
        if lines and lines[0] == "0" and len(lines) > 1:
            teryt, voiv, county, commune = (lines[1].split("|") + ["", "", "", ""])[:4]
            return {"teryt_gmina": teryt, "voivodeship": voiv, "county": county, "commune": commune}
    except DataUnavailableError as err:
        log.warning("ULDK region lookup failed for %.3f %.3f: %s", lat, lon, err)
    return {}


def select_points(client: HttpClient) -> tuple[list[dict], dict[str, int]]:
    offsets = [(0.0, 0.0)] + [
        (dy * NEIGHBOUR_OFFSET, dx * NEIGHBOUR_OFFSET)
        for dy in (-1, 0, 1)
        for dx in (-1, 0, 1)
        if (dy, dx) != (0, 0)
    ]
    points: list[dict] = []
    nodes = grid_nodes()
    stats = {"nodes": len(nodes), "outside_or_nodata": 0, "non_arable": 0, "arable": 0}
    for i, (nlat, nlon) in enumerate(nodes):
        tried: list[dict] = []
        chosen = None
        for dy, dx in offsets:
            lat, lon = round(nlat + dy, 4), round(nlon + dx, 4)
            code, src = fetch_iung_code(client, lat, lon)
            tried.append({"lat": lat, "lon": lon, "code": code})
            if code is not None and 1 <= code <= 4:
                chosen = {"lat": lat, "lon": lon, "code": code, "retrieved_at": src.retrieved_at}
                break
            if (dy, dx) == (0, 0) and code in (None, 0, 255):
                # centre outside Poland / no data: do not hammer the WMS with 8 more queries
                break
        if chosen is None:
            centre = tried[0]["code"]
            stats["outside_or_nodata" if centre in (None, 0, 255) else "non_arable"] += 1
            continue
        stats["arable"] += 1
        pid = point_id(chosen["lat"], chosen["lon"])
        points.append(
            {
                "id": pid,
                "grid_node": {"lat": nlat, "lon": nlon},
                "lat": chosen["lat"],
                "lon": chosen["lon"],
                "iung_code": chosen["code"],
                "iung_retrieved_at": chosen["retrieved_at"],
                "n_tries": len(tried),
            }
        )
        log.info("[%3d/%d] node %.2f %.2f -> %s (kat. %d, %d prob)", i + 1, len(nodes), nlat, nlon, pid, chosen["code"], len(tried))
    log.info("IUNG selection: %s", stats)
    return points, stats


def spatial_subsample(points: list[dict], n: int) -> list[dict]:
    """Deterministic farthest-point sampling -> ``n`` points spread evenly over Poland."""
    if len(points) <= n:
        return points
    xy = np.array([[p["lon"] * np.cos(np.radians(52.0)) * 111.0, p["lat"] * 111.0] for p in points])
    centroid = xy.mean(axis=0)
    chosen = [int(np.argmin(((xy - centroid) ** 2).sum(axis=1)))]
    dist = ((xy - xy[chosen[0]]) ** 2).sum(axis=1)
    while len(chosen) < n:
        nxt = int(np.argmax(dist))
        chosen.append(nxt)
        dist = np.minimum(dist, ((xy - xy[nxt]) ** 2).sum(axis=1))
    return [points[i] for i in sorted(chosen)]


def season_window(year: int) -> tuple[dt.date, dt.date]:
    (m0, d0), (m1, d1) = FORECAST_SEASON
    return dt.date(year, m0, d0), dt.date(year, m1, d1)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--end", type=dt.date.fromisoformat, default=dt.date.today() - dt.timedelta(days=5))
    ap.add_argument("--skip-forecasts", action="store_true")
    ap.add_argument("--max-points", type=int, default=50)
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    for d in (WEATHER_DIR, FORECAST_DIR, SOILS_DIR):
        d.mkdir(parents=True, exist_ok=True)

    # Conservative pacing: an Open-Meteo 10-year request weighs ~60 "calls" (600/min, 5000/h limits).
    client = HttpClient(
        timeout=60.0,
        retries=5,
        min_interval_s={
            "archive-api.open-meteo.com": 8.0,
            "previous-runs-api.open-meteo.com": 3.0,
            "sgeo.iung.pl": 0.3,
            "uldk.gugik.gov.pl": 0.3,
        },
    )
    started = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()

    points_file = SOILS_DIR / "points.json"
    points, iung_stats = select_points(client)
    points = spatial_subsample(points, args.max_points)
    iung_stats["selected"] = len(points)
    for p in points:
        p.update(region_of(client, p["lat"], p["lon"]))
    points_file.write_text(json.dumps({"points": points, "selection_stats": iung_stats}, indent=1, ensure_ascii=False), encoding="utf-8")
    log.info("%d arable points saved to %s", len(points), points_file)

    manifest_points = []
    for i, p in enumerate(points):
        series, src = fetch_archive(client, p["lat"], p["lon"], ARCHIVE_START, args.end)
        df = pd.DataFrame({"date": series.dates, "precip": series.precip, "et0": series.et0})
        df.to_parquet(WEATHER_DIR / f"{p['id']}.parquet", index=False)
        years = df["date"].dt.year
        annual = df.groupby(years)[["precip", "et0"]].sum(min_count=300)
        entry = {
            "id": p["id"],
            "lat": p["lat"],
            "lon": p["lon"],
            "iung_code": p["iung_code"],
            "voivodeship": p.get("voivodeship", ""),
            "weather_file": f"weather/{p['id']}.parquet",
            "weather_retrieved_at": src.retrieved_at,
            "weather_url": src.url,
            "weather_grid": src.detail,
            "n_days": int(len(df)),
            "missing_precip": int(df["precip"].isna().sum()),
            "missing_et0": int(df["et0"].isna().sum()),
            "annual_precip_mean": round(float(annual["precip"].mean()), 1),
            "annual_et0_mean": round(float(annual["et0"].mean()), 1),
        }
        if not args.skip_forecasts:
            frames = []
            fc_src = []
            for year in FORECAST_YEARS:
                s, e = season_window(year)
                e = min(e, args.end)
                if e <= s:
                    continue
                dates, f, fsrc = fetch_previous_runs(client, p["lat"], p["lon"], s, e)
                frames.append(pd.DataFrame({"date": dates, **{f"f{k + 1}": f[:, k] for k in range(f.shape[1])}}))
                fc_src.append({"url": fsrc.url, "retrieved_at": fsrc.retrieved_at})
            fdf = pd.concat(frames, ignore_index=True)
            fdf.to_parquet(FORECAST_DIR / f"{p['id']}.parquet", index=False)
            entry.update(
                {
                    "forecast_file": f"forecasts/{p['id']}.parquet",
                    "forecast_requests": fc_src,
                    "forecast_days": int(len(fdf)),
                    "forecast_missing_cells": int(fdf.drop(columns="date").isna().sum().sum()),
                }
            )
        manifest_points.append(entry)
        log.info(
            "[%2d/%d] %s %s: %d dni, P=%.0f mm/rok, ET0=%.0f mm/rok, braki P/ET0=%d/%d",
            i + 1, len(points), p["id"], p.get("voivodeship", ""), entry["n_days"],
            entry["annual_precip_mean"], entry["annual_et0_mean"], entry["missing_precip"], entry["missing_et0"],
        )

    finished = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    manifest = {
        "description": "Pre-fetched dataset for training/evaluating the KOWR RL irrigation agent.",
        "fetch_started_utc": started,
        "fetch_finished_utc": finished,
        "script": "ml/rl_irrigator/scripts/fetch_training_data.py",
        "split": {"train": "2016-2023", "validation": "2024", "test": "2025-2026"},
        "sources": {
            "weather_archive": {
                "url": ARCHIVE_URL,
                "variables": ["precipitation_sum", "et0_fao_evapotranspiration"],
                "model": "best_match (Open-Meteo default reanalysis blend)",
                "date_range": [ARCHIVE_START.isoformat(), args.end.isoformat()],
                "license": LICENSES["open-meteo"],
            },
            "archived_forecasts": {
                "url": PREVIOUS_RUNS_URL,
                "model": FORECAST_MODEL,
                "variables": [f"precipitation_previous_day{k}" for k in range(1, 8)],
                "seasons": [f"{y}-03-15..{y}-10-31" for y in FORECAST_YEARS],
                "license": LICENSES["open-meteo"],
            },
            "soil_category": {"url": IUNG_WMS_URL, "layer": "Susza:katgl_landuse2024", "license": LICENSES["iung"]},
            "regions": {"url": ULDK_URL, "request": "GetCommuneByXY", "license": LICENSES["uldk"]},
        },
        "grid": {"lon": LON_RANGE, "lat": LAT_RANGE, "step_deg": STEP, "neighbour_offset_deg": NEIGHBOUR_OFFSET},
        "iung_selection": iung_stats,
        "n_points": len(manifest_points),
        "points": manifest_points,
    }
    (DATA_DIR / "MANIFEST.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8")
    log.info("MANIFEST.json written: %d points", len(manifest_points))
    client.close()


if __name__ == "__main__":
    main()
