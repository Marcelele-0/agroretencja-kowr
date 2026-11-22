"""Pre-fetch GUGiK orthophoto WMTS tiles (z5-z11 across Poland + high-zoom warm tiles for presets).

Usage:
    uv run python scripts/prefetch_map_tiles.py
"""

from __future__ import annotations

import argparse
import asyncio
import math
import os
import tempfile
from collections import Counter
from pathlib import Path

import httpx
from shapely.geometry import Point, Polygon, box

from backend.config import get_settings

ORTHO_WMTS_UPSTREAM = "https://mapy.geoportal.gov.pl/wss/service/PZGIK/ORTO/WMTS/StandardResolution"

POLAND_OUTLINE = Polygon([
    (14.0, 53.9), (14.1, 52.5), (14.6, 51.0), (14.8, 50.8),
    (16.2, 50.0), (17.6, 49.9), (18.8, 49.4), (20.1, 49.1), (22.8, 48.9),
    (24.2, 50.4), (24.0, 52.2), (23.9, 53.6), (23.5, 54.4),
    (22.7, 54.4), (19.6, 54.5), (18.4, 54.9), (17.2, 54.7), (15.5, 54.2), (14.0, 53.9),
]).buffer(0.25)

# Key parcels / presets pre-warmed at high zoom (lon, lat): Darnowo 30, Czerniejewo, Wilków, Kosmowo
WARM_POINTS = (
    (16.7366, 52.0574),  # Darnowo 30
    (17.5490, 52.4511),  # 300302_5.0005.49
    (23.7580, 50.7450),  # 060408_2.0166.7
    (17.5320, 52.4350),  # 300302_5.0008.12/3
)


def deg2tile(lon: float, lat: float, z: int) -> tuple[int, int]:
    lat_r = math.radians(lat)
    n = 2**z
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(lat_r)) / math.pi) / 2.0 * n)
    return max(0, min(n - 1, x)), max(0, min(n - 1, y))


def tile2bounds(x: int, y: int, z: int) -> tuple[float, float, float, float]:
    n = 2**z
    lon1 = x / n * 360.0 - 180.0
    lon2 = (x + 1) / n * 360.0 - 180.0
    lat1 = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / n))))
    lat2 = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    return lon1, lat1, lon2, lat2


def ortho_poland_tiles(min_z: int = 5, max_z: int = 11) -> list[tuple[int, int, int]]:
    tiles: list[tuple[int, int, int]] = []
    for z in range(min_z, max_z + 1):
        x0, y0 = deg2tile(13.8, 55.0, z)
        x1, y1 = deg2tile(24.4, 48.8, z)
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                if z <= 8 or POLAND_OUTLINE.intersects(box(*tile2bounds(x, y, z))):
                    tiles.append((z, x, y))
    return tiles


def ortho_warm_tiles() -> list[tuple[int, int, int]]:
    seen: set[tuple[int, int, int]] = set()
    for lon, lat in WARM_POINTS:
        if not POLAND_OUTLINE.contains(Point(lon, lat)):
            continue
        for z, rad in ((12, 1), (13, 1), (14, 1), (15, 2), (16, 2), (17, 2)):
            cx, cy = deg2tile(lon, lat, z)
            for dx in range(-rad, rad + 1):
                for dy in range(-rad, rad + 1):
                    seen.add((z, cx + dx, cy + dy))
    return sorted(seen)


async def fetch_one(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    url: str,
    dest: Path,
) -> str:
    if dest.exists() and dest.stat().st_size > 0:
        return "cached"
    async with sem:
        for attempt in range(3):
            try:
                resp = await client.get(url, timeout=15.0)
                if resp.status_code == 200 and resp.headers.get("content-type", "").startswith("image/") and resp.content:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    fd, tmp = tempfile.mkstemp(dir=dest.parent, suffix=".tmp")
                    try:
                        with os.fdopen(fd, "wb") as fh:
                            fh.write(resp.content)
                        os.replace(tmp, dest)
                    except Exception:
                        if os.path.exists(tmp):
                            os.unlink(tmp)
                        raise
                    return "downloaded"
                if resp.status_code < 500 and resp.status_code != 429:
                    return f"skip_{resp.status_code}"
            except (httpx.HTTPError, OSError) as exc:
                _ = exc
            await asyncio.sleep(0.3 * (attempt + 1))
    return "err"


async def run(max_z: int, concurrency: int) -> None:
    s = get_settings()
    tile_root = s.cache_dir / "tiles"
    all_tiles = sorted(set(ortho_poland_tiles(5, max_z)) | set(ortho_warm_tiles()))
    print(f"GUGiK ORTO WMTS z5..z{max_z} + warm: {len(all_tiles)} unique tiles -> {tile_root / 'ortho-wmts'}")

    sem = asyncio.Semaphore(concurrency)
    counts: Counter[str] = Counter()
    async with httpx.AsyncClient(
        headers={"User-Agent": s.http_user_agent},
        limits=httpx.Limits(max_connections=concurrency + 4),
    ) as client:
        tasks = [
            fetch_one(
                client,
                sem,
                f"{ORTHO_WMTS_UPSTREAM}?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=ORTOFOTOMAPA&STYLE=default&FORMAT=image/jpeg&TILEMATRIXSET=EPSG:3857&TILEMATRIX=EPSG:3857:{z}&TILEROW={y}&TILECOL={x}",
                tile_root / "ortho-wmts" / str(z) / str(x) / f"{y}.jpg",
            )
            for z, x, y in all_tiles
        ]
        total = len(tasks)
        for i, coro in enumerate(asyncio.as_completed(tasks), 1):
            res = await coro
            counts[res] += 1
            if i % 500 == 0 or i == total:
                print(
                    f"[{i}/{total}] downloaded={counts['downloaded']} cached={counts['cached']} err={counts['err']}",
                    flush=True,
                )
    skips = {k: v for k, v in sorted(counts.items()) if k.startswith("skip_")}
    if skips:
        print(f"Non-200 skips: {skips}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-z", type=int, default=11)
    parser.add_argument("--concurrency", type=int, default=10)
    args = parser.parse_args()
    asyncio.run(run(args.max_z, args.concurrency))


if __name__ == "__main__":
    main()
