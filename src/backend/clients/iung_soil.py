"""IUNG-PIB soil drought susceptibility categories (I very light ... IV heavy), WMS GetFeatureInfo.

Layer ``Susza:katgl_landuse2024`` (the layer of the SMSR 'Mapa kategorii glebowych').
Pixel values: 1-4 soil category, 5 organic soils, 11 forest, 12 water, 13 built-up,
14 roads / wasteland, 19 and 99 unclassified, 0 and 255 nodata.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from typing import Any

from pydantic import BaseModel

from backend.clients.base import (
    BaseSourceClient,
    FetchOutcome,
    SourceDescriptor,
    SourceUnavailableError,
)
from backend.clients.geo import interior_sample_points, parcel_shape
from hydro_core.schemas import DataMode, IungSoilCategory

_CATEGORY_BY_VALUE = {
    1: IungSoilCategory.I,
    2: IungSoilCategory.II,
    3: IungSoilCategory.III,
    4: IungSoilCategory.IV,
}
_ORGANIC = 5
_NON_AGRICULTURAL = {
    11: "las lub zadrzewienie",
    12: "woda",
    13: "teren zabudowany",
    14: "teren komunikacyjny lub nieużytek",
}
_CACHE_TTL_S = 30 * 24 * 3600


class IungSoilResult(BaseModel):
    """Soil category of the parcel from sample points (majority vote)."""

    category: IungSoilCategory
    sample_values: list[int | None]
    category_share: float
    organic_points: int
    non_agricultural_points: list[str]
    note: str | None = None


def classify_samples(values: list[int | None]) -> IungSoilResult:
    """Majority vote of IUNG categories among parcel sample points."""
    cats = [_CATEGORY_BY_VALUE[v] for v in values if v in _CATEGORY_BY_VALUE]
    organic = sum(1 for v in values if v == _ORGANIC)
    non_ag = [_NON_AGRICULTURAL[v] for v in values if v in _NON_AGRICULTURAL]

    if not cats and not organic:
        raise SourceUnavailableError(
            "W punktach działki mapa IUNG nie wskazuje gruntów ornych"
            + (f" ({', '.join(sorted(set(non_ag)))})." if non_ag else " (brak danych).")
        )
    notes: list[str] = []
    if organic > len(cats):
        category = IungSoilCategory.IV
        share = organic / len(values)
        notes.append(
            "Przeważają gleby organiczne – IUNG nie wyznacza dla nich kategorii; przyjęto kat. IV "
            "(duża pojemność wodna), wynik ma niższą pewność."
        )
    else:
        category, count = Counter(cats).most_common(1)[0]
        share = count / len(values)
        if len(set(cats)) > 1:
            notes.append("Na działce występuje kilka kategorii gleby – przyjęto dominującą.")
    if non_ag:
        notes.append(f"Część punktów działki to: {', '.join(sorted(set(non_ag)))}.")
    return IungSoilResult(
        category=category,
        sample_values=values,
        category_share=round(share, 2),
        organic_points=organic,
        non_agricultural_points=non_ag,
        note=" ".join(notes) or None,
    )


class IungSoilClient(BaseSourceClient[IungSoilResult]):
    """Category of soil susceptibility to drought used to pick the legal KBW threshold."""

    descriptor = SourceDescriptor(
        key="iung_soil",
        category="glebowe",
        source_name="IUNG-PIB – kategorie podatności gleb na suszę (SMSR, warstwa katgl_landuse2024)",
        endpoint="https://sgeo.iung.pl/geoserver/Susza/wms",
        spatial_resolution="mapa glebowo-rolnicza 1:25 000 (raster)",
        license="Dane publiczne IUNG-PIB (System Monitoringu Suszy Rolniczej)",
        attribution="Źródło: IUNG-PIB, System Monitoringu Suszy Rolniczej",
    )

    @property
    def timeout_s(self) -> float:
        return self.settings.iung_timeout_s

    async def _value_at(self, lon: float, lat: float) -> int | None:
        d = 0.0005
        params = {
            "SERVICE": "WMS",
            "VERSION": "1.1.1",
            "REQUEST": "GetFeatureInfo",
            "LAYERS": self.settings.iung_layer,
            "QUERY_LAYERS": self.settings.iung_layer,
            "SRS": "EPSG:4326",
            "BBOX": f"{lon - d:.6f},{lat - d:.6f},{lon + d:.6f},{lat + d:.6f}",
            "WIDTH": "11",
            "HEIGHT": "11",
            "X": "5",
            "Y": "5",
            "INFO_FORMAT": "application/json",
            "FEATURE_COUNT": "1",
        }
        resp = await self.http.get(self.settings.iung_wms_url, params=params, timeout=self.settings.iung_timeout_s)
        resp.raise_for_status()
        features = resp.json().get("features", [])
        if not features:
            return None
        value = features[0].get("properties", {}).get("GRAY_INDEX")
        return None if value is None else int(value)

    async def _fetch(self, **kwargs: Any) -> FetchOutcome[IungSoilResult]:
        parcel_id: str = kwargs["parcel_id"]
        geojson: dict[str, Any] = kwargs["geojson"]
        cache_key = f"iung:{parcel_id}"
        cached = await self.cache.get(cache_key)
        if cached:
            return FetchOutcome(
                data=IungSoilResult.model_validate(cached),
                mode=DataMode.PRE_FETCHED,
                observation_date="mapa kategorii glebowych IUNG (bufor)",
            )
        points = interior_sample_points(parcel_shape(geojson), self.settings.iung_sample_points)
        values = await asyncio.gather(*(self._value_at(p.x, p.y) for p in points))
        result = classify_samples(list(values))
        await self.cache.set(cache_key, result.model_dump(mode="json"), _CACHE_TTL_S)
        return FetchOutcome(
            data=result,
            mode=DataMode.LIVE,
            observation_date="mapa kategorii glebowych IUNG (warstwa 2024)",
            message=result.note,
        )