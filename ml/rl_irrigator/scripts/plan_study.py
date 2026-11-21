"""E8: can a 14-day irrigation plan made in one morning work?

For each season and each plan start t0 (every 7 days, full 14-day window inside the season):
* reference (closed loop): the policy decides every day with that day's forecast and the true weather;
* plan: the same policy run forward on the weather assumed on t0 (forecast where available,
  day-of-year climatology beyond it), see ``kowr_rl.plan``;
* plan without forecast: climatology for all 14 days (shows what the forecast adds).

Metrics by plan day k = 1..14: recall / precision of the planned irrigation days against the closed
loop, |planned - actual| water accumulated by day k; and the cost of executing the plan blindly
(open loop) in the true weather: transpiration deficit (ETc - ETa) and deep percolation over the 14
days, compared with re-planning every day.

Forecast sources: ``--forecast prev7`` = real archived ECMWF IFS 0.25 leads 1-7 (Previous Runs),
climatology for days 8-14; ``--forecast hres14`` = real archived ECMWF HRES runs with leads 1-14
(Single Runs API, weekly plan starts; needs data/single_runs/single_runs.parquet).

    uv run --group train python ml/rl_irrigator/scripts/plan_study.py --dataset holdout --forecast prev7
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kowr_rl import ARTIFACT_MODELS_DIR, DATA_DIR, REPORTS_DIR  # noqa: E402
from kowr_rl.baselines import baseline_policies  # noqa: E402
from kowr_rl.config import load_crops, load_rl_config  # noqa: E402
from kowr_rl.dataset import factorial_specs, load_points  # noqa: E402
from kowr_rl.plan import analog_weather, closed_loop_states, make_plan, rollout, scenario_plans  # noqa: E402
from kowr_rl.policy import load_policy  # noqa: E402
from kowr_rl.simulator import build_batch  # noqa: E402

H = 14


def hres_issued(batch, t0: int, sr: pd.DataFrame | None) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(B, 14) precipitation and ET0 of the archived HRES run for plan start t0 (NaN if absent),
    and a (B,) mask of seasons that have that run."""
    P = np.full((batch.size, H), np.nan)
    E = np.full((batch.size, H), np.nan)
    has = np.zeros(batch.size, dtype=bool)
    if sr is None:
        return P, E, has
    for i, m in enumerate(batch.meta):
        day = (batch.start_dates[i] + np.timedelta64(t0, "D")).astype(str)
        g = sr.get((m["point"], day))
        if g is not None:
            P[i], E[i], has[i] = g[0], g[1], True
    return P, E, has


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", choices=["train_points", "holdout"], default="holdout")
    ap.add_argument("--years", type=int, nargs="+", default=None)
    ap.add_argument("--forecast", choices=["prev7", "hres14"], default="prev7")
    ap.add_argument("--budgets", type=float, nargs="+", default=[100.0, 250.0])
    ap.add_argument("--policies", nargs="+", default=["rl", "fao_small", "fao_rule"])
    ap.add_argument("--rl-weights", default=str(ARTIFACT_MODELS_DIR / "ppo_irrigator_weights.npz"))
    ap.add_argument("--tag", default="")
    args = ap.parse_args()

    cfg = load_rl_config()
    years = args.years or (cfg["split"]["test_years"] if args.dataset == "holdout" else cfg["split"]["val_years"])
    pts = load_points() if args.dataset == "train_points" else load_points(str(DATA_DIR / "holdout"))
    crops = list(load_crops())
    batch = build_batch(factorial_specs(pts, years, crops, [1, 2, 3, 4], args.budgets, forecast="real"))
    sr = None
    if args.forecast == "hres14":
        df = pd.read_parquet(DATA_DIR / "single_runs" / "single_runs.parquet").sort_values(["point", "t", "lead"])
        sr = {}
        for (pid, t), g in df.groupby(["point", "t"]):
            if len(g) == H:
                sr[(pid, t)] = (g.precip.to_numpy(), g.et0.to_numpy())
    pols = dict(baseline_policies())
    pols["rl"] = load_policy(args.rl_weights)
    L = batch.length
    point_map = {p.id: p for p in pts}
    ana_p, ana_e = analog_weather(batch, point_map, range(2016, 2027))
    out: dict = {"dataset": args.dataset, "years": years, "forecast": args.forecast, "n_seasons": int(batch.size), "policies": {}}
    for name in args.policies:
        pol = pols[name]
        dr_s, bl_s, irr_s = closed_loop_states(batch, pol)
        acc = {v: {"tp": np.zeros(H), "plan": np.zeros(H), "act": np.zeros(H), "agree": np.zeros(H), "n": np.zeros(H), "werr": np.zeros(H)}
               for v in ("forecast", "climatology")}
        tol1 = {v: np.zeros(H) for v in acc}  # planned irrigation day matched by an actual one within +-1 day
        scen = {"act": [], "mean": [], "p10": [], "p90": [], "prob": [], "act_days": []}
        cost = {k: [] for k in ("closed_def", "closed_dp", "closed_w", "open_def", "open_dp", "open_w", "clim_def", "clim_dp", "clim_w")}
        n_plans = 0
        for t0 in range(0, batch.horizon - H + 1, 7):
            ok = (t0 + H) <= L
            if args.forecast == "hres14":
                P, E, has = hres_issued(batch, t0, sr)
                ok &= has
            if not ok.any():
                continue
            idx = np.where(ok)[0]
            sub = batch.subset(idx)
            dr0, bl0 = dr_s[idx, t0], bl_s[idx, t0]
            kw = {}
            if args.forecast == "hres14":
                kw = {"precip_issued": P[idx], "et0_issued": E[idx]}
            plans = {"forecast": make_plan(sub, pol, t0, dr0, bl0, H, **kw),
                     "climatology": make_plan(sub, pol, t0, dr0, bl0, H, use_forecast=False)}
            actual = irr_s[idx, t0 : t0 + H]
            # analog-year scenarios after the forecast horizon
            sp = scenario_plans(sub, pol, t0, dr0, bl0, ana_p[:, idx], ana_e[:, idx], H,
                                precip_issued=P[idx] if args.forecast == "hres14" else None)
            tot = sp.sum(axis=2)  # (Y, B)
            scen["act"].append(actual.sum(axis=1))
            scen["mean"].append(tot.mean(axis=0))
            scen["p10"].append(np.percentile(tot, 10, axis=0))
            scen["p90"].append(np.percentile(tot, 90, axis=0))
            scen["prob"].append((sp > 0).mean(axis=0))  # (B, H)
            scen["act_days"].append(actual > 0)
            act_pad = np.pad(actual > 0, ((0, 0), (1, 1)))
            near = act_pad[:, :-2] | act_pad[:, 1:-1] | act_pad[:, 2:]
            for v, plan in plans.items():
                tol1[v] += ((plan > 0) & near).sum(axis=0)
            for v, plan in plans.items():
                a = acc[v]
                pi, ai = plan > 0, actual > 0
                a["tp"] += (pi & ai).sum(axis=0)
                a["plan"] += pi.sum(axis=0)
                a["act"] += ai.sum(axis=0)
                a["agree"] += (pi == ai).sum(axis=0)
                a["n"] += len(idx)
                a["werr"] += np.abs(np.cumsum(plan, axis=1) - np.cumsum(actual, axis=1)).sum(axis=0)
            closed = rollout(sub, pol, t0, H, dr0, bl0)
            opened = rollout(sub, None, t0, H, dr0, bl0, doses=plans["forecast"])
            climo = rollout(sub, None, t0, H, dr0, bl0, doses=plans["climatology"])
            for pre, r in (("closed", closed), ("open", opened), ("clim", climo)):
                cost[f"{pre}_def"].append((r.etc - r.eta).sum(axis=1))
                cost[f"{pre}_dp"].append(r.dp.sum(axis=1))
                cost[f"{pre}_w"].append(r.irr.sum(axis=1))
            n_plans += len(idx)
        res = {"n_plans": n_plans, "by_day": {}}
        for v, a in acc.items():
            res.setdefault("precision_within_1_day", {})[v] = (tol1[v] / np.maximum(a["plan"], 1)).round(3).tolist()
            res["by_day"][v] = {
                "recall": (a["tp"] / np.maximum(a["act"], 1)).round(3).tolist(),
                "precision": (a["tp"] / np.maximum(a["plan"], 1)).round(3).tolist(),
                "accuracy": (a["agree"] / np.maximum(a["n"], 1)).round(4).tolist(),
                "irrigation_day_rate_actual": (a["act"] / np.maximum(a["n"], 1)).round(4).tolist(),
                "cum_water_abs_err_mm": (a["werr"] / np.maximum(a["n"], 1)).round(2).tolist(),
            }
        sc = {k: np.concatenate(v) for k, v in scen.items()}
        prob, actd = sc["prob"], sc["act_days"]
        res["scenarios"] = {
            "n_analog_years": int(ana_p.shape[0] - 1),
            "water_14d_actual_mm": float(sc["act"].mean()),
            "water_14d_scenario_mean_mm": float(sc["mean"].mean()),
            "water_14d_bias_mm": float((sc["mean"] - sc["act"]).mean()),
            "water_14d_mae_mm": float(np.abs(sc["mean"] - sc["act"]).mean()),
            "water_14d_p10_p90_coverage": float(((sc["act"] >= sc["p10"]) & (sc["act"] <= sc["p90"])).mean()),
            "recall_by_day_prob_ge_0_5": ((((prob >= 0.5) & actd).sum(axis=0)) / np.maximum(actd.sum(axis=0), 1)).round(3).tolist(),
            "precision_by_day_prob_ge_0_5": ((((prob >= 0.5) & actd).sum(axis=0)) / np.maximum((prob >= 0.5).sum(axis=0), 1)).round(3).tolist(),
            "brier_by_day": (((prob - actd) ** 2).mean(axis=0)).round(4).tolist(),
            "brier_climatology_by_day": (((actd.mean(axis=0) - actd) ** 2).mean(axis=0)).round(4).tolist(),
        }
        c = {k: np.concatenate(v) for k, v in cost.items()}
        res["window_14d"] = {
            "replan_daily": {"deficit_mm": float(c["closed_def"].mean()), "dp_mm": float(c["closed_dp"].mean()), "water_mm": float(c["closed_w"].mean())},
            "plan_with_forecast_open_loop": {"deficit_mm": float(c["open_def"].mean()), "dp_mm": float(c["open_dp"].mean()), "water_mm": float(c["open_w"].mean())},
            "plan_climatology_open_loop": {"deficit_mm": float(c["clim_def"].mean()), "dp_mm": float(c["clim_dp"].mean()), "water_mm": float(c["clim_w"].mean())},
        }
        out["policies"][name] = res
        bd = res["by_day"]["forecast"]
        w = res["window_14d"]
        print(f"== {name}: {n_plans} plans ({args.dataset} {years}, {args.forecast})")
        print("  recall by day   :", " ".join(f"{x:.2f}" for x in bd["recall"]))
        print("  precision by day:", " ".join(f"{x:.2f}" for x in bd["precision"]))
        print("  |cum water err| :", " ".join(f"{x:.1f}" for x in bd["cum_water_abs_err_mm"]))
        print("  clim-plan recall:", " ".join(f"{x:.2f}" for x in res["by_day"]["climatology"]["recall"]))
        print("  precision +-1 d :", " ".join(f"{x:.2f}" for x in res["precision_within_1_day"]["forecast"]))
        s_ = res["scenarios"]
        print(f"  scenarios: 14-d water actual {s_['water_14d_actual_mm']:.1f} vs scenario mean {s_['water_14d_scenario_mean_mm']:.1f} mm "
              f"(MAE {s_['water_14d_mae_mm']:.1f}, P10-P90 coverage {s_['water_14d_p10_p90_coverage']:.0%})")
        print("  scen recall p>=.5:", " ".join(f"{x:.2f}" for x in s_["recall_by_day_prob_ge_0_5"]))
        print("  Brier vs clim    :", " ".join(f"{a:.3f}/{b:.3f}" for a, b in zip(s_["brier_by_day"], s_["brier_climatology_by_day"])))
        print(f"  14 d: re-plan daily deficit {w['replan_daily']['deficit_mm']:.2f} mm dp {w['replan_daily']['dp_mm']:.2f} water {w['replan_daily']['water_mm']:.1f} | "
              f"plan open-loop deficit {w['plan_with_forecast_open_loop']['deficit_mm']:.2f} dp {w['plan_with_forecast_open_loop']['dp_mm']:.2f} water {w['plan_with_forecast_open_loop']['water_mm']:.1f} | "
              f"climatology plan deficit {w['plan_climatology_open_loop']['deficit_mm']:.2f} dp {w['plan_climatology_open_loop']['dp_mm']:.2f} water {w['plan_climatology_open_loop']['water_mm']:.1f}")
    path = REPORTS_DIR / f"plan14_{args.dataset}_{args.forecast}{args.tag}.json"
    path.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print("saved", path)


if __name__ == "__main__":
    main()
