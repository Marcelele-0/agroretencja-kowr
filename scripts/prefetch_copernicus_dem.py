"""Pre-fetch national Copernicus DEM GLO-30 COG tiles (~1.3 GB) into data/cache/copernicus_dem/.

Usage:
    uv run python scripts/prefetch_copernicus_dem.py
    uv run python scripts/prefetch_copernicus_dem.py --workers 6
"""

from __future__ import annotations

import argparse
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx

from backend.config import get_settings


def tile_names() -> list[str]:
    """Return 1x1 deg Copernicus DEM tile names covering Poland (lat 49..54, lon 14..24)."""
    return [
        f"Copernicus_DSM_COG_10_N{lat:02d}_00_E{lon:03d}_00_DEM"
        for lat in range(49, 55)
        for lon in range(14, 25)
    ]


def download_tile(url_template: str, out_dir: Path, name: str, user_agent: str) -> str:
    dest = out_dir / f"{name}.tif"
    if dest.exists() and dest.stat().st_size > 100_000:
        return f"{name}: cached ({dest.stat().st_size / 1e6:.1f} MB)"

    url = url_template.format(name=name)
    out_dir.mkdir(parents=True, exist_ok=True)
    with (
        httpx.Client(headers={"User-Agent": user_agent}, timeout=120.0, follow_redirects=True) as client,
        client.stream("GET", url) as resp,
    ):
            if resp.status_code == 404:
                return f"{name}: 404 (skipped)"
            resp.raise_for_status()
            fd, tmp = tempfile.mkstemp(dir=out_dir, suffix=".tif.tmp")
            try:
                with os.fdopen(fd, "wb") as fh:
                    for chunk in resp.iter_bytes(chunk_size=1024 * 256):
                        fh.write(chunk)
                os.replace(tmp, dest)
            except Exception:
                if os.path.exists(tmp):
                    os.unlink(tmp)
                raise
    return f"{name}: ok ({dest.stat().st_size / 1e6:.1f} MB)"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()

    s = get_settings()
    names = tile_names()
    print(f"Copernicus DEM 30 m tiles for Poland: {len(names)} -> {s.copernicus_dem_dir}")

    ok = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {
            pool.submit(download_tile, s.copernicus_dem_url_template, s.copernicus_dem_dir, n, s.http_user_agent): n
            for n in names
        }
        for fut in as_completed(futs):
            name = futs[fut]
            try:
                msg = fut.result()
                ok += 1
                print(f"[{ok}/{len(names)}] {msg}", flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"[ERR] {name}: {exc}", flush=True)


if __name__ == "__main__":
    main()
