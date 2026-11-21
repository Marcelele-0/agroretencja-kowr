"""Daily surface runoff RO by the SCS Curve Number method with a slope correction.

RO = (P - Ia)^2 / (P - Ia + S) for P > Ia, with S = 25400 / CN - 254 [mm] and Ia = 0.2 S
(USDA-SCS 1972). Slope correction of CN (Huang et al. 2006, for slopes 0.14-140 %):
CN_slope = CN * (322.79 + 15.63 a) / (a + 323.52), a = slope [m/m].

Base CN for cultivated land in good condition by IUNG drought category; the category is used
as a proxy of the hydrologic soil group (I light/sandy ~ A ... IV heavy ~ C/D). Assumption,
DO WERYFIKACJI Z MENTOREM. Retention measures shift CN (``cn_delta`` in retention_measures.yaml).
"""

from __future__ import annotations

import math

import numpy as np

from hydro_core.schemas import IungSoilCategory

BASE_CN: dict[IungSoilCategory, float] = {
    IungSoilCategory.I: 64.0,
    IungSoilCategory.II: 72.0,
    IungSoilCategory.III: 79.0,
    IungSoilCategory.IV: 83.0,
}
GRASSLAND_CN_DELTA = -10.0  # permanent sward infiltrates better than row crops
CN_MIN, CN_MAX = 30.0, 98.0


def slope_adjusted_cn(cn: float, slope_deg: float) -> float:
    """Huang et al. (2006) slope correction of the curve number."""
    a = math.tan(math.radians(max(0.0, slope_deg)))
    adjusted = cn * (322.79 + 15.63 * a) / (a + 323.52)
    return float(min(CN_MAX, max(CN_MIN, adjusted)))


def parcel_cn(category: IungSoilCategory, slope_deg: float, grassland: bool = False, delta: float = 0.0) -> float:
    """Curve number of the parcel for a soil category, mean slope and optional measures."""
    cn = BASE_CN[category] + (GRASSLAND_CN_DELTA if grassland else 0.0) + delta
    return slope_adjusted_cn(min(CN_MAX, max(CN_MIN, cn)), slope_deg)


def scs_runoff(precip_mm: np.ndarray, cn: np.ndarray | float) -> np.ndarray:
    """Daily runoff [mm] for daily precipitation (vectorised; broadcasts ``cn``)."""
    p = np.asarray(precip_mm, dtype=np.float64)
    s = 25400.0 / np.asarray(cn, dtype=np.float64) - 254.0
    ia = 0.2 * s
    excess = np.maximum(p - ia, 0.0)
    return np.where(excess > 0, excess**2 / np.maximum(excess + s, 1e-9), 0.0)
