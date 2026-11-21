"""E9: 14-day irrigation plan from the ECMWF ensemble vs analog years (hold-out 2025-2026).

For every crop and weekly start day t (ECMWF ENS run 00 UTC t-1) the deployed policy plans 14 days
on every scenario: the 51 ENS members (precipitation raw, ET0 calibrated Makkink; climatology after
day 14 for the forecast windows of late plan days) or the 10 analog years (E8). Reference: what the
policy really does when it decides every day with that day's forecast and the true weather.

Metrics by plan day: Brier score of the irrigation-day probability (and skill vs analog and vs the
base rate), recall at p >= 0.5; 14-day water need: MAE and P10-P90 coverage.

    uv run --group train python ml/rl_irrigator/scripts/plan_ens_study.py
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kowr_rl import ARTIFACT_MODELS_DIR, DATA_DIR, REPORTS_DIR  # noqa: E402
from kowr_rl.config import load_crops  # noqa: E402
from kowr_rl.dataset import factorial_specs, load_points  # noqa: E402
from kowr_rl.forecast14 import EnsCalibration, makkink_et0  # noqa: E402
from kowr_rl.plan import analog_weather, closed_loop_states, make_plan, scenario_plans  # noqa: E402
from kowr_rl.policy import load_policy  # noqa: E402
from kowr_rl.simulator import N_FC, build_batch  # noqa: E402

H = 14
CROPS = ["kukurydza", "ziemniak", "pszenica_ozima", "burak_cukrowy", "uzytki_zielone", "truskawki"]


def main() -> None:
    pts = load_points(str(DATA_DIR / "holdout"))
    pmap = {p.id: p for p in pts}
    crops = load_crops()
    pol = load_policy(ARTIFACT_MODELS_DIR / "ppo_irrigator_weights.npz")
    cal = EnsCalibration.load()
    acc = {v: {"brier": np.zeros(H), "tp": np.zeros(H), "act": np.zeros(H), "plan": np.zeros(H), "w_err": [], "w_cov": []} for v in ("ens", "analog")}
    base = {"act_days": [], "n": 0}
    for y in (2025, 2026):
        z = np.load(DATA_DIR / "ens_archive" / f"ens_{y}.npz")
        E = {k: z[k] for k in z.files}
        for ck in CROPS:
            batch = build_batch(factorial_specs(pts, [y], [ck], [1, 2, 3, 4], [150.0], forecast="real"))
            col = np.array([int(np.where(E["point_ids"] == m["point"])[0][0]) for m in batch.meta])
            dr_s, bl_s, irr_s = closed_loop_states(batch, pol)
            ana_p, ana_e = analog_weather(batch, pmap, range(2016, 2027))
            s = crops[ck].season_start(y)
            L = int(batch.length[0])
            for i, init in enumerate(E["init_dates"]):
                t0 = (dt.date.fromisoformat(str(init)) + dt.timedelta(days=1) - s).days
                if t0 < 0 or t0 + H > L:
                    continue
                dr0, bl0 = dr_s[:, t0], bl_s[:, t0]
                actual = irr_s[:, t0 : t0 + H]
                span = H + N_FC
                clim_p = np.pad(batch.clim_p[:, t0 : t0 + span], ((0, 0), (0, max(0, span - batch.clim_p[:, t0 : t0 + span].shape[1]))), mode="edge")
                clim_e = np.pad(batch.clim_e[:, t0 : t0 + span], ((0, 0), (0, max(0, span - batch.clim_e[:, t0 : t0 + span].shape[1]))), mode="edge")
                p_m = E["precip"][i][col][:, :, 1 : H + 1]  # (B, M, 14)
                e_m = cal.et0(makkink_et0(E["t2m"][i][col][:, :, 1 : H + 1], E["rs"][i][col][:, :, 1 : H + 1]))
                plans_e = []
                for m in range(p_m.shape[1]):
                    ap, ae = clim_p.copy(), clim_e.copy()
                    ap[:, :H], ae[:, :H] = p_m[:, m], e_m[:, m]
                    plans_e.append(make_plan(batch, pol, t0, dr0, bl0, H, precip_issued=ap, et0_issued=ae, keep_today_forecast=True))
                plans = {"ens": np.stack(plans_e), "analog": scenario_plans(batch, pol, t0, dr0, bl0, ana_p, ana_e, H)}
                ad = actual > 0
                for v, sp in plans.items():
                    a = acc[v]
                    prob = (sp > 0).mean(axis=0)
                    a["brier"] += ((prob - ad) ** 2).sum(axis=0)
                    a["tp"] += ((prob >= 0.5) & ad).sum(axis=0)
                    a["act"] += ad.sum(axis=0)
                    a["plan"] += (prob >= 0.5).sum(axis=0)
                    tot = sp.sum(axis=2)
                    act_tot = actual.sum(axis=1)
                    a["w_err"].append(np.abs(tot.mean(axis=0) - act_tot))
                    a["w_cov"].append((act_tot >= np.percentile(tot, 10, axis=0)) & (act_tot <= np.percentile(tot, 90, axis=0)))
                base["act_days"].append(ad)
                base["n"] += len(ad)
    ad_all = np.concatenate(base["act_days"])
    rate = ad_all.mean(axis=0)
    brier_clim = ((rate[None, :] - ad_all) ** 2).mean(axis=0)
    out = {"n_plans": int(base["n"]), "crops": CROPS, "budget_mm": 150, "irrigation_day_rate": rate.round(4).tolist(), "methods": {}}
    for v, a in acc.items():
        br = a["brier"] / base["n"]
        out["methods"][v] = {
            "brier_by_day": br.round(4).tolist(),
            "brier_skill_vs_base_rate": (1 - br / brier_clim).round(3).tolist(),
            "recall_p_ge_0_5": (a["tp"] / np.maximum(a["act"], 1)).round(3).tolist(),
            "precision_p_ge_0_5": (a["tp"] / np.maximum(a["plan"], 1)).round(3).tolist(),
            "water_14d_mae_mm": float(np.concatenate(a["w_err"]).mean()),
            "water_14d_p10_p90_coverage": float(np.concatenate(a["w_cov"]).mean()),
        }
    out["brier_skill_ens_vs_analog"] = (1 - np.array(out["methods"]["ens"]["brier_by_day"]) / np.maximum(np.array(out["methods"]["analog"]["brier_by_day"]), 1e-9)).round(3).tolist()
    (REPORTS_DIR / "plan14_ens_holdout.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"plans: {base['n']}")
    for v in ("ens", "analog"):
        r = out["methods"][v]
        print(f"== {v:6s} Brier skill vs base rate: " + " ".join(f"{x:+.2f}" for x in r["brier_skill_vs_base_rate"]))
        print(f"   {'':6s} recall p>=0.5          : " + " ".join(f"{x:.2f}" for x in r["recall_p_ge_0_5"]))
        print(f"   {'':6s} water 14 d: MAE {r['water_14d_mae_mm']:.1f} mm, P10-P90 coverage {r['water_14d_p10_p90_coverage']:.0%}")
    print("== Brier skill ENS vs analog:", " ".join(f"{x:+.2f}" for x in out["brier_skill_ens_vs_analog"]))


if __name__ == "__main__":
    main()
