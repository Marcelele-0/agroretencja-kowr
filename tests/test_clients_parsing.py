"""Offline tests of data client parsers (no network)."""

from __future__ import annotations

import pytest
from shapely.geometry import Point, shape

from backend.clients.base import SourceUnavailableError
from backend.clients.bdot10k import analyse_network, detect_swap, parse_bdot_gml
from backend.clients.geo import interior_sample_points, to_puwg92
from backend.clients.imgw import nearest_station
from backend.clients.iung_soil import classify_samples
from backend.clients.meteo import grid_cell_key, merge_blocks, split_by_year
from backend.clients.pipeline import build_station_context, choose_soil
from backend.clients.soilgrids import category_from_awc, saxton_rawls_awc
from backend.clients.uldk import build_parcel, normalize_parcel_id, parse_uldk_response
from hydro_core.schemas import IungSoilCategory

ULDK_OK = (
    "0\nSRID=4326;POLYGON((21.5898755360546 52.1716703525845,21.590427010087 52.1722618744221,"
    "21.5903404941328 52.1722747106878,21.5897037202636 52.1722079694071,"
    "21.5893322491612 52.1718521967733,21.5898755360546 52.1716703525845))"
    "|141201_1.0001.6509|mazowieckie|powiat miński|Mińsk Mazowiecki (miasto)|Mińsk Mazowiecki|6509\n"
)

GML = b"""<?xml version="1.0" encoding="UTF-8"?>
<gml:FeatureCollection xmlns:gml="http://www.opengis.net/gml/3.2"
  xmlns:ot="urn:gugik:specyfikacje:gmlas:bazaDanychObiektowTopograficznych10k:2.0">
 <gml:featureMember><ot:OT_SWRM_L gml:id="a">
  <ot:szerokosc uom="m">1.0</ot:szerokosc>
  <ot:geometria><gml:Curve><gml:segments><gml:LineStringSegment>
   <gml:posList>677000.0 481500.0 677000.0 481700.0</gml:posList>
  </gml:LineStringSegment></gml:segments></gml:Curve></ot:geometria>
 </ot:OT_SWRM_L></gml:featureMember>
</gml:FeatureCollection>"""


def test_parse_uldk_and_build_parcel() -> None:
    status, rows = parse_uldk_response(ULDK_OK)
    assert status == 0
    parcel = build_parcel(rows[0])
    assert parcel.parcel_id == "141201_1.0001.6509"
    assert parcel.county == "powiat miński"
    assert 0.1 < parcel.area_ha < 0.6
    assert 52.17 < parcel.centroid_lat < 52.18


def test_uldk_not_found() -> None:
    with pytest.raises(SourceUnavailableError):
        parse_uldk_response("-1 brak wyników\n")


@pytest.mark.parametrize("raw", ["141201_1.0001.6509", " 060606_2.0014.AR_3.756 "])
def test_parcel_id_accepted(raw: str) -> None:
    assert normalize_parcel_id(raw) == raw.strip()


@pytest.mark.parametrize("raw", ["", "abc", "141201_1.0001.6509&request=x", "1" * 100])
def test_parcel_id_rejected(raw: str) -> None:
    with pytest.raises(SourceUnavailableError):
        normalize_parcel_id(raw)


def test_interior_points_inside_parcel() -> None:
    _, rows = parse_uldk_response(ULDK_OK)
    geom = shape(build_parcel(rows[0]).geojson)
    points = interior_sample_points(geom, 5)
    assert 1 <= len(points) <= 5
    assert all(geom.buffer(1e-6).contains(p) for p in points)


def test_iung_majority_and_non_agricultural() -> None:
    res = classify_samples([2, 2, 3, 13, 2])
    assert res.category == IungSoilCategory.II
    assert res.non_agricultural_points == ["teren zabudowany"]
    with pytest.raises(SourceUnavailableError):
        classify_samples([11, 13, None])
    assert classify_samples([5, 5, 2]).category == IungSoilCategory.IV


