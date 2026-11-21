"""Policies usable by the batch simulator and by live inference.

* :class:`SB3BatchPolicy` - wraps a trained MaskablePPO (+ VecNormalize) for batch simulation
  (training/evaluation only, needs torch).
* :func:`export_npz` - exports the actor MLP + observation normalisation to a NumPy ``.npz``.
* :class:`NumpyPolicy` - torch-free runtime policy loaded from that ``.npz``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from .simulator import SimState, action_mask, default_actions

OBS_LOW, OBS_HIGH = -10.0, 10.0  # same clipping as the environment


def policy_obs(state: SimState) -> np.ndarray:
    return np.clip(state.obs(), OBS_LOW, OBS_HIGH)


class SB3BatchPolicy:
    def __init__(self, model: Any, vecnorm: Any | None = None, actions: np.ndarray | None = None) -> None:
        self.model = model
        self.vecnorm = vecnorm
        self.actions = default_actions() if actions is None else actions

    def __call__(self, state: SimState) -> np.ndarray:
        obs = policy_obs(state)
        if self.vecnorm is not None:
            obs = self.vecnorm.normalize_obs(obs)
        masks = action_mask(state.budget_left, self.actions)
        a, _ = self.model.predict(obs, action_masks=masks, deterministic=True)
        return self.actions[np.asarray(a, dtype=np.int64)]


def export_npz(model: Any, vecnorm: Any | None, path: Path, meta: dict[str, Any] | None = None) -> Path:
    """Export the actor network of a MaskablePPO MlpPolicy and obs normalisation to ``.npz``."""
    import torch.nn as nn

    pol = model.policy
    linears = [m for m in pol.mlp_extractor.policy_net if isinstance(m, nn.Linear)]
    act = pol.activation_fn.__name__.lower()
    arrays: dict[str, np.ndarray] = {}
    for i, lin in enumerate(linears):
        arrays[f"W{i}"] = lin.weight.detach().cpu().numpy().astype(np.float64)
        arrays[f"b{i}"] = lin.bias.detach().cpu().numpy().astype(np.float64)
    arrays["Wout"] = pol.action_net.weight.detach().cpu().numpy().astype(np.float64)
    arrays["bout"] = pol.action_net.bias.detach().cpu().numpy().astype(np.float64)
    if vecnorm is not None and vecnorm.norm_obs:
        arrays["obs_mean"] = vecnorm.obs_rms.mean.astype(np.float64)
        arrays["obs_var"] = vecnorm.obs_rms.var.astype(np.float64)
        arrays["clip_obs"] = np.array(float(vecnorm.clip_obs))
        arrays["epsilon"] = np.array(float(vecnorm.epsilon))
    arrays["actions_mm"] = default_actions()
    info = {"n_hidden": len(linears), "activation": act, **(meta or {})}
    arrays["meta_json"] = np.array(json.dumps(info))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(path, **arrays)
    return path


class NumpyPolicy:
    """Torch-free MaskablePPO actor (deterministic = argmax of masked logits)."""

    def __init__(self, arrays: dict[str, np.ndarray]) -> None:
        self.meta = json.loads(str(arrays["meta_json"]))
        n = int(self.meta["n_hidden"])
        self.layers = [(arrays[f"W{i}"], arrays[f"b{i}"]) for i in range(n)]
        self.out = (arrays["Wout"], arrays["bout"])
        self.actions = np.asarray(arrays["actions_mm"], dtype=np.float64)
        self.obs_mean = arrays.get("obs_mean")
        self.obs_var = arrays.get("obs_var")
        self.clip_obs = float(arrays["clip_obs"]) if "clip_obs" in arrays else None
        self.eps = float(arrays["epsilon"]) if "epsilon" in arrays else 1e-8
        act = self.meta["activation"]
        self._act = {"tanh": np.tanh, "relu": lambda x: np.maximum(x, 0.0)}[act]

    @classmethod
    def load(cls, path: Path | str) -> NumpyPolicy:
        with np.load(path, allow_pickle=False) as z:
            return cls({k: z[k] for k in z.files})

    def normalize(self, obs: np.ndarray) -> np.ndarray:
        obs = np.clip(np.asarray(obs, dtype=np.float64), OBS_LOW, OBS_HIGH)
        if self.obs_mean is None:
            return obs
        x = (obs - self.obs_mean) / np.sqrt(self.obs_var + self.eps)
        return np.clip(x, -self.clip_obs, self.clip_obs)

    def logits(self, obs: np.ndarray) -> np.ndarray:
        h = self.normalize(obs)
        for W, b in self.layers:
            h = self._act(h @ W.T + b)
        W, b = self.out
        return h @ W.T + b

    def probabilities(self, obs: np.ndarray, masks: np.ndarray) -> np.ndarray:
        lg = np.where(masks, self.logits(obs), -np.inf)
        lg = lg - lg.max(axis=1, keepdims=True)
        p = np.exp(lg)
        return p / p.sum(axis=1, keepdims=True)

    def act(self, obs: np.ndarray, masks: np.ndarray) -> np.ndarray:
        return np.argmax(np.where(masks, self.logits(obs), -np.inf), axis=1)

    def __call__(self, state: SimState) -> np.ndarray:
        masks = action_mask(state.budget_left, self.actions)
        return self.actions[self.act(state.obs(), masks)]
