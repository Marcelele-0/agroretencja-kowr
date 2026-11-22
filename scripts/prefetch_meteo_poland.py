"""Pre-fetch 10-year Poland daily weather grid (2016-2025) from Open-Meteo Archive.

Resumable batch downloads saved in data/cache/meteo/batches/ and compiled into
data/cache/meteo/poland_grid_2016_2025.npz.

Usage:
    uv run python scripts/prefetch_meteo_poland.py
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
from pathlib import Path

import httpx
import numpy as np
from shapely.geometry import Point, Polygon

from backend.config import get_settings

DAILY_VARS = (
    "precipitation_sum",
    "et0_fao_evapotranspiration",
    "temperature_2m_mean",
    "temperature_2m_max",
    "temperature_2m_min",
)
START_DATE = "2016-01-01"
END_DATE = "2025-12-31"
STEP_DEG = 0.45

# Generous Poland outline buffered by ~25 km so every border parcel sits inside the grid
POLAND_OUTLINE = Polygon([
    (14.0, 53.9), (14.1, 52.5), (14.6, 51.0), (14.8, 50.8),
    (16.2, 50.0), (17.6, 49.9), (18.8, 49.4), (20.1, 49.1), (22.8, 48.9),
    (24.2, 50.4), (24.0, 52.2), (23.9, 53.6), (23.5, 54.4),
    (22.7, 54.4), (19.6, 54.5), (18.4, 54.9), (17.2, 54.7), (15.5, 54.2), (14.0, 53.9),
]).buffer(0.25)


class QuotaExhaustedError(RuntimeError):
    """Raised when Open-Meteo reports hourly or daily API quota exhaustion."""


def build_grid_points() -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    lat = 49.0
    while lat <= 54.85:
        lon = 14.1
        while lon <= 24.15:
            if POLAND_OUTLINE.contains(Point(lon, lat)):
                pts.append((round(lat, 2), round(lon, 2)))
            lon = round(lon + STEP_DEG, 4)
        lat = round(lat + STEP_DEG, 4)
    return pts


def _extract_429_reason(resp: httpx.Response) -> str:
    try:
        payload = resp.json()
        if isinstance(payload, dict) and payload.get("reason"):
            return str(payload["reason"])
    except (ValueError, TypeError):
        pass
    return resp.text.strip()[:200] or "HTTP 429"


def fetch_batch(
    client: httpx.Client, url: str, api_key: str | None, batch: list[tuple[float, float]]
) -> list[dict]:
    params: dict[str, str] = {
        "latitude": ",".join(f"{lat:.2f}" for lat, _ in batch),
        "longitude": ",".join(f"{lon:.2f}" for _, lon in batch),
        "start_date": START_DATE,
        "end_date": END_DATE,
        "daily": ",".join(DAILY_VARS),
        "timezone": "Europe/Warsaw",
    }
    if api_key:
        params["apikey"] = api_key

    max_attempts = 4
    for attempt in range(max_attempts):
        resp = client.get(url, params=params, timeout=45.0)
        if resp.status_code == 429:
            reason = _extract_429_reason(resp)
            lower = reason.lower()
            if "hourly" in lower or "daily" in lower:
                print(f"[STOP] Open-Meteo quota: {reason}", flush=True)
                raise QuotaExhaustedError(reason)
            if ("minutely" in lower or "concurrent" in lower) and attempt < 3:
                print(f"  HTTP 429 ({reason}), retrying in 62s...", flush=True)
                time.sleep(62.0)
                continue
            if attempt + 1 < max_attempts:
                print(f"  HTTP 429 ({reason}), retrying in 62s...", flush=True)
                time.sleep(62.0)
                continue
            resp.raise_for_status()
        if resp.status_code >= 500:
            if attempt + 1 >= max_attempts:
                resp.raise_for_status()
            wait = 10.0 * (attempt + 1)
            print(f"  HTTP {resp.status_code}, retrying in {wait:.0f}s...", flush=True)
            time.sleep(wait)
            continue
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else [data]
    raise RuntimeError("Unreachable")


def assemble_npz(batch_files: list[Path], out_path: Path) -> None:
    records: list[dict] = []
    for bf in sorted(batch_files):
        records.extend(json.loads(bf.read_text(encoding="utf-8")))
    if not records:
        raise RuntimeError("No weather batches available to assemble.")

    dates = records[0]["daily"]["time"]
    n_pts = len(records)
    n_days = len(dates)
    lats = np.array([float(r["latitude"]) for r in records], dtype=np.float64)
    lons = np.array([float(r["longitude"]) for r in records], dtype=np.float64)
    elevations = np.array(
        [np.nan if r.get("elevation") is None else float(r["elevation"]) for r in records],
        dtype=np.float64,
    )
    arrays = {v: np.full((n_pts, n_days), np.nan, dtype=np.float32) for v in DAILY_VARS}
    for i, rec in enumerate(records):
        daily = rec["daily"]
        for v in DAILY_VARS:
            vals = [np.nan if x is None else float(x) for x in daily[v][:n_days]]
            arrays[v][i, : len(vals)] = np.array(vals, dtype=np.float32)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=out_path.parent, suffix=".npz.tmp")
    os.close(fd)
    try:
        np.savez_compressed(
            tmp,
            lats=lats,
            lons=lons,
            elevations=elevations,
            dates=np.array(dates),
            precip=arrays["precipitation_sum"],
            et0=arrays["et0_fao_evapotranspiration"],
            tmean=arrays["temperature_2m_mean"],
            tmax=arrays["temperature_2m_max"],
            tmin=arrays["temperature_2m_min"],
        )
        os.replace(tmp, out_path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-size", type=int, default=6)
    parser.add_argument("--pause", type=float, default=15.0)
    args = parser.parse_args()

    s = get_settings()
    pts = build_grid_points()
    batches = [pts[i : i + args.batch_size] for i in range(0, len(pts), args.batch_size)]
    batch_dir = s.meteo_grid_path.parent / "batches"
    batch_dir.mkdir(parents=True, exist_ok=True)
    api_key = s.open_meteo_api_key.get_secret_value() if s.open_meteo_api_key else None
    print(f"Poland meteo grid ({STEP_DEG} deg): {len(pts)} points in {len(batches)} batches -> {s.meteo_grid_path}")

    done_files: list[Path] = []
    saved_new = 0
    with httpx.Client(
        headers={"User-Agent": s.http_user_agent},
        limits=httpx.Limits(max_connections=1),
    ) as client:
        for idx, batch in enumerate(batches):
            bf = batch_dir / f"step_045_batch_{idx:03d}.json"
            if bf.exists() and bf.stat().st_size > 1000:
                done_files.append(bf)
                continue
            try:
                rows = fetch_batch(client, s.open_meteo_archive_url, api_key, batch)
            except QuotaExhaustedError:
                break
            except Exception as exc:  # noqa: BLE001
                print(f"[WARN] batch {idx + 1}/{len(batches)} failed: {exc}", flush=True)
                break
            fd, tmp = tempfile.mkstemp(dir=batch_dir, suffix=".json.tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(rows, fh)
            os.replace(tmp, bf)
            done_files.append(bf)
            saved_new += 1
            print(f"[{idx + 1}/{len(batches)}] saved ({len(rows)} pts)", flush=True)
            if saved_new % 5 == 0:
                assemble_npz(done_files, s.meteo_grid_path)
                print(
                    f"  -> updated {s.meteo_grid_path.name} ({len(done_files)}/{len(batches)} batches, "
                    f"{s.meteo_grid_path.stat().st_size / 1e6:.1f} MB)",
                    flush=True,
                )
            if idx + 1 < len(batches):
                time.sleep(args.pause)

    if done_files:
        assemble_npz(done_files, s.meteo_grid_path)
        print(
            f"Assembled {s.meteo_grid_path} from {len(done_files)}/{len(batches)} batches "
            f"({s.meteo_grid_path.stat().st_size / 1e6:.1f} MB)",
            flush=True,
        )


if __name__ == "__main__":
    main()
