"""Data access for the RL irrigation module.

Sources (all free, no API key; see README for licences):

* Open-Meteo Historical Weather API (archive, ``best_match`` reanalysis) - daily P and ET0.
* Open-Meteo Forecast API (``ecmwf_ifs025``) - live 15-day forecast and recent days.
* Open-Meteo Previous Runs API (``ecmwf_ifs025``) - archived forecasts with lead 1..7 days
  (available from 2024), used to calibrate and to test the policy on *real* forecasts.
* IUNG-PIB WMS ``Susza:katgl_landuse2024`` - soil drought-susceptibility category of a point.
* GUGiK ULDK - parcel geometry by cadastral ID (integration only).

Every request goes through :class:`HttpClient`, which caches responses on disk, retries
with exponential backoff (429/5xx/timeouts) and keeps a minimum interval per host so we
respect public API limits. Only coordinates / parcel IDs are ever sent.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx
import numpy as np

from . import DATA_DIR

log = logging.getLogger(__name__)

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
PREVIOUS_RUNS_URL = "https://previous-runs-api.open-meteo.com/v1/forecast"
IUNG_WMS_URL = "https://sgeo.iung.pl/geoserver/Susza/wms"
ULDK_URL = "https://uldk.gugik.gov.pl/"

TIMEZONE = "Europe/Warsaw"
DAILY_VARS = ("precipitation_sum", "et0_fao_evapotranspiration")
#: Forecast model used both live and for archived forecasts (consistent leads 1..7).
FORECAST_MODEL = "ecmwf_ifs025"
FORECAST_LEADS = 7
#: First day with archived ECMWF forecasts at all leads in the Previous Runs API.
PREVIOUS_RUNS_START = dt.date(2024, 1, 1)
USER_AGENT = "kowr-rl/0.1 (HackNation 2026 prototype)"

LICENSES = {
    "open-meteo": "CC BY 4.0 (Open-Meteo.com; ECMWF / Copernicus ERA5 data); free API for non-commercial use",
    "iung": "IUNG-PIB, Mapa podatnosci gleb na susze (katgl_landuse2024), publiczna usluga WMS",
    "uldk": "GUGiK ULDK, dane publiczne (ustawa Prawo geodezyjne i kartograficzne)",
}


class DataUnavailableError(RuntimeError):
    """Raised when a source cannot provide the requested data (offline, error, no coverage)."""


@dataclass(frozen=True)
class SourceRecord:
    """Provenance of one dataset used in a computation (shown in the report)."""

    name: str
    url: str
    retrieved_at: str  # ISO 8601, UTC
    mode: str  # "live" (fetched now) | "cache" (fetched earlier, see retrieved_at)
    license: str
    detail: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class HttpResult:
    payload: Any
    url: str
    retrieved_at: str
    from_cache: bool


def _utcnow_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


class HttpClient:
    """Small caching HTTP client with retries, backoff and per-host pacing.

    Args:
        cache_dir: where JSON cache files are written (``None`` disables the cache).
        timeout: per-request timeout in seconds.
        retries: number of retries after the first attempt.
        backoff_s: base for exponential backoff.
        min_interval_s: minimum seconds between *live* requests to the same host.
        offline: never touch the network; raise if a response is not cached.
        max_wait_s: longest wait accepted before a retry (rate limit / backoff); a longer
            requested wait raises immediately (interactive use must not hang).
    """

    def __init__(
        self,
        cache_dir: Path | None = DATA_DIR / "cache",
        *,
        timeout: float = 8.0,
        retries: int = 4,
        backoff_s: float = 2.0,
        min_interval_s: float | dict[str, float] = 0.0,
        offline: bool = False,
        transport: httpx.BaseTransport | None = None,
        max_wait_s: float | None = None,
    ) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir is not None else None
        if self.cache_dir is not None:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self.retries = retries
        self.backoff_s = backoff_s
        self.max_wait_s = max_wait_s
        self.min_interval_s = min_interval_s
        self.offline = offline
        self._last_call: dict[str, float] = {}
        self._lock = threading.Lock()
        self._client = httpx.Client(
            timeout=timeout, headers={"User-Agent": USER_AGENT}, transport=transport
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> HttpClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- cache -------------------------------------------------------------------------
    @staticmethod
    def full_url(url: str, params: dict[str, Any]) -> str:
        return f"{url}?{urlencode(params, doseq=True)}" if params else url

    def _cache_path(self, full_url: str) -> Path | None:
        if self.cache_dir is None:
            return None
        key = hashlib.sha256(full_url.encode()).hexdigest()[:32]
        host = urlparse(full_url).netloc.replace(":", "_")
        return self.cache_dir / host / f"{key}.json"

    def _read_cache(self, path: Path | None, max_age_s: float | None) -> dict[str, Any] | None:
        if path is None or not path.exists():
            return None
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if max_age_s is not None and not self.offline:
            fetched = dt.datetime.fromisoformat(entry["retrieved_at"])
            age = (dt.datetime.now(dt.timezone.utc) - fetched).total_seconds()
            if age > max_age_s:
                return None
        return entry

    # -- pacing ------------------------------------------------------------------------
    def _interval_for(self, host: str) -> float:
        if isinstance(self.min_interval_s, dict):
            return float(self.min_interval_s.get(host, 0.0))
        return float(self.min_interval_s)

    def _pace(self, host: str) -> None:
        interval = self._interval_for(host)
        if interval <= 0:
            return
        with self._lock:
            wait = self._last_call.get(host, 0.0) + interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last_call[host] = time.monotonic()

    # -- public ------------------------------------------------------------------------
    def get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        *,
        kind: str = "json",
        max_age_s: float | None = None,
    ) -> HttpResult:
        """GET with cache. ``max_age_s=None`` means a cached response never expires."""
        params = params or {}
        full = self.full_url(url, params)
        cpath = self._cache_path(full)
        cached = self._read_cache(cpath, max_age_s)
        if cached is not None:
            return HttpResult(cached["payload"], full, cached["retrieved_at"], True)
        if self.offline:
            raise DataUnavailableError(f"offline mode and no cached response for {full}")

        host = urlparse(url).netloc
        last_err: Exception | None = None
        for attempt in range(self.retries + 1):
            self._pace(host)
            try:
                resp = self._client.get(url, params=params)
                if resp.status_code == 429 or resp.status_code >= 500:
                    retry_after = resp.headers.get("Retry-After")
                    reason = resp.text[:200]
                    wait = float(retry_after) if retry_after and retry_after.isdigit() else None
                    if wait is None:
                        # Open-Meteo minutely limit -> wait a minute; otherwise exponential.
                        wait = 61.0 if "Minutely" in reason else self.backoff_s * (2**attempt)
                    last_err = DataUnavailableError(f"HTTP {resp.status_code} from {host}: {reason}")
                    if "Hourly" in reason or "Daily" in reason:
                        raise last_err  # retrying would not help within a reasonable time
                    if attempt == self.retries or (self.max_wait_s is not None and wait > self.max_wait_s):
                        kind_txt = "limit zapytan" if resp.status_code == 429 else "blad serwera"
                        raise DataUnavailableError(
                            f"{host}: {kind_txt} (HTTP {resp.status_code}); sprobuj ponownie za ok. {wait:.0f} s"
                        )
                    log.warning("HTTP %s from %s, retry in %.0fs", resp.status_code, host, wait)
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                payload: Any = resp.json() if kind == "json" else resp.text
                if kind == "json" and isinstance(payload, dict) and payload.get("error"):
                    raise DataUnavailableError(f"{host}: {payload.get('reason')}")
                retrieved = _utcnow_iso()
                if cpath is not None:
                    cpath.parent.mkdir(parents=True, exist_ok=True)
                    tmp = cpath.with_suffix(".tmp")
                    tmp.write_text(
                        json.dumps({"url": full, "retrieved_at": retrieved, "payload": payload}),
                        encoding="utf-8",
                    )
                    tmp.replace(cpath)
                return HttpResult(payload, full, retrieved, False)
            except (httpx.TimeoutException, httpx.TransportError) as err:
                last_err = err
                wait = self.backoff_s * (2**attempt)
                log.warning("%s for %s, retry in %.0fs", type(err).__name__, host, wait)
                time.sleep(wait)
            except httpx.HTTPStatusError as err:
                raise DataUnavailableError(f"HTTP {err.response.status_code} from {host}") from err
        raise DataUnavailableError(f"giving up on {host}: {last_err}")


# ======================================================================================
# Weather
# ======================================================================================
@dataclass
class DailySeries:
    """Daily precipitation and reference evapotranspiration (mm/day), NaN = missing."""

    dates: np.ndarray  # datetime64[D]
    precip: np.ndarray
    et0: np.ndarray

    def __post_init__(self) -> None:
        self.dates = np.asarray(self.dates, dtype="datetime64[D]")
        self.precip = np.asarray(self.precip, dtype=np.float64)
        self.et0 = np.asarray(self.et0, dtype=np.float64)

    def __len__(self) -> int:
        return len(self.dates)

    def window(self, start: dt.date, end: dt.date) -> DailySeries:
        """Inclusive date window."""
        s, e = np.datetime64(start, "D"), np.datetime64(end, "D")
        m = (self.dates >= s) & (self.dates <= e)
        return DailySeries(self.dates[m], self.precip[m], self.et0[m])

    def last_valid_date(self) -> dt.date | None:
        ok = np.isfinite(self.precip) & np.isfinite(self.et0)
        if not ok.any():
            return None
        return self.dates[np.flatnonzero(ok)[-1]].astype(dt.date)

    def merged_with(self, other: DailySeries) -> DailySeries:
        """Fill dates missing (or NaN) in ``self`` with values from ``other``."""
        all_dates = np.union1d(self.dates, other.dates)
        p = np.full(len(all_dates), np.nan)
        e = np.full(len(all_dates), np.nan)
        for src in (other, self):  # self has priority -> written last
            idx = np.searchsorted(all_dates, src.dates)
            okp, oke = np.isfinite(src.precip), np.isfinite(src.et0)
            p[idx[okp]] = src.precip[okp]
            e[idx[oke]] = src.et0[oke]
        return DailySeries(all_dates, p, e)


def _arr(values: list[Any]) -> np.ndarray:
    return np.array([np.nan if v is None else float(v) for v in values], dtype=np.float64)


def parse_daily(payload: dict[str, Any]) -> DailySeries:
    daily = payload["daily"]
    return DailySeries(
        np.array(daily["time"], dtype="datetime64[D]"),
        _arr(daily["precipitation_sum"]),
        _arr(daily["et0_fao_evapotranspiration"]),
    )


def _om_source(name: str, res: HttpResult, detail: str) -> SourceRecord:
    return SourceRecord(
        name=name,
        url=res.url,
        retrieved_at=res.retrieved_at,
        mode="cache" if res.from_cache else "live",
        license=LICENSES["open-meteo"],
        detail=detail,
    )


def fetch_archive(
    client: HttpClient, lat: float, lon: float, start: dt.date, end: dt.date
) -> tuple[DailySeries, SourceRecord]:
    """Daily P and ET0 from the Open-Meteo archive (reanalysis, ~9-25 km grid)."""
    params = {
        "latitude": round(lat, 4),
        "longitude": round(lon, 4),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "daily": ",".join(DAILY_VARS),
        "timezone": TIMEZONE,
    }
    recent = end >= dt.date.today() - dt.timedelta(days=10)
    res = client.get(ARCHIVE_URL, params, max_age_s=6 * 3600 if recent else None)
    series = parse_daily(res.payload)
    grid = f"punkt siatki {res.payload.get('latitude'):.3f}N {res.payload.get('longitude'):.3f}E"
    return series, _om_source(
        "Open-Meteo Historical Weather API (best_match: ERA5/ERA5-Land/ECMWF IFS)",
        res,
        f"P i ET0 (FAO-56 Penman-Monteith) {start}..{end}; {grid}",
    )


def fetch_forecast(
    client: HttpClient,
    lat: float,
    lon: float,
    *,
    past_days: int = 92,
    forecast_days: int = 16,
    model: str = FORECAST_MODEL,
) -> tuple[DailySeries, SourceRecord]:
    """Recent days + up to 15-day forecast (ECMWF IFS 0.25 deg)."""
    params = {
        "latitude": round(lat, 4),
        "longitude": round(lon, 4),
        "daily": ",".join(DAILY_VARS),
        "past_days": past_days,
        "forecast_days": forecast_days,
        "models": model,
        "timezone": TIMEZONE,
    }
    res = client.get(FORECAST_URL, params, max_age_s=3 * 3600)
    return parse_daily(res.payload), _om_source(
        f"Open-Meteo Forecast API ({model})",
        res,
        f"ostatnie {past_days} dni i prognoza {forecast_days} dni",
    )


def parse_previous_runs(payload: dict[str, Any], leads: int = FORECAST_LEADS) -> tuple[np.ndarray, np.ndarray]:
    """Hourly archived forecasts -> (dates[D], F[D, leads]) daily sums.

    ``F[d, k-1]`` is the precipitation forecast for day ``d`` issued ``k`` days earlier.
    Days with any missing hour get NaN.
    """
    hourly = payload["hourly"]
    times = np.array(hourly["time"], dtype="datetime64[h]")
    days = times.astype("datetime64[D]")
    udays, inv = np.unique(days, return_inverse=True)
    out = np.full((len(udays), leads), np.nan)
    for k in range(1, leads + 1):
        vals = _arr(hourly[f"precipitation_previous_day{k}"])
        sums = np.bincount(inv, weights=np.nan_to_num(vals), minlength=len(udays))
        n_ok = np.bincount(inv, weights=np.isfinite(vals).astype(float), minlength=len(udays))
        n_all = np.bincount(inv, minlength=len(udays))
        col = np.where(n_ok == n_all, sums, np.nan)
        out[:, k - 1] = col
    return udays, out


def fetch_previous_runs(
    client: HttpClient,
    lat: float,
    lon: float,
    start: dt.date,
    end: dt.date,
    *,
    model: str = FORECAST_MODEL,
    leads: int = FORECAST_LEADS,
) -> tuple[np.ndarray, np.ndarray, SourceRecord]:
    """Archived daily precipitation forecasts for lead 1..``leads`` days."""
    if end < PREVIOUS_RUNS_START:
        raise DataUnavailableError("archived forecasts are only available from 2024")
    start = max(start, PREVIOUS_RUNS_START)
    params = {
        "latitude": round(lat, 4),
        "longitude": round(lon, 4),
        "hourly": ",".join(f"precipitation_previous_day{k}" for k in range(1, leads + 1)),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "models": model,
        "timezone": TIMEZONE,
    }
    recent = end >= dt.date.today() - dt.timedelta(days=10)
    res = client.get(PREVIOUS_RUNS_URL, params, max_age_s=6 * 3600 if recent else None)
    dates, f = parse_previous_runs(res.payload, leads)
    return dates, f, _om_source(
        f"Open-Meteo Previous Runs API ({model})",
        res,
        f"archiwalne prognozy opadu, wyprzedzenie 1-{leads} dni, {start}..{end}",
    )


# ======================================================================================
# Soil (IUNG-PIB) and parcels (GUGiK ULDK)
# ======================================================================================
def iung_params(lat: float, lon: float, half: float = 0.001) -> dict[str, Any]:
    return {
        "service": "WMS",
        "version": "1.1.1",
        "request": "GetFeatureInfo",
        "layers": "Susza:katgl_landuse2024",
        "query_layers": "Susza:katgl_landuse2024",
        "info_format": "application/json",
        "srs": "EPSG:4326",
        "bbox": f"{lon - half:.6f},{lat - half:.6f},{lon + half:.6f},{lat + half:.6f}",
        "width": 101,
        "height": 101,
        "x": 50,
        "y": 50,
        "styles": "",
    }


def parse_iung(payload: dict[str, Any]) -> int | None:
    feats = payload.get("features") or []
    if not feats:
        return None
    val = feats[0].get("properties", {}).get("GRAY_INDEX")
    return None if val is None else int(round(float(val)))


def fetch_iung_code(client: HttpClient, lat: float, lon: float) -> tuple[int | None, SourceRecord]:
    """Raw IUNG raster code at the point (1-4 soil category, 5 organic, 11+ non-arable)."""
    res = client.get(IUNG_WMS_URL, iung_params(lat, lon), max_age_s=None)
    return parse_iung(res.payload), SourceRecord(
        name="IUNG-PIB WMS Susza:katgl_landuse2024 (kategorie podatnosci gleb na susze)",
        url=res.url,
        retrieved_at=res.retrieved_at,
        mode="cache" if res.from_cache else "live",
        license=LICENSES["iung"],
        detail=f"punkt {lat:.5f}N {lon:.5f}E",
    )


@dataclass(frozen=True)
class Parcel:
    parcel_id: str
    teryt: str
    wkt: str
    centroid_lat: float
    centroid_lon: float


def parse_uldk(text: str) -> tuple[str, str]:
    """ULDK text -> (wkt without SRID, teryt). Raises if the parcel was not found."""
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    if not lines or lines[0] != "0" or len(lines) < 2:
        raise DataUnavailableError(f"ULDK: parcel not found ({lines[0] if lines else 'empty'})")
    geom, _, teryt = lines[1].partition("|")
    if geom.upper().startswith("SRID="):
        geom = geom.split(";", 1)[1]
    return geom, teryt


def _polygon_centroid(wkt: str) -> tuple[float, float]:
    try:
        from shapely import wkt as shapely_wkt

        c = shapely_wkt.loads(wkt).centroid
        return float(c.y), float(c.x)
    except ImportError:  # pragma: no cover - shapely is a runtime dep of the monorepo
        nums = wkt[wkt.index("((") + 2 : wkt.index(")")].split(",")
        xy = np.array([[float(v) for v in p.split()] for p in nums])
        return float(xy[:, 1].mean()), float(xy[:, 0].mean())


def fetch_parcel(client: HttpClient, parcel_id: str) -> tuple[Parcel, SourceRecord]:
    params = {"request": "GetParcelById", "id": parcel_id, "result": "geom_wkt,teryt", "srid": 4326}
    res = client.get(ULDK_URL, params, kind="text", max_age_s=30 * 86400)
    wkt, teryt = parse_uldk(res.payload)
    lat, lon = _polygon_centroid(wkt)
    return Parcel(parcel_id, teryt, wkt, lat, lon), SourceRecord(
        name="GUGiK ULDK GetParcelById",
        url=res.url,
        retrieved_at=res.retrieved_at,
        mode="cache" if res.from_cache else "live",
        license=LICENSES["uldk"],
        detail=f"TERYT {teryt}",
    )
