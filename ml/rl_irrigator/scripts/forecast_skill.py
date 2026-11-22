"""E8: skill of archived ECMWF precipitation forecasts by lead time (days), against climatology.

Sources (all real, archived):
* Previous Runs API, ECMWF IFS 0.25 (data/forecasts, data/holdout/forecasts): leads 1-7, every day;
* Single Runs API, ECMWF IFS HRES 9 km (data/single_runs/single_runs.parquet, if fetched): leads 1-14,
  12 UTC run of the day before the target window, weekly.

Per lead: bias (sum F / sum P), MAE, MAE skill score vs climatology (1 - MAE_F / MAE_clim; 0 = no
better than the day-of-year mean of the other years), Spearman r, and for rain >= 5 mm the hit rate and
false-alarm ratio. Also the skill of window sums (days 1-7 and 8-14). Season months IV-IX only.

    uv run --group train python ml/rl_irrigator/scripts/forecast_skill.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kowr_rl import DATA_DIR, REPORTS_DIR  # noqa: E402

RAIN = 5.0


def load_truth(data_dir: Path) -> dict[str, pd.DataFrame]:
    man = json.loads((data_dir / "MANIFEST.json").read_text(encoding="utf-8"))
    out = {}
    for p in man["points"]:
        w = pd.read_parquet(data_dir / p["weather_file"])
        w["date"] = pd.to_datetime(w["date"])
        w = w.set_index("date")
        w["doy"] = w.index.dayofyear
        out[p["id"]] = w
    return out


def clim_for(w: pd.DataFrame, dates: pd.DatetimeIndex) -> np.ndarray:
    """Day-of-year mean precipitation over the years other than the target year."""
    res = np.full(len(dates), np.nan)
    for y in np.unique(dates.year):
        m = dates.year == y
        other = w[w.index.year != y]
        doy_mean = other.groupby("doy")["precip"].mean()
        res[m] = doy_mean.reindex(dates[m].dayofyear).to_numpy()
    return res


def scores(f: np.ndarray, o: np.ndarray, c: np.ndarray) -> dict[str, float]:
    ok = np.isfinite(f) & np.isfinite(o) & np.isfinite(c)
    f, o, c = f[ok], o[ok], c[ok]
    if len(f) < 30:
        return {"n": int(len(f))}
    mae_f, mae_c = np.abs(f - o).mean(), np.abs(c - o).mean()
    rain = o >= RAIN
    pred = f >= RAIN
    return {
        "n": int(len(f)),
        "bias_ratio": float(f.sum() / max(o.sum(), 1e-9)),
        "mae_mm": float(mae_f),
        "mae_clim_mm": float(mae_c),
        "mae_skill_vs_clim": float(1 - mae_f / mae_c),
        "spearman_r": float(pd.Series(f).corr(pd.Series(o), method="spearman")),
        "hit_rate_5mm": float((pred & rain).sum() / max(rain.sum(), 1)),
        "false_alarm_ratio_5mm": float((pred & ~rain).sum() / max(pred.sum(), 1)),
    }


def previous_runs(data_dir: Path, years: list[int]) -> dict[str, dict]:
    truth = load_truth(data_dir)
    man = json.loads((data_dir / "MANIFEST.json").read_text(encoding="utf-8"))
    rows = []
    for p in man["points"]:
        if not p.get("forecast_file") or not (data_dir / p["forecast_file"]).exists():
            continue
        f = pd.read_parquet(data_dir / p["forecast_file"])
        f["date"] = pd.to_datetime(f["date"])
        f = f[f.date.dt.year.isin(years) & f.date.dt.month.between(4, 9)]
        w = truth[p["id"]]
        obs = w["precip"].reindex(f.date).to_numpy()
        clim = clim_for(w, pd.DatetimeIndex(f.date))
        for k in range(1, 8):
            rows.append(pd.DataFrame({"lead": k, "f": f[f"f{k}"].to_numpy(), "o": obs, "c": clim}))
    df = pd.concat(rows)
    return {str(k): scores(g.f.to_numpy(), g.o.to_numpy(), g.c.to_numpy()) for k, g in df.groupby("lead")}


def single_runs(sets: dict[str, Path]) -> dict[str, dict]:
    path = DATA_DIR / "single_runs" / "single_runs.parquet"
    if not path.exists():
        return {}
    sr = pd.read_parquet(path)
    sr["date"] = pd.to_datetime(sr["date"])
    out = {}
    for set_name, data_dir in sets.items():
        d = sr[(sr.set == set_name) & sr.date.dt.month.between(4, 9)].copy()
        if d.empty:
            continue
        truth = load_truth(data_dir)
        d["o"] = np.nan
        d["c"] = np.nan
        for pid, g in d.groupby("point"):
            w = truth[pid]
            d.loc[g.index, "o"] = w["precip"].reindex(g.date).to_numpy()
            d.loc[g.index, "c"] = clim_for(w, pd.DatetimeIndex(g.date))
        res = {str(k): scores(g.precip.to_numpy(), g.o.to_numpy(), g.c.to_numpy()) for k, g in d.groupby("lead")}
        # window sums: days 1-7 and 8-14 of the same run
        win = {}
        for name, (a, b) in {"days_1_7": (1, 7), "days_8_14": (8, 14)}.items():
            g = d[(d.lead >= a) & (d.lead <= b)].groupby(["point", "t"])
            agg = g.agg(f=("precip", "sum"), o=("o", "sum"), c=("c", "sum"), n=("precip", "count"))
            agg = agg[agg.n == b - a + 1]
            win[name] = scores(agg.f.to_numpy(), agg.o.to_numpy(), agg.c.to_numpy())
        out[set_name] = {"by_lead": res, "windows": win}
    return out


def main() -> None:
    out = {
        "note": "daily precipitation, months IV-IX; skill score vs day-of-year climatology of the other years (0 = no skill)",
        "previous_runs_ifs025_val2024": previous_runs(DATA_DIR, [2024]),
        "previous_runs_ifs025_test_holdout": previous_runs(DATA_DIR / "holdout", [2025, 2026]),
        "single_runs_hres": single_runs({"calibration_2024": DATA_DIR, "test_holdout": DATA_DIR / "holdout"}),
    }
    path = REPORTS_DIR / "forecast_skill_by_lead.json"
    path.write_text(json.dumps(out, indent=1), encoding="utf-8")
    for name in ("previous_runs_ifs025_val2024", "previous_runs_ifs025_test_holdout"):
        print(f"== {name}")
        for k, s in out[name].items():
            if "mae_skill_vs_clim" in s:
                print(f"  lead {k:>2}: n={s['n']:6d} skill={s['mae_skill_vs_clim']:+.2f} r={s['spearman_r']:.2f} bias={s['bias_ratio']:.2f} hit5={s['hit_rate_5mm']:.2f} far5={s['false_alarm_ratio_5mm']:.2f}")
    for set_name, r in out["single_runs_hres"].items():
        print(f"== single_runs_hres {set_name}")
        for k, s in r["by_lead"].items():
            if "mae_skill_vs_clim" in s:
                print(f"  lead {k:>2}: n={s['n']:6d} skill={s['mae_skill_vs_clim']:+.2f} r={s['spearman_r']:.2f} bias={s['bias_ratio']:.2f} hit5={s['hit_rate_5mm']:.2f} far5={s['false_alarm_ratio_5mm']:.2f}")
        for k, s in r["windows"].items():
            if "mae_skill_vs_clim" in s:
                print(f"  {k}: n={s['n']} skill={s['mae_skill_vs_clim']:+.2f} r={s['spearman_r']:.2f}")
    print("saved", path)


if __name__ == "__main__":
    main()
