"""Synthetic, network-free inputs shared by engine and API tests (imported by conftest.py)."""

from __future__ import annotations

import datetime as dt

import numpy as np

from backend.clients.bdot10k import HydroNetworkContext
from backend.clients.iung_soil import NonAgriculturalParcelError, classify_samples
from backend.clients.meteo import DailyWeatherSeries
from backend.clients.nmt import DemRaster
from backend.clients.pipeline import ParcelDataBundle
from hydro_core.crops import crop_params, taw_for
from hydro_core.fao_balance import BalanceScenario, run_balance
from hydro_core.features import AnalysisInputs, FeatureContext, HydroContext
from hydro_core.history import kbw_for_year
from hydro_core.runoff import parcel_cn
from hydro_core.schemas import CropCode, IungSoilCategory, ParcelGeometry
from hydro_core.timeline import build_timeline
from hydro_core.topography import analyse_terrain

ANALYSIS_DATE = dt.date(2021, 7, 10)
TODAY = dt.date(2026, 11, 21)
PARCEL_GEOJSON = {
    "type": "Polygon",
    "coordinates": [[[16.99, 52.60], [16.995, 52.60], [16.995, 52.603], [16.99, 52.603], [16.99, 52.60]]],
}


def synthetic_weather(end: dt.date = ANALYSIS_DATE, seed: int = 0, dry_last_days: int = 0) -> DailyWeatherSeries:
    rng = np.random.default_rng(seed)
    start = dt.date(end.year - 10, 1, 1)
    days = [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]
    doy = np.array([d.timetuple().tm_yday for d in days])
    season = np.sin(np.pi * np.clip(doy - 30, 0, 300) / 300)
    et0 = np.round(0.3 + 4.2 * season, 2)
    precip = np.round(rng.gamma(0.45, 4.5, len(days)), 1)
    if dry_last_days:
        precip[-dry_last_days:] = 0.0
    tmax = np.round(2 + 25 * season, 1)
    return DailyWeatherSeries(
        dates=days, precipitation_mm=precip.tolist(), et0_mm=et0.tolist(), tmean_c=(tmax - 6).tolist(),
        tmax_c=tmax.tolist(), tmin_c=(tmax - 12).tolist(), providers=["open-meteo"], grid_elevation_m=90.0,
    )


def synthetic_dem(slope_x: float = 0.05) -> DemRaster:
    y, x = np.mgrid[0:120, 0:120]
    elevation = (100 + slope_x * (120 - x) + 0.01 * y).astype("float32")
    elevation[60:66, 40:46] -= 0.6  # a small closed depression
    mask = np.zeros((120, 120), dtype=bool)
    mask[20:100, 20:100] = True
    return DemRaster(
        elevation=elevation, transform=(2.0, 0.0, 400000.0, 0.0, -2.0, 500000.0), resolution_m=2.0,
        source="synthetic", is_surface_model=False, parcel_mask=mask,
        mean_elevation_m=100.0, min_elevation_m=98.0, max_elevation_m=106.0,
    )


def make_inputs(
    crop: CropCode = CropCode.KUKURYDZA,
    category: IungSoilCategory = IungSoilCategory.II,
    analysis_date: dt.date = ANALYSIS_DATE,
    weather: DailyWeatherSeries | None = None,
    dem: DemRaster | None = None,
) -> FeatureContext:
    w = weather or synthetic_weather(analysis_date)
    tl = build_timeline(
        dates=w.dates, precipitation_mm=w.precipitation_mm, et0_mm=w.et0_mm, tmax_c=w.tmax_c,
        analysis_date=analysis_date, today=TODAY,
    )
    d = dem or synthetic_dem()
    terrain = analyse_terrain(d.elevation, d.transform, d.resolution_m, d.parcel_mask)
    cp = crop_params(crop)
    taw = taw_for(category)
    cn = parcel_cn(category, terrain.mean_slope_deg)
    trace = run_balance(tl, cp, [BalanceScenario("baseline", taw, cn)])
    inputs = AnalysisInputs(
        analysis_date=analysis_date, today=TODAY, crop_code=crop, crop=cp, soil_category=category,
        soil_source="IUNG-PIB", taw_mm=taw, cn=cn, timeline=tl, trace=trace, kbw_periods=[],
        parcel_area_ha=12.0, terrain=terrain, hydro=HydroContext(nearest_ditch_m=40.0, ditches_touching_parcel=0),
    )
    fx = FeatureContext(inputs)
    inputs.kbw_periods = kbw_for_year(fx, analysis_date.year)
    return FeatureContext(inputs)



def make_bundle(
    analysis_date: dt.date = ANALYSIS_DATE, iung_values: list[int | None] | None = None, weather: DailyWeatherSeries | None = None
) -> ParcelDataBundle:
    parcel = ParcelGeometry(
        parcel_id="302111_5.0015.72", centroid_lat=52.6015, centroid_lon=16.9925, area_ha=12.0,
        bbox_wgs84=(16.99, 52.60, 16.995, 52.603), geojson=PARCEL_GEOJSON, voivodeship="wielkopolskie",
        county="powiat poznański", commune="Murowana Goślina", region="Test", parcel_number="72",
    )
    try:
        iung = classify_samples(iung_values or [2, 2, 2, 3, 2])
        non_ag = None
        category = iung.category
    except NonAgriculturalParcelError as exc:
        iung, category = None, None
        non_ag = exc.land_cover
    return ParcelDataBundle(
        parcel=parcel, analysis_date=analysis_date, weather_history=weather or synthetic_weather(analysis_date),
        weather_forecast=None, weather_station=None, sentinel2=None, dem=synthetic_dem(), soil_iung=iung,
        soil_grids=None, soil_category=category, soil_source_layer="IUNG-PIB (test)",
        hydro_network=HydroNetworkContext(
            powiat_teryt="3021", search_radius_m=500, nearest_ditch_m=40.0, ditch_length_in_radius_m=300.0,
            ditches_touching_parcel=0, nearest_watercourse_m=None, nearest_water_body_m=None, features=[],
            features_geojson={"type": "FeatureCollection", "features": []}, bdot_data_date=None,
        ),
        audit=[], total_time_s=0.1, non_agricultural=non_ag,
    )
