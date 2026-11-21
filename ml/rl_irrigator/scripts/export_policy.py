"""Export the deployed RL policy to artifacts/models/ (runtime .npz without torch) + model card.

One run -> a single MLP; several runs -> an ensemble .npz (average of action probabilities).
``<run>@dom`` selects the E6 "dominant" checkpoint of a run (see ``kowr_rl.evaluate.ensure_npz``).

Usage: uv run --group train python ml/rl_irrigator/scripts/export_policy.py E6f_s1@dom E6f_s2@dom E6f_s3@dom
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kowr_rl import ARTIFACT_MODELS_DIR, ARTIFACT_VALIDATION_DIR, MODELS_DIR  # noqa: E402
from kowr_rl.config import load_rl_config  # noqa: E402
from kowr_rl.evaluate import ensure_npz  # noqa: E402
from kowr_rl.policy import save_ensemble  # noqa: E402


def _member(run: str) -> dict:
    name, _, ckpt = run.partition("@")
    d = MODELS_DIR / name
    hist = json.loads((d / "val_history.json").read_text(encoding="utf-8"))
    h = hist["history"]
    if ckpt in ("dom", "domc"):
        flag = "dominant" if ckpt == "dom" else "dominant_all_crops"
        h = [x for x in h if x.get(flag)] or h
    best = max(h, key=lambda x: x["reward"])
    return {
        "run": run,
        "checkpoint": {"dom": "best_dominant", "domc": "best_domcrops"}.get(ckpt, "best"),
        "validation_best": best,
        "train_config": json.loads((d / "run_config.json").read_text(encoding="utf-8")),
        "reference": hist["reference"],
    }


def main() -> None:
    runs = sys.argv[1:]
    if not runs:
        raise SystemExit(__doc__)
    ARTIFACT_MODELS_DIR.mkdir(parents=True, exist_ok=True)
    dst = ARTIFACT_MODELS_DIR / "ppo_irrigator_weights.npz"
    srcs = [ensure_npz(r) for r in runs]
    if len(runs) == 1:
        shutil.copy2(srcs[0], dst)
    else:
        save_ensemble(srcs, dst, {"members": runs})
    members = [_member(r) for r in runs]
    cfg = load_rl_config()
    tests = {}
    for tag, fname in (("test_train_points", "rl_eval.json"), ("test_holdout", "rl_eval_holdout.json")):
        p = ARTIFACT_VALIDATION_DIR / fname
        if p.exists():
            ev = json.loads(p.read_text(encoding="utf-8"))
            tests[tag] = {k: ev.get(k) for k in ("ready", "recommended_default_policy", "deployed_run", "generated_at", "dataset")}
    card = {
        "name": "FAO-RL-Irrigator (MaskablePPO)" + (f", ensemble of {len(runs)}" if len(runs) > 1 else ""),
        "exported_at": dt.datetime.now().isoformat(timespec="seconds"),
        "runs": runs,
        "weights": dst.name,
        "runtime": "NumPy only (kowr_rl.policy.load_policy): MLP actor(s) + VecNormalize observation statistics"
        + ("; ensemble = mean of the members' action probabilities" if len(runs) > 1 else ""),
        "observation": "v2 (17 features, kowr_rl.simulator.OBS_NAMES)",
        "train_years": cfg["split"]["train_years"],
        "validation_year": cfg["split"]["val_years"],
        "members": members,
        "test_decision": tests,
        "status": "default" if cfg["default_policy"] == "rl" else "experimental",
        "intended_use": "Step 4 (optional irrigation) of the KOWR workflow, after water assessment, causes, retention and agrotechnics.",
        "limitations": "Trained on a single-layer FAO-56 bucket simulator with FAO-33 yield response; see ml/rl_irrigator/README.md.",
    }
    (ARTIFACT_MODELS_DIR / "ppo_irrigator_card.json").write_text(json.dumps(card, indent=1), encoding="utf-8")
    print(f"exported {', '.join(map(str, srcs))} -> {dst} ({dst.stat().st_size / 1024:.0f} KiB); card written")


if __name__ == "__main__":
    main()
