"""Run the data collection for parcels and print per-source timings, modes and key values.

Usage:
    uv run python scripts/probe_sources.py --parcel 141201_1.0001.6509 --date 2025-07-15
    uv run python scripts/probe_sources.py --parcel 141201_1.0001.6509 --parcel 060405_2.0223.756
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import date

from backend.clients.nmt import pending_background_tasks
from backend.clients.pipeline import ParcelDataCollector, ParcelNotFoundError, new_http_client
from hydro_core.kbw import compute_kbw_season, current_period
from hydro_core.schemas import AnalysisRequest, CropCode


async def probe(parcel_ids: list[str], analysis_date: date, crop: CropCode) -> None:
    async with new_http_client() as http:
        collector = ParcelDataCollector(http)
        for pid in parcel_ids:
            print(f"\n=== Działka {pid} | data {analysis_date} | uprawa {crop.value}")
            try:
                bundle = await collector.collect(
                    AnalysisRequest(parcel_id=pid, crop=crop, analysis_date=analysis_date)
                )
            except ParcelNotFoundError as exc:
                print(f"  BŁĄD: {exc}")
                continue

            p = bundle.parcel
            print(f"  {p.voivodeship} / {p.county} / {p.commune} / {p.region}, {p.area_ha:.2f} ha")
            print(f"  {'źródło':<62} {'tryb':<12} {'czas':>6}  uwagi")
            for a in bundle.audit:
                print(f"  {a.source_name[:62]:<62} {a.mode.value:<12} {a.fetch_time_s or 0:>5.1f}s  {a.message or ''}")
            print(f"  RAZEM: {bundle.total_time_s:.1f} s")

            print(f"  Gleba: kat. {bundle.soil_category.value if bundle.soil_category else 'brak'} – {bundle.soil_source_layer}")
            if bundle.soil_grids:
                print(f"    SoilGrids: WOD 0–100 cm ≈ {bundle.soil_grids.awc_mm_0_100} mm, SOC {bundle.soil_grids.topsoil_soc_g_kg} g/kg")
            if bundle.dem:
                print(f"  Teren: {bundle.dem.source}, {bundle.dem.resolution_m} m, wys. {bundle.dem.min_elevation_m}–{bundle.dem.max_elevation_m} m n.p.m., raster {bundle.dem.elevation.shape}")
            if bundle.weather_station:
                ws = bundle.weather_station
                print(f"  IMGW: {ws.station_name}, {ws.distance_km} km, Δh {ws.elevation_diff_m} m, T {ws.observed_air_temp_c} °C → {ws.lapse_corrected_temp_c} °C na działce")
            s2 = bundle.sentinel2
            if s2:
                cur = s2.current
                print(f"  Sentinel-2 teraz: {cur.acquired if cur else 'brak'} NDVI {cur.ndvi_mean if cur else '-'} NDMI {cur.ndmi_mean if cur else '-'} | historia {len(s2.history)} lat, z-score NDMI {s2.ndmi_anomaly_z}")
            if bundle.hydro_network:
                h = bundle.hydro_network
                print(f"  BDOT10k: najbliższy rów {h.nearest_ditch_m} m, rowy w 500 m: {h.ditch_length_in_radius_m} m, ciek {h.nearest_watercourse_m} m, woda {h.nearest_water_body_m} m")

            w = bundle.weather_history
            if w and bundle.soil_category:
                periods = compute_kbw_season(
                    w.dates, w.precipitation_mm, w.et0_mm, analysis_date.year, crop,
                    bundle.soil_category, as_of=analysis_date,
                )
                cur_p = current_period(periods)
                if cur_p:
                    print(f"  KBW {cur_p.label}: {cur_p.kbw_mm} mm, próg {cur_p.threshold_mm} mm, susza: {cur_p.is_drought} ({cur_p.note or ''})")
                droughts = []
                for year in range(analysis_date.year - 10, analysis_date.year):
                    season = compute_kbw_season(w.dates, w.precipitation_mm, w.et0_mm, year, crop, bundle.soil_category)
                    droughts.append(f"{year}:{sum(1 for x in season if x.is_drought)}")
                print(f"  Okresy suszy KBW w latach: {' '.join(droughts)}")
            print(f"  Stopka: {bundle.timing_footer()}")

        pending = pending_background_tasks()
        if pending:
            print(f"\nCzekam do 120 s na {len(pending)} pobrania NMT w tle (zapis do bufora)...")
            await asyncio.wait(pending, timeout=120)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parcel", action="append", required=True, help="Identyfikator działki (TERYT)")
    parser.add_argument("--date", type=date.fromisoformat, default=date.today())
    parser.add_argument("--crop", type=CropCode, default=CropCode.PSZENICA_OZIMA)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.WARNING)
    asyncio.run(probe(args.parcel, args.date, args.crop))


if __name__ == "__main__":
    main()