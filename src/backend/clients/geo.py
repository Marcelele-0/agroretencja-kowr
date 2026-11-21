"""Geometry helpers shared by data clients (WGS84 <-> PUWG 1992, distances, sample points)."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import shapely
from pyproj import Transformer
from shapely.geometry import Point, box, shape
from shapely.geometry.base import BaseGeometry

WGS84 = "EPSG:4326"
PUWG1992 = "EPSG:2180"

_TO_PUWG = Transformer.from_crs(WGS84, PUWG1992, always_xy=True)
_TO_WGS = Transformer.from_crs(PUWG1992, WGS84, always_xy=True)

EARTH_RADIUS_KM = 6371.0088


def _reproject(geom: BaseGeometry, transformer: Transformer) -> BaseGeometry:
    def apply(coords: np.ndarray) -> np.ndarray:
        x, y = transformer.transform(coords[:, 0], coords[:, 1])
        return np.column_stack([x, y])

    return shapely.transform(geom, apply)


def to_puwg92(geom: BaseGeometry) -> BaseGeometry:
    """Reproject WGS84 (lon, lat) geometry to EPSG:2180 (easting, northing)."""
    return _reproject(geom, _TO_PUWG)


def to_wgs84(geom: BaseGeometry) -> BaseGeometry:
    """Reproject EPSG:2180 (easting, northing) geometry to WGS84 (lon, lat)."""
    return _reproject(geom, _TO_WGS)


def parcel_shape(geojson: dict[str, Any]) -> BaseGeometry:
    """Return shapely geometry of a parcel stored as GeoJSON (WGS84)."""
    return shape(geojson)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def interior_sample_points(polygon_wgs84: BaseGeometry, n: int = 5) -> list[Point]:
    """Return up to ``n`` points inside the parcel: one central point plus one per quadrant."""
    poly = to_puwg92(polygon_wgs84)
    points = [poly.representative_point()]
    if n > 1:
        minx, miny, maxx, maxy = poly.bounds
        cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
        quadrants = [
            box(minx, miny, cx, cy),
            box(cx, miny, maxx, cy),
            box(minx, cy, cx, maxy),
            box(cx, cy, maxx, maxy),
        ]
        for quad in quadrants:
            part = poly.intersection(quad)
            if not part.is_empty and part.area > 1.0:
                points.append(part.representative_point())
    return [to_wgs84(p) for p in points[:n]]  # type: ignore[misc]