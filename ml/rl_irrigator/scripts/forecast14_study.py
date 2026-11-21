"""E9: 14-day soil-water forecast study (three-zone model).

``collect``: for every point (its IUNG category + SoilGrids profile), crop and weekly start day t
(ECMWF ENS run of 00 UTC t-1), the state on t comes from the model run on the observed weather; the
next 14 days are forecast with
  * ``ens``     the 51 ECMWF members (ET0 from calibrated Makkink, raw precipitation),
  * ``analog``  the same calendar days of the other 10 years (the E8 method),
  * ``persist`` the state of day t kept for 14 days.
"Truth" = the model on the observed weather (independent ERA5-Land check: ``era5_check.py``).
Saved to data/forecast14/<dataset>.npz.

``fit``   (training points, 2024 = validation year): per lead day the spread inflation lambda of the
          ENS root-zone forecast and the weight w of a mixture ENS / analog that minimise the CRPS ->
          config/forecast14_postproc.json.
``test``  (hold-out points, 2025-2026): raw ENS, post-processed ENS, analog, persistence: CRPS,
          P10-P90 coverage and Brier score of crop stress, skill vs analog -> reports/forecast14.json.

    uv run --group train python ml/rl_irrigator/scripts/forecast14_study.py collect --dataset train_points
    uv run --group train python ml/rl_irrigator/scripts/forecast14_study.py collect --dataset holdout
    uv run --group train python ml/rl_irrigator/scripts/forecast14_study.py fit
    uv run --group train python ml/rl_irrigator/scripts/forecast14_study.py test
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kowr_rl import CONFIG_DIR, DATA_DIR, REPORTS_DIR  # noqa: E402
from kowr_rl.config import load_crops, load_soils  # noqa: E402
from kowr_rl.dataset import load_points  # noqa: E402
from kowr_rl.forecast14 import N_DAYS, EnsCalibration, crps_ensemble, makkink_et0, run_members  # noqa: E402
from kowr_rl.soil3 import canopy_share, relative_water, root_depth_curve, root_fractions, spin_up3, step3, zone_awc_from_profile  # noqa: E402

H = N_DAYS
CROPS = ["kukurydza", "ziemniak", "pszenica_ozima", "burak_cukrowy", "uzytki_zielone", "truskawki"]
OUT = DATA_DIR / "forecast14"
POST = CONFIG_DIR / "forecast14_postproc.json"
LAMBDAS = np.array([1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0])
WEIGHTS = np.array([0.0, 0.25, 0.5, 0.75, 1.0])  # weight of the ENS part in the ENS/analog mixture


def series(w, start: dt.date, n: int) -> tuple[np.ndarray, np.ndarray]:
    dates = np.asarray(w.dates, dtype="datetime64[D]")
    want = np.datetime64(start) + np.arange(n)
    idx = np.clip(np.searchsorted(dates, want), 0, len(dates) - 1)
    hit = dates[idx] == want
    p, e = np.full(n, np.nan), np.full(n, np.nan)
    p[hit], e[hit] = np.asarray(w.precip)[idx[hit]], np.asarray(w.et0)[idx[hit]]
    return p, e


def season_truth(pt, c, y: int, awc: np.ndarray) -> tuple[np.ndarray, np.ndarray] | None:
    s = c.season_start(y)
    pre0 = dt.date(y - 1, 8, 1)
    pp, pe = series(pt.weather, pre0, (s - pre0).days)
    d = spin_up3(pp[None], pe[None], awc[None])[0]
    sp, se = series(pt.weather, s, c.length)
    if np.isnan(sp).any() or np.isnan(se).any():
        return None
    kc, ft, fr = c.kc_curve(), canopy_share(c), root_fractions(root_depth_curve(c))
    D = np.zeros((c.length + 1, 3))
    ks = np.zeros(c.length)
    D[0] = d
    for t in range(c.length):
        st = step3(D[t][None], awc[None], fr[t][None], sp[t:t + 1], np.zeros(1), se[t:t + 1], kc[t:t + 1], ft[t:t + 1], np.array([c.p]))
        D[t + 1], ks[t] = st.d[0], st.ks[0]
    return D, ks


def collect(dataset: str) -> None:
    crops, soils = load_crops(), load_soils()["categories"]
    cal = EnsCalibration.load()
    prof_path = DATA_DIR / "soils" / "soilgrids_profiles.json"
    profiles = json.loads(prof_path.read_text(encoding="utf-8"))["points"] if prof_path.exists() else {}
    pts = load_points() if dataset == "train_points" else load_points(str(DATA_DIR / "holdout"))
    years = [2024] if dataset == "train_points" else [2025, 2026]
    rec = {k: [] for k in ("ens_root", "ens_stress", "ens_zone", "ana_root", "ana_stress", "ana_zone", "true_root", "true_stress",
                           "true_zone", "init_root", "init_zone", "init_stress", "meta")}
    n_ana = 10
    for y in years:
        z = np.load(DATA_DIR / "ens_archive" / f"ens_{y}.npz")
        E = {k: z[k] for k in z.files}
        for pt in pts:
            j = int(np.where(E["point_ids"] == pt.id)[0][0])
            cat = int(pt.iung_code) if int(pt.iung_code) in soils else 2
            awc = zone_awc_from_profile(profiles.get(pt.id), float(soils[cat]["taw_mm"]))
            for ck in CROPS:
                c = crops[ck]
                tr = season_truth(pt, c, y, awc)
                if tr is None:
                    continue
                D, ks = tr
                s = c.season_start(y)
                kc, ft, fr = c.kc_curve(), canopy_share(c), root_fractions(root_depth_curve(c))
                for i, init in enumerate(E["init_dates"]):
                    t0 = (dt.date.fromisoformat(str(init)) + dt.timedelta(days=1) - s).days
                    if t0 < 0 or t0 + H > c.length:
                        continue
                    frac, kcw, ftw = fr[t0:t0 + H], kc[t0:t0 + H], ft[t0:t0 + H]
                    p_ens = E["precip"][i, j][:, 1:H + 1]
                    mk = makkink_et0(E["t2m"][i, j][:, 1:H + 1], E["rs"][i, j][:, 1:H + 1])
                    e_ens = cal.et0(mk) if cal is not None else mk
                    ap_, ae_ = [], []
                    for yy in range(2016, 2027):
                        if yy == y:
                            continue
                        a1, a2 = series(pt.weather, (s + dt.timedelta(days=t0)).replace(year=yy), H)
                        if np.isfinite(a1).all() and np.isfinite(a2).all():
                            ap_.append(a1)
                            ae_.append(a2)
                    if len(ap_) < 5:
                        continue
                    while len(ap_) < n_ana:  # pad by repeating (keeps arrays rectangular)
                        ap_.append(ap_[len(ap_) % 5])
                        ae_.append(ae_[len(ae_) % 5])
                    re = run_members(D[t0], awc, frac, kcw, ftw, c.p, p_ens, e_ens)
                    ra = run_members(D[t0], awc, frac, kcw, ftw, c.p, np.array(ap_[:n_ana]), np.array(ae_[:n_ana]))
                    Dt = D[t0 + 1 : t0 + H + 1]
                    taw_r = (frac * awc[None]).sum(axis=1)
                    rec["ens_root"].append(re["rw_root"].astype(np.float32))
                    rec["ens_stress"].append(re["stress"].mean(axis=0).astype(np.float32))
                    rec["ens_zone"].append(re["rw"].astype(np.float32))
                    rec["ana_root"].append(ra["rw_root"].astype(np.float32))
                    rec["ana_stress"].append(ra["stress"].mean(axis=0).astype(np.float32))
                    rec["ana_zone"].append(ra["rw"].astype(np.float32))
                    rec["true_root"].append(np.clip(1 - (frac * Dt).sum(axis=1) / np.maximum(taw_r, 1e-9), 0, 1).astype(np.float32))
                    rec["true_stress"].append((ks[t0:t0 + H] < 1 - 1e-9).astype(np.float32))
                    rec["true_zone"].append(relative_water(Dt, awc[None]).astype(np.float32))
                    rec["init_root"].append(np.float32(np.clip(1 - (frac[0] * D[t0]).sum() / max(taw_r[0], 1e-9), 0, 1)))
                    rec["init_zone"].append(relative_water(D[t0], awc).astype(np.float32))
                    rec["init_stress"].append(np.float32(ks[max(t0 - 1, 0)] < 1 - 1e-9))
                    rec["meta"].append(f"{pt.id}|{y}|{ck}|{init}|{cat}")
    OUT.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(OUT / f"{dataset}.npz", **{k: np.asarray(v) for k, v in rec.items()})
    print(f"{dataset}: {len(rec['meta'])} forecasts saved")


def inflate(mem: np.ndarray, lam: np.ndarray) -> np.ndarray:
    """mem (N, M, H) -> median + lam_h (mem - median), clipped to [0, 1]."""
    med = np.median(mem, axis=1, keepdims=True)
    return np.clip(med + lam[None, None, :] * (mem - med), 0.0, 1.0)


def mixture_crps(ens: np.ndarray, ana: np.ndarray, y: np.ndarray, w: float) -> np.ndarray:
    """CRPS of the weighted mixture (weight w on the ENS members): ens (N, M1, H), ana (N, M2, H), y (N, H) -> (N, H)."""
    m1, m2 = ens.shape[1], ana.shape[1]
    x = np.concatenate([ens, ana], axis=1)
    wt = np.concatenate([np.full(m1, w / m1), np.full(m2, (1 - w) / m2)])
    t1 = (wt[None, :, None] * np.abs(x - y[:, None, :])).sum(axis=1)
    diff = np.abs(x[:, :, None, :] - x[:, None, :, :])  # (N, M, M, H)
    t2 = 0.5 * np.einsum("i,j,nijh->nh", wt, wt, diff)
    return t1 - t2


def mixture_quantiles(ens: np.ndarray, ana: np.ndarray, w: float, qs: list[float]) -> np.ndarray:
    m1, m2 = ens.shape[1], ana.shape[1]
    x = np.concatenate([ens, ana], axis=1)
    wt = np.concatenate([np.full(m1, w / m1), np.full(m2, (1 - w) / m2)])
    order = np.argsort(x, axis=1)
    xs = np.take_along_axis(x, order, axis=1)
    cw = np.cumsum(wt[order], axis=1)
    out = []
    for q in qs:
        idx = (cw >= q - 1e-12).argmax(axis=1)
        out.append(np.take_along_axis(xs, idx[:, None, :], axis=1)[:, 0, :])
    return np.stack(out)


def fit() -> None:
    z = np.load(OUT / "train_points.npz")
    ens, ana, y = z["ens_root"], z["ana_root"], z["true_root"]
    lam_best, w_best, rows = np.ones(H), np.ones(H), []
    rng = np.random.default_rng(0)
    sub = rng.choice(len(y), size=min(len(y), 3000), replace=False)  # mixture CRPS is O(M^2)
    for h in range(H):
        best = (np.inf, 1.0, 1.0)
        for lam in LAMBDAS:
            e = inflate(ens[sub][:, :, h : h + 1], np.array([lam]))
            for w in WEIGHTS:
                cr = mixture_crps(e, ana[sub][:, :, h : h + 1], y[sub][:, h : h + 1], float(w)).mean()
                if cr < best[0]:
                    best = (cr, lam, w)
        lam_best[h], w_best[h] = best[1], best[2]
        rows.append({"lead": h + 1, "crps": float(best[0]), "lambda": float(best[1]), "w_ens": float(best[2])})
        print(f"lead {h + 1:2d}: lambda {best[1]:.2f}  w_ens {best[2]:.2f}  CRPS {best[0]:.4f}")
    # conformal widening of the P10-P90 interval around the median (target coverage 80 %)
    scale = np.ones(H)
    ens_pp = inflate(ens, lam_best)
    for h in range(H):
        q = mixture_quantiles(ens_pp[:, :, h : h + 1], ana[:, :, h : h + 1], float(w_best[h]), [0.1, 0.5, 0.9])[:, :, 0]
        for c in np.arange(1.0, 4.01, 0.05):
            lo, hi = q[1] - c * (q[1] - q[0]) - 0.01 * (c - 1), q[1] + c * (q[2] - q[1]) + 0.01 * (c - 1)
            if ((y[:, h] >= lo - 1e-9) & (y[:, h] <= hi + 1e-9)).mean() >= 0.80:
                scale[h] = c
                break
        else:
            scale[h] = 4.0
    print("interval scale:", " ".join(f"{x:.2f}" for x in scale))
    POST.write_text(json.dumps({"fitted_on": "training points, 2024 (validation year)", "n_forecasts": int(len(y)),
                                "lambda": lam_best.tolist(), "w_ens": w_best.tolist(), "interval_scale": scale.tolist(),
                                "interval_note": "P10/P90 = median -/+ scale x (median - q10 / q90 - median) +/- 0.01 (scale - 1)", "rows": rows}, indent=1), encoding="utf-8")
    print("saved", POST)


def test() -> None:
    z = np.load(OUT / "holdout.npz")
    post = json.loads(POST.read_text(encoding="utf-8"))
    lam, w = np.array(post["lambda"]), np.array(post["w_ens"])
    ens, ana, y = z["ens_root"], z["ana_root"], z["true_root"]
    ens_pp = inflate(ens, lam)
    N = len(y)
    res = {"n_forecasts": int(N), "crops": CROPS, "postproc": post, "methods": {}}
    persist = np.repeat(z["init_root"][:, None], H, axis=1)
    crps = {
        "ens_raw": crps_ensemble(np.moveaxis(ens, 1, 0), y).mean(axis=0),
        "analog": crps_ensemble(np.moveaxis(ana, 1, 0), y).mean(axis=0),
        "persist": np.abs(persist - y).mean(axis=0),
    }
    pp = np.zeros(H)
    cover = {"ens_raw": [], "analog": [], "ens_pp": []}
    for h in range(H):
        pp[h] = mixture_crps(ens_pp[:, :, h : h + 1], ana[:, :, h : h + 1], y[:, h : h + 1], float(w[h])).mean()
    crps["ens_pp"] = pp
    for name, (e_, a_, ww) in {"ens_raw": (ens, ana, np.ones(H)), "analog": (ens, ana, np.zeros(H)), "ens_pp": (ens_pp, ana, w)}.items():
        for h in range(H):
            q = mixture_quantiles(e_[:, :, h : h + 1], a_[:, :, h : h + 1], float(ww[h]), [0.1, 0.9])
            cover[name].append(float(((y[:, h] >= q[0][:, 0] - 1e-9) & (y[:, h] <= q[1][:, 0] + 1e-9)).mean()))
    sc = np.array(post["interval_scale"])
    cov_cal, width = [], []
    for h in range(H):
        q = mixture_quantiles(ens_pp[:, :, h : h + 1], ana[:, :, h : h + 1], float(w[h]), [0.1, 0.5, 0.9])[:, :, 0]
        lo = np.clip(q[1] - sc[h] * (q[1] - q[0]) - 0.01 * (sc[h] - 1), 0, 1)
        hi = np.clip(q[1] + sc[h] * (q[2] - q[1]) + 0.01 * (sc[h] - 1), 0, 1)
        cov_cal.append(float(((y[:, h] >= lo - 1e-9) & (y[:, h] <= hi + 1e-9)).mean()))
        width.append(float((hi - lo).mean()))
    cover["ens_pp_conformal"] = cov_cal
    # stress probability (Brier) and zones
    st_true = z["true_stress"]
    brier = {"ens": ((z["ens_stress"] - st_true) ** 2).mean(axis=0), "analog": ((z["ana_stress"] - st_true) ** 2).mean(axis=0),
             "persist": ((np.repeat(z["init_stress"][:, None], H, axis=1) - st_true) ** 2).mean(axis=0),
             "climatology": ((st_true.mean(axis=0, keepdims=True) - st_true) ** 2).mean(axis=0)}
    zone_crps = {}
    for name, key in (("ens", "ens_zone"), ("analog", "ana_zone")):
        zone_crps[name] = np.stack([crps_ensemble(np.moveaxis(z[key][..., k], 1, 0), z["true_zone"][..., k]).mean(axis=0) for k in range(3)], axis=1)
    res["methods"] = {
        "crps_root": {k: v.round(5).tolist() for k, v in crps.items()},
        "skill_crps_root_vs_analog": {k: (1 - v / crps["analog"]).round(3).tolist() for k, v in crps.items()},
        "p10_p90_coverage_root": {k: np.round(v, 3).tolist() for k, v in cover.items()},
        "brier_stress": {k: v.round(4).tolist() for k, v in brier.items()},
        "skill_brier_stress_vs_analog": {k: (1 - v / brier["analog"]).round(3).tolist() for k, v in brier.items()},
        "skill_crps_zones_vs_analog": (1 - zone_crps["ens"] / zone_crps["analog"]).round(3).tolist(),
        "stress_rate_by_day": st_true.mean(axis=0).round(3).tolist(),
        "interval_width_root_conformal": np.round(width, 3).tolist(),
    }
    (REPORTS_DIR / "forecast14.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    print(f"hold-out 2025-2026: {N} forecasts")
    for k in ("ens_raw", "ens_pp", "persist"):
        print(f"  skill CRPS root vs analog {k:8s}: " + " ".join(f"{x:+.2f}" for x in res["methods"]["skill_crps_root_vs_analog"][k]))
    for k in ("ens_raw", "ens_pp", "ens_pp_conformal", "analog"):
        print(f"  P10-P90 coverage {k:8s}: " + " ".join(f"{x:.2f}" for x in res["methods"]["p10_p90_coverage_root"][k]))
    print("  skill Brier stress ens vs analog: " + " ".join(f"{x:+.2f}" for x in res["methods"]["skill_brier_stress_vs_analog"]["ens"]))
    print("  skill Brier stress ens vs clim  : " + " ".join(f"{1 - a / c:+.2f}" for a, c in zip(brier["ens"], brier["climatology"])))
    print("  skill CRPS zones (top/root/deep) ens vs analog, days 1/7/14: " + " | ".join(
        "/".join(f"{x:+.2f}" for x in res["methods"]["skill_crps_zones_vs_analog"][h]) for h in (0, 6, 13)))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["collect", "fit", "test"])
    ap.add_argument("--dataset", choices=["train_points", "holdout"], default="holdout")
    a = ap.parse_args()
    {"collect": lambda: collect(a.dataset), "fit": fit, "test": test}[a.cmd]()


if __name__ == "__main__":
    main()
