"""E9: three-zone soil-water model of a parcel (0-28 / 28-100 / 100-200 cm).

The zones follow the ERA5-Land soil layers (0-7 + 7-28, 28-100, 100-255 cm) so that the model can
be initialised from and verified against ERA5-Land. Capacities come from the ISRIC SoilGrids
retention profile (theta at -33 and -1500 kPa) and are scaled so that 0-100 cm holds the TAW of the
IUNG-PIB category (keeps consistency with the drought monitor SMSR); without a profile the water is
spread evenly with depth.

Daily step (FAO-56 tipping bucket, extended to layers):
* infiltration P + I fills the zones from the top; what does not fit leaves as deep percolation;
* ETc = Kc ET0 is split into potential evaporation (1 - f_T) ETc and potential transpiration
  f_T ETc, f_T = canopy share from the Kc curve (0.1 at Kc_ini -> 1 at Kc_mid);
* evaporation dries only the top zone (Kr = 1 until 30 % of its water is used, then linear to 0);
* transpiration uses the root zone 0..Zr(t): Ks from the root-zone depletion as in FAO-56
  (RAW = p TAW_r), water taken from the zones in proportion to their accessible water;
* roots of annual crops grow from 0.2 m at the season start to Zr_max at the end of development;
  winter and perennial crops keep Zr_max.
Mass balance per day: dS = P + I - E - T - DP (closes exactly).

State variables are depletions below field capacity D_z [mm]; the relative water content of a zone
is ``1 - D_z / AWC_z`` (0 = wilting point, 1 = field capacity), shown to farmers as a class (no
false precision).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from .config import CropParams, crops_raw
from .simulator import adjusted_p

ZONE_BOUNDS_M = np.array([[0.0, 0.28], [0.28, 1.0], [1.0, 2.0]])
ZONE_LABELS_PL = ("wierzch 0-28 cm", "strefa korzeni 28-100 cm", "glebia 100-200 cm")
SOILGRIDS_DEPTHS_CM = [(0, 5), (5, 15), (15, 30), (30, 60), (60, 100), (100, 200)]
# winter / perennial crops: roots already at full depth when the season starts
FULL_ROOTS = {"pszenica_ozima", "rzepak", "chmiel", "drzewa_owocowe", "krzewy_owocowe", "truskawki", "uzytki_zielone"}
ROOT_START_M = 0.2
EVAP_STAGE1_FRAC = 0.3


def zone_awc_from_profile(profile: dict[str, Any] | None, taw_0_100: float) -> np.ndarray:
    """(3,) available water [mm] of the zones; 0-100 cm scaled to ``taw_0_100`` (IUNG category)."""
    dens = None
    if profile and "wv0033" in profile and "wv1500" in profile:
        fc, wp = profile["wv0033"]["by_depth"], profile["wv1500"]["by_depth"]
        layer = []
        for top, bot in SOILGRIDS_DEPTHS_CM:
            key = f"{top}-{bot}cm"
            if fc.get(key) is None or wp.get(key) is None:
                layer = []
                break
            layer.append(max((fc[key] - wp[key]) / 100.0, 0.0))  # volumetric fraction
        if layer:
            dens = layer
    awc = np.zeros(3)
    for z, (zt, zb) in enumerate(ZONE_BOUNDS_M * 100):
        for (top, bot), d in zip(SOILGRIDS_DEPTHS_CM, dens or [1.0] * 6):
            ov = max(0.0, min(zb, bot) - max(zt, top))
            awc[z] += d * ov * 10.0  # cm -> mm
    scale = taw_0_100 / max(awc[0] + awc[1], 1e-9)
    return awc * scale


def max_root_depth(crop: CropParams) -> float:
    rng = crops_raw()["crops"][crop.key].get("max_root_depth_m", [1.0, 1.0])
    return float(min(np.mean(rng), 1.5))


def root_depth_curve(crop: CropParams) -> np.ndarray:
    """(L,) root depth [m] per season day."""
    zmax = max_root_depth(crop)
    L = crop.length
    if crop.key in FULL_ROOTS:
        return np.full(L, zmax)
    grow = crop.l_ini + crop.l_dev
    i = np.arange(L)
    return ROOT_START_M + (zmax - ROOT_START_M) * np.clip(i / max(grow, 1), 0, 1)


def canopy_share(crop: CropParams) -> np.ndarray:
    kc = crop.kc_curve()
    span = max(crop.kc_mid - crop.kc_ini, 1e-6)
    return np.clip(0.1 + 0.9 * (kc - crop.kc_ini) / span, 0.1, 1.0)


def root_fractions(zr: np.ndarray) -> np.ndarray:
    """(..., 3) share of each zone inside the root zone 0..zr."""
    zr = np.asarray(zr)[..., None]
    top, bot = ZONE_BOUNDS_M[:, 0], ZONE_BOUNDS_M[:, 1]
    return np.clip((np.minimum(zr, bot) - top) / (bot - top), 0.0, 1.0)


@dataclass
class Step3:
    d: np.ndarray  # (B, 3) depletion after the day
    e: np.ndarray  # (B,) actual evaporation
    t: np.ndarray  # (B,) actual transpiration
    tp: np.ndarray  # (B,) potential transpiration
    etc: np.ndarray  # (B,)
    dp: np.ndarray  # (B,) deep percolation
    ks: np.ndarray  # (B,)
    dr: np.ndarray  # (B,) root-zone depletion at the start of the day
    taw_r: np.ndarray  # (B,) root-zone TAW
    raw_r: np.ndarray  # (B,)


def step3(d: np.ndarray, awc: np.ndarray, frac: np.ndarray, p: np.ndarray, irr: np.ndarray, et0: np.ndarray,
          kc: np.ndarray, f_t: np.ndarray, p_tab: np.ndarray) -> Step3:
    """One day for B parcels. d, awc, frac: (B, 3); others (B,)."""
    d = np.minimum(d.copy(), awc)
    taw_r = (frac * awc).sum(axis=1)
    dr = (frac * d).sum(axis=1)
    etc = kc * et0
    raw_r = adjusted_p(p_tab, etc) * taw_r
    ks = np.where(dr <= raw_r, 1.0, (taw_r - dr) / np.maximum(taw_r - raw_r, 1e-9))
    ks = np.clip(np.where(taw_r > 0, ks, 0.0), 0.0, 1.0)
    # infiltration from the top
    w = p + irr
    for z in range(3):
        take = np.minimum(w, d[:, z])
        d[:, z] -= take
        w = w - take
    dp = w
    # evaporation from the top zone
    ep = (1.0 - f_t) * etc
    kr = np.clip((awc[:, 0] - d[:, 0]) / np.maximum((1 - EVAP_STAGE1_FRAC) * awc[:, 0], 1e-9), 0.0, 1.0)
    e = np.minimum(ep * kr, awc[:, 0] - d[:, 0])
    d[:, 0] += e
    # transpiration from the root zone
    tp = f_t * etc
    avail = frac * (awc - d)
    tot = avail.sum(axis=1)
    t = np.minimum(ks * tp, tot)
    share = np.where(tot[:, None] > 0, avail / np.maximum(tot[:, None], 1e-12), 0.0)
    d += share * t[:, None]
    d = np.minimum(d, awc)
    return Step3(d=d, e=e, t=t, tp=tp, etc=etc, dp=dp, ks=ks, dr=dr, taw_r=taw_r, raw_r=raw_r)


def relative_water(d: np.ndarray, awc: np.ndarray) -> np.ndarray:
    """Relative water content 0 (wilting point) .. 1 (field capacity)."""
    return np.clip(1.0 - d / np.maximum(awc, 1e-9), 0.0, 1.0)


def water_class(rw: np.ndarray) -> np.ndarray:
    """0..4 = bardzo sucho, sucho, umiarkowanie, wilgotno, pelny zapas (thresholds 0.2/0.4/0.6/0.8)."""
    return np.digitize(rw, [0.2, 0.4, 0.6, 0.8])


WATER_CLASS_PL = ("bardzo sucho", "sucho", "umiarkowany zapas", "wilgotno", "pelny zapas")


def spin_up3(precip: np.ndarray, et0: np.ndarray, awc: np.ndarray, kc: float = 0.35, f_t: float = 0.3, p_tab: float = 0.55,
             d0: np.ndarray | None = None, root_m: float = 1.5) -> np.ndarray:
    """Depletion after running the days before the season (B, n) from ``d0`` (default: full profile).
    The previous crop / winter cover takes water down to ``root_m``, so the deep zone keeps the
    memory of dry summers and autumns that the winter did not refill."""
    B = precip.shape[0]
    d = np.zeros((B, 3)) if d0 is None else d0.copy()
    frac = np.broadcast_to(root_fractions(np.array(root_m)), (B, 3))
    kcv, ftv, ptv = np.full(B, kc), np.full(B, f_t), np.full(B, p_tab)
    zero = np.zeros(B)
    for i in range(precip.shape[1]):
        ok = np.isfinite(precip[:, i]) & np.isfinite(et0[:, i])
        st = step3(d, awc, frac, np.nan_to_num(precip[:, i]), zero, np.nan_to_num(et0[:, i]), kcv, ftv, ptv)
        d = np.where(ok[:, None], st.d, d)
    return d
