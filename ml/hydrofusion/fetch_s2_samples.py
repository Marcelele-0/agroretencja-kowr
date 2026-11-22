"""Download Sentinel-2 L2A NDMI observations at the training points (Earth Search, no account).

For every point and growing season 2018-2026 (March-October) the script lists all scenes over
the point (STAC), reads the 3x3 window of the 20 m scene classification (SCL) around the point
and - when at least 6 of 9 pixels are vegetation / bare soil - the B8A and B11 bands. NDMI is
computed exactly like the runtime client (backend.clients.sentinel2): reflectance = DN * scale
+ offset from each scene's metadata, NDMI = (B8A - B11) / (B8A + B11), mean of clear pixels.

Resumable: per-point results are cached in data/cache/hydrofusion/s2/<point>.json.
Output: ml/hydrofusion/data/s2_samples.parquet (point, date, scene, clear pixels, NDMI).

    uv run python ml/hydrofusion/fetch_s2_samples.py [--workers 48] [--points 5]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.windows import Window

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import CACHE_DIR, MIN_CLEAR_PIXELS, S2_FILE, S2_YEARS, SEASON_MONTHS, Point, load_points

from backend.clients.sentinel2 import _GDAL_ENV, SCL_CLEAR, _band_scaling

log = logging.getLogger("fetch_s2")
STAC_URL = "https://earth-search.aws.element84.com/v1/search"
COLLECTION = "sentinel-2-c1-l2a"
MAX_SCENE_CLOUD = 80.0
POINT_CACHE = CACHE_DIR / "s2"
_TRANSFORMERS: dict[str, Transformer] = {}
_TLOCK = threading.Lock()


def _transformer(crs: str) -> Transformer:
    with _TLOCK:
        if crs not in _TRANSFORMERS:
            _TRANSFORMERS[crs] = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
        return _TRANSFORMERS[crs]


def search_scenes(client: httpx.Client, p: Point, year: int) -> list[dict[str, Any]]:
    """All scenes over the point in the season, best (least cloudy) scene per day."""
    body = {
        "collections": [COLLECTION],
        "intersects": {"type": "Point", "coordinates": [p.lon, p.lat]},
        "datetime": f"{year}-{SEASON_MONTHS[0]:02d}-01T00:00:00Z/{year}-{SEASON_MONTHS[1]:02d}-31T23:59:59Z",
        "limit": 300,
        "query": {"eo:cloud_cover": {"lt": MAX_SCENE_CLOUD}},
        "fields": {"include": ["id", "properties.datetime", "properties.eo:cloud_cover", "assets.scl", "assets.nir08", "assets.swir16"]},
    }
    for attempt in range(4):
        try:
            r = client.post(STAC_URL, json=body, timeout=30)
            r.raise_for_status()
            break
        except httpx.HTTPError:
            if attempt == 3:
                raise
            time.sleep(2 * (attempt + 1))
    best: dict[str, dict[str, Any]] = {}
    for item in r.json().get("features", []):
        day = item["properties"]["datetime"][:10]
        cc = item["properties"].get("eo:cloud_cover", 100.0)
        if day not in best or cc < best[day]["properties"].get("eo:cloud_cover", 100.0):
            best[day] = item
    return list(best.values())


def _window(src: Any, p: Point) -> Window:
    x, y = _transformer(str(src.crs)).transform(p.lon, p.lat)
    row, col = src.index(x, y)
    return Window(col - 1, row - 1, 3, 3)


def read_scene(item: dict[str, Any], p: Point) -> dict[str, Any]:
    """SCL first; spectral bands only when the 3x3 window is clear enough."""
    out: dict[str, Any] = {"scene": item["id"], "date": item["properties"]["datetime"][:10],
                           "scene_cloud": item["properties"].get("eo:cloud_cover"), "clear_px": 0, "ndmi": None}
    with rasterio.Env(**_GDAL_ENV):
        with rasterio.open(item["assets"]["scl"]["href"]) as src:
            win = _window(src, p)
            scl = src.read(1, window=win, boundless=True, fill_value=0)
        clear = np.isin(scl, SCL_CLEAR)
        out["clear_px"] = int(clear.sum())
        if out["clear_px"] < MIN_CLEAR_PIXELS:
            return out
        bands = {}
        for name in ("nir08", "swir16"):
            asset = item["assets"][name]
            scale, offset = _band_scaling(asset)
            with rasterio.open(asset["href"]) as src:
                win = _window(src, p)
                raw = src.read(1, window=win, boundless=True, fill_value=0).astype("float64")
            raw[raw <= 0] = np.nan
            bands[name] = raw * scale + offset
    with np.errstate(divide="ignore", invalid="ignore"):
        ndmi = (bands["nir08"] - bands["swir16"]) / (bands["nir08"] + bands["swir16"])
    vals = ndmi[clear & np.isfinite(ndmi)]
    if vals.size >= MIN_CLEAR_PIXELS:
        out["ndmi"] = round(float(vals.mean()), 4)
        out["ndmi_std"] = round(float(vals.std()), 4)
    return out


def process_point(p: Point, pool: ThreadPoolExecutor, client: httpx.Client) -> list[dict[str, Any]]:
    cache_file = POINT_CACHE / f"{p.id}.json"
    cached = json.loads(cache_file.read_text(encoding="utf-8")) if cache_file.exists() else {"years": {}}
    for year in S2_YEARS:
        if str(year) in cached["years"]:
            continue
        items = search_scenes(client, p, year)
        futures = {pool.submit(read_scene, it, p): it["id"] for it in items}
        rows = []
        failed = 0
        for fut in as_completed(futures):
            try:
                rows.append(fut.result())
            except Exception:
                failed += 1
                log.debug("scene %s failed", futures[fut], exc_info=True)
        if failed > max(3, 0.2 * len(items)):
            log.warning("%s %d: %d of %d scenes failed - year not cached, will retry", p.id, year, failed, len(items))
        else:
            cached["years"][str(year)] = sorted(rows, key=lambda r: r["date"])
            cached["retrieved_at"] = datetime.now(UTC).isoformat(timespec="seconds")
            cache_file.write_text(json.dumps(cached), encoding="utf-8")
        n_ok = sum(1 for r in rows if r.get("ndmi") is not None)
        log.info("%s %d: %d scenes, %d clear NDMI, %d failed", p.id, year, len(items), n_ok, failed)
    return [{"point_id": p.id, **r} for rows in cached["years"].values() for r in rows]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workers", type=int, default=64, help="parallel scene reads (HTTP range requests)")
    ap.add_argument("--parallel-points", type=int, default=4, help="points processed at the same time")
    ap.add_argument("--points", type=int, default=0, help="only the first N points (test run)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("rasterio").setLevel(logging.ERROR)
    POINT_CACHE.mkdir(parents=True, exist_ok=True)
    points = load_points()
    if args.points:
        points = points[: args.points]
    started = time.perf_counter()
    all_rows: list[dict[str, Any]] = []
    headers = {"User-Agent": "AgroRetencja-KOWR/0.1 (HydroFusion-PL training)"}
    with (
        ThreadPoolExecutor(max_workers=args.workers) as pool,
        ThreadPoolExecutor(max_workers=args.parallel_points) as outer,
        httpx.Client(headers=headers) as client,
    ):
        futures = [outer.submit(process_point, p, pool, client) for p in points]
        for k, fut in enumerate(as_completed(futures), start=1):
            all_rows += fut.result()
            elapsed = time.perf_counter() - started
            log.info("== %d/%d points done, %.1f min elapsed, ~%.0f min left", k, len(points), elapsed / 60,
                     elapsed / k * (len(points) - k) / 60)

    import pandas as pd

    df = pd.DataFrame(all_rows)
    df["date"] = pd.to_datetime(df["date"])
    S2_FILE.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(S2_FILE, index=False)
    clear = df["ndmi"].notna().sum()
    log.info("saved %s: %d scenes, %d clear NDMI observations (%.1f min)", S2_FILE, len(df), clear,
             (time.perf_counter() - started) / 60)


if __name__ == "__main__":
    main()
