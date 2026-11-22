"""Runtime configuration: every external endpoint is swappable via environment / .env (prefix AGRO_)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Endpoints, time budgets and cache locations for all data sources."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env", env_prefix="AGRO_", extra="ignore"
    )

    # Global
    analysis_deadline_s: float = 25.0
    analysis_cache_ttl_s: float = 3600.0
    # Frontend origins allowed by CORS (JSON list in .env, e.g. ["https://agroretencja.example"])
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
        "http://127.0.0.1:4173",
        "https://licho-cachyos-pc.tail886ef4.ts.net",
    ]
    http_user_agent: str = "AgroRetencja-KOWR/0.1 (prototyp HackNation 2026)"
    cache_dir: Path = PROJECT_ROOT / "data" / "cache"

    # GUGiK ULDK - parcel boundaries
    uldk_url: str = "https://uldk.gugik.gov.pl/"
    uldk_timeout_s: float = 4.0

    # Open-Meteo - weather history (ERA5 / ERA5-Land) and ECMWF IFS forecast
    open_meteo_archive_url: str = "https://archive-api.open-meteo.com/v1/archive"
    open_meteo_forecast_url: str = "https://api.open-meteo.com/v1/forecast"
    open_meteo_forecast_model: str = "ecmwf_ifs"
    open_meteo_api_key: SecretStr | None = None
    open_meteo_archive_delay_days: int = 5
    open_meteo_timeout_s: float = 8.0

    # NASA POWER - fallback for weather history when Open-Meteo is rate limited
    nasa_power_url: str = "https://power.larc.nasa.gov/api/temporal/daily/point"
    nasa_power_timeout_s: float = 12.0
    weather_history_timeout_s: float = 20.0

    # IMGW-PIB - telemetric station network (current observations)
    imgw_meteo_url: str = "https://danepubliczne.imgw.pl/api/data/meteo"
    imgw_timeout_s: float = 5.0
    imgw_far_station_km: float = 25.0

    # Sentinel-2 L2A Collection 1 (Earth Search STAC, no account)
    s2_stac_search_url: str = "https://earth-search.aws.element84.com/v1/search"
    s2_collection: str = "sentinel-2-c1-l2a"
    s2_timeout_s: float = 18.0
    s2_max_scene_cloud_pct: float = 70.0
    s2_min_parcel_clear_fraction: float = 0.6
    s2_current_window_days: int = 15
    s2_history_window_days: int = 10
    s2_history_years: int = 9
    s2_max_concurrent_reads: int = 16

    # GUGiK NMT 1 m (WCS) + Copernicus DEM GLO-30 fallback (COG, no account)
    nmt_wcs_url: str = (
        "https://mapy.geoportal.gov.pl/wss/service/PZGIK/NMT/GRID1/WCS/DigitalTerrainModelFormatTIFF"
    )
    nmt_coverage_id: str = "DTM_PL-KRON86-NH_TIFF"
    nmt_buffer_m: float = 50.0
    nmt_max_pixels_per_side: int = 2000
    nmt_gugik_timeout_s: float = 4.5
    nmt_timeout_s: float = 21.0
    nmt_background_timeout_s: float = 120.0
    nmt_gugik_short_wait_s: float = 2.0
    nmt_gugik_cooldown_s: float = 300.0
    copernicus_dem_url_template: str = (
        "https://copernicus-dem-30m.s3.amazonaws.com/{name}/{name}.tif"
    )

    # IUNG-PIB soil drought susceptibility (WMS GetFeatureInfo)
    iung_wms_url: str = "https://sgeo.iung.pl/geoserver/Susza/wms"
    iung_layer: str = "Susza:katgl_landuse2024"
    iung_timeout_s: float = 5.0
    iung_sample_points: int = 5

    # ISRIC SoilGrids v2.0 (REST)
    soilgrids_url: str = "https://rest.isric.org/soilgrids/v2.0/properties/query"
    soilgrids_timeout_s: float = 8.0

    # GUGiK BDOT10k - ditches, watercourses, water bodies (per-county GML ZIP, HTTP range reads)
    bdot10k_zip_url_template: str = (
        "https://opendata.geoportal.gov.pl/bdot10k/schemat2021/{woj}/{powiat}_GML.zip"
    )
    bdot10k_counties_wfs_url: str = (
        "https://mapy.geoportal.gov.pl/wss/service/PZGIK/BDOT/WFS/PobieranieBDOT10k"
    )
    bdot10k_search_radius_m: float = 500.0
    bdot10k_timeout_s: float = 12.0

    @property
    def cache_db_path(self) -> Path:
        """SQLite file for JSON response cache."""
        return self.cache_dir / "sources.sqlite"

    @property
    def bdot10k_cache_dir(self) -> Path:
        """Directory with extracted per-county BDOT10k hydrography."""
        return self.cache_dir / "bdot10k"

    @property
    def copernicus_dem_dir(self) -> Path:
        """Directory with pre-fetched national Copernicus DEM 30 m GeoTIFF tiles."""
        return self.cache_dir / "copernicus_dem"

    @property
    def soilgrids_dir(self) -> Path:
        """Directory with pre-fetched national SoilGrids 250 m GeoTIFF clips."""
        return self.cache_dir / "soilgrids"

    @property
    def meteo_grid_path(self) -> Path:
        """Compressed NPZ grid of 10-year Poland daily weather history."""
        return self.cache_dir / "meteo" / "poland_grid_2016_2025.npz"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return process-wide settings."""
    return Settings()