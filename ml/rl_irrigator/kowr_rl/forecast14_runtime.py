"""E9 runtime: 14-day soil-water forecast for ``recommend(plan_days=14)``.

Weather sources for the next 14 days (in this order):
* the live ECMWF IFS 0.25 ensemble (51 members, Open-Meteo Ensemble API) when the analysis date is
  today;
* the archived ECMWF IFS ENS (dynamical.org Zarr, from 2024-04-02) for historical dates, when the
  optional ``xarray``/``zarr`` packages are installed and ``KOWR_RL_ENS_ARCHIVE=1``;
* otherwise the same calendar days of the other archive years (analog years).
The soil profile comes from ISRIC SoilGrids 2.0 (cached forever); without it the available water is
spread evenly with depth. The 0-100 cm water always equals the TAW of the IUNG-PIB soil category.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any

import numpy as np

from . import CONFIG_DIR
from .config import CropParams
from .forecast14 import N_DAYS, EnsCalibration, makkink_et0, run_members
from .soil3 import (
    WATER_CLASS_PL,
    ZONE_BOUNDS_M,
    canopy_share,
    relative_water,
    root_depth_curve,
    root_fractions,
    spin_up3,
    step3,
    water_class,
)

ENSEMBLE_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"
SOILGRIDS_URL = "https://rest.isric.org/soilgrids/v2.0/properties/query"
DYNAMICAL_URL = "https://data.dynamical.org/ecmwf/ifs-ens/forecast-15-day-0-25-degree/latest.zarr"
POSTPROC_PATH = CONFIG_DIR / "forecast14_postproc.json"
ARCHIVE_ENS_START = dt.date(2024, 4, 2)
SOILGRIDS_LICENSE = "CC BY 4.0 (ISRIC - World Soil Information, SoilGrids 2.0)"
ENS_LICENSE = "CC BY 4.0 (ECMWF IFS ENS; Open-Meteo.com / dynamical.org)"
ZONE_LABELS = tuple(f"{int(a * 100)}-{int(b * 100)} cm" for a, b in ZONE_BOUNDS_M)
DATA_SCALE = ("Skala danych: pogoda z modelu ECMWF na siatce 0,25° (ok. 25 km, 51 członków ensemble), gleba z map SoilGrids "
              "(250 m) dopasowanych do pojemności kategorii IUNG-PIB. Stan strefy to wartość reprezentatywna dla punktu, "
              "podawana jako klasa z prawdopodobieństwem.")
_DYN: dict[str, Any] = {}


def _as_date(d: Any) -> dt.date:
    return d if isinstance(d, dt.date) else dt.date.fromisoformat(str(d))


def archive_enabled() -> bool:
    return os.environ.get("KOWR_RL_ENS_ARCHIVE", "") == "1"


# --------------------------------------------------------------------------- data
def fetch_ensemble_live(client: Any, lat: float, lon: float, start: Any) -> dict[str, Any] | None:
    """Daily precipitation, mean 2 m temperature and short-wave radiation [MJ m-2] of the ECMWF
    members for the local days start .. start+13 -> arrays (M, 14). None if the response is short."""
    res = client.get(ENSEMBLE_URL, {"latitude": round(lat, 4), "longitude": round(lon, 4), "models": "ecmwf_ifs025", "forecast_days": 16,
                                    "hourly": "precipitation,temperature_2m,shortwave_radiation", "timezone": "Europe/Warsaw"},
                     max_age_s=3 * 3600)
    h = res.payload["hourly"]
    day = np.asarray(h["time"], dtype="datetime64[h]").astype("datetime64[D]")
    days = np.datetime64(_as_date(start), "D") + np.arange(N_DAYS)
    out: dict[str, np.ndarray] = {}
    for var, key in (("precipitation", "precip"), ("temperature_2m", "t2m"), ("shortwave_radiation", "rs")):
        rows = []
        for name in sorted(k for k in h if k == var or k.startswith(var + "_member")):
            v = np.asarray([np.nan if x is None else x for x in h[name]], dtype=float)
            row = np.full(N_DAYS, np.nan)
            for i, d in enumerate(days):
                m = day == d
                if m.sum() >= 23 and np.isfinite(v[m]).all():
                    row[i] = v[m].sum() if key == "precip" else (v[m].mean() if key == "t2m" else v[m].sum() * 3600.0 / 1e6)
            rows.append(row)
        out[key] = np.asarray(rows)
    n = min(len(v) for v in out.values())
    if n < 10 or not np.isfinite(out["precip"][:n, :7]).all():
        return None
    return {k: v[:n] for k, v in out.items()} | {"name": "Open-Meteo Ensemble API (ECMWF IFS 0.25 ENS)", "url": res.url,
                                               "retrieved_at": res.retrieved_at, "from_cache": res.from_cache}


def _daily_steps(values: np.ndarray, lead_h: np.ndarray, kind: str) -> np.ndarray:
    """(L, ...) values at lead hours -> (15, ...) UTC-day totals (rate/flux) or means (instant)."""
    n = N_DAYS + 1
    out = np.full((n,) + values.shape[1:], np.nan)
    if kind in ("rate", "flux"):
        dt_h = np.diff(lead_h)
        day = ((lead_h[:-1] + lead_h[1:]) / 2 // 24).astype(int)
        amount = values[1:] * (dt_h.reshape((-1,) + (1,) * (values.ndim - 1)) * 3600.0)
        for d in range(n):
            if (day == d).any():
                out[d] = np.nansum(amount[day == d], axis=0)
        if kind == "flux":
            out /= 1e6
    else:
        day = (lead_h // 24).astype(int)
        for d in range(n):
            if (day == d).any():
                out[d] = np.nanmean(values[day == d], axis=0)
    return out


def fetch_ensemble_archive(lat: float, lon: float, start: Any) -> dict[str, Any] | None:
    """Archived ECMWF IFS ENS: run of 00 UTC on start-1, UTC days start .. start+13 -> (M, 14)."""
    start = _as_date(start)
    if not archive_enabled() or start < ARCHIVE_ENS_START:
        return None
    try:
        import xarray as xr
    except ImportError:
        return None
    if "ds" not in _DYN:
        _DYN["ds"] = xr.open_zarr(DYNAMICAL_URL, chunks=None)
    ds = _DYN["ds"]
    init = np.datetime64(start - dt.timedelta(days=1), "D")
    if init not in set(np.asarray(ds.init_time.values, dtype="datetime64[D]")):
        return None
    lead_h = (ds.lead_time.values / np.timedelta64(1, "h")).astype(float)
    out = {}
    for var, key, kind in (("precipitation_surface", "precip", "rate"), ("temperature_2m", "t2m", "instant"),
                           ("downward_short_wave_radiation_flux_surface", "rs", "flux")):
        v = ds[var].sel(init_time=init, latitude=lat, longitude=lon, method="nearest").load().values  # (L, M)
        out[key] = _daily_steps(np.asarray(v, dtype=float), lead_h, kind)[1 : N_DAYS + 1].T
    return out | {"name": "ECMWF IFS ENS 0.25 (archiwum dynamical.org)", "url": DYNAMICAL_URL,
                  "retrieved_at": dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat(), "from_cache": False}


def fetch_soil_profile(client: Any, lat: float, lon: float) -> tuple[dict[str, Any], Any]:
    """SoilGrids field capacity (wv0033) / wilting point (wv1500) profile, 6 layers, % vol."""
    depths = ["0-5cm", "5-15cm", "15-30cm", "30-60cm", "60-100cm", "100-200cm"]
    res = client.get(SOILGRIDS_URL, {"lon": round(lon, 4), "lat": round(lat, 4), "property": ["wv0033", "wv1500"], "depth": depths, "value": "mean"})
    out: dict[str, Any] = {}
    for layer in res.payload["properties"]["layers"]:
        f = float(layer["unit_measure"]["d_factor"])
        out[layer["name"]] = {"by_depth": {d["label"]: (None if d["values"].get("mean") is None else d["values"]["mean"] / f) for d in layer["depths"]}}
    return out, res


def ens_weather(ens: dict[str, Any], calib: EnsCalibration | None, h: int) -> tuple[np.ndarray, np.ndarray]:
    """(M, h) precipitation (raw members) and calibrated Makkink ET0 of the members."""
    t2m, rs = ens["t2m"][:, :h], ens["rs"][:, :h]
    t2m = np.where(np.isfinite(t2m), t2m, np.nanmean(t2m, axis=0, keepdims=True))
    rs = np.where(np.isfinite(rs), rs, np.nanmean(rs, axis=0, keepdims=True))
    mk = makkink_et0(t2m, rs)
    e = np.maximum(calib.et0_a[:h] + calib.et0_b[:h] * mk, 0.0) if calib is not None else mk
    p = ens["precip"][:, :h]
    p = np.where(np.isfinite(p), p, np.nanmean(p, axis=0, keepdims=True))
    return np.nan_to_num(p), np.nan_to_num(e)


def load_postproc(path: Path = POSTPROC_PATH) -> dict[str, Any] | None:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# --------------------------------------------------------------------------- state on the analysis day
def zone_state(dates: np.ndarray, precip: np.ndarray, et0: np.ndarray, crop: CropParams, date: dt.date, awc: np.ndarray,
               past_doses: np.ndarray | None = None) -> dict[str, np.ndarray]:
    """Depletion of the 3 zones on the morning of ``date``: spin-up from 1 August of the previous year
    to the sowing day, then the season with the crop's Kc, roots and canopy and the past doses."""
    dates = np.asarray(dates, dtype="datetime64[D]")
    year = date.year
    s = crop.season_start(year)

    def window(a: dt.date, n: int) -> tuple[np.ndarray, np.ndarray]:
        want = np.datetime64(a, "D") + np.arange(n)
        idx = np.clip(np.searchsorted(dates, want), 0, max(len(dates) - 1, 0))
        hit = dates[idx] == want
        p, e = np.full(n, np.nan), np.full(n, np.nan)
        p[hit], e[hit] = np.asarray(precip)[idx[hit]], np.asarray(et0)[idx[hit]]
        return p, e

    pre0 = dt.date(year - 1, 8, 1)
    pp, pe = window(pre0, (s - pre0).days)
    d = spin_up3(pp[None], pe[None], awc[None])[0]
    t_date = (date - s).days
    kc, ft, fr = crop.kc_curve(), canopy_share(crop), root_fractions(root_depth_curve(crop))
    sp, se = window(s, t_date)
    for t in range(t_date):
        if not (np.isfinite(sp[t]) and np.isfinite(se[t])):
            continue
        irr = np.array([0.0 if past_doses is None or t >= len(past_doses) else float(past_doses[t])])
        d = step3(d[None], awc[None], fr[t][None], sp[t : t + 1], irr, se[t : t + 1], kc[t : t + 1], ft[t : t + 1], np.array([crop.p])).d[0]
    return {"d0": d, "kc": kc, "f_t": ft, "frac": fr, "t_date": np.array(t_date)}