def test_saxton_rawls_light_vs_loam() -> None:
    sand_awc = saxton_rawls_awc(90, 3, 1.0) * 1000
    loam_awc = saxton_rawls_awc(40, 20, 2.5) * 1000
    assert category_from_awc(sand_awc) == IungSoilCategory.I
    assert category_from_awc(loam_awc) in (IungSoilCategory.II, IungSoilCategory.III)


def test_meteo_blocks_split_and_merge() -> None:
    block = {
        "time": ["2024-12-31", "2025-01-01"],
        "precipitation_sum": [1.0, 2.0],
        "et0_fao_evapotranspiration": [0.1, 0.2],
        "temperature_2m_mean": [0, 1],
        "temperature_2m_max": [1, 2],
        "temperature_2m_min": [-1, 0],
    }
    years = split_by_year(block)
    assert set(years) == {2024, 2025}
    merged = merge_blocks(years[2024], block)
    assert merged["time"] == ["2024-12-31", "2025-01-01"]
    assert grid_cell_key(52.17, 21.59) == "52.15_21.60"


def test_nearest_station_and_lapse_rate() -> None:
    stations = [
        {"kod_stacji": "1", "nazwa_stacji": "DALEKO", "lat": "50.0", "lon": "20.0", "wysokosc_npm": "300", "temperatura_powietrza": "5"},
        {"kod_stacji": "2", "nazwa_stacji": "BLISKO", "lat": "52.2", "lon": "21.6", "wysokosc_npm": "100", "temperatura_powietrza": "4.0"},
        {"kod_stacji": "3", "nazwa_stacji": "BEZ T", "lat": "52.17", "lon": "21.59", "wysokosc_npm": "100", "temperatura_powietrza": None},
    ]
    obs = nearest_station(stations, 52.17, 21.59, far_km=25)
    assert obs is not None and obs.station_name == "Blisko" and not obs.is_far
    ctx = build_station_context(obs, parcel_elevation_m=200.0)
    assert ctx is not None and ctx.elevation_diff_m == 100.0
    assert ctx.lapse_corrected_temp_c == pytest.approx(3.35, abs=0.05)


def test_bdot_gml_parse_axis_and_analysis() -> None:
    feats = parse_bdot_gml(GML, "OT_SWRM_L")
    assert len(feats) == 1 and feats[0]["w"] == 1.0
    assert detect_swap(feats, ref_e=677000, ref_n=481600) is False
    assert detect_swap(feats, ref_e=481600, ref_n=677000) is True

    parcel = to_puwg92(Point(21.5898, 52.1719)).buffer(30)
    county = {"powiat": "1412", "classes": {"OT_SWRM_L": feats, "OT_SWRS_L": [], "OT_SWKN_L": [], "OT_PTWP_A": []}}
    ctx = analyse_network(county, parcel, radius_m=500)
    assert ctx.nearest_ditch_m is not None and ctx.nearest_ditch_m < 100
    assert ctx.ditch_length_in_radius_m > 100
    assert ctx.features_geojson["features"]


def test_choose_soil_layers() -> None:
    assert choose_soil(None, None)[0] is None
    iung = classify_samples([3, 3, 3])
    assert choose_soil(iung, None)[0] == IungSoilCategory.III


def test_dem_cache_roundtrip(tmp_path) -> None:
    import numpy as np

    from backend.clients.nmt import DemRaster, load_dem, save_dem

    dem = DemRaster(
        elevation=np.arange(12, dtype="float32").reshape(3, 4),
        transform=(1.0, 0.0, 500000.0, 0.0, -1.0, 400000.0),
        resolution_m=1.0,
        source="test",
        is_surface_model=False,
        parcel_mask=np.ones((3, 4), dtype=bool),
        mean_elevation_m=5.5,
        min_elevation_m=0.0,
        max_elevation_m=11.0,
    )
    path = tmp_path / "nmt" / "x.npz"
    save_dem(path, dem)
    loaded = load_dem(path)
    assert loaded.elevation.shape == (3, 4)
    assert loaded.transform == dem.transform
    assert loaded.mean_elevation_m == 5.5