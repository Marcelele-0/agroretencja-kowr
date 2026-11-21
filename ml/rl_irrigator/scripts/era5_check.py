"""E9: independent check of the three-zone soil model and the 14-day forecast against ERA5-Land.

ERA5-Land (ECMWF land-surface model HTESSEL driven by ERA5, ~9 km) gives the daily soil moisture of
4 layers (0-7, 7-28, 28-100, 100-255 cm). It never enters our model, so it is an independent
reference. Our zones are aligned with its layers: 0-28 = (0-7, 7-28), 28-100, 100-200 ~ 100-255.

Land cover: grassland all year (Kc / roots / canopy of ``uzytki_zielone`` in its season, the
spin-up cover outside it), both for the three-zone model and for the one-bucket FAO-56 model
(TAW of the IUNG category over 0-100 cm, the same Kc and p).

ERA5-Land volumetric water [m3/m3] is mapped to the model's relative water by per-point quantile
mapping (CDF matching) fitted on 2017-2023 (each model gets its own mapping); the checks run on the
growing seasons (IV-X) of 2024-2026.

``structure``: daily agreement of the zone states with ERA5-Land - correlation of the values, of the
                anomalies (day-of-year climate removed) and of the 7-day changes, RMSE, share of days
                in the same water class; the 0-100 cm state of the 3-zone model vs the 1-bucket model.
``forecast``:   hold-out points, 2025-2026, weekly ECMWF ENS runs: the forecast change of the water
                in 0-28 / 28-100 / 0-100 cm after 1..14 days vs the change in ERA5-Land; CRPS of the
                ENS and analog ensembles, MAE of persistence (no change) and of the model run on the
                observed weather (upper bound of the weather forecast).

Output: reports/era5land_check.json

    uv run --group train python ml/rl_irrigator/scripts/era5_check.py structure
    uv run --group train python ml/rl_irrigator/scripts/era5_check.py forecast
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kowr_rl import DATA_DIR, REPORTS_DIR  # noqa: E402
from kowr_rl.config import load_crops, load_soils  # noqa: E402
from kowr_rl.dataset import load_points  # noqa: E402
from kowr_rl.forecast14 import N_DAYS, EnsCalibration, crps_ensemble, makkink_et0, run_members  # noqa: E402
from kowr_rl.simulator import balance_step  # noqa: E402
from kowr_rl.soil3 import canopy_share, relative_water, root_depth_curve, root_fractions, step3, water_class, zone_awc_from_profile  # noqa: E402

E5 = DATA_DIR / "era5land"
OUT = REPORTS_DIR / "era5land_check.json"
START = dt.date(2016, 1, 1)
FIT_YEARS = range(2017, 2024)
EVAL_YEARS = (2024, 2025, 2026)
COLS = ["sm_0_to_7cm", "sm_7_to_28cm", "sm_28_to_100cm", "sm_100_to_255cm"]
OFF = {"kc": 0.35, "f_t": 0.3, "p": 0.55, "root_m": 1.5}  # cover outside the grass season (as spin_up3)
ZONES = ("0-28 cm", "28-100 cm", "100-200 cm", "0-100 cm")


# --------------------------------------------------------------------------- inputs
def load_set() -> tuple[list, dict[str, str], np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    man = json.loads((E5 / "MANIFEST.json").read_text(encoding="utf-8"))
    ids = {r["id"]: r["set"] for r in man["points"]}
    pts = [p for p in list(load_points()) + list(load_points(str(DATA_DIR / "holdout"))) if p.id in ids and (E5 / f"{p.id}.parquet").exists()]
    end = min(pd.read_parquet(E5 / f"{p.id}.parquet")["date"].max().date() for p in pts)
    end = min(end, min(dt.date.fromisoformat(str(p.weather.dates[-1])) for p in pts))
    dates = np.datetime64(START, "D") + np.arange((end - START).days + 1)
    B, N = len(pts), len(dates)
    P, E, TH = np.full((B, N), np.nan), np.full((B, N), np.nan), np.full((B, N, 4), np.nan)
    for b, p in enumerate(pts):
        wd = np.asarray(p.weather.dates, dtype="datetime64[D]")
        idx = np.clip(np.searchsorted(wd, dates), 0, len(wd) - 1)
        hit = wd[idx] == dates
        P[b, hit], E[b, hit] = np.asarray(p.weather.precip)[idx[hit]], np.asarray(p.weather.et0)[idx[hit]]
        df = pd.read_parquet(E5 / f"{p.id}.parquet")
        ed = df["date"].values.astype("datetime64[D]")
        idx = np.clip(np.searchsorted(ed, dates), 0, len(ed) - 1)
        hit = ed[idx] == dates
        TH[b, hit] = df[COLS].to_numpy(dtype=float)[idx[hit]]
    soils = load_soils()["categories"]
    prof_path = DATA_DIR / "soils" / "soilgrids_profiles.json"
    profiles = json.loads(prof_path.read_text(encoding="utf-8"))["points"] if prof_path.exists() else {}
    taw = np.array([float(soils[int(p.iung_code) if int(p.iung_code) in soils else 2]["taw_mm"]) for p in pts])
    awc = np.stack([zone_awc_from_profile(profiles.get(p.id), t) for p, t in zip(pts, taw)])
    return pts, ids, dates, P, E, TH, awc, taw


def day_params(dates: np.ndarray) -> dict[str, np.ndarray]:
    """Kc, canopy share, root fractions (N, 3) and p of grassland in season, spin-up cover outside."""
    c = load_crops()["uzytki_zielone"]
    kc_c, ft_c, fr_c = c.kc_curve(), canopy_share(c), root_fractions(root_depth_curve(c))
    N = len(dates)
    kc, ft, p = np.full(N, OFF["kc"]), np.full(N, OFF["f_t"]), np.full(N, OFF["p"])
    fr = np.repeat(root_fractions(np.array(OFF["root_m"]))[None], N, axis=0)
    years = dates.astype("datetime64[Y]").astype(int) + 1970
    for y in np.unique(years):
        s = np.datetime64(c.season_start(int(y)), "D")
        t = (dates - s).astype(int)
        m = (t >= 0) & (t < c.length)
        kc[m], ft[m], fr[m], p[m] = kc_c[t[m]], ft_c[t[m]], fr_c[t[m]], c.p
    return {"kc": kc, "f_t": ft, "frac": fr, "p": p}


def run_models(P: np.ndarray, E: np.ndarray, awc: np.ndarray, taw: np.ndarray, prm: dict[str, np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    """Continuous runs from 1 I 2016 (full profile): 3-zone depletion (B, N, 3) and 1-bucket depletion (B, N), end of day."""
    B, N = P.shape
    d, dr = np.zeros((B, 3)), np.zeros(B)
    D3, D1 = np.zeros((B, N, 3)), np.zeros((B, N))
    zero = np.zeros(B)
    for i in range(N):
        ok = np.isfinite(P[:, i]) & np.isfinite(E[:, i])
        p, e = np.nan_to_num(P[:, i]), np.nan_to_num(E[:, i])
        kc, ft, pt = np.full(B, prm["kc"][i]), np.full(B, prm["f_t"][i]), np.full(B, prm["p"][i])
        st = step3(d, awc, np.repeat(prm["frac"][i][None], B, axis=0), p, zero, e, kc, ft, pt)
        d = np.where(ok[:, None], st.d, d)
        dr_new = balance_step(dr, p, zero, e, kc, taw, pt)[0]
        dr = np.where(ok, dr_new, dr)
        D3[:, i], D1[:, i] = d, dr
    return D3, D1


def model_states(D3: np.ndarray, D1: np.ndarray, awc: np.ndarray, taw: np.ndarray) -> np.ndarray:
    """(B, N, 5): relative water of 0-28, 28-100, 100-200, 0-100 (3-zone) and 0-100 (1-bucket)."""
    rw = relative_water(D3, awc[:, None, :])
    rw01 = 1 - (D3[..., 0] + D3[..., 1]) / (awc[:, None, 0] + awc[:, None, 1])
    rw1 = np.clip(1 - D1 / taw[:, None], 0, 1)
    return np.concatenate([rw, rw01[..., None], rw1[..., None]], axis=2)


def era5_states(TH: np.ndarray) -> np.ndarray:
    """(B, N, 5) ERA5-Land layers aggregated to the model zones (thickness-weighted)."""
    top = (7 * TH[..., 0] + 21 * TH[..., 1]) / 28
    z01 = (7 * TH[..., 0] + 21 * TH[..., 1] + 72 * TH[..., 2]) / 100
    return np.stack([top, TH[..., 2], TH[..., 3], z01, z01], axis=2)


def cdf_match(era: np.ndarray, mod: np.ndarray, fit: np.ndarray) -> np.ndarray:
    """Per point and zone quantile mapping of ERA5-Land onto the model's relative water."""
    qs = np.linspace(0, 1, 101)
    out = np.full_like(era, np.nan)
    for b in range(era.shape[0]):
        for k in range(era.shape[2]):
            e, m = era[b, fit, k], mod[b, fit, k]
            ok = np.isfinite(e) & np.isfinite(m)
            if ok.sum() < 200:
                continue
            qe = np.maximum.accumulate(np.quantile(e[ok], qs) + np.linspace(0, 1e-9, len(qs)))
            out[b, :, k] = np.interp(era[b, :, k], qe, np.quantile(m[ok], qs))
    return out


