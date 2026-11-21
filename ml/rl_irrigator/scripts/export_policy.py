"""Export the deployed RL policy to artifacts/models/ (runtime .npz without torch) + model card.

Usage: uv run --group train python ml/rl_irrigator/scripts/export_policy.py E4_s1
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


def main() -> None:
    run = sys.argv[1]
    src = ensure_npz(run)
    ARTIFACT_MODELS_DIR.mkdir(parents=True, exist_ok=True)
    dst = ARTIFACT_MODELS_DIR / "ppo_irrigator_weights.npz"
    shutil.copy2(src, dst)
    d = MODELS_DIR / run
    hist = json.loads((d / "val_history.json").read_text(encoding="utf-8"))
    best = max(hist["history"], key=lambda h: h["reward"])
    ev_path = ARTIFACT_VALIDATION_DIR / "rl_eval.json"
    ev = json.loads(ev_path.read_text(encoding="utf-8")) if ev_path.exists() else {}
    card = {
        "name": "FAO-RL-Irrigator (MaskablePPO)",
        "exported_at": dt.datetime.now().isoformat(timespec="seconds"),
        "run": run,
        "weights": dst.name,
        "runtime": "NumPy only (kowr_rl.policy.NumpyPolicy); MLP actor + VecNormalize observation statistics",
        "train_config": json.loads((d / "run_config.json").read_text(encoding="utf-8")),
        "train_years": load_rl_config()["split"]["train_years"],
        "validation": {"year": load_rl_config()["split"]["val_years"], "best": best, "reference": hist["reference"]},
        "test_decision": {k: ev.get(k) for k in ("ready", "recommended_default_policy", "deployed_run", "generated_at")},
        "status": "default" if load_rl_config()["default_policy"] == "rl" else "experimental",
        "intended_use": "Step 4 (optional irrigation) of the KOWR workflow, after water assessment, causes, retention and agrotechnics.",
        "limitations": "Trained on a single-layer FAO-56 bucket simulator with FAO-33 yield response; see ml/rl_irrigator/README.md.",
    }
    (ARTIFACT_MODELS_DIR / "ppo_irrigator_card.json").write_text(json.dumps(card, indent=1), encoding="utf-8")
    print(f"exported {src} -> {dst} ({dst.stat().st_size / 1024:.0f} KiB); card written")


if __name__ == "__main__":
    main()
