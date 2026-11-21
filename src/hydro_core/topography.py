"""Terrain analysis of the parcel DEM: slope, aspect, depressions, D8 flow, accumulation, TWI.

Pure NumPy, no I/O. Input: elevation grid in a metric CRS (EPSG:2180) with an affine transform
and the parcel mask. Large 1 m grids are averaged to at most ``max_cells_per_side`` cells per
side first, so the whole analysis stays well under one second.

* Depressions: priority-flood fill (Barnes et al. 2014); depth = filled - original.
* Flow: D8 steepest descent on the filled surface; accumulation in descending elevation order.
* TWI = ln(a / tan beta) with a = specific catchment area [m] (Beven & Kirkby 1979).
"""

from __future__ import annotations

import heapq
import math
import warnings
from dataclasses import dataclass

import numpy as np

# D8 neighbour offsets (row, col) and their distance factor
_OFFSETS = ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1))
_DIST = tuple(math.hypot(dr, dc) for dr, dc in _OFFSETS)
MIN_DEPRESSION_DEPTH_M = 0.05


@dataclass
class TerrainAnalysis:
    """Grids (analysis resolution) and parcel-level metrics."""

    resolution_m: float
    transform: tuple[float, float, float, float, float, float]
    mask: np.ndarray
    elevation: np.ndarray
    slope_deg: np.ndarray
    aspect_deg: np.ndarray
    accumulation_cells: np.ndarray
    flow_to: np.ndarray  # flat index of the D8 receiver, -1 = outlet
    twi: np.ndarray
    depression_depth_m: np.ndarray
    mean_slope_deg: float
    p90_slope_deg: float
    mean_twi: float
    p90_twi: float
    south_facing_share: float
    wet_zone_share: float
    depression_volume_m3: float
    depression_area_m2: float
    relief_m: float
    outlet_rc: tuple[int, int] | None
    wettest_rc: tuple[int, int] | None
    source: str = ""

    def cell_center(self, row: int, col: int) -> tuple[float, float]:
        a, b, c, d, e, f = self.transform
        x = c + (col + 0.5) * a + (row + 0.5) * b
        y = f + (col + 0.5) * d + (row + 0.5) * e
        return x, y