def doy_anom(x: np.ndarray, dates: np.ndarray, fit: np.ndarray) -> np.ndarray:
    doy = (dates - dates.astype("datetime64[Y]")).astype(int)
    out = np.full_like(x, np.nan)
    for b in range(x.shape[0]):
        for k in range(x.shape[2]):
            v = x[b, :, k]
            ok = fit & np.isfinite(v)
            n = np.maximum(np.bincount(doy[ok], minlength=367), 1)
            clim = np.bincount(doy[ok], weights=v[ok], minlength=367) / n
            sm = np.convolve(np.concatenate([clim[-15:], clim, clim[:15]]), np.ones(31) / 31, mode="same")[15:-15]
            out[b, :, k] = v - sm[doy]
    return out


def _corr(a: np.ndarray, b: np.ndarray) -> float:
    ok = np.isfinite(a) & np.isfinite(b)
    return float(np.corrcoef(a[ok], b[ok])[0, 1]) if ok.sum() > 30 else float("nan")


# --------------------------------------------------------------------------- structure
def structure() -> dict:
    pts, ids, dates, P, E, TH, awc, taw = load_set()
    prm = day_params(dates)
    D3, D1 = run_models(P, E, awc, taw, prm)
    mod = model_states(D3, D1, awc, taw)
    era = era5_states(TH)
    years = dates.astype("datetime64[Y]").astype(int) + 1970
    month = dates.astype("datetime64[M]").astype(int) % 12 + 1
    fit = np.isin(years, list(FIT_YEARS))
    ev = np.isin(years, EVAL_YEARS) & (month >= 4) & (month <= 10)
    em = cdf_match(era, mod, fit)
    am, ae = doy_anom(mod, dates, fit), doy_anom(em, dates, fit)
    lag = 7
    dm, de = mod[:, lag:] - mod[:, :-lag], em[:, lag:] - em[:, :-lag]
    ev7 = ev[lag:]
    names = list(ZONES[:3]) + ["0-100 cm (3 strefy)", "0-100 cm (1 wiadro FAO-56)"]
    res: dict = {"n_points": len(pts), "points_by_set": {s: sum(1 for p in pts if ids[p.id] == s) for s in ("train", "holdout")},
                 "fit_years": [FIT_YEARS.start, FIT_YEARS.stop - 1], "eval": "IV-X 2024-2026", "zones": {}}
    for k, name in enumerate(names):
        a, b = mod[:, ev, k].ravel(), em[:, ev, k].ravel()
        ok = np.isfinite(a) & np.isfinite(b)
        cls_a, cls_b = water_class(a[ok]), water_class(b[ok])
        per_point = [_corr(am[i, ev, k], ae[i, ev, k]) for i in range(len(pts))]
        res["zones"][name] = {
            "r_values": _corr(a, b),
            "r_anomalies": _corr(am[:, ev, k].ravel(), ae[:, ev, k].ravel()),
            "r_anomalies_median_point": float(np.nanmedian(per_point)),
            "r_change_7d": _corr(dm[:, ev7, k].ravel(), de[:, ev7, k].ravel()),
            "rmse": float(np.sqrt(np.mean((a[ok] - b[ok]) ** 2))),
            "same_class": float((cls_a == cls_b).mean()),
            "within_one_class": float((np.abs(cls_a - cls_b) <= 1).mean()),
        }
    return res


