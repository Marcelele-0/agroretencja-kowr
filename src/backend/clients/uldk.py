"""GUGiK ULDK - cadastral parcel boundaries by ID, by point or by precinct name + number."""

from __future__ import annotations

import asyncio
import re
from typing import Any

from shapely import wkt
from shapely.errors import GEOSException
from shapely.geometry import mapping

from backend.clients.base import (
    BaseSourceClient,
    FetchOutcome,
    SourceDescriptor,
    SourceUnavailableError,
)
from backend.clients.geo import to_puwg92
from hydro_core.schemas import DataMode, ParcelCandidate, ParcelGeometry

_RESULT_FIELDS = "geom_wkt,teryt,voivodeship,county,commune,region,parcel"
_SEARCH_FIELDS = "teryt,voivodeship,county,commune,region,parcel"
_PARCEL_ID_RE = re.compile(r"^\d{6}_\d\.[0-9A-Za-z_.\-/]{1,40}$")
_CACHE_TTL_S = 7 * 24 * 3600
_CORRUPT_RETRIES = 2
_CORRUPT_RETRY_DELAY_S = 0.3
# Under load ULDK answers some requests with HTTP 302 to a placeholder page
# (integracja01.gugik.gov.pl/resp/uldk.php) that returns "0" + a dummy WKB string
# (observed 2026-11-21, random per request). Such answers are retried, not parsed.
_BUSY_RETRIES = 4
_BUSY_RETRY_DELAY_S = 0.2
_BUSY_LOCATION_MARK = "/resp/"


class UldkTransientError(SourceUnavailableError):
    """ULDK is temporarily unable to answer (overload redirect, corrupt answer): retryable."""


class CorruptGeometryError(UldkTransientError):
    """ULDK answered, but the geometry text is not a valid parcel polygon (worth a retry)."""


def normalize_parcel_id(raw: str) -> str:
    """Trim the user input and validate the TERYT parcel identifier format."""
    value = raw.strip().replace(" ", "")
    if not _PARCEL_ID_RE.match(value):
        raise SourceUnavailableError(
            "Niepoprawny identyfikator działki. Przykład: 141201_1.0001.6509."
        )
    return value


def parse_uldk_response(text: str) -> tuple[int, list[list[str]]]:
    """Parse ULDK plain-text response into (status_or_count, rows of '|' separated fields)."""
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    if not lines:
        raise SourceUnavailableError("ULDK zwróciło pustą odpowiedź.")
    head = lines[0].split()[0]
    try:
        status = int(head)
    except ValueError as exc:
        raise SourceUnavailableError("ULDK zwróciło nieczytelną odpowiedź.") from exc
    if status < 0:
        raise SourceUnavailableError("Nie znaleziono działki w ULDK.")
    return status, [line.split("|") for line in lines[1:]]


def build_parcel(row: list[str]) -> ParcelGeometry:
    """Convert one ULDK row (geom_wkt + attributes, SRID 4326) into ParcelGeometry."""
    geom_text, teryt, voiv, county, commune, region, number = (row + [""] * 7)[:7]
    if ";" in geom_text:
        geom_text = geom_text.split(";", 1)[1]
    try:
        geom = wkt.loads(geom_text)
    except (GEOSException, ValueError) as exc:
        # ULDK intermittently returns a WKB-like hex string instead of WKT (observed 2026-11-21)
        raise CorruptGeometryError("ULDK zwróciło uszkodzoną geometrię działki.") from exc
    if geom.is_empty:
        raise SourceUnavailableError("ULDK zwróciło pustą geometrię działki.")
    if geom.geom_type not in ("Polygon", "MultiPolygon"):
        raise CorruptGeometryError(f"ULDK zwróciło geometrię typu {geom.geom_type} zamiast poligonu.")
    area_ha = to_puwg92(geom).area / 10_000.0
    centroid = geom.representative_point() if not geom.centroid.within(geom) else geom.centroid
    return ParcelGeometry(
        parcel_id=teryt,
        centroid_lat=round(centroid.y, 6),
        centroid_lon=round(centroid.x, 6),
        area_ha=round(max(area_ha, 1e-4), 4),
        bbox_wgs84=tuple(round(v, 6) for v in geom.bounds),  # type: ignore[arg-type]
        geojson=mapping(geom),
        voivodeship=voiv or None,
        county=county or None,
        commune=commune or None,
        region=region or None,
        parcel_number=number or None,
    )


