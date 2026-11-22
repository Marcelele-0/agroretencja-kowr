"""Joint wetness zones (POST /api/zones): shared grid, border continuity, crop borders, contract."""

from __future__ import annotations

import math

import numpy as np
import pytest
from fastapi.testclient import TestClient
from rasterio.transform import from_origin
from shapely.geometry import Point, box, mapping, shape
from shapely.ops import unary_union

from backend.clients.geo import to_puwg92, to_wgs84
from backend.clients.nmt import DemRaster, _finalize
from backend.clients.pipeline import ParcelNotFoundError
from backend.main import app
from backend.map_layers import MapContext, _zones
from backend.zones import ZoneParcelInput, compute_joint_zones
from hydro_core.schemas import ParcelGeometry
from hydro_core.topography import analyse_terrain, terrain_wetness_offset

X0, Y0 = 833310.0, 333510.0  # EPSG:2180, Lublin region


def _z(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    return (
        100.0
        + 0.004 * (x - 833000.0)
        + 1.5 * np.sin(2 * np.pi * x / 260.0) * np.cos(2 * np.pi * y / 310.0)
        + 0.8 * np.sin(2 * np.pi * (x + y) / 170.0)
    )


def _dem(p, res: float, buf: float, source: str = "syn", z=_z) -> DemRaster:
    """Synthetic DEM around a parcel, snapped like the NMT client (GUGiK 1 m / Copernicus 30 m)."""
    minx, miny, maxx, maxy = p.bounds
    left, bottom = math.floor((minx - buf) / res) * res, math.floor((miny - buf) / res) * res
    right, top = math.ceil((maxx + buf) / res) * res, math.ceil((maxy + buf) / res) * res
    w, h = round((right - left) / res), round((top - bottom) / res)
    xs = left + (np.arange(w) + 0.5) * res
    ys = top - (np.arange(h) + 0.5) * res
    gx, gy = np.meshgrid(xs, ys)
    return _finalize(z(gx, gy).astype("float32"), from_origin(left, top, res, res), res, source, res > 10, p)


def _gj(p) -> dict:
    return mapping(to_wgs84(p))


def _shares(fc: dict) -> dict[int, float]:
    return {f["properties"]["value"]: f["properties"]["area_pct"] for f in fc["features"]}


def _class_at(fc: dict, x: float, y: float) -> int | None:
    pt = to_wgs84(Point(x, y))
    for f in fc["features"]:
        if shape(f["geometry"]).buffer(1e-9).contains(pt):
            return int(f["properties"]["value"])
    return None


def _border_classes(zones: dict, a: str, b: str, x_border: float, ys: list[float]) -> list[tuple[int, int]]:
    out = []
    for y in ys:
        ca, cb = _class_at(zones[a], x_border - 3.0, y), _class_at(zones[b], x_border + 3.0, y)
        assert ca is not None and cb is not None
        out.append((ca, cb))
    return out


BOX_A = box(X0, Y0, X0 + 300.0, Y0 + 400.0)
BOX_B = box(X0 + 300.0, Y0, X0 + 600.0, Y0 + 400.0)
BORDER_YS = [Y0 + 10.0 + 2.5 * k for k in range(153)]


@pytest.mark.parametrize(("res", "buf"), [(30.0, 390.0), (1.0, 120.0)])
@pytest.mark.parametrize("ws", [-0.6, 1.3])
def test_single_parcel_matches_map_layer(res: float, buf: float, ws: float) -> None:
    p = BOX_A
    dem = _dem(p, res, buf)
    terrain = analyse_terrain(dem.elevation, dem.transform, dem.resolution_m, dem.parcel_mask)
    old = _shares(_zones(MapContext(_gj(p), ws, terrain, None, [], None, None, None)))
    new_fc = compute_joint_zones([ZoneParcelInput("A", _gj(p), ws)], [dem]).zones["A"]
    new = _shares(new_fc)
    assert abs(sum(new.values()) - 100.0) <= 0.5
    # Same classes; shares differ only by the smoother (upsampled + simplified) zone edges.
    major_old = {v for v, s in old.items() if s >= 3.0}
    assert major_old <= set(new)
    for v in set(old) | set(new):
        assert abs(old.get(v, 0.0) - new.get(v, 0.0)) <= 3.0, (v, old, new)
    assert max(old, key=old.get) == max(new, key=new.get)


def test_adjacent_same_crop_meets_without_border_line() -> None:
    gj_a, gj_b = _gj(BOX_A), _gj(BOX_B)
    # Each parcel has its own DEM raster (different extents), as from the NMT cache.
    dems = [_dem(BOX_A, 30.0, 390.0), _dem(BOX_B, 30.0, 390.0)]
    joint = compute_joint_zones([ZoneParcelInput("A", gj_a, -0.6), ZoneParcelInput("B", gj_b, -0.4)], dems)
    pairs = _border_classes(joint.zones, "A", "B", X0 + 300.0, BORDER_YS)
    diffs = [abs(a - b) for a, b in pairs]
    assert max(diffs) <= 1
    joint_mismatch = sum(d > 0 for d in diffs) / len(diffs)

    solo_a = compute_joint_zones([ZoneParcelInput("A", gj_a, -0.6)], dems[:1]).zones
    solo_b = compute_joint_zones([ZoneParcelInput("B", gj_b, -0.4)], dems[1:]).zones
    solo = _border_classes({**solo_a, **solo_b}, "A", "B", X0 + 300.0, BORDER_YS)
    solo_mismatch = sum(a != b for a, b in solo) / len(solo)

    assert joint_mismatch <= 0.15, joint_mismatch
    assert joint_mismatch < solo_mismatch
    # No class change along (most of) the shared border: longest run of mismatches is short.
    run = best = 0
    for d in diffs:
        run = run + 1 if d else 0
        best = max(best, run)
    assert best * 2.5 < 0.25 * 400.0
    for key in ("A", "B"):
        assert abs(sum(_shares(joint.zones[key]).values()) - 100.0) <= 0.5


def test_crop_border_keeps_own_state_and_same_crop_blends() -> None:
    gj_a, gj_b = _gj(BOX_A), _gj(BOX_B)
    flat: list[DemRaster] = []  # no terrain: classes come from the base state only
    diff = compute_joint_zones(
        [ZoneParcelInput("A", gj_a, -1.6, "pszenica"), ZoneParcelInput("B", gj_b, 0.4, "kukurydza")], flat
    )
    pairs = _border_classes(diff.zones, "A", "B", X0 + 300.0, BORDER_YS)
    assert all(a == -2 and b == 0 for a, b in pairs)
    assert set(_shares(diff.zones["A"])) == {-2}
    assert set(_shares(diff.zones["B"])) == {0}

    same = compute_joint_zones(
        [ZoneParcelInput("A", gj_a, -1.6, "pszenica"), ZoneParcelInput("B", gj_b, 0.4, "pszenica")], flat
    )
    pairs = _border_classes(same.zones, "A", "B", X0 + 300.0, BORDER_YS)
    assert all(abs(a - b) <= 1 for a, b in pairs)
    assert all(a == -1 for a, _ in pairs)  # blended -0.6 at the border on the A side
    # Interiors keep their own state.
    assert _class_at(same.zones["A"], X0 + 50.0, Y0 + 200.0) == -2
    assert _class_at(same.zones["B"], X0 + 550.0, Y0 + 200.0) == 0


def test_narrow_strip_and_far_parcel_get_zones() -> None:
    a = box(X0, Y0, X0 + 200.0, Y0 + 300.0)
    strip = box(X0 + 200.0, Y0, X0 + 204.0, Y0 + 300.0)
    b = box(X0 + 204.0, Y0, X0 + 400.0, Y0 + 300.0)
    far = box(X0 + 2000.0, Y0, X0 + 2100.0, Y0 + 100.0)
    union = unary_union([a, b, far])
    parcels = [
        ZoneParcelInput("a", _gj(a), -1.0),
        ZoneParcelInput("strip", _gj(strip), 2.0),
        ZoneParcelInput("b", _gj(b), -1.0),
        ZoneParcelInput("far", _gj(far), 3.0),
    ]
    joint = compute_joint_zones(parcels, [_dem(union, 30.0, 390.0)])
    assert set(joint.zones) == {"a", "strip", "b", "far"}
    for key, p in (("a", a), ("strip", strip), ("b", b), ("far", far)):
        fc = joint.zones[key]
        assert fc["features"], key
        assert abs(sum(_shares(fc).values()) - 100.0) <= 0.5, key
        cover = unary_union([to_puwg92(shape(f["geometry"])) for f in fc["features"]])
        assert cover.difference(p.buffer(0.01)).area < 0.01 * p.area, key
    # A non-adjacent parcel is not blended with the group.
    assert _class_at(joint.zones["far"], X0 + 2050.0, Y0 + 50.0) is not None


# ------------------------------------------------------------- zones keep the parcel state

HOLLOW_CX, HOLLOW_CY = X0 + 300.0, Y0 + 200.0


def _hollow(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Broad bowl around BOX_A | BOX_B with local undulations: the TPI offset is wet everywhere."""
    r2 = (x - HOLLOW_CX) ** 2 + (y - HOLLOW_CY) ** 2
    return 100.0 + 5.0e-5 * r2 + 1.5 * np.sin(2 * np.pi * x / 180.0) * np.cos(2 * np.pi * y / 216.0)


def _mean_value(fc: dict) -> float:
    s = _shares(fc)
    return sum(v * a for v, a in s.items()) / sum(s.values())


@pytest.mark.parametrize(("res", "buf"), [(30.0, 390.0), (1.0, 240.0)])
@pytest.mark.parametrize("ws", [-1.0, 0.4])
def test_parcel_in_hollow_keeps_its_state(res: float, buf: float, ws: float) -> None:
    p = BOX_A
    dem = _dem(p, res, buf, z=_hollow)
    terrain = analyse_terrain(dem.elevation, dem.transform, dem.resolution_m, dem.parcel_mask)
    raw = terrain_wetness_offset(terrain)[terrain.mask]
    assert raw.min() > 0.0 and raw.mean() > 0.8  # uncentred, the whole parcel would shift wetter
    layer = _zones(MapContext(_gj(p), ws, terrain, None, [], None, None, None))
    joint = compute_joint_zones([ZoneParcelInput("A", _gj(p), ws)], [dem]).zones["A"]
    for fc in (layer, joint):
        assert abs(_mean_value(fc) - ws) <= 0.25, (ws, _shares(fc))
        ranked = sorted(_shares(fc).items(), key=lambda kv: -kv[1])
        assert round(ws) in [v for v, _ in ranked[:2]], ranked
    assert abs(_mean_value(layer) - _mean_value(joint)) <= 0.15


def test_same_crop_neighbours_in_hollow_stay_continuous() -> None:
    dems = [_dem(BOX_A, 30.0, 390.0, z=_hollow), _dem(BOX_B, 30.0, 390.0, z=_hollow)]
    states = {"A": -1.0, "B": -0.4}
    joint = compute_joint_zones(
        [ZoneParcelInput("A", _gj(BOX_A), states["A"], "pszenica"),
         ZoneParcelInput("B", _gj(BOX_B), states["B"], "pszenica")],
        dems,
    )
    diffs = [abs(a - b) for a, b in _border_classes(joint.zones, "A", "B", X0 + 300.0, BORDER_YS)]
    assert max(diffs) <= 1
    assert sum(d > 0 for d in diffs) / len(diffs) <= 0.15
    for key, ws in states.items():
        assert abs(_mean_value(joint.zones[key]) - ws) <= 0.25, (key, _shares(joint.zones[key]))


def test_different_crop_neighbours_keep_own_mean() -> None:
    dems = [_dem(BOX_A, 30.0, 390.0, z=_hollow), _dem(BOX_B, 30.0, 390.0, z=_hollow)]
    states = {"A": -1.6, "B": 0.4}
    joint = compute_joint_zones(
        [ZoneParcelInput("A", _gj(BOX_A), states["A"], "pszenica"),
         ZoneParcelInput("B", _gj(BOX_B), states["B"], "kukurydza")],
        dems,
    )
    for key, ws in states.items():
        assert abs(_mean_value(joint.zones[key]) - ws) <= 0.25, (key, _shares(joint.zones[key]))


# ---------------------------------------------------------------------------------- API


def _parcel_geometry(pid: str, p) -> ParcelGeometry:
    w = to_wgs84(p)
    c = w.centroid
    return ParcelGeometry(
        parcel_id=pid, centroid_lat=c.y, centroid_lon=c.x, area_ha=p.area / 10_000.0,
        bbox_wgs84=tuple(w.bounds), geojson=mapping(w),
    )


class FakeNmt:
    def __init__(self) -> None:
        self.calls = 0

    def group_source(self, parcel_ids: list[str]) -> str:
        return "copernicus"

    async def group_dems(self, parcels: list[ParcelGeometry]) -> list[DemRaster]:
        self.calls += 1
        return [_dem(unary_union([BOX_A, BOX_B]), 30.0, 390.0, "Copernicus DEM GLO-30 (test)")]


class FakeZonesCollector:
    def __init__(self) -> None:
        self.parcels = {"P-A": _parcel_geometry("P-A", BOX_A), "P-B": _parcel_geometry("P-B", BOX_B)}
        self.clients = {"nmt": FakeNmt()}

    async def resolve_parcel(self, parcel_id: str | None = None, lat=None, lon=None):
        if parcel_id not in self.parcels:
            raise ParcelNotFoundError("Nie znaleziono działki w ULDK.")
        return self.parcels[parcel_id], None


@pytest.fixture
def zones_client():
    with TestClient(app) as c:
        fake = FakeZonesCollector()
        app.state.collector = fake
        yield c, fake


def test_zones_endpoint_contract(zones_client) -> None:
    client, fake = zones_client
    body = {"parcels": [
        {"parcel_id": "P-A", "water_state": -0.6, "crop": "pszenica"},
        {"parcel_id": "P-B", "water_state": -0.4},
        {"parcel_id": "P-A", "water_state": 3.0},
        {"parcel_id": "missing", "water_state": 0.0},
    ]}
    r = client.post("/api/zones", json=body)
    assert r.status_code == 200, r.text
    d = r.json()
    assert set(d["zones"]) == {"P-A", "P-B"}
    assert set(d["errors"]) == {"missing"} and d["errors"]["missing"]
    assert d["grid_m"] == 10.0 and "Copernicus" in d["dem_source"] and d["elapsed_s"] >= 0
    for fc in d["zones"].values():
        assert fc["type"] == "FeatureCollection"
        props = [f["properties"] for f in fc["features"]]
        assert all(-5 <= p["value"] <= 5 and p["label"] and p["area_ha"] > 0 for p in props)
        assert abs(sum(p["area_pct"] for p in props) - 100.0) <= 0.5
    # Same request: in-process cache, no second DEM load.
    assert client.post("/api/zones", json=body).json()["zones"] == d["zones"]
    assert fake.clients["nmt"].calls == 1
    # Another crop or water_state recomputes zones from the cached prepared terrain without reloading DEMs.
    body["parcels"][1]["crop"] = "kukurydza"
    body["parcels"][1]["water_state"] = 1.5
    r2 = client.post("/api/zones", json=body)
    assert r2.status_code == 200
    assert fake.clients["nmt"].calls == 1
    expected = compute_joint_zones(
        [
            ZoneParcelInput("P-A", fake.parcels["P-A"].geojson, -0.6, "pszenica"),
            ZoneParcelInput("P-B", fake.parcels["P-B"].geojson, 1.5, "kukurydza"),
        ],
        [_dem(unary_union([BOX_A, BOX_B]), 30.0, 390.0, "Copernicus DEM GLO-30 (test)")],
    )
    assert _shares(r2.json()["zones"]["P-A"]) == _shares(expected.zones["P-A"])
    assert _shares(r2.json()["zones"]["P-B"]) == _shares(expected.zones["P-B"])


@pytest.mark.parametrize("body", [
    {"parcels": []},
    {"parcels": [{"parcel_id": "P-A", "water_state": 7}]},
    {"parcels": [{"parcel_id": "P-A"}]},
    {"parcels": [{"parcel_id": f"P-{i}", "water_state": 0} for i in range(13)]},
])
def test_zones_endpoint_rejects_invalid_input(zones_client, body) -> None:
    client, _ = zones_client
    assert client.post("/api/zones", json=body).status_code == 422
