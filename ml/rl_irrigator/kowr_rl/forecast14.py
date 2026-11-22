"""E9: 14-day soil-water forecast from the ECMWF ensemble (51 members) and the three-zone model.

Pipeline for a parcel and a start day t (morning):
1. state on t: three-zone model (``soil3``) spun up from August of the previous year and run on the
   observed weather up to t-1 (optionally replaced by CDF-matched ERA5-Land, see ``era5_insert``);
2. weather for t .. t+13: the 51 ECMWF ENS members (run of 00 UTC t-1), calibrated per lead day:
   precipitation by quantile mapping to the observed climate of the calibration year, ET0 by a
   linear calibration of the Makkink formula (temperature + short-wave radiation) to FAO-56 ET0;
3. the three-zone model runs 14 days for every member -> distribution of the relative water content
   of every zone, of the crop stress (Ks < 1) and of the water deficit.

Calibration tables are fitted on 2024 (validation year) by ``scripts/ens_calibrate.py`` and stored
in ``config/ens_calibration.json`` (used at runtime too).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from . import CONFIG_DIR
from .soil3 import relative_water, step3

N_DAYS = 14
CAL_PATH = CONFIG_DIR / "ens_calibration.json"


# --------------------------------------------------------------------------- ET0 from ensemble fields
def makkink_et0(t2m_c: np.ndarray, rs_mj: np.ndarray) -> np.ndarray:
    """Makkink reference evaporation [mm/day] (KNMI form): 0.65 * D/(D+g) * Rs / lambda."""
    t = np.asarray(t2m_c, dtype=float)
    es = 0.6108 * np.exp(17.27 * t / (t + 237.3))
    delta = 4098.0 * es / (t + 237.3) ** 2
    gamma = 0.0665  # kPa/K at ~100 m a.s.l.
    lam = 2.45  # MJ/kg
    return np.maximum(0.65 * delta / (delta + gamma) * np.asarray(rs_mj, dtype=float) / lam, 0.0)


# --------------------------------------------------------------------------- calibration
class EnsCalibration:
    """Per-lead quantile mapping of precipitation and linear calibration of Makkink ET0."""

    def __init__(self, table: dict[str, Any]) -> None:
        self.table = table
        self.q_fc = np.asarray(table["precip_qm"]["forecast_quantiles"], dtype=float)  # (14, Q)
        self.q_obs = np.asarray(table["precip_qm"]["observed_quantiles"], dtype=float)  # (14, Q)
        self.et0_a = np.asarray(table["et0_linear"]["a"], dtype=float)  # (14,)
        self.et0_b = np.asarray(table["et0_linear"]["b"], dtype=float)

    @classmethod
    def load(cls, path: Path = CAL_PATH) -> EnsCalibration | None:
        return cls(json.loads(Path(path).read_text(encoding="utf-8"))) if Path(path).exists() else None

    def precip(self, p: np.ndarray) -> np.ndarray:
        """p: (..., 14) member precipitation for lead days 1..14 -> calibrated."""
        out = np.empty_like(p, dtype=float)
        for k in range(p.shape[-1]):
            out[..., k] = np.interp(p[..., k], self.q_fc[k], self.q_obs[k], left=0.0)
        return np.maximum(out, 0.0)

    def et0(self, mk: np.ndarray) -> np.ndarray:
        """mk: (..., 14) Makkink ET0 -> calibrated FAO-56-like ET0."""
        return np.maximum(self.et0_a + self.et0_b * mk, 0.0)


def fit_calibration(fc_p: np.ndarray, obs_p: np.ndarray, fc_mk: np.ndarray, obs_et0: np.ndarray, n_q: int = 201) -> dict[str, Any]:
    """fc_p, fc_mk: (N, M, 14) members; obs_p, obs_et0: (N, 14) observed. Fitted per lead day."""
    qs = np.linspace(0, 1, n_q)
    qf, qo, a, b = [], [], [], []
    for k in range(fc_p.shape[-1]):
        f = fc_p[:, :, k].ravel()
        o = obs_p[:, k]
        f, o = f[np.isfinite(f)], o[np.isfinite(o)]
        qf.append(np.quantile(f, qs))
        qo.append(np.quantile(o, qs))
        m = np.nanmean(fc_mk[:, :, k], axis=1)
        ok = np.isfinite(m) & np.isfinite(obs_et0[:, k])
        bb, aa = np.polyfit(m[ok], obs_et0[ok, k], 1)
        a.append(float(aa))
        b.append(float(bb))
    # strictly increasing forecast quantiles for interpolation
    qf = np.maximum.accumulate(np.asarray(qf) + np.linspace(0, 1e-6, n_q)[None, :], axis=1)
    return {
        "precip_qm": {"quantile_levels": qs.tolist(), "forecast_quantiles": qf.tolist(), "observed_quantiles": np.asarray(qo).tolist()},
        "et0_linear": {"a": a, "b": b},
    }


# --------------------------------------------------------------------------- forecast run
def run_members(d0: np.ndarray, awc: np.ndarray, frac: np.ndarray, kc: np.ndarray, f_t: np.ndarray, p_tab: float,
                precip: np.ndarray, et0: np.ndarray, irr: np.ndarray | None = None) -> dict[str, np.ndarray]:
    """Three-zone model for M weather scenarios over H days from the same state.

    d0 (3,), awc (3,), frac (H, 3), kc / f_t (H,), precip / et0 (M, H) -> per member and day:
    relative water of the zones ``rw`` (M, H, 3) at the END of each day, root-zone ``rw_root`` (M, H),
    ``stress`` (M, H) = Ks < 1, ``deficit_mm`` (M, H) = potential - actual transpiration.
    """
    M, H = precip.shape
    d = np.repeat(d0[None, :], M, axis=0).astype(float)
    awc_b = np.repeat(awc[None, :], M, axis=0)
    rw = np.zeros((M, H, 3))
    rw_root = np.zeros((M, H))
    stress = np.zeros((M, H), dtype=bool)
    deficit = np.zeros((M, H))
    for h in range(H):
        fr = np.repeat(frac[h][None, :], M, axis=0)
        st = step3(d, awc_b, fr, precip[:, h], np.zeros(M) if irr is None else irr[:, h], et0[:, h],
                   np.full(M, kc[h]), np.full(M, f_t[h]), np.full(M, p_tab))
        d = st.d
        rw[:, h] = relative_water(d, awc_b)
        taw_r = (fr * awc_b).sum(axis=1)
        rw_root[:, h] = np.clip(1 - (fr * d).sum(axis=1) / np.maximum(taw_r, 1e-9), 0, 1)
        stress[:, h] = st.ks < 1 - 1e-9
        deficit[:, h] = st.tp - st.t
    return {"rw": rw, "rw_root": rw_root, "stress": stress, "deficit_mm": deficit}


def crps_ensemble(members: np.ndarray, obs: np.ndarray) -> np.ndarray:
    """Fair CRPS of an ensemble: members (M, ...) vs obs (...)."""
    m = members.shape[0]
    term1 = np.abs(members - obs[None]).mean(axis=0)
    srt = np.sort(members, axis=0)
    i = np.arange(1, m + 1).reshape((m,) + (1,) * (members.ndim - 1))
    term2 = (2 * (2 * i - m - 1) * srt).sum(axis=0) / (m * (m - 1)) / 2  # E|X-X'|/2 (fair)
    return term1 - term2
