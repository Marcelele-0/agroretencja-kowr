"""ISRIC SoilGrids v2.0 - texture and organic carbon; fallback estimate of the IUNG soil category.

Available water (field capacity - wilting point) per layer from Saxton & Rawls (2006)
pedotransfer functions, summed over 0-100 cm and mapped to IUNG WOD thresholds
(I < 127.5 mm, II 127.5-170, III 170-202.5, IV > 202.5). This is an approximation.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from backend.clients.base import (
    BaseSourceClient,
    FetchOutcome,
    SourceDescriptor,
    SourceUnavailableError,
)
from hydro_core.schemas import DataMode, IungSoilCategory

_DEPTHS = ("0-5cm", "5-15cm", "15-30cm", "30-60cm", "60-100cm")
_PROPERTIES = ("sand", "clay", "soc", "bdod")
_CACHE_TTL_S = 90 * 24 * 3600


class SoilLayer(BaseModel):
    """One SoilGrids depth interval."""

    top_cm: int
    bottom_cm: int
    sand_pct: float
    clay_pct: float
    soc_g_kg: float
    bulk_density_g_cm3: float | None
    awc_vol: float


class SoilGridsProfile(BaseModel):
    """Soil profile at the parcel point with derived available water capacity."""

    layers: list[SoilLayer]
    awc_mm_0_100: float
    estimated_category: IungSoilCategory
    topsoil_soc_g_kg: float
    topsoil_sand_pct: float


def saxton_rawls_awc(sand_pct: float, clay_pct: float, organic_matter_pct: float) -> float:
    """Volumetric available water (theta33 - theta1500) from Saxton & Rawls (2006), eq. 1-2."""
    s, c = sand_pct / 100.0, clay_pct / 100.0
    om = min(max(organic_matter_pct, 0.0), 8.0)
    t1500t = -0.024 * s + 0.487 * c + 0.006 * om + 0.005 * s * om - 0.013 * c * om + 0.068 * s * c + 0.031
    t1500 = t1500t + (0.14 * t1500t - 0.02)
    t33t = -0.251 * s + 0.195 * c + 0.011 * om + 0.006 * s * om - 0.027 * c * om + 0.452 * s * c + 0.299
    t33 = t33t + (1.283 * t33t**2 - 0.374 * t33t - 0.015)
    return max(0.0, t33 - t1500)


def category_from_awc(awc_mm: float) -> IungSoilCategory:
    """Map available water [mm] to IUNG drought susceptibility category."""
    if awc_mm < 127.5:
        return IungSoilCategory.I
    if awc_mm < 170.0:
        return IungSoilCategory.II
    if awc_mm <= 202.5:
        return IungSoilCategory.III
    return IungSoilCategory.IV


def parse_soilgrids(payload: dict[str, Any]) -> SoilGridsProfile:
    """Convert SoilGrids REST JSON into a profile with AWC and estimated category."""
    values: dict[str, dict[str, float | None]] = {}
    for layer in payload["properties"]["layers"]:
        factor = float(layer["unit_measure"].get("d_factor", 1)) or 1.0
        values[layer["name"]] = {
            d["label"]: (None if d["values"].get("mean") is None else d["values"]["mean"] / factor)
            for d in layer["depths"]
        }

    layers: list[SoilLayer] = []
    total_mm = 0.0
    for label in _DEPTHS:
        sand = values.get("sand", {}).get(label)
        clay = values.get("clay", {}).get(label)
        soc = values.get("soc", {}).get(label)
        if sand is None or clay is None or soc is None:
            raise SourceUnavailableError(
                "SoilGrids nie ma danych dla tego punktu (obszar zabudowany lub wodny)."
            )
        top, bottom = (int(x) for x in label.removesuffix("cm").split("-"))
        awc = saxton_rawls_awc(sand, clay, soc * 1.724 / 10.0)
        total_mm += awc * (bottom - top) * 10.0
        layers.append(
            SoilLayer(
                top_cm=top,
                bottom_cm=bottom,
                sand_pct=round(sand, 1),
                clay_pct=round(clay, 1),
                soc_g_kg=round(soc, 1),
                bulk_density_g_cm3=values.get("bdod", {}).get(label),
                awc_vol=round(awc, 4),
            )
        )
    return SoilGridsProfile(
        layers=layers,
        awc_mm_0_100=round(total_mm, 1),
        estimated_category=category_from_awc(total_mm),
        topsoil_soc_g_kg=layers[0].soc_g_kg,
        topsoil_sand_pct=layers[0].sand_pct,
    )


class SoilGridsClient(BaseSourceClient[SoilGridsProfile]):
    """Texture, organic carbon and available water at the parcel centre (250 m grid)."""

    descriptor = SourceDescriptor(
        key="soilgrids",
        category="glebowe",
        source_name="ISRIC SoilGrids v2.0",
        endpoint="https://rest.isric.org/soilgrids/v2.0/properties/query",
        spatial_resolution="250 m",
        license="CC BY 4.0",
        attribution="SoilGrids v2.0 © ISRIC – World Soil Information (CC BY 4.0)",
    )

    @property
    def timeout_s(self) -> float:
        return self.settings.soilgrids_timeout_s

    async def _fetch(self, **kwargs: Any) -> FetchOutcome[SoilGridsProfile]:
        lat: float = kwargs["lat"]
        lon: float = kwargs["lon"]
        cache_key = f"soilgrids:{lat:.3f}:{lon:.3f}"
        cached = await self.cache.get(cache_key)
        if cached:
            return FetchOutcome(
                data=SoilGridsProfile.model_validate(cached),
                mode=DataMode.PRE_FETCHED,
                observation_date="SoilGrids v2.0 (2020)",
            )
        params: list[tuple[str, str]] = [("lon", f"{lon:.5f}"), ("lat", f"{lat:.5f}"), ("value", "mean")]
        params += [("property", p) for p in _PROPERTIES]
        params += [("depth", d) for d in _DEPTHS]
        resp = await self.http.get(self.settings.soilgrids_url, params=params, timeout=self.settings.soilgrids_timeout_s)
        resp.raise_for_status()
        profile = parse_soilgrids(resp.json())
        await self.cache.set(cache_key, profile.model_dump(mode="json"), _CACHE_TTL_S)
        return FetchOutcome(data=profile, mode=DataMode.LIVE, observation_date="SoilGrids v2.0 (2020)")