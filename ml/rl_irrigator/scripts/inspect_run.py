"""Plot single VALIDATION seasons (2024, real forecasts) for a trained run vs the FAO rule.

Looks for simulator exploits: irrigation on a full soil, dumping the budget, ignoring stress.
Usage: uv run --group train python ml/rl_irrigator/scripts/inspect_run.py E1_s1
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kowr_rl import REPORTS_DIR  # noqa: E402
from kowr_rl.baselines import make_fao_rule  # noqa: E402
from kowr_rl.config import load_crops  # noqa: E402
from kowr_rl.evaluate import ensure_npz, fig_seasons  # noqa: E402
from kowr_rl.policy import NumpyPolicy  # noqa: E402
from kowr_rl.simulator import simulate  # noqa: E402
from kowr_rl.train import validation_batch  # noqa: E402


def main() -> None:
    run = sys.argv[1]
    pol = NumpyPolicy.load(ensure_npz(run))
    val = validation_batch()
    fao = make_fao_rule()
    r_rl, r_fao = simulate(val, pol), simulate(val, fao)
    d_w, d_y = r_rl.water_mm - r_fao.water_mm, r_rl.yield_rel - r_fao.yield_rel
    # irrigation applied while the soil was above RAW (no stress) -> potential waste
    rec = simulate(val, pol, record=True).traces
    early = np.nansum(np.where(rec["dr"] < 0.5 * rec["raw"], rec["irr"], 0.0), axis=1)
    print(f"{run}: mean water on soil < 50% RAW depleted: {early.mean():.1f} mm/season (share of RL water {early.sum() / max(r_rl.water_mm.sum(), 1):.1%})")
    picks = {
        "najwiecej wody ponad FAO": int(np.argmax(d_w)),
        "najwieksza oszczednosc wody": int(np.argmin(d_w)),
        "najwiekszy spadek plonu": int(np.argmin(d_y)),
        "najwiecej wody na wilgotna glebe": int(np.argmax(early)),
    }
    cases = []
    for title, i in picks.items():
        sub = val.subset([i])
        L = int(sub.length[0])
        case = {"length": L, "taw": float(sub.taw[0]), "precip": sub.precip[0, :L]}
        for s, p in (("fao_rule", fao), ("rl", pol)):
            r = simulate(sub, p, record=True)
            case[s] = {"dr": r.traces["dr"][0, :L], "irr": r.traces["irr"][0, :L], "yield": float(r.yield_rel[0])}
            case["raw"] = r.traces["raw"][0, :L]
        m = val.meta[i]
        case["title"] = f"{title}\n{load_crops()[m['crop']].label_pl}, {m['year']}, kat. {m['soil_cat']}, budzet {m['budget']:.0f} mm"
        cases.append(case)
        print(f"  {title}: {m['crop']} {m['point']} kat.{m['soil_cat']} budzet {m['budget']:.0f}: RL {r_rl.water_mm[i]:.0f} mm/{r_rl.yield_rel[i]:.3f}  FAO {r_fao.water_mm[i]:.0f} mm/{r_fao.yield_rel[i]:.3f}")
    out = REPORTS_DIR / "figures" / f"inspect_{run}.png"
    fig_seasons(cases, out)
    print("saved", out)


if __name__ == "__main__":
    main()
