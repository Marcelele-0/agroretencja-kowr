"""M5 evaluation on the TEST years with REAL archived ECMWF forecasts.

Factorial design: 50 points x test years x 4 crops x 4 IUNG soil categories x budget levels.
Strategies: none, calendar, FAO rule, FAO + forecast, RL (each trained seed + the deployed
one chosen on validation), DP oracle (perfect foresight). Uncertainty: two-way cluster
bootstrap over points and years. Writes ``reports/evaluation.md`` with figures and
``artifacts/validation/rl_eval.json``.

Usage: uv run --group train python -m kowr_rl.evaluate --runs E1_s1 E1_s2 E1_s3
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import time
from pathlib import Path
from typing import Any

import numpy as np

from . import ARTIFACT_VALIDATION_DIR, MODELS_DIR, REPORTS_DIR
from .baselines import baseline_policies, run_oracle
from .config import load_crops, load_rl_config, load_soils
from .forecast import noisy_forecast
from .policy import NumpyPolicy
from .simulator import SeasonBatch, SimResult, build_batch, simulate

LABELS = {
    "none": "Bez nawadniania",
    "calendar": "Kalendarz",
    "fao_rule": "Regula FAO",
    "fao_forecast": "FAO + prognoza",
    "rl": "Agent RL",
    "oracle": "Wyrocznia (znana pogoda)",
}
# Dataviz reference palette (validated): identity colours follow the strategy everywhere.
INK, INK2, MUTED, GRID, BASE, SURFACE = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7", "#fcfcfb"
COLORS = {"fao_rule": "#2a78d6", "rl": "#eb6834", "fao_forecast": "#1baf7a", "none": "#b8b7b0", "calendar": "#9f9e98", "oracle": "#52514e"}
METRICS = ("yield_rel", "water_mm", "dp_mm", "stress_days")


# ======================================================================================
# Policies
# ======================================================================================
def ensure_npz(run: str) -> Path:
    """Export ``models/<run>/best_model.zip`` (+ VecNormalize) to ``policy.npz`` if needed."""
    d = MODELS_DIR / run
    npz = d / "policy.npz"
    if npz.exists() and npz.stat().st_mtime >= (d / "best_model.zip").stat().st_mtime:
        return npz
    from sb3_contrib import MaskablePPO
    from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize

    from .env import KowrIrrigationEnv
    from .policy import export_npz

    model = MaskablePPO.load(d / "best_model.zip", device="cpu")
    venv = VecNormalize.load(str(d / "best_vecnormalize.pkl"), DummyVecEnv([lambda: KowrIrrigationEnv("val")]))
    hist = json.loads((d / "val_history.json").read_text(encoding="utf-8"))
    best = max(hist["history"], key=lambda h: h["reward"])
    export_npz(model, venv, npz, meta={"run": run, "val": best, "exported_at": dt.datetime.now().isoformat(timespec="seconds")})
    return npz


def best_run(runs: list[str]) -> str:
    def score(r: str) -> float:
        h = json.loads((MODELS_DIR / r / "val_history.json").read_text(encoding="utf-8"))["history"]
        return max(x["reward"] for x in h)

    return max(runs, key=score)


# ======================================================================================
# Results table
# ======================================================================================
def to_rows(batch: SeasonBatch, res: SimResult, strategy: str) -> list[dict[str, Any]]:
    rows = []
    for i, m in enumerate(batch.meta):
        rows.append(
            {
                "strategy": strategy,
                "point": m["point"],
                "year": m["year"],
                "crop": m["crop"],
                "soil_cat": m["soil_cat"],
                "true_soil": m["soil_cat"] == m["true_soil_cat"],
                "budget": m["budget"],
                "yield_rel": float(res.yield_rel[i]),
                "water_mm": float(res.water_mm[i]),
                "dp_mm": float(res.dp_mm[i]),
                "stress_days": float(res.stress_days[i]),
                "reward": float(res.reward[i]),
                "precip_mm": float(res.precip_mm[i]),
                "etc_mm": float(res.etc_mm[i]),
            }
        )
    return rows


def cluster_bootstrap(df: Any, value: str, reps: int, rng: np.random.Generator) -> tuple[float, float, float]:
    """Mean and 95% CI; resampling points and years independently (two-way cluster bootstrap)."""
    piv = df.groupby(["point", "year"])[value].mean().unstack("year")
    M = piv.to_numpy(dtype=np.float64)
    P, Y = M.shape
    pi = rng.integers(0, P, size=(reps, P))
    yi = rng.integers(0, Y, size=(reps, Y))
    boots = np.nanmean(M[pi[:, :, None], yi[:, None, :]], axis=(1, 2))
    return float(np.nanmean(M)), float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))


def paired(df: Any, a: str, b: str) -> Any:
    """Season-level differences a - b (same season, same budget)."""
    key = ["point", "year", "crop", "soil_cat", "budget"]
    x = df[df.strategy == a].set_index(key)[list(METRICS)]
    y = df[df.strategy == b].set_index(key)[list(METRICS)]
    return (x - y).reset_index()


# ======================================================================================
# Figures
# ======================================================================================
def _style(ax: Any) -> None:
    ax.set_facecolor(SURFACE)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(BASE)
    ax.tick_params(colors=INK2, labelsize=9)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def fig_strategies(summary: dict[str, dict[str, Any]], order: list[str], path: Path) -> None:
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 2, figsize=(11, 3.8), facecolor=SURFACE)
    for ax, metric, title, fmt in (
        (axes[0], "yield_rel", "Sredni plon wzgledny (FAO-33)", "{:.3f}"),
        (axes[1], "water_mm", "Zuzyta woda [mm/sezon]", "{:.0f}"),
    ):
        _style(ax)
        ax.grid(axis="y", visible=False)
        ax.grid(axis="x", color=GRID, linewidth=0.8)
        ys = np.arange(len(order))[::-1]
        for y, s in zip(ys, order):
            mean, lo, hi = summary[s][metric]
            ax.barh(y, mean, height=0.55, color=COLORS[s], edgecolor=SURFACE, linewidth=2)
            ax.errorbar(mean, y, xerr=[[mean - lo], [hi - mean]], color=INK, linewidth=1, capsize=3)
            ax.text(hi, y, "  " + fmt.format(mean), va="center", ha="left", fontsize=9, color=INK)
        ax.set_yticks(ys, [LABELS[s] for s in order])
        ax.set_title(title, loc="left", fontsize=11, color=INK)
        if metric == "yield_rel":
            lo_all = min(summary[s][metric][1] for s in order)
            ax.set_xlim(max(0.0, lo_all - 0.05), 1.03)
        else:
            ax.set_xlim(0, max(summary[s][metric][2] for s in order) * 1.18)
    fig.text(0.01, 0.01, "Lata testowe 2025-2026, prawdziwe prognozy ECMWF; slupki bledu: 95% bootstrap po punktach i latach.", fontsize=8, color=INK2)
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(path, dpi=150, facecolor=SURFACE)
    plt.close(fig)


def fig_budget(per_budget: dict[str, dict[float, dict[str, float]]], order: list[str], budgets: list[float], path: Path) -> None:
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 2, figsize=(11, 3.8), facecolor=SURFACE)
    for ax, metric, title in ((axes[0], "yield_rel", "Plon wzgledny vs budzet wody"), (axes[1], "water_mm", "Zuzyta woda [mm] vs budzet")):
        _style(ax)
        for s in order:
            ys = [per_budget[s][b][metric] for b in budgets]
            ls = "--" if s == "oracle" else "-"
            ax.plot(budgets, ys, ls, color=COLORS[s], linewidth=2, marker="o", markersize=5, label=LABELS[s])
        ax.set_xticks(budgets)
        ax.set_xlabel("Budzet wody na sezon [mm]", color=INK2, fontsize=9)
        ax.set_title(title, loc="left", fontsize=11, color=INK)
    axes[0].legend(frameon=False, fontsize=8, loc="lower right")
    fig.tight_layout()
    fig.savefig(path, dpi=150, facecolor=SURFACE)
    plt.close(fig)


def fig_seasons(cases: list[dict[str, Any]], path: Path) -> None:
    """Small multiples per season: depletion (% TAW) | rain & doses (mm) | cumulative water (mm)."""
    import matplotlib.pyplot as plt

    n = len(cases)
    fig, axes = plt.subplots(3, n, figsize=(4.2 * n, 7.5), facecolor=SURFACE, sharex="col", gridspec_kw={"height_ratios": [1.3, 1, 0.8]})
    axes = np.atleast_2d(axes).reshape(3, n)
    for c, case in enumerate(cases):
        days = np.arange(case["length"])
        taw = case["taw"]
        a0, a1, a2 = axes[0, c], axes[1, c], axes[2, c]
        for ax in (a0, a1, a2):
            _style(ax)
        a0.plot(days, 100 * case["raw"] / taw, color=MUTED, linewidth=1.2, label="prog stresu (RAW)")
        for s in ("fao_rule", "rl"):
            a0.plot(days, 100 * case[s]["dr"] / taw, color=COLORS[s], linewidth=2, label=LABELS[s])
        a0.set_ylim(100, 0)  # depletion grows downwards: drier = lower
        a0.set_title(case["title"], loc="left", fontsize=9.5, color=INK)
        a1.bar(days, case["precip"], width=0.9, color=BASE, label="opad")
        for s, off in (("fao_rule", -0.2), ("rl", 0.2)):
            irr = case[s]["irr"]
            m = irr > 0
            a1.scatter(days[m] + off, irr[m], s=26, color=COLORS[s], edgecolor=SURFACE, linewidth=1, zorder=3, label=f"dawka - {LABELS[s]}")
        totals = {s: float(np.sum(case[s]["irr"])) for s in ("fao_rule", "rl")}
        hi_s = max(totals, key=totals.get)
        for s in ("fao_rule", "rl"):
            a2.plot(days, np.cumsum(case[s]["irr"]), color=COLORS[s], linewidth=2)
            # the larger total is labelled above its line end, the smaller one below -> no overlap
            va = "bottom" if s == hi_s else "top"
            a2.annotate(
                f"{LABELS[s]}: {totals[s]:.0f} mm, plon {case[s]['yield']:.3f}",
                (days[-1], totals[s]),
                xytext=(4, 3 if va == "bottom" else -3),
                textcoords="offset points",
                fontsize=7.5,
                color=INK,
                va=va,
            )
        a2.set_ylim(0, max(max(totals.values()) * 1.35, 10))
        a2.set_xlabel("Dzien sezonu", color=INK2, fontsize=9)
        if c == 0:
            a0.set_ylabel("Niedobor wody [% TAW]", color=INK2, fontsize=9)
            a1.set_ylabel("Opad / dawka [mm]", color=INK2, fontsize=9)
            a2.set_ylabel("Woda skumulowana [mm]", color=INK2, fontsize=9)
            a0.legend(frameon=False, fontsize=7.5, loc="lower left")
            a1.legend(frameon=False, fontsize=7.5, loc="upper left")
        a2.set_xlim(0, case["length"] * 1.55)
    fig.tight_layout()
    fig.savefig(path, dpi=140, facecolor=SURFACE)
    plt.close(fig)


# ======================================================================================
# Main evaluation
# ======================================================================================
def run_evaluation(runs: list[str], *, oracle: bool = True, reps: int | None = None) -> dict[str, Any]:
    import pandas as pd

    from .dataset import factorial_specs, load_points

    cfg = load_rl_config()
    ev = cfg["evaluation"]
    reps = reps or ev["bootstrap_reps"]
    years = cfg["split"]["test_years"]
    budgets = [float(b) for b in ev["budget_levels_mm"]]
    crops = cfg["episode"]["crops"]
    soils = cfg["episode"]["soil_categories"]
    rng = np.random.default_rng(0)
    t0 = time.perf_counter()
    points = load_points()
    batch = build_batch(factorial_specs(points, years, crops, soils, budgets, forecast="real"))
    fc_missing = float(np.nanmean([m["forecast_missing_frac"] for m in batch.meta]))
    print(f"test batch: {batch.size} seasons, forecast cells missing {fc_missing:.4f} ({time.perf_counter() - t0:.1f}s)")

    rows: list[dict[str, Any]] = []
    results: dict[str, SimResult] = {}
    for name, pol in baseline_policies().items():
        results[name] = simulate(batch, pol)
        rows += to_rows(batch, results[name], name)
    deployed = best_run(runs)
    policies = {r: NumpyPolicy.load(ensure_npz(r)) for r in runs}
    for r, pol in policies.items():
        res = simulate(batch, pol)
        rows += to_rows(batch, res, f"rl:{r}")
        if r == deployed:
            results["rl"] = res
            rows += to_rows(batch, res, "rl")
    # Robustness: same seasons, synthetic (training-noise) forecasts instead of real ECMWF.
    robust = {}
    for s in ev["forecast_noise_seeds"]:
        b2 = batch.subset(np.arange(batch.size))
        b2.forecast = noisy_forecast(b2.fut, np.random.default_rng(s))
        for name, pol in (("rl", policies[deployed]), ("fao_forecast", baseline_policies()["fao_forecast"])):
            r = simulate(b2, pol)
            robust.setdefault(name, []).append({"yield": float(r.yield_rel.mean()), "water_mm": float(r.water_mm.mean())})
    if oracle:
        t1 = time.perf_counter()
        results["oracle"] = run_oracle(batch)
        rows += to_rows(batch, results["oracle"], "oracle")
        print(f"oracle: {time.perf_counter() - t1:.0f}s")
    df = pd.DataFrame(rows)
    order = [s for s in ("none", "calendar", "fao_rule", "fao_forecast", "rl", "oracle") if s in results]

    summary: dict[str, dict[str, Any]] = {}
    for s in order + [f"rl:{r}" for r in runs]:
        d = df[df.strategy == s]
        summary[s] = {m: cluster_bootstrap(d, m, reps, rng) for m in METRICS}
        summary[s]["yield_min"] = float(d.yield_rel.min())
        summary[s]["yield_p05"] = float(d.yield_rel.quantile(0.05))
    none_y = df[df.strategy == "none"].set_index(["point", "year", "crop", "soil_cat", "budget"]).yield_rel
    for s in order:
        if s == "none":
            continue
        d = df[df.strategy == s].set_index(["point", "year", "crop", "soil_cat", "budget"])
        gain = (d.yield_rel - none_y).mean()
        summary[s]["yield_gain_pp_per_100mm"] = float(100 * gain / max(d.water_mm.mean(), 1e-9) * 100)

    # Per budget level
    per_budget: dict[str, dict[float, dict[str, float]]] = {}
    for s in order:
        per_budget[s] = {}
        for b in budgets:
            d = df[(df.strategy == s) & (df.budget == b)]
            per_budget[s][b] = {m: float(d[m].mean()) for m in METRICS}
            per_budget[s][b]["yield_min"] = float(d.yield_rel.min())
    # RL vs FAO: paired differences with bootstrap CIs
    diff = paired(df, "rl", "fao_rule")
    diff_ff = paired(df, "rl", "fao_forecast")
    decision_rows = []
    rule = ev["ready_rule"]
    for label, sub in [("wszystkie budzety", diff)] + [(f"{b:.0f} mm", diff[diff.budget == b]) for b in budgets]:
        dy = cluster_bootstrap(sub, "yield_rel", reps, rng)
        dw = cluster_bootstrap(sub, "water_mm", reps, rng)
        pass_a = dy[0] >= -rule["max_yield_drop_pp"] / 100 and dw[0] < 0
        pass_b = dy[0] > 0
        decision_rows.append({"scope": label, "d_yield": dy, "d_water": dw, "pass_less_water": pass_a, "pass_higher_yield": pass_b, "pass": pass_a or pass_b})
    ready = all(r["pass"] for r in decision_rows)
    vs_ff = {"d_yield": cluster_bootstrap(diff_ff, "yield_rel", reps, rng), "d_water": cluster_bootstrap(diff_ff, "water_mm", reps, rng)}

    # Crop x soil (pooled over budgets)
    crop_soil = []
    for c in crops:
        for s in soils:
            sub = diff[(diff.crop == c) & (diff.soil_cat == s)]
            f = df[(df.strategy == "fao_rule") & (df.crop == c) & (df.soil_cat == s)]
            crop_soil.append(
                {
                    "crop": c,
                    "soil": s,
                    "fao_yield": float(f.yield_rel.mean()),
                    "fao_water": float(f.water_mm.mean()),
                    "d_yield": float(sub.yield_rel.mean()),
                    "d_water": float(sub.water_mm.mean()),
                    "rl_worse_1pp_share": float((sub.yield_rel < -0.01).mean()),
                }
            )

    # Example seasons RL vs FAO
    key = ["point", "year", "crop", "soil_cat", "budget"]
    dd = diff.copy()
    picks = []
    cand = dd[(dd.yield_rel >= -0.005)].sort_values("water_mm")
    if len(cand):
        picks.append(("najwieksza oszczednosc wody przy tym samym plonie", cand.iloc[0]))
    picks.append(("najgorszy przypadek agenta RL (najwiekszy spadek plonu)", dd.sort_values("yield_rel").iloc[0]))
    dry = df[(df.strategy == "none") & (df.budget == 100.0) & (df.crop == "kukurydza")].sort_values("yield_rel")
    if len(dry):
        r0 = dry.iloc[0]
        same = (dd.point == r0.point) & (dd.year == r0.year) & (dd.crop == "kukurydza") & (dd.soil_cat == r0.soil_cat) & (dd.budget == 100.0)
        picks.append(("najsuchszy sezon kukurydzy, budzet 100 mm", dd[same].iloc[0]))
    med = dd[(dd.crop == "ziemniak") & (dd.budget == 150.0)]
    if len(med):
        picks.append(("typowy sezon ziemniaka, budzet 150 mm", med.iloc[(med.water_mm - med.water_mm.median()).abs().argsort().iloc[0]]))
    cases = []
    idx_of = {tuple(m[k] for k in key): i for i, m in enumerate(batch.meta)}
    for title, row in picks:
        i = idx_of[tuple(row[k] for k in key)]
        sub = batch.subset([i])
        case = {"length": int(sub.length[0]), "taw": float(sub.taw[0]), "precip": sub.precip[0, : sub.length[0]]}
        for s, pol in (("fao_rule", baseline_policies()["fao_rule"]), ("rl", policies[deployed])):
            r = simulate(sub, pol, record=True)
            L = case["length"]
            case[s] = {"dr": r.traces["dr"][0, :L], "irr": r.traces["irr"][0, :L], "yield": float(r.yield_rel[0])}
            case["raw"] = r.traces["raw"][0, :L]
        m = batch.meta[i]
        case["title"] = f"{title}\n{load_crops()[m['crop']].label_pl}, {m['year']}, kat. {m['soil_cat']}, {m['point']}, budzet {m['budget']:.0f} mm"
        cases.append(case)

    figdir = REPORTS_DIR / "figures"
    figdir.mkdir(parents=True, exist_ok=True)
    fig_strategies(summary, order, figdir / "strategies_test.png")
    fig_budget(per_budget, [s for s in order if s not in ("calendar",)], budgets, figdir / "budget_curves_test.png")
    fig_seasons(cases, figdir / "example_seasons.png")

    out = {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "test_years": years,
        "n_seasons_per_strategy": int(batch.size),
        "forecast": "real archived ECMWF IFS 0.25 (Open-Meteo Previous Runs), lead 1-7",
        "forecast_missing_frac": fc_missing,
        "runs": runs,
        "deployed_run": deployed,
        "summary": summary,
        "per_budget": {s: {str(b): v for b, v in d.items()} for s, d in per_budget.items()},
        "rl_vs_fao": decision_rows,
        "rl_vs_fao_forecast": vs_ff,
        "robustness_synthetic_forecast": robust,
        "crop_soil": crop_soil,
        "ready": ready,
        "recommended_default_policy": "rl" if ready else "fao_rule",
        "bootstrap_reps": reps,
    }
    ARTIFACT_VALIDATION_DIR.mkdir(parents=True, exist_ok=True)
    (ARTIFACT_VALIDATION_DIR / "rl_eval.json").write_text(json.dumps(out, indent=1, default=float), encoding="utf-8")
    write_report(out, order)
    return out


def _ci(t: tuple[float, float, float], fmt: str = "{:.3f}", scale: float = 1.0) -> str:
    m, lo, hi = (x * scale for x in t)
    return f"{fmt.format(m)} [{fmt.format(lo)}; {fmt.format(hi)}]"


def write_report(out: dict[str, Any], order: list[str]) -> None:
    S = out["summary"]
    crops = load_crops()
    soils = load_soils()["categories"]
    L: list[str] = []
    L.append("# Ewaluacja agenta RL nawadniania (lata testowe 2025-2026)\n")
    L.append(f"Wygenerowano: {out['generated_at']} przez `kowr_rl.evaluate`. Liczby pochodza z `artifacts/validation/rl_eval.json`.\n")
    L.append(
        f"**Uklad:** 50 punktow rolnych x lata {', '.join(map(str, out['test_years']))} x 4 uprawy x 4 kategorie gleby IUNG x 4 budzety wody "
        f"= {out['n_seasons_per_strategy']} sezonow na strategie. Kazda strategia dostaje te same dane: pogode z archiwum Open-Meteo i "
        f"**prawdziwe archiwalne prognozy ECMWF** (wyprzedzenie 1-7 dni; brakujace komorki: {100 * out['forecast_missing_frac']:.2f}%). "
        f"Wybrany agent: `{out['deployed_run']}` (najlepszy na walidacji 2024). Przedzialy: 95% bootstrap po punktach i latach "
        f"({out['bootstrap_reps']} powtorzen). Przy tylko dwoch latach testowych przedzialy sa szerokie, bo obejmuja niepewnosc miedzy latami.\n"
    )
    verdict = "**GOTOWY**" if out["ready"] else "**NIEGOTOWY - eksperymentalny**"
    L.append(f"## Werdykt: agent RL {verdict}\n")
    L.append(
        "Kryterium z planu: na tescie plon nie gorszy od reguly FAO o wiecej niz 1 pkt proc. przy mniejszym zuzyciu wody "
        "albo wyzszy plon przy tym samym budzecie. Sprawdzane lacznie i osobno dla kazdego poziomu budzetu.\n"
    )
    L.append("| Zakres | Delta plonu RL - FAO [pkt proc.] | Delta wody RL - FAO [mm] | Mniej wody, plon >= -1 pkt | Wyzszy plon | Wynik |")
    L.append("|---|---|---|---|---|---|")
    for r in out["rl_vs_fao"]:
        L.append(
            f"| {r['scope']} | {_ci(r['d_yield'], '{:+.2f}', 100)} | {_ci(r['d_water'], '{:+.1f}')} | "
            f"{'tak' if r['pass_less_water'] else 'nie'} | {'tak' if r['pass_higher_yield'] else 'nie'} | {'OK' if r['pass'] else 'NIE'} |"
        )
    ff = out["rl_vs_fao_forecast"]
    L.append(
        f"\nRL vs **FAO + prognoza** (uczciwszy rywal, ktory tez korzysta z prognozy): delta plonu {_ci(ff['d_yield'], '{:+.2f}', 100)} pkt proc., "
        f"delta wody {_ci(ff['d_water'], '{:+.1f}')} mm.\n"
    )
    L.append(f"Domyslna strategia w module: **`{out['recommended_default_policy']}`** (`config/rl.yaml`, `default_policy`).\n")

    L.append("## Wyniki zbiorcze\n")
    L.append("![Strategie](figures/strategies_test.png)\n")
    L.append("| Strategia | Plon sredni [95% CI] | Plon min | Plon 5. percentyl | Woda [mm] | Przyrost plonu na 100 mm [pkt proc.] | Dni stresu | Perkolacja [mm] |")
    L.append("|---|---|---|---|---|---|---|---|")
    for s in order:
        g = S[s].get("yield_gain_pp_per_100mm")
        L.append(
            f"| {LABELS[s]} | {_ci(S[s]['yield_rel'])} | {S[s]['yield_min']:.3f} | {S[s]['yield_p05']:.3f} | {_ci(S[s]['water_mm'], '{:.0f}')} | "
            f"{'-' if g is None else f'{g:.1f}'} | {S[s]['stress_days'][0]:.0f} | {S[s]['dp_mm'][0]:.0f} |"
        )
    L.append("\n**Ziarna losowe agenta RL** (te same sezony testowe):\n")
    L.append("| Przebieg | Plon | Woda [mm] | Perkolacja [mm] |")
    L.append("|---|---|---|---|")
    for r in out["runs"]:
        s = S[f"rl:{r}"]
        L.append(f"| `{r}`{' (wybrany)' if r == out['deployed_run'] else ''} | {s['yield_rel'][0]:.4f} | {s['water_mm'][0]:.1f} | {s['dp_mm'][0]:.1f} |")
    rb = out["robustness_synthetic_forecast"]
    L.append(
        "\n**Odpornosc na rodzaj prognozy:** te same sezony z syntetyczna prognoza z treningu zamiast prawdziwej ECMWF "
        f"(srednio z {len(rb.get('rl', []))} ziaren szumu): RL plon {np.mean([x['yield'] for x in rb['rl']]):.4f}, woda {np.mean([x['water_mm'] for x in rb['rl']]):.1f} mm; "
        f"FAO + prognoza plon {np.mean([x['yield'] for x in rb['fao_forecast']]):.4f}, woda {np.mean([x['water_mm'] for x in rb['fao_forecast']]):.1f} mm.\n"
    )

    L.append("## Wedlug budzetu wody\n")
    L.append("![Budzet](figures/budget_curves_test.png)\n")
    budgets = list(next(iter(out["per_budget"].values())).keys())
    L.append("| Strategia | " + " | ".join(f"{float(b):.0f} mm: plon / woda" for b in budgets) + " |")
    L.append("|---|" + "---|" * len(budgets))
    for s in order:
        L.append(f"| {LABELS[s]} | " + " | ".join(f"{out['per_budget'][s][b]['yield_rel']:.3f} / {out['per_budget'][s][b]['water_mm']:.0f}" for b in budgets) + " |")

    L.append("\n## Wedlug uprawy i kategorii gleby (wszystkie budzety)\n")
    L.append("| Uprawa | Gleba (TAW) | FAO: plon / woda | Delta plonu RL-FAO [pkt proc.] | Delta wody RL-FAO [mm] | Sezony z RL gorszym o >1 pkt |")
    L.append("|---|---|---|---|---|---|")
    for r in out["crop_soil"]:
        L.append(
            f"| {crops[r['crop']].label_pl} | {r['soil']} ({soils[r['soil']]['taw_mm']} mm) | {r['fao_yield']:.3f} / {r['fao_water']:.0f} | "
            f"{100 * r['d_yield']:+.2f} | {r['d_water']:+.1f} | {100 * r['rl_worse_1pp_share']:.0f}% |"
        )
    L.append("\n## Przykladowe sezony: agent RL vs regula FAO\n")
    L.append("![Sezony](figures/example_seasons.png)\n")
    L.append(
        "Gorny panel: niedobor wody w strefie korzeniowej (% TAW, wyzej = wilgotniej) i prog stresu RAW. Srodkowy: opad (szary) i dawki obu strategii. "
        "Dolny: woda skumulowana z plonem koncowym. Jeden z przykladow to celowo **najgorszy** przypadek agenta.\n"
    )
    L.append("## Ograniczenia ewaluacji\n")
    L.append(
        "- Plon to wynik modelu FAO-33 (jedna wartosc Ky na sezon), a nie pomiar polowy. Model nie rozroznia wrazliwosci faz rozwoju.\n"
        "- Tylko dwa lata testowe, wiec niepewnosc miedzy latami jest duza (szerokie przedzialy).\n"
        "- Kategorie gleby sa liczone w ukladzie czynnikowym (kazdy punkt w kazdej kategorii), wiec nie odpowiadaja rzeczywistemu rozkladowi gleb.\n"
        "- Parametry upraw i TAW sa do weryfikacji z mentorem. Pogoda pochodzi z reanalizy na siatce ok. 9-25 km.\n"
        "- Brak podsiaku wod gruntowych i splywu powierzchniowego w bilansie (model jednowarstwowy).\n"
    )
    (REPORTS_DIR / "evaluation.md").write_text("\n".join(L) + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", nargs="+", required=True)
    ap.add_argument("--no-oracle", action="store_true")
    ap.add_argument("--reps", type=int, default=None)
    args = ap.parse_args()
    out = run_evaluation(args.runs, oracle=not args.no_oracle, reps=args.reps)
    print(json.dumps({k: out[k] for k in ("deployed_run", "ready", "recommended_default_policy")}))
    for r in out["rl_vs_fao"]:
        print(r["scope"], "dY=%+.4f [%+.4f, %+.4f]" % r["d_yield"], "dW=%+.1f [%+.1f, %+.1f]" % r["d_water"], "OK" if r["pass"] else "NO")


if __name__ == "__main__":
    main()
