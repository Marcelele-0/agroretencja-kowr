"""E9: calibrate the archived ECMWF ENS forecasts (fit on 2024) and check them on 2025-2026.

* precipitation: per-lead quantile mapping of the member values to the observed daily climate;
* ET0: Makkink (2 m temperature + short-wave radiation) -> linear per lead to FAO-56 ET0.
Verification per lead day (1..14), against the observed archive at the hold-out points: CRPS of the
ensemble vs a climatological ensemble (same calendar days of the other years), and its skill score.

Output: config/ens_calibration.json (runtime) and reports/ens_calibration.json.

    uv run --group train python ml/rl_irrigator/scripts/ens_calibrate.py
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kowr_rl import DATA_DIR, REPORTS_DIR  # noqa: E402
from kowr_rl.dataset import load_points  # noqa: E402
from kowr_rl.forecast14 import CAL_PATH, N_DAYS, EnsCalibration, crps_ensemble, fit_calibration, makkink_et0  # noqa: E402


def load_year(year: int) -> dict:
    z = np.load(DATA_DIR / "ens_archive" / f"ens_{year}.npz")
    return {k: z[k] for k in z.files}


def obs_matrix(points: dict, ids: np.ndarray, inits: np.ndarray, var: str) -> np.ndarray:
    """(I, P, 14) observed values for lead days 1..14 after each init date."""
    out = np.full((len(inits), len(ids), N_DAYS), np.nan)
    for j, pid in enumerate(ids):
        if pid not in points:
            continue
        w = points[pid].weather
        dates = np.asarray(w.dates, dtype="datetime64[D]")
        series = np.asarray(w.precip if var == "precip" else w.et0)
        for i, d0 in enumerate(inits):
            want = np.datetime64(d0) + np.arange(1, N_DAYS + 1)
            idx = np.clip(np.searchsorted(dates, want), 0, len(dates) - 1)
            hit = dates[idx] == want
            out[i, j, hit] = series[idx[hit]]
    return out


def clim_members(points: dict, ids: np.ndarray, inits: np.ndarray, var: str, years: range) -> np.ndarray:
    """(I, P, Y, 14) the same calendar days in the other years (climatological ensemble)."""
    out = np.full((len(inits), len(ids), len(years), N_DAYS), np.nan)
    for j, pid in enumerate(ids):
        if pid not in points:
            continue
        w = points[pid].weather
        dates = np.asarray(w.dates, dtype="datetime64[D]")
        series = np.asarray(w.precip if var == "precip" else w.et0)
        for i, d0 in enumerate(inits):
            base = dt.date.fromisoformat(str(d0))
            for yi, y in enumerate(years):
                if y == base.year:
                    continue
                try:
                    start = np.datetime64(base.replace(year=y)) + np.arange(1, N_DAYS + 1)
                except ValueError:
                    continue
                idx = np.clip(np.searchsorted(dates, start), 0, len(dates) - 1)
                hit = dates[idx] == start
                out[i, j, yi, hit] = series[idx[hit]]
    return out


def main() -> None:
    pts = {p.id: p for p in load_points()} | {p.id: p for p in load_points(str(DATA_DIR / "holdout"))}
    cal = load_year(2024)
    ids = cal["point_ids"]
    inits = cal["init_dates"]
    fc_p = cal["precip"][..., 1 : N_DAYS + 1]  # (I, P, M, 14)
    fc_mk = makkink_et0(cal["t2m"][..., 1 : N_DAYS + 1], cal["rs"][..., 1 : N_DAYS + 1])
    obs_p = obs_matrix(pts, ids, inits, "precip")
    obs_e = obs_matrix(pts, ids, inits, "et0")
    I, P, M, _ = fc_p.shape
    table = fit_calibration(fc_p.reshape(I * P, M, N_DAYS), obs_p.reshape(I * P, N_DAYS), fc_mk.reshape(I * P, M, N_DAYS), obs_e.reshape(I * P, N_DAYS))
    table["fitted_on"] = {"year": 2024, "inits": int(I), "points": int(P), "members": int(M), "note": "weekly 00 UTC runs; lead day k = UTC day init+k"}
    CAL_PATH.write_text(json.dumps(table), encoding="utf-8")
    calib = EnsCalibration(table)

    # ---- verification on the test years at the hold-out points
    hold_ids = {p.id for p in load_points(str(DATA_DIR / "holdout"))}
    report: dict = {"fitted_on": table["fitted_on"], "test": {}}
    for year in (2025, 2026):
        z = load_year(year)
        sel = np.array([pid in hold_ids for pid in z["point_ids"]])
        ids_y, inits_y = z["point_ids"][sel], z["init_dates"]
        p_raw = z["precip"][:, sel][..., 1 : N_DAYS + 1]
        p_cal = calib.precip(p_raw)
        mk = makkink_et0(z["t2m"][:, sel][..., 1 : N_DAYS + 1], z["rs"][:, sel][..., 1 : N_DAYS + 1])
        e_cal = calib.et0(mk)
        o_p = obs_matrix(pts, ids_y, inits_y, "precip")
        o_e = obs_matrix(pts, ids_y, inits_y, "et0")
        c_p = clim_members(pts, ids_y, inits_y, "precip", range(2016, 2027))
        c_e = clim_members(pts, ids_y, inits_y, "et0", range(2016, 2027))
        res = {}
        for name, fc, ob, cl in (("precip", (p_raw, p_cal), o_p, c_p), ("et0", (mk, e_cal), o_e, c_e)):
            rows = []
            for k in range(N_DAYS):
                o = ob[..., k]
                ok = np.isfinite(o)
                cr_raw = crps_ensemble(np.moveaxis(fc[0][..., k], 2, 0)[:, ok], o[ok]).mean()
                cr_cal = crps_ensemble(np.moveaxis(fc[1][..., k], 2, 0)[:, ok], o[ok]).mean()
                clm = np.moveaxis(cl[..., k], 2, 0)[:, ok]
                clm = np.where(np.isfinite(clm), clm, np.nanmean(clm, axis=0, keepdims=True))
                cr_cl = crps_ensemble(clm, o[ok]).mean()
                rows.append({"lead": k + 1, "crps_raw": float(cr_raw), "crps_cal": float(cr_cal), "crps_clim": float(cr_cl),
                             "skill_raw": float(1 - cr_raw / cr_cl), "skill_cal": float(1 - cr_cal / cr_cl),
                             "bias_ratio_raw": float(np.nanmean(fc[0][..., k].mean(axis=2)[ok]) / max(np.nanmean(o[ok]), 1e-9)),
                             "bias_ratio_cal": float(np.nanmean(fc[1][..., k].mean(axis=2)[ok]) / max(np.nanmean(o[ok]), 1e-9))})
            res[name] = rows
        report["test"][str(year)] = res
    (REPORTS_DIR / "ens_calibration.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    for year, res in report["test"].items():
        for name, rows in res.items():
            print(f"== {year} {name}: CRPS skill vs climatology by lead (raw / calibrated), bias raw->cal")
            print("   " + " ".join(f"{r['lead']}:{r['skill_raw']:+.2f}/{r['skill_cal']:+.2f}" for r in rows))
            print("   bias " + " ".join(f"{r['bias_ratio_raw']:.2f}->{r['bias_ratio_cal']:.2f}" for r in rows[::3]))


if __name__ == "__main__":
    main()
