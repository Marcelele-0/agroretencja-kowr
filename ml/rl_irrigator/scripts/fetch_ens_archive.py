"""E9: archived ECMWF IFS ENS forecasts (51 members, 15 days) at the dataset points.

Source: dynamical.org Zarr copy of ECMWF open data ("ECMWF IFS ENS forecast, 15 day, 0.25 degree",
CC BY 4.0), forecasts initialised daily at 00 UTC since 2024-04-01. No API quota: the store is
read directly (sharded Zarr v3, inner chunks of 32x32 grid cells), so the cost does not depend on
the number of points.

For weekly initialisations in the seasons (15 III - 31 X) of 2024, 2025 and 2026 and for every
point (training + hold-out, nearest grid cell), daily aggregates per member and UTC day 0..14 after
the initialisation day:
* precipitation [mm/day]  (rate averaged since the previous step x step length),
* 2 m temperature [deg C, daily mean of the instantaneous values],
* downward short-wave radiation [MJ m-2 day-1] (flux averaged since the previous step x length).

A plan starting on day t uses the run initialised at 00 UTC of t-1 (published in the morning of
t-1), i.e. forecast days 1..14 = UTC days t .. t+13.

Output: data/ens_archive/ens_<year>.npz  (in .gitignore; MANIFEST.json alongside).

    uv run --group train python ml/rl_irrigator/scripts/fetch_ens_archive.py
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kowr_rl import DATA_DIR  # noqa: E402

URL = "https://data.dynamical.org/ecmwf/ifs-ens/forecast-15-day-0-25-degree/latest.zarr"
OUT = DATA_DIR / "ens_archive"
N_DAYS = 15
log = logging.getLogger("ens")


def points() -> list[dict]:
    out = []
    for d in (DATA_DIR, DATA_DIR / "holdout"):
        out += json.loads((d / "MANIFEST.json").read_text(encoding="utf-8"))["points"]
    return out


def init_dates(year: int) -> list[dt.date]:
    d, end = max(dt.date(year, 3, 14), dt.date(2024, 4, 1)), dt.date(year, 10, 23)
    out = []
    while d <= end:
        out.append(d)
        d += dt.timedelta(days=7)
    return out


def daily_from_steps(values: np.ndarray, lead_h: np.ndarray, kind: str) -> np.ndarray:
    """values (L, M, P) at lead steps -> (N_DAYS, M, P) daily aggregates over UTC days 0..14."""
    out = np.full((N_DAYS,) + values.shape[1:], np.nan, dtype=np.float32)
    if kind in ("rate", "flux"):  # averaged over (prev, s]
        dt_h = np.diff(lead_h)
        mid = (lead_h[:-1] + lead_h[1:]) / 2
        day = (mid // 24).astype(int)
        amount = values[1:] * (dt_h[:, None, None] * 3600.0)  # rate * seconds = mm  |  W m-2 * s = J m-2
        for d in range(N_DAYS):
            m = day == d
            if m.any():
                out[d] = np.nansum(amount[m], axis=0)
        if kind == "flux":
            out /= 1e6  # J -> MJ
    else:  # instantaneous: mean of the values valid within the day
        day = (lead_h // 24).astype(int)
        for d in range(N_DAYS):
            m = (day == d) & (lead_h < 24 * (d + 1))
            if m.any():
                out[d] = np.nanmean(values[m], axis=0)
    return out


def main() -> None:
    import xarray as xr

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    OUT.mkdir(parents=True, exist_ok=True)
    pts = points()
    ds = xr.open_zarr(URL, chunks=None)
    lat, lon = ds.latitude.values, ds.longitude.values
    iy = np.array([int(np.argmin(np.abs(lat - p["lat"]))) for p in pts])
    ix = np.array([int(np.argmin(np.abs(lon - p["lon"]))) for p in pts])
    box = dict(latitude=slice(lat[iy.min()], lat[iy.max()]), longitude=slice(lon[ix.min()], lon[ix.max()]))
    lead_h = (ds.lead_time.values / np.timedelta64(1, "h")).astype(float)
    variables = {"precipitation_surface": "rate", "temperature_2m": "instant", "downward_short_wave_radiation_flux_surface": "flux"}
    names = {"precipitation_surface": "precip", "temperature_2m": "t2m", "downward_short_wave_radiation_flux_surface": "rs"}
    available = set(np.asarray(ds.init_time.values, dtype="datetime64[D]").astype(str))

    def one(init: dt.date) -> tuple[dt.date, dict[str, np.ndarray] | None]:
        if init.isoformat() not in available:
            return init, None
        res = {}
        for var, kind in variables.items():
            for attempt in range(4):
                try:
                    a = ds[var].sel(init_time=np.datetime64(init.isoformat()), **box).load().values  # (L, M, lat, lon)
                    break
                except Exception as err:  # network hiccups
                    log.warning("%s %s attempt %d: %s", init, var, attempt + 1, err)
                    time.sleep(5 * (attempt + 1))
            else:
                return init, None
            pv = a[:, :, iy - iy.min(), ix - ix.min()]  # (L, M, P)
            res[names[var]] = daily_from_steps(pv, lead_h, kind)  # (15, M, P)
        return init, res

    started = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    summary = {}
    for year in (2024, 2025, 2026):
        dates = init_dates(year)
        got = {}
        with ThreadPoolExecutor(max_workers=6) as ex:
            for i, (init, res) in enumerate(ex.map(one, dates)):
                if res is not None:
                    got[init] = res
                if (i + 1) % 5 == 0:
                    log.info("%d: %d/%d inits", year, i + 1, len(dates))
        inits = sorted(got)
        arrays = {k: np.stack([got[d][k] for d in inits]).transpose(0, 3, 2, 1) for k in ("precip", "t2m", "rs")}  # (I, P, M, D)
        np.savez_compressed(
            OUT / f"ens_{year}.npz", init_dates=np.array([d.isoformat() for d in inits]), point_ids=np.array([p["id"] for p in pts]),
            grid_lat=lat[iy], grid_lon=lon[ix], **arrays,
        )
        summary[str(year)] = {"inits": len(inits), "requested": len(dates)}
        log.info("%d saved: %d inits", year, len(inits))
    (OUT / "MANIFEST.json").write_text(json.dumps({
        "source": URL, "model": "ECMWF IFS ENS 0.25 (open data), 51 members, 00 UTC runs", "license": "CC BY 4.0 (ECMWF, dynamical.org)",
        "fetched_started_utc": started, "fetched_finished_utc": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "init_dates": "weekly, 14 III - 23 X of each year (from 2024-04-01)", "days": "UTC days 0..14 after the initialisation day",
        "variables": {"precip": "mm/day", "t2m": "deg C daily mean", "rs": "MJ m-2 day-1"}, "summary": summary,
    }, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
