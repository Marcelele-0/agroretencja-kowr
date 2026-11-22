"""Pre-fetch SoilGrids v2.0 250 m GeoTIFF layers for Poland (~150 MB) into data/cache/soilgrids/.

Clips ISRIC virtual rasters (/vsicurl/...) directly over Poland and saves compressed int16 TIFFs.

Usage:
    uv run python scripts/prefetch_soilgrids.py
    uv run python scripts/prefetch_soilgrids.py --workers 4
"""

from __future__ import annotations

import argparse
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import rasterio
from rasterio.warp import transform_bounds
from rasterio.windows import Window, from_bounds

from backend.config import get_settings

PROPERTIES = ("sand", "clay", "soc", "bdod")
DEPTHS = ("0-5cm", "5-15cm", "15-30cm", "30-60cm", "60-100cm")
POLAND_WGS84 = (13.8, 48.8, 24.5, 55.1)
VRT_TEMPLATE = "/vsicurl/https://files.isric.org/soilgrids/latest/data/{prop}/{prop}_{depth}_mean.vrt"
GDAL_ENV = {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "GDAL_HTTP_TIMEOUT": "30",
    "GDAL_HTTP_MAX_RETRY": "3",
    "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
}


def fetch_clip(out_dir: Path, prop: str, depth: str) -> str:
    dest = out_dir / f"{prop}_{depth}.tif"
    if dest.exists() and dest.stat().st_size > 100_000:
        return f"{prop}_{depth}: cached ({dest.stat().st_size / 1e6:.1f} MB)"

    out_dir.mkdir(parents=True, exist_ok=True)
    vrt = VRT_TEMPLATE.format(prop=prop, depth=depth)
    with rasterio.Env(**GDAL_ENV), rasterio.open(vrt) as src:
        left, bottom, right, top = transform_bounds("EPSG:4326", src.crs, *POLAND_WGS84, densify_pts=21)
        win = from_bounds(left, bottom, right, top, transform=src.transform).round_offsets().round_lengths()
        win = win.intersection(Window(0, 0, src.width, src.height))
        data = src.read(1, window=win)
        transform = src.window_transform(win)
        profile = src.profile.copy()
        profile.update(
            driver="GTiff",
            height=data.shape[0],
            width=data.shape[1],
            transform=transform,
            compress="deflate",
            tiled=True,
            blockxsize=256,
            blockysize=256,
        )
        fd, tmp = tempfile.mkstemp(dir=out_dir, suffix=".tif.tmp")
        os.close(fd)
        try:
            with rasterio.open(tmp, "w", **profile) as dst:
                dst.write(data, 1)
            os.replace(tmp, dest)
        except Exception:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
    return f"{prop}_{depth}: ok ({dest.stat().st_size / 1e6:.1f} MB, {data.shape[1]}x{data.shape[0]})"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()

    s = get_settings()
    jobs = [(p, d) for p in PROPERTIES for d in DEPTHS]
    print(f"SoilGrids 250 m layers for Poland: {len(jobs)} -> {s.soilgrids_dir}")

    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {pool.submit(fetch_clip, s.soilgrids_dir, p, d): f"{p}_{d}" for p, d in jobs}
        for fut in as_completed(futs):
            label = futs[fut]
            try:
                msg = fut.result()
                done += 1
                print(f"[{done}/{len(jobs)}] {msg}", flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"[ERR] {label}: {exc}", flush=True)


if __name__ == "__main__":
    main()