# --------------------------------------------------------------------------- forecast distribution
def zone_forecast(d0: np.ndarray, awc: np.ndarray, frac: np.ndarray, kc: np.ndarray, f_t: np.ndarray, p_tab: float,
                  ens_p: np.ndarray | None, ens_e: np.ndarray | None, analog_p: np.ndarray, analog_e: np.ndarray,
                  post: dict[str, Any] | None, irr: np.ndarray | None = None) -> dict[str, Any]:
    """Distribution of the zones, the root zone and crop stress for the next H days.

    ENS members (root zone widened by the lead factor ``lambda``) mixed with analog years by the
    lead weights ``w_ens`` and the conformal P10-P90 factor ``interval_scale`` - all fitted on the
    validation year 2024 (``config/forecast14_postproc.json``). ``irr`` (H,) = planned doses."""
    H = frac.shape[0]
    post = post or {}
    lam = np.asarray(post.get("lambda", [1.0] * N_DAYS)[:H], dtype=float)
    w_ens = np.asarray(post.get("w_ens", [1.0] * N_DAYS)[:H], dtype=float)
    scale = np.asarray(post.get("interval_scale", [1.0] * N_DAYS)[:H], dtype=float)
    runs, weights = [], []

    def run(p: np.ndarray, e: np.ndarray) -> dict[str, np.ndarray]:
        dose = None if irr is None else np.repeat(np.asarray(irr, dtype=float)[None, :H], p.shape[0], axis=0)
        return run_members(d0, awc, frac, kc, f_t, p_tab, p[:, :H], e[:, :H], dose)

    if ens_p is not None and len(ens_p):
        r = run(ens_p, ens_e)
        med = np.median(r["rw_root"], axis=0, keepdims=True)
        r["rw_root"] = np.clip(med + lam[None, :] * (r["rw_root"] - med), 0.0, 1.0)
        runs.append(r)
        weights.append(w_ens)
    if len(analog_p):
        runs.append(run(analog_p, analog_e))
        weights.append(1.0 - w_ens if runs[:-1] else np.ones(H))
    if not runs:
        return {}
    rw = np.concatenate([r["rw"] for r in runs])
    root = np.concatenate([r["rw_root"] for r in runs])
    stress = np.concatenate([r["stress"] for r in runs])
    w = np.concatenate([np.repeat((wt / r["rw_root"].shape[0])[None, :], r["rw_root"].shape[0], axis=0) for r, wt in zip(runs, weights)])
    w = w / np.maximum(w.sum(axis=0, keepdims=True), 1e-12)

    def wq(x: np.ndarray, q: float) -> np.ndarray:
        o = np.argsort(x, axis=0)
        xs, ws = np.take_along_axis(x, o, axis=0), np.take_along_axis(w, o, axis=0)
        idx = (np.cumsum(ws, axis=0) >= q - 1e-12).argmax(axis=0)
        return np.take_along_axis(xs, idx[None, :], axis=0)[0]

    med, q10, q90 = wq(root, 0.5), wq(root, 0.1), wq(root, 0.9)
    lo = np.clip(med - scale * (med - q10) - 0.01 * (scale - 1), 0, 1)
    hi = np.clip(med + scale * (q90 - med) + 0.01 * (scale - 1), 0, 1)
    cls = water_class(rw)
    zone_probs = np.stack([(w[:, :, None] * (cls == k)).sum(axis=0) for k in range(5)], axis=-1)  # (H, 3, 5)
    root_cls = water_class(root)
    root_probs = np.stack([(w * (root_cls == k)).sum(axis=0) for k in range(5)], axis=-1)  # (H, 5)
    deficit = np.concatenate([r["deficit_mm"] for r in runs])
    return {"root_median": med, "root_lo": lo, "root_hi": hi, "zone_class_probs": zone_probs, "root_class_probs": root_probs,
            "stress_probability": (w * stress).sum(axis=0), "deficit_mm": (w * deficit).sum(axis=0),
            "n_members": int(root.shape[0]), "ens_used": bool(ens_p is not None and len(ens_p))}


