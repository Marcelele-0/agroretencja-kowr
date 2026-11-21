"""KOWR RL irrigation module (HackNation 2026, step 4 of the KOWR workflow).

Daily FAO-56 soil-water simulator, baseline irrigation strategies, a Gymnasium
environment and a MaskablePPO policy that allocates a limited seasonal water
budget. Runtime inference uses only NumPy (policy exported to ``.npz``).
"""

from __future__ import annotations

from pathlib import Path

__version__ = "0.1.0"

#: Root of the ``ml/rl_irrigator`` module (config/, data/, models/ live here).
MODULE_ROOT: Path = Path(__file__).resolve().parent.parent
#: Root of the monorepo (``artifacts/`` lives here).
REPO_ROOT: Path = MODULE_ROOT.parent.parent

CONFIG_DIR: Path = MODULE_ROOT / "config"
DATA_DIR: Path = MODULE_ROOT / "data"
MODELS_DIR: Path = MODULE_ROOT / "models"
REPORTS_DIR: Path = MODULE_ROOT / "reports"
ARTIFACT_MODELS_DIR: Path = REPO_ROOT / "artifacts" / "models"
ARTIFACT_VALIDATION_DIR: Path = REPO_ROOT / "artifacts" / "validation"