def _block_mean(arr: np.ndarray, k: int) -> np.ndarray:
    if k <= 1:
        return arr.astype(np.float64)
    h, w = arr.shape
    hh, ww = math.ceil(h / k) * k, math.ceil(w / k) * k
    pad = np.full((hh, ww), np.nan)
    pad[:h, :w] = arr
    blocks = pad.reshape(hh // k, k, ww // k, k)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN blocks -> NaN
        return np.nanmean(blocks, axis=(1, 3))


def _block_any(mask: np.ndarray, k: int) -> np.ndarray:
    if k <= 1:
        return mask.astype(bool)
    h, w = mask.shape
    hh, ww = math.ceil(h / k) * k, math.ceil(w / k) * k
    pad = np.zeros((hh, ww), dtype=float)
    pad[:h, :w] = mask
    return pad.reshape(hh // k, k, ww // k, k).mean(axis=(1, 3)) >= 0.5


def _priority_flood(dem: np.ndarray, eps: float = 1e-4) -> np.ndarray:
    """Fill depressions so every cell drains to the grid edge (epsilon gradient keeps flow)."""
    h, w = dem.shape
    filled = dem.copy()
    closed = np.zeros((h, w), dtype=bool)
    heap: list[tuple[float, int, int]] = []
    for r in range(h):
        for c in (0, w - 1):
            if not closed[r, c]:
                heapq.heappush(heap, (filled[r, c], r, c))
                closed[r, c] = True
    for c in range(1, w - 1):
        for r in (0, h - 1):
            if not closed[r, c]:
                heapq.heappush(heap, (filled[r, c], r, c))
                closed[r, c] = True
    while heap:
        z, r, c = heapq.heappop(heap)
        for dr, dc in _OFFSETS:
            rr, cc = r + dr, c + dc
            if 0 <= rr < h and 0 <= cc < w and not closed[rr, cc]:
                closed[rr, cc] = True
                if filled[rr, cc] <= z:
                    filled[rr, cc] = z + eps
                heapq.heappush(heap, (filled[rr, cc], rr, cc))
    return filled


def _d8_receivers(filled: np.ndarray, cell: float) -> np.ndarray:
    """Flat index of the steepest-descent neighbour, -1 where no lower neighbour (edge outlet)."""
    h, w = filled.shape
    pad = np.pad(filled, 1, constant_values=np.inf)
    best_drop = np.zeros((h, w))
    best = np.full((h, w), -1, dtype=np.int64)
    rows, cols = np.indices((h, w))
    for (dr, dc), dist in zip(_OFFSETS, _DIST, strict=True):
        neigh = pad[1 + dr : 1 + dr + h, 1 + dc : 1 + dc + w]
        drop = (filled - neigh) / (dist * cell)
        better = drop > best_drop
        best_drop = np.where(better, drop, best_drop)
        target = (rows + dr) * w + (cols + dc)
        best = np.where(better, target, best)
    return best.ravel()


def _accumulate(filled: np.ndarray, receivers: np.ndarray) -> np.ndarray:
    """Number of upstream cells including the cell itself."""
    acc = np.ones(filled.size)
    order = np.argsort(filled.ravel())[::-1]
    for i in order:
        j = receivers[i]
        if j >= 0:
            acc[j] += acc[i]
    return acc.reshape(filled.shape)


def analyse_terrain(
    elevation: np.ndarray,
    transform: tuple[float, float, float, float, float, float],
    resolution_m: float,
    parcel_mask: np.ndarray,
    *,
    max_cells_per_side: int = 300,
    source: str = "",
) -> TerrainAnalysis:
    """Full terrain analysis; raises ValueError when the parcel has no elevation values."""
    k = max(1, math.ceil(max(elevation.shape) / max_cells_per_side))
    dem = _block_mean(np.asarray(elevation, dtype=np.float64), k)
    mask = _block_any(np.asarray(parcel_mask, dtype=bool), k)
    if not mask.any():
        r, c = np.unravel_index(int(np.nanargmin(np.abs(dem - np.nanmean(dem)))), dem.shape)
        mask[r, c] = True
    cell = float(resolution_m) * k
    a, b, c0, d, e, f = transform
    tr = (a * k, b * k, c0, d * k, e * k, f)

    finite = np.isfinite(dem)
    if not (finite & mask).any():
        raise ValueError("Brak wysokości terenu w granicach działki.")
    dem = np.where(finite, dem, np.nanmax(dem) + 1.0)

    gy, gx = np.gradient(dem, cell)
    slope = np.degrees(np.arctan(np.hypot(gx, gy)))
    # aspect: 0 = north, clockwise; rows grow southwards, so -gy points north
    aspect = (np.degrees(np.arctan2(-gx, gy)) + 360.0) % 360.0

    filled = _priority_flood(dem)
    depth = np.where(filled - dem > MIN_DEPRESSION_DEPTH_M, filled - dem, 0.0)
    receivers = _d8_receivers(filled, cell)
    acc = _accumulate(filled, receivers)
    tan_b = np.maximum(np.tan(np.radians(slope)), 0.001)
    twi = np.log(acc * cell / tan_b)

    m = mask & finite
    sl_in, twi_in = slope[m], twi[m]
    wet_threshold = float(np.percentile(twi[finite], 80))
    acc_in = np.where(m, acc, -1.0)
    outlet = np.unravel_index(int(np.argmax(acc_in)), acc.shape)
    twi_masked = np.where(m, twi, -np.inf)
    wettest = np.unravel_index(int(np.argmax(twi_masked)), twi.shape)
    south = (aspect[m] >= 135) & (aspect[m] <= 225) & (sl_in > 1.0)

    return TerrainAnalysis(
        resolution_m=cell,
        transform=tr,
        mask=m,
        elevation=dem,
        slope_deg=slope,
        aspect_deg=aspect,
        accumulation_cells=acc,
        flow_to=receivers,
        twi=twi,
        depression_depth_m=depth,
        mean_slope_deg=round(float(sl_in.mean()), 2),
        p90_slope_deg=round(float(np.percentile(sl_in, 90)), 2),
        mean_twi=round(float(twi_in.mean()), 2),
        p90_twi=round(float(np.percentile(twi_in, 90)), 2),
        south_facing_share=round(float(south.mean()), 3),
        wet_zone_share=round(float((twi_in > wet_threshold).mean()), 3),
        depression_volume_m3=round(float(depth[m].sum() * cell * cell), 1),
        depression_area_m2=round(float((depth[m] > 0).sum() * cell * cell), 1),
        relief_m=round(float(dem[m].max() - dem[m].min()), 2),
        outlet_rc=(int(outlet[0]), int(outlet[1])),
        wettest_rc=(int(wettest[0]), int(wettest[1])),
        source=source,
    )


def twi_relative(analysis: TerrainAnalysis) -> np.ndarray:
    """TWI of each cell relative to the parcel: z-score clipped to [-2, 2] (NaN outside parcel)."""
    vals = analysis.twi[analysis.mask]
    std = float(vals.std()) or 1.0
    z = (analysis.twi - float(vals.mean())) / std
    return np.where(analysis.mask, np.clip(z, -2.0, 2.0), np.nan)