def _probs(p: np.ndarray) -> dict[str, float]:
    return {WATER_CLASS_PL[k]: float(round(float(p[k]), 2)) for k in range(len(WATER_CLASS_PL))}


def soil_zones_block(date: dt.date, d0: np.ndarray, awc: np.ndarray, frac: np.ndarray, fc: dict[str, Any],
                     fc_plan: dict[str, Any] | None, profile_source: str, weather_source: str) -> dict[str, Any]:
    """JSON block ``plan_14d.soil_zones``: classes with probabilities per zone and day."""
    rw0 = relative_water(d0, awc)
    taw_r0 = float((frac[0] * awc).sum())
    root0 = float(np.clip(1 - (frac[0] * d0).sum() / max(taw_r0, 1e-9), 0, 1))
    days = []
    for k in range(len(fc["root_median"])):
        zp = fc["zone_class_probs"][k]
        rp = fc["root_class_probs"][k]
        day = {
            "day": k + 1,
            "date": (date + dt.timedelta(days=k)).isoformat(),
            "root_zone": {
                "class": WATER_CLASS_PL[int(np.argmax(rp))],
                "class_probabilities": _probs(rp),
                "water_pct_median": float(round(100 * float(fc["root_median"][k]))),
                "water_pct_p10": float(round(100 * float(fc["root_lo"][k]))),
                "water_pct_p90": float(round(100 * float(fc["root_hi"][k]))),
            },
            "zones": [{"zone": ZONE_LABELS[z], "class": WATER_CLASS_PL[int(np.argmax(zp[z]))], "class_probabilities": _probs(zp[z])} for z in range(3)],
            "stress_probability_no_irrigation": float(round(float(fc["stress_probability"][k]), 2)),
        }
        if fc_plan:
            day["stress_probability_with_plan"] = float(round(float(fc_plan["stress_probability"][k]), 2))
            day["root_zone"]["water_pct_median_with_plan"] = float(round(100 * float(fc_plan["root_median"][k])))
        days.append(day)
    return {
        "zones": [{"zone": ZONE_LABELS[z], "available_water_mm": float(round(float(awc[z]), 1)),
                   "rooted_fraction_today": float(round(float(frac[0][z]), 2))} for z in range(3)],
        "classes": list(WATER_CLASS_PL),
        "class_bounds_pct": [0, 20, 40, 60, 80, 100],
        "today": {"root_zone_water_pct": float(round(100 * root0)),
                  "zones": [{"zone": ZONE_LABELS[z], "water_pct": float(round(100 * float(rw0[z]))),
                             "class": WATER_CLASS_PL[int(water_class(rw0[z]))]} for z in range(3)]},
        "days": days,
        "water_deficit_14d_no_irrigation_mm": float(round(float(np.sum(fc["deficit_mm"])), 1)),
        "n_scenarios": fc["n_members"],
        "weather_source": weather_source,
        "soil_profile_source": profile_source,
        "data_scale": DATA_SCALE,
        "method": ("Trzy strefy gleby (0-28, 28-100, 100-200 cm, jak warstwy ERA5-Land): parowanie z wierzchniej strefy, "
                   "transpiracja z zasięgu korzeni rosnącego od 0,2 m do głębokości maksymalnej uprawy. Stan na dziś z bilansu "
                   "od 1 sierpnia poprzedniego roku; kolejne 14 dni dla każdego członka ensemble ECMWF, z poprawką rozrzutu "
                   "i przedziału P10–P90 dopasowaną na roku walidacyjnym 2024."),
    }