class UldkClient(BaseSourceClient[ParcelGeometry]):
    """Parcel geometry from ULDK (live) with SQLite cache."""

    descriptor = SourceDescriptor(
        key="uldk",
        category="przestrzenne",
        source_name="GUGiK ULDK (Usługa Lokalizacji Działek Katastralnych)",
        endpoint="https://uldk.gugik.gov.pl/",
        spatial_resolution="granice ewidencyjne (EGiB)",
        license="Dane publiczne GUGiK, bezpłatne (Prawo geodezyjne i kartograficzne)",
        attribution="Źródło: GUGiK, ULDK",
    )

    @property
    def timeout_s(self) -> float:
        return self.settings.uldk_timeout_s

    async def _get(self, params: dict[str, str]) -> str:
        for _attempt in range(_BUSY_RETRIES + 1):
            response = await self.http.get(
                self.settings.uldk_url,
                params=params,
                timeout=self.settings.uldk_timeout_s,
                follow_redirects=False,
            )
            if response.is_redirect and _BUSY_LOCATION_MARK in response.headers.get("location", ""):
                await asyncio.sleep(_BUSY_RETRY_DELAY_S)
                continue
            response.raise_for_status()
            return response.text
        raise UldkTransientError("ULDK jest chwilowo przeciążone (odpowiedź zastępcza serwera GUGiK).")

    async def _fetch(self, **kwargs: Any) -> FetchOutcome[ParcelGeometry]:
        parcel_id: str | None = kwargs.get("parcel_id")
        lat: float | None = kwargs.get("lat")
        lon: float | None = kwargs.get("lon")

        if parcel_id:
            pid = normalize_parcel_id(parcel_id)
            cache_key = f"uldk:id:{pid}"
            cached = await self.cache.get(cache_key)
            if cached:
                return FetchOutcome(
                    data=ParcelGeometry.model_validate(cached),
                    mode=DataMode.PRE_FETCHED,
                    observation_date="stan EGiB w ULDK (bufor 7 dni)",
                    message="Granice działki z bufora (pobrane wcześniej z ULDK).",
                )
            params = {"request": "GetParcelById", "id": pid, "result": _RESULT_FIELDS, "srid": "4326"}
        elif lat is not None and lon is not None:
            cache_key = None
            params = {
                "request": "GetParcelByXY",
                "xy": f"{lon:.7f},{lat:.7f},4326",
                "result": _RESULT_FIELDS,
                "srid": "4326",
            }
        else:
            raise SourceUnavailableError("Podaj identyfikator działki albo wskaż punkt na mapie.")

        parcel: ParcelGeometry | None = None
        for attempt in range(_CORRUPT_RETRIES + 1):
            _, rows = parse_uldk_response(await self._get(params))
            if not rows:
                raise SourceUnavailableError("Nie znaleziono działki w ULDK.")
            try:
                parcel = build_parcel(rows[0])
                break
            except CorruptGeometryError:
                if attempt == _CORRUPT_RETRIES:
                    raise
                await asyncio.sleep(_CORRUPT_RETRY_DELAY_S)
        assert parcel is not None
        await self.cache.set(f"uldk:id:{parcel.parcel_id}", parcel.model_dump(mode="json"), _CACHE_TTL_S)
        if cache_key and cache_key != f"uldk:id:{parcel.parcel_id}":
            await self.cache.set(cache_key, parcel.model_dump(mode="json"), _CACHE_TTL_S)
        return FetchOutcome(data=parcel, mode=DataMode.LIVE, observation_date="stan EGiB w ULDK")

    async def search(self, query: str) -> list[ParcelCandidate]:
        """Search by 'Obręb numer' (e.g. 'Stara Wieś 756'); may return parcels from many counties."""
        text = query.strip()
        if not text or len(text) > 80:
            return []
        params = {"request": "GetParcelByIdOrNr", "id": text, "result": _SEARCH_FIELDS}
        try:
            _, rows = parse_uldk_response(await self._get(params))
        except SourceUnavailableError:
            return []
        candidates: list[ParcelCandidate] = []
        for row in rows:
            teryt, voiv, county, commune, region, number = (row + [""] * 6)[:6]
            candidates.append(
                ParcelCandidate(
                    parcel_id=teryt,
                    voivodeship=voiv or None,
                    county=county or None,
                    commune=commune or None,
                    region=region or None,
                    parcel_number=number or None,
                )
            )
        return candidates