# --------------------------------------------------------------------------- forecast
def forecast() -> dict:
    pts, ids, dates, P, E, TH, awc, taw = load_set()
    prm = day_params(dates)
    D3, D1 = run_models(P, E, awc, taw, prm)
    mod = model_states(D3, D1, awc, taw)
    years = dates.astype("datetime64[Y]").astype(int) + 1970
    em = cdf_match(era5_states(TH), mod, np.isin(years, list(FIT_YEARS)))
    cal = EnsCalibration.load()
    H = N_DAYS
    zsel = [0, 1, 3]  # 0-28, 28-100, 0-100 (3-zone)
    acc = {k: {m: [] for m in ("ens", "analog", "persist", "obs_weather")} for k in zsel}
    corr_rows = {k: {"ens_median": [], "truth": []} for k in zsel}
    n_fc = 0
    for y in (2025, 2026):
        zf = DATA_DIR / "ens_archive" / f"ens_{y}.npz"
        if not zf.exists():
            continue
        z = np.load(zf)
        Ez = {k: z[k] for k in z.files}
        for b, p in enumerate(pts):
            if ids[p.id] != "holdout":
                continue
            hit = np.where(Ez["point_ids"] == p.id)[0]
            if not len(hit):
                continue
            j = int(hit[0])
            for i, init in enumerate(Ez["init_dates"]):
                t = int((np.datetime64(str(init), "D") + 1 - dates[0]).astype(int))  # first forecast day
                if t < 1 or t + H > len(dates) or not np.isfinite(em[b, t - 1 : t + H, zsel]).all():
                    continue
                d0 = D3[b, t - 1]
                sl = slice(t, t + H)
                frac, kc, ft = prm["frac"][sl], prm["kc"][sl], prm["f_t"][sl]
                p_tab = float(prm["p"][t])
                pe = Ez["precip"][i, j][:, 1 : H + 1]
                ee = cal.et0(makkink_et0(Ez["t2m"][i, j][:, 1 : H + 1], Ez["rs"][i, j][:, 1 : H + 1])) if cal else None
                ap, ae = [], []
                for yy in range(2016, 2027):
                    if yy == y:
                        continue
                    try:
                        s = np.datetime64(dt.date.fromisoformat(str(dates[t])).replace(year=yy), "D")
                    except ValueError:
                        continue
                    k0 = int((s - dates[0]).astype(int))
                    if 0 <= k0 and k0 + H <= len(dates) and np.isfinite(P[b, k0 : k0 + H]).all() and np.isfinite(E[b, k0 : k0 + H]).all():
                        ap.append(P[b, k0 : k0 + H])
                        ae.append(E[b, k0 : k0 + H])
                if len(ap) < 5 or ee is None or not np.isfinite(pe).all():
                    continue
                runs = {"ens": run_members(d0, awc[b], frac, kc, ft, p_tab, pe, ee),
                        "analog": run_members(d0, awc[b], frac, kc, ft, p_tab, np.array(ap), np.array(ae))}
                rw0 = mod[b, t - 1]
                truth = em[b, t : t + H] - em[b, t - 1][None]  # (H, 5)
                for k in zsel:
                    tr = truth[:, k]
                    for m, r in runs.items():
                        if k == 3:
                            fz = (r["rw"][..., 0] * awc[b, 0] + r["rw"][..., 1] * awc[b, 1]) / (awc[b, 0] + awc[b, 1])
                        else:
                            fz = r["rw"][..., k]
                        dlt = fz - rw0[k]
                        acc[k][m].append(crps_ensemble(dlt, tr))
                        if m == "ens":
                            corr_rows[k]["ens_median"].append(np.median(dlt, axis=0))
                            corr_rows[k]["truth"].append(tr)
                    acc[k]["persist"].append(np.abs(tr))
                    acc[k]["obs_weather"].append(np.abs((mod[b, t : t + H, k] - rw0[k]) - tr))
                n_fc += 1
    res: dict = {"n_forecasts": n_fc, "points": "hold-out", "years": [2025, 2026], "target": "change of the relative water after 1..14 days vs ERA5-Land (CDF-matched)", "zones": {}}
    for k in zsel:
        sc = {m: np.mean(v, axis=0) for m, v in acc[k].items()}
        em_, tr_ = np.array(corr_rows[k]["ens_median"]), np.array(corr_rows[k]["truth"])
        res["zones"][ZONES[k]] = {
            "crps_or_mae": {m: np.round(v, 4).tolist() for m, v in sc.items()},
            "skill_ens_vs_analog": np.round(1 - sc["ens"] / sc["analog"], 3).tolist(),
            "skill_ens_vs_persistence": np.round(1 - sc["ens"] / sc["persist"], 3).tolist(),
            "skill_obs_weather_vs_persistence": np.round(1 - sc["obs_weather"] / sc["persist"], 3).tolist(),
            "r_ens_median_vs_era5_change": [round(_corr(em_[:, h], tr_[:, h]), 3) for h in range(H)],
        }
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["structure", "forecast", "all"])
    a = ap.parse_args()
    rep = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    if a.cmd in ("structure", "all"):
        rep["structure"] = structure()
        for name, r in rep["structure"]["zones"].items():
            print(f"{name:28s} r {r['r_values']:.2f}  r_anom {r['r_anomalies']:.2f} (median point {r['r_anomalies_median_point']:.2f})  "
                  f"r_d7 {r['r_change_7d']:.2f}  rmse {r['rmse']:.3f}  same class {r['same_class']:.0%}  +-1 {r['within_one_class']:.0%}")
    if a.cmd in ("forecast", "all"):
        rep["forecast"] = forecast()
        print(f"forecasts: {rep['forecast']['n_forecasts']}")
        for name, r in rep["forecast"]["zones"].items():
            print(f"== {name}: skill ENS vs analog   " + " ".join(f"{x:+.2f}" for x in r["skill_ens_vs_analog"]))
            print(f"   {'':8s} skill ENS vs persistence " + " ".join(f"{x:+.2f}" for x in r["skill_ens_vs_persistence"]))
            print(f"   {'':8s} obs weather vs persist.  " + " ".join(f"{x:+.2f}" for x in r["skill_obs_weather_vs_persistence"]))
            print(f"   {'':8s} r(ENS median, ERA5)      " + " ".join(f"{x:.2f}" for x in r["r_ens_median_vs_era5_change"]))
    OUT.write_text(json.dumps(rep, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
