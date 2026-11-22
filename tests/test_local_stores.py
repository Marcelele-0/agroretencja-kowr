"""Offline tests for national local data readers (SoilGrids, meteo grid, Copernicus DEM)."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from backend.clients.base import SourceUnavailableError
from backend.clients.geo import parcel_shape, to_puwg92
from backend.clients.meteo import OPEN_METEO, lookup_local_meteo_year
from backend.clients.nmt import _read_copernicus
from backend.clients.soilgrids import (
    NO_DATA_MESSAGE,
    SoilGridsProfile,
    parse_soilgrids,
    read_local_soilgrids,
)


def _write_tif(path: Path, arr: np.ndarray, *, left: float, top: float, res: float, nodata: float | None = -32768) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=arr.shape[0],
        width=arr.shape[1],
        count=1,
        dtype=arr.dtype,
        crs="EPSG:4326",
        transform=from_origin(left, top, res, res),
        nodata=nodata,
    ) as dst:
        dst.write(arr, 1)


def test_read_local_soilgrids_matches_parse_and_uses_3x3_fallback(tmp_path: Path) -> None:
    assert read_local_soilgrids(tmp_path, 52.05, 16.73) is None

    depths = ("0-5cm", "5-15cm", "15-30cm", "30-60cm", "60-100cm")
    raw_means = {"sand": 550, "clay": 150, "soc": 120, "bdod": 145}
    for prop, val in raw_means.items():
        for depth in depths:
            grid = np.full((5, 5), val, dtype=np.int16)
            if prop == "sand" and depth == "0-5cm":
                grid[2, 2] = -32768  # force 3x3 neighbor mean
            _write_tif(tmp_path / f"{prop}_{depth}.tif", grid, left=16.70, top=52.10, res=0.02, nodata=-32768)

    prof = read_local_soilgrids(tmp_path, 52.05, 16.73)
    assert isinstance(prof, SoilGridsProfile)

    # Same inputs via JSON parser
    payload = {
        "properties": {
            "layers": [
                {
                    "name": prop,
                    "unit_measure": {"d_factor": 100 if prop == "bdod" else 10},
                    "depths": [{"label": d, "values": {"mean": val}} for d in depths],
                }
                for prop, val in raw_means.items()
            ]
        }
    }
    expected = parse_soilgrids(payload)
    assert prof.awc_mm_0_100 == expected.awc_mm_0_100
    assert prof.estimated_category == expected.estimated_category
    assert prof.layers[0].bulk_density_g_cm3 == pytest.approx(1.45, abs=1e-2)

    # Out of bounds -> None (lets caller fall back to REST API)
    assert read_local_soilgrids(tmp_path, 55.0, 22.0) is None

    # All-nodata 3x3 patch -> SourceUnavailableError (no data at point)
    for depth in depths:
        nod = np.full((5, 5), -32768, dtype=np.int16)
        _write_tif(tmp_path / f"sand_{depth}.tif", nod, left=16.70, top=52.10, res=0.02, nodata=-32768)
    with pytest.raises(SourceUnavailableError, match=NO_DATA_MESSAGE[:20]):
        read_local_soilgrids(tmp_path, 52.05, 16.73)


def test_lookup_local_meteo_year(tmp_path: Path) -> None:
    npz = tmp_path / "poland_grid_2016_2025.npz"
    dates = np.array(["2020-01-01", "2020-01-02", "2021-01-01", "2021-01-02"])
    precip = np.array([[1.2, 0.0, 3.4, 0.5], [10.0, 11.0, 12.0, 13.0]], dtype=np.float32)
    et0 = np.array([[0.4, 0.5, 0.6, 0.7], [1.0, 1.1, 1.2, 1.3]], dtype=np.float32)
    tmean = np.array([[2.0, 3.0, 4.0, 5.0], [6.0, 7.0, 8.0, 9.0]], dtype=np.float32)
    np.savez_compressed(
        npz,
        lats=np.array([52.0, 50.0], dtype=np.float64),
        lons=np.array([16.8, 20.0], dtype=np.float64),
        elevations=np.array([85.0, 220.0], dtype=np.float64),
        dates=dates,
        precip=precip,
        et0=et0,
        tmean=tmean,
        tmax=tmean + 3.0,
        tmin=tmean - 3.0,
    )

    hit = lookup_local_meteo_year(npz, 52.05, 16.74, 2021)
    assert hit is not None
    assert hit["time"] == ["2021-01-01", "2021-01-02"]
    assert hit["precipitation_sum"] == [3.4, 0.5]
    assert hit["provider"] == OPEN_METEO
    assert hit["elevation"] == pytest.approx(85.0)

    # Far away (> 35 km) or missing year -> None
    assert lookup_local_meteo_year(npz, 54.5, 18.5, 2021) is None
    assert lookup_local_meteo_year(npz, 52.05, 16.74, 2026) is None


def test_read_copernicus_prefers_local_tif_over_network(tmp_path: Path) -> None:
    tile = tmp_path / "Copernicus_DSM_COG_10_N52_00_E016_00_DEM.tif"
    elev = np.full((60, 60), 92.5, dtype=np.float32)
    _write_tif(tile, elev, left=16.5, top=52.2, res=0.005, nodata=-9999.0)

    parcel_wgs = {
        "type": "Polygon",
        "coordinates": [[[16.73, 52.055], [16.735, 52.055], [16.735, 52.058], [16.73, 52.058], [16.73, 52.055]]],
    }
    p2180 = to_puwg92(parcel_shape(parcel_wgs))
    minx, miny, maxx, maxy = p2180.bounds
    buf = 120.0
    bounds = (
        math.floor((minx - buf) / 30.0) * 30.0,
        math.floor((miny - buf) / 30.0) * 30.0,
        math.ceil((maxx + buf) / 30.0) * 30.0,
        math.ceil((maxy + buf) / 30.0) * 30.0,
    )
    dem = _read_copernicus(
        "https://offline.invalid/{name}.tif",
        (16.73, 52.055, 16.735, 52.058),
        bounds,
        p2180,
        local_dir=tmp_path,
    )
    assert dem.mean_elevation_m == pytest.approx(92.5, abs=0.1)
