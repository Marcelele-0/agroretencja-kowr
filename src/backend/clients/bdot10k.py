"""GUGiK BDOT10k hydrography: drainage ditches, rivers/streams, canals, water bodies.

No national download is needed. For the parcel's county (powiat = first 4 TERYT digits) the
client reads only the needed GML files from the county ZIP with HTTP range requests
(~1-3 MB instead of ~30 MB), extracts the geometry once and stores it in
``data/cache/bdot10k/<powiat>.json``. Later analyses in the same county use the local file.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import tempfile
import zipfile
from pathlib import Path
from statistics import median
from typing import Any
from xml.etree import ElementTree as ET

import httpx
from pydantic import BaseModel
from shapely.geometry import LineString, Polygon, mapping
from shapely.strtree import STRtree

from backend.clients.base import (
    BaseSourceClient,
    FetchOutcome,
    SourceDescriptor,
    SourceUnavailableError,
)
from backend.clients.geo import parcel_shape, to_puwg92, to_wgs84
from hydro_core.schemas import DataMode

GML_NS = "{http://www.opengis.net/gml/3.2}"

# BDOT10k class -> (geometry kind, label shown to the farmer)
HYDRO_CLASSES: dict[str, tuple[str, str]] = {
    "OT_SWRM_L": ("line", "rów melioracyjny"),
    "OT_SWRS_L": ("line", "rzeka lub strumień"),
    "OT_SWKN_L": ("line", "kanał"),
    "OT_PTWP_A": ("polygon", "woda powierzchniowa (staw, jezioro, rzeka)"),
}
_CACHE_FORMAT_VERSION = 1


class HydroFeature(BaseModel):
    """One hydrographic object near the parcel."""

    bdot_class: str
    label: str
    distance_m: float
    length_m: float | None = None
    width_m: float | None = None


class HydroNetworkContext(BaseModel):
    """Water network around the parcel (EPSG:2180 distances)."""

    powiat_teryt: str
    search_radius_m: float
    nearest_ditch_m: float | None
    ditch_length_in_radius_m: float
    ditches_touching_parcel: int
    nearest_watercourse_m: float | None
    nearest_water_body_m: float | None
    features: list[HydroFeature]
    features_geojson: dict[str, Any]
    bdot_data_date: str | None


class _HttpRangeFile(io.RawIOBase):
    """Seekable read-only file over HTTP range requests (for zipfile)."""

    def __init__(self, client: httpx.Client, url: str) -> None:
        self._client = client
        self._url = url
        head = client.head(url)
        head.raise_for_status()
        if head.headers.get("accept-ranges", "").lower() != "bytes":
            raise SourceUnavailableError("Serwer BDOT10k nie obsługuje pobierania fragmentów pliku.")
        self.size = int(head.headers["content-length"])
        self.last_modified = head.headers.get("last-modified")
        self._pos = 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._pos

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        if whence == io.SEEK_SET:
            self._pos = offset
        elif whence == io.SEEK_CUR:
            self._pos += offset
        else:
            self._pos = self.size + offset
        return self._pos

    def readinto(self, buffer: Any) -> int:
        if self._pos >= self.size:
            return 0
        end = min(self.size, self._pos + len(buffer)) - 1
        resp = self._client.get(self._url, headers={"Range": f"bytes={self._pos}-{end}"})
        resp.raise_for_status()
        data = resp.content
        buffer[: len(data)] = data
        self._pos += len(data)
        return len(data)


def _pairs(pos_list: str) -> list[tuple[float, float]]:
    nums = [float(v) for v in pos_list.split()]
    return list(zip(nums[0::2], nums[1::2], strict=False))


def parse_bdot_gml(content: bytes, bdot_class: str) -> list[dict[str, Any]]:
    """Extract raw coordinates (file axis order) and width for one BDOT10k class file."""
    kind = HYDRO_CLASSES[bdot_class][0]
    out: list[dict[str, Any]] = []
    for _, elem in ET.iterparse(io.BytesIO(content), events=("end",)):
        if not elem.tag.endswith("}" + bdot_class):
            continue
        width = None
        for child in elem:
            if child.tag.endswith("}szerokosc") and child.text:
                try:
                    width = float(child.text)
                except ValueError:
                    width = None
        if kind == "line":
            coords = [pt for pl in elem.iter(f"{GML_NS}posList") for pt in _pairs(pl.text or "")]
        else:
            ext = next(elem.iter(f"{GML_NS}exterior"), None)
            pl = next(ext.iter(f"{GML_NS}posList"), None) if ext is not None else None
            coords = _pairs(pl.text or "") if pl is not None else []
        if len(coords) >= (2 if kind == "line" else 4):
            out.append({"c": coords, "w": width})
        elem.clear()
    return out


def detect_swap(features: list[dict[str, Any]], ref_e: float, ref_n: float) -> bool:
    """True when the file stores (northing, easting) instead of (easting, northing)."""
    sample = [pt for f in features[:50] for pt in f["c"][:2]]
    if not sample:
        return False
    as_is = median(abs(a - ref_e) + abs(b - ref_n) for a, b in sample)
    swapped = median(abs(b - ref_e) + abs(a - ref_n) for a, b in sample)
    return swapped < as_is


def download_county_hydrography(
    url: str, powiat: str, ref_e: float, ref_n: float, timeout_s: float, user_agent: str
) -> dict[str, Any]:
    """Read hydrography classes of one county ZIP via HTTP range requests (blocking)."""
    with httpx.Client(timeout=timeout_s, headers={"User-Agent": user_agent}, follow_redirects=True) as client:
        remote = _HttpRangeFile(client, url)
        with zipfile.ZipFile(io.BufferedReader(remote, buffer_size=1 << 20)) as zf:
            members = {
                cls: next((n for n in zf.namelist() if n.endswith(f"__{cls}.xml")), None)
                for cls in HYDRO_CLASSES
            }
            classes: dict[str, list[dict[str, Any]]] = {}
            for cls, name in members.items():
                if name is None:
                    classes[cls] = []
                    continue
                feats = parse_bdot_gml(zf.read(name), cls)
                if detect_swap(feats, ref_e, ref_n):
                    for f in feats:
                        f["c"] = [(b, a) for a, b in f["c"]]
                for f in feats:
                    f["c"] = [(round(x, 1), round(y, 1)) for x, y in f["c"]]
                classes[cls] = feats
    return {
        "version": _CACHE_FORMAT_VERSION,
        "powiat": powiat,
        "source_url": url,
        "last_modified": remote.last_modified,
        "classes": classes,
    }


def _write_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, separators=(",", ":"))
    os.replace(tmp, path)


def download_and_store(
    url: str, powiat: str, ref_e: float, ref_n: float, timeout_s: float, user_agent: str, path: Path
) -> dict[str, Any]:
    """Download county hydrography and save it to the local cache file (blocking)."""
    county = download_county_hydrography(url, powiat, ref_e, ref_n, timeout_s, user_agent)
    _write_atomic(path, county)
    return county


def analyse_network(
    county: dict[str, Any], parcel_2180: Any, radius_m: float
) -> HydroNetworkContext:
    """Distances and lengths of water features around the parcel."""
    area = parcel_2180.buffer(radius_m)
    geoms: list[Any] = []
    meta: list[tuple[str, float | None]] = []
    for cls, feats in county["classes"].items():
        for f in feats:
            geom = LineString(f["c"]) if HYDRO_CLASSES[cls][0] == "line" else Polygon(f["c"])
            if not geom.is_valid:
                geom = geom.buffer(0)
            geoms.append(geom)
            meta.append((cls, f.get("w")))

    tree = STRtree(geoms)
    nearby_idx = tree.query(area, predicate="intersects")
    features: list[HydroFeature] = []
    geo_features: list[dict[str, Any]] = []
    nearest: dict[str, float] = {}
    ditch_length = 0.0
    touching = 0
    for i in nearby_idx:
        geom = geoms[int(i)]
        cls, width = meta[int(i)]
        dist = float(geom.distance(parcel_2180))
        nearest[cls] = min(nearest.get(cls, float("inf")), dist)
        clipped = geom.intersection(area)
        length = float(clipped.length) if HYDRO_CLASSES[cls][0] == "line" else None
        if cls == "OT_SWRM_L":
            ditch_length += length or 0.0
            if dist <= 5.0:
                touching += 1
        features.append(
            HydroFeature(
                bdot_class=cls,
                label=HYDRO_CLASSES[cls][1],
                distance_m=round(dist, 1),
                length_m=round(length, 1) if length is not None else None,
                width_m=width,
            )
        )
        geo_features.append(
            {
                "type": "Feature",
                "geometry": mapping(to_wgs84(clipped)),
                "properties": {"bdot_class": cls, "label": HYDRO_CLASSES[cls][1], "distance_m": round(dist, 1)},
            }
        )
    features.sort(key=lambda f: f.distance_m)

    def near(*classes: str) -> float | None:
        vals = [nearest[c] for c in classes if c in nearest]
        return round(min(vals), 1) if vals else None

    return HydroNetworkContext(
        powiat_teryt=county["powiat"],
        search_radius_m=radius_m,
        nearest_ditch_m=near("OT_SWRM_L"),
        ditch_length_in_radius_m=round(ditch_length, 1),
        ditches_touching_parcel=touching,
        nearest_watercourse_m=near("OT_SWRS_L", "OT_SWKN_L"),
        nearest_water_body_m=near("OT_PTWP_A"),
        features=features[:50],
        features_geojson={"type": "FeatureCollection", "features": geo_features},
        bdot_data_date=county.get("last_modified"),
    )


class Bdot10kClient(BaseSourceClient[HydroNetworkContext]):
    """Ditches and water around the parcel for retention recommendations (weirs, slowing outflow)."""

    descriptor = SourceDescriptor(
        key="bdot10k",
        category="hydrologiczne",
        source_name="GUGiK BDOT10k – sieć wodna (rowy OT_SWRM_L, cieki, kanały, wody powierzchniowe)",
        endpoint="https://opendata.geoportal.gov.pl/bdot10k/schemat2021/",
        spatial_resolution="1:10 000",
        license="Dane GUGiK udostępniane bez opłat do dowolnych celów (od 31.07.2020)",
        attribution="Źródło: GUGiK, BDOT10k",
    )

    @property
    def timeout_s(self) -> float:
        return self.settings.bdot10k_timeout_s

    def _cache_path(self, powiat: str) -> Path:
        return self.settings.bdot10k_cache_dir / f"{powiat}.json"

    async def _fetch(self, **kwargs: Any) -> FetchOutcome[HydroNetworkContext]:
        parcel_id: str = kwargs["parcel_id"]
        geojson: dict[str, Any] = kwargs["geojson"]
        powiat = parcel_id[:4]
        if not powiat.isdigit():
            raise SourceUnavailableError("Nie można ustalić powiatu z identyfikatora działki.")
        parcel_2180 = to_puwg92(parcel_shape(geojson))
        path = self._cache_path(powiat)
        mode = DataMode.PRE_FETCHED

        county: dict[str, Any] | None = None
        if path.exists():
            county = await asyncio.to_thread(lambda: json.loads(path.read_text(encoding="utf-8")))
            if county.get("version") != _CACHE_FORMAT_VERSION:
                county = None
        if county is None:
            url = self.settings.bdot10k_zip_url_template.format(woj=powiat[:2], powiat=powiat)
            ref = parcel_2180.centroid
            # The thread keeps running after a timeout and still fills the cache for the next analysis.
            county = await asyncio.to_thread(
                download_and_store,
                url,
                powiat,
                ref.x,
                ref.y,
                self.settings.bdot10k_timeout_s,
                self.settings.http_user_agent,
                path,
            )
            mode = DataMode.LIVE

        context = await asyncio.to_thread(
            analyse_network, county, parcel_2180, self.settings.bdot10k_search_radius_m
        )
        return FetchOutcome(
            data=context,
            mode=mode,
            observation_date=f"BDOT10k powiat {powiat}, plik z {county.get('last_modified') or 'b.d.'}",
            message=(
                "Uwzględniono obiekty z powiatu działki; przy granicy powiatu rowy z sąsiedniego "
                "powiatu mogą zostać pominięte."
            ),
        )