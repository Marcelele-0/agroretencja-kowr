"""Feature registry: every variable used by the score, veto rules, causes, advice and ML.

A feature is a named function of the analysis inputs with a KOWR group, unit and label::

    @feature("kbw_60", group="klimat", unit="mm", label="Klimatyczny bilans wodny 60 dni")
    def kbw_60(fx: FeatureContext) -> float | None:
        return fx.timeline.sum_before(fx.timeline.precip - fx.timeline.et0, 60)

``FeatureContext.get(name)`` computes it lazily once per analysis (memoised), so features may
depend on other features. The same names are the input vector of the HydroFusion-PL model
(``FeatureContext.vector(names)``) -> no training/serving skew, and the cause decomposition
groups variables by ``group``. Adding a variable to the formula = one decorated function here
+ using its name in a score component (hydro_core/scoring.py).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from hydro_core.crops import STAGES, CropParams, stage_on
from hydro_core.fao_balance import BalanceTrace, days_to_stress, runoff_sum
from hydro_core.registry import Registry
from hydro_core.schemas import CropCode, IungSoilCategory, KbwPeriodResult
from hydro_core.timeline import DailyTimeline
from hydro_core.topography import TerrainAnalysis

# KOWR cause groups (step 2) + helper groups not attributed as causes
CAUSE_GROUPS = ("klimat", "gleba", "splyw", "uprawa")
GROUP_LABELS = {
    "klimat": "Klimat: opad vs parowanie",
    "gleba": "Gleba: pojemność wodna",
    "splyw": "Teren: spływ powierzchniowy",
    "uprawa": "Uprawa: faza i zapotrzebowanie",
    "satelita": "Stan łanu z satelity",
    "stan": "Bilans wodny strefy korzeniowej",
    "kontekst": "Kontekst działki",
}


@dataclass(frozen=True)
class SatelliteObservation:
    """Parcel-level Sentinel-2 values (neutral of the client classes)."""

    acquired: dt.date | None = None
    ndvi: float | None = None
    ndmi: float | None = None
    ndmi_p10: float | None = None
    ndmi_p90: float | None = None
    ndmi_history_mean: float | None = None
    ndmi_anomaly_z: float | None = None
    cloud_pct: float | None = None
    reconstructed: bool = False
    interval: tuple[float, float] | None = None
    previous_ndmi: float | None = None  # last clear scene 16-60 days before the analysis date
    previous_acquired: dt.date | None = None


@dataclass(frozen=True)
class HydroContext:
    """Water network near the parcel (BDOT10k)."""

    nearest_ditch_m: float | None = None
    ditches_touching_parcel: int = 0
    ditch_length_in_radius_m: float = 0.0
    nearest_watercourse_m: float | None = None
    nearest_water_body_m: float | None = None
    available: bool = True  # False when BDOT10k did not answer (no ditch data at all)


@dataclass
class AnalysisInputs:
    """Everything the pure engine needs; built by the backend from the data bundle."""

    analysis_date: dt.date
    today: dt.date
    crop_code: CropCode
    crop: CropParams
    soil_category: IungSoilCategory
    soil_source: str
    taw_mm: float
    cn: float
    timeline: DailyTimeline
    trace: BalanceTrace
    kbw_periods: list[KbwPeriodResult]
    parcel_area_ha: float
    terrain: TerrainAnalysis | None = None
    satellite: SatelliteObservation = field(default_factory=SatelliteObservation)
    hydro: HydroContext = field(default_factory=HydroContext)
    soil_sand_pct: float | None = None
    soil_soc_g_kg: float | None = None
    soil_awc_mm: float | None = None
    soil_organic: bool = False
    terrain_is_fallback: bool = False
    weather_is_fallback: bool = False
    missing_sources: list[str] = field(default_factory=list)
    # the farmer's own data (None = not given)
    tile_drainage: bool | None = None
    gauge_rain_mm: float | None = None  # replaced the grid rain of the last GAUGE_DAYS days
    gauge_grid_rain_mm: float | None = None  # what the grid had for the same days
    extra: dict[str, Any] = field(default_factory=dict)


FeatureFn = Callable[["FeatureContext"], float | None]


@dataclass(frozen=True)
class FeatureSpec:
    name: str
    group: str
    unit: str
    label: str
    fn: FeatureFn


FEATURES: Registry[FeatureSpec] = Registry("zmienne (features)")


def feature(name: str, *, group: str, unit: str = "", label: str = "") -> Callable[[FeatureFn], FeatureFn]:
    """Register a feature function."""

    def deco(fn: FeatureFn) -> FeatureFn:
        FEATURES.add(name, FeatureSpec(name, group, unit, label or name, fn))
        return fn

    return deco


class FeatureContext:
    """Lazy, memoised access to registered features for one analysis."""

    def __init__(self, inputs: AnalysisInputs) -> None:
        self.inputs = inputs
        self._cache: dict[str, float | None] = {}
        self._computing: set[str] = set()

    # shortcuts used by feature functions
    @property
    def timeline(self) -> DailyTimeline:
        return self.inputs.timeline

    @property
    def trace(self) -> BalanceTrace:
        return self.inputs.trace

    @property
    def i0(self) -> int:
        return self.inputs.timeline.analysis_index

    def get(self, name: str) -> float | None:
        if name in self._cache:
            return self._cache[name]
        if name in self._computing:
            raise RecursionError(f"Cykliczna zależność zmiennych: {name}")
        self._computing.add(name)
        try:
            value = FEATURES.get(name).fn(self)
        except (ValueError, ZeroDivisionError, IndexError):
            value = None
        finally:
            self._computing.discard(name)
        if value is not None and not np.isfinite(value):
            value = None
        self._cache[name] = None if value is None else float(value)
        return self._cache[name]

    def vector(self, names: list[str]) -> np.ndarray:
        """Feature vector for an ML model (NaN = missing, LightGBM handles it natively)."""
        return np.array([np.nan if (v := self.get(n)) is None else v for n in names], dtype=np.float64)

    def snapshot(self, groups: tuple[str, ...] | None = None) -> dict[str, float | None]:
        return {n: self.get(n) for n, s in FEATURES.items() if groups is None or s.group in groups}


# ======================================================================================
# Climate (known days up to the analysis date)
# ======================================================================================
def _psum(fx: FeatureContext, days: int) -> float | None:
    return fx.timeline.sum_before(fx.timeline.precip, days)


@feature("p_15", group="klimat", unit="mm", label="Opad 15 dni")
def _p15(fx: FeatureContext) -> float | None:
    return _psum(fx, 15)


@feature("p_30", group="klimat", unit="mm", label="Opad 30 dni")
def _p30(fx: FeatureContext) -> float | None:
    return _psum(fx, 30)


@feature("p_60", group="klimat", unit="mm", label="Opad 60 dni")
def _p60(fx: FeatureContext) -> float | None:
    return _psum(fx, 60)


@feature("et0_30", group="klimat", unit="mm", label="Parowanie wskaźnikowe ET0 30 dni")
def _et0_30(fx: FeatureContext) -> float | None:
    return fx.timeline.sum_before(fx.timeline.et0, 30)


@feature("kbw_30", group="klimat", unit="mm", label="KBW 30 dni")
def _kbw30(fx: FeatureContext) -> float | None:
    return fx.timeline.sum_before(fx.timeline.precip - fx.timeline.et0, 30)


@feature("kbw_60", group="klimat", unit="mm", label="KBW 60 dni (ciągły)")
def _kbw60(fx: FeatureContext) -> float | None:
    return fx.timeline.sum_before(fx.timeline.precip - fx.timeline.et0, 60)


@feature("kbw_60_normal", group="klimat", unit="mm", label="KBW 60 dni – średnia z innych lat")
def _kbw60_normal(fx: FeatureContext) -> float | None:
    tl = fx.timeline
    vals = []
    for year in tl.years():
        if year == fx.inputs.analysis_date.year:
            continue
        try:
            day = fx.inputs.analysis_date.replace(year=year)
        except ValueError:
            day = fx.inputs.analysis_date.replace(year=year, day=28)
        idx = tl.index_of(day)
        if idx is not None and idx >= 59:
            v = tl.sum_before(tl.precip - tl.et0, 60, idx)
            if v is not None:
                vals.append(v)
    return float(np.mean(vals)) if vals else None


@feature("kbw_60_anomaly", group="klimat", unit="mm", label="Odchylenie KBW 60 dni od normy")
def _kbw60_anom(fx: FeatureContext) -> float | None:
    a, b = fx.get("kbw_60"), fx.get("kbw_60_normal")
    return None if a is None or b is None else a - b


@feature("cdd", group="klimat", unit="dni", label="Dni bez opadu (< 1 mm) przed datą")
def _cdd(fx: FeatureContext) -> float | None:
    p = fx.timeline.precip[: fx.i0 + 1][::-1]
    wet = np.flatnonzero(p >= 1.0)
    return float(wet[0]) if wet.size else float(len(p))


@feature("tmax_7", group="klimat", unit="°C", label="Średnia Tmax 7 dni")
def _tmax7(fx: FeatureContext) -> float | None:
    chunk = fx.timeline.tmax[max(0, fx.i0 - 6) : fx.i0 + 1]
    return float(np.nanmean(chunk)) if np.isfinite(chunk).any() else None


@feature("forecast_p_3", group="klimat", unit="mm", label="Opad prognozowany 3 dni")
def _fp3(fx: FeatureContext) -> float | None:
    return float(np.nansum(fx.timeline.precip[fx.i0 + 1 : fx.i0 + 4]))


@feature("forecast_p_7", group="klimat", unit="mm", label="Opad prognozowany 7 dni")
def _fp7(fx: FeatureContext) -> float | None:
    return float(np.nansum(fx.timeline.precip[fx.i0 + 1 : fx.i0 + 8]))


@feature("forecast_et0_7", group="klimat", unit="mm", label="ET0 prognozowane 7 dni")
def _fe7(fx: FeatureContext) -> float | None:
    return float(np.nansum(fx.timeline.et0[fx.i0 + 1 : fx.i0 + 8]))


@feature("forecast_hot_days", group="klimat", unit="dni", label="Dni z Tmax ≥ 30 °C w prognozie 7 dni")
def _hot(fx: FeatureContext) -> float | None:
    t = fx.timeline.tmax[fx.i0 + 1 : fx.i0 + 8]
    return float(np.sum(t >= 30.0)) if np.isfinite(t).any() else None


# ======================================================================================
# Soil
# ======================================================================================
@feature("taw_mm", group="gleba", unit="mm", label="Pojemność wodna strefy korzeniowej (TAW)")
def _taw(fx: FeatureContext) -> float | None:
    return fx.inputs.taw_mm


@feature("iung_category", group="gleba", unit="", label="Kategoria gleby IUNG (1–4)")
def _cat(fx: FeatureContext) -> float | None:
    return float(["I", "II", "III", "IV"].index(fx.inputs.soil_category.value) + 1)


@feature("sand_pct", group="gleba", unit="%", label="Udział piasku (SoilGrids)")
def _sand(fx: FeatureContext) -> float | None:
    return fx.inputs.soil_sand_pct


@feature("soc_g_kg", group="gleba", unit="g/kg", label="Węgiel organiczny (SoilGrids)")
def _soc(fx: FeatureContext) -> float | None:
    return fx.inputs.soil_soc_g_kg


@feature("awc_mm_0_100", group="gleba", unit="mm", label="Woda użyteczna 0–100 cm (SoilGrids)")
def _awc(fx: FeatureContext) -> float | None:
    return fx.inputs.soil_awc_mm


# ======================================================================================
# Terrain and runoff
# ======================================================================================
def _terrain(fx: FeatureContext) -> TerrainAnalysis:
    if fx.inputs.terrain is None:
        raise ValueError("no terrain")
    return fx.inputs.terrain


@feature("slope_deg", group="splyw", unit="°", label="Średni spadek terenu")
def _slope(fx: FeatureContext) -> float | None:
    return _terrain(fx).mean_slope_deg


@feature("slope_p90_deg", group="splyw", unit="°", label="Spadek – 90. percentyl")
def _slope90(fx: FeatureContext) -> float | None:
    return _terrain(fx).p90_slope_deg


@feature("twi_mean", group="splyw", unit="", label="Średni wskaźnik wilgotności topograficznej TWI")
def _twi(fx: FeatureContext) -> float | None:
    return _terrain(fx).mean_twi


@feature("south_share", group="splyw", unit="", label="Udział stoków południowych")
def _south(fx: FeatureContext) -> float | None:
    return _terrain(fx).south_facing_share


@feature("depression_volume_m3", group="splyw", unit="m³", label="Objętość naturalnych zagłębień")
def _depr(fx: FeatureContext) -> float | None:
    return _terrain(fx).depression_volume_m3


@feature("runoff_60d_mm", group="splyw", unit="mm", label="Spływ powierzchniowy 60 dni")
def _ro60(fx: FeatureContext) -> float | None:
    return runoff_sum(fx.trace, 60)


@feature("runoff_share_60d", group="splyw", unit="", label="Część opadu utracona na spływ (60 dni)")
def _ro_share(fx: FeatureContext) -> float | None:
    p, ro = fx.get("p_60"), fx.get("runoff_60d_mm")
    return None if not p or ro is None else ro / p


@feature("nearest_ditch_m", group="kontekst", unit="m", label="Odległość do rowu melioracyjnego")
def _ditch(fx: FeatureContext) -> float | None:
    return fx.inputs.hydro.nearest_ditch_m


# ======================================================================================
# Crop
# ======================================================================================
@feature("kc_today", group="uprawa", unit="", label="Współczynnik uprawy Kc")
def _kc(fx: FeatureContext) -> float | None:
    return float(fx.trace.kc[fx.i0])


@feature("doy", group="uprawa", unit="", label="Dzień roku")
def _doy(fx: FeatureContext) -> float | None:
    return float(fx.inputs.analysis_date.timetuple().tm_yday)


@feature("stage_code", group="uprawa", unit="", label="Faza (0 poza sezonem, 1–4 ini/dev/mid/late)")
def _stage(fx: FeatureContext) -> float | None:
    st = stage_on(fx.inputs.crop, fx.inputs.analysis_date)
    return 0.0 if st is None else float(STAGES.index(st) + 1)


@feature("etc_30", group="uprawa", unit="mm", label="Zapotrzebowanie uprawy ETc 30 dni")
def _etc30(fx: FeatureContext) -> float | None:
    return float(fx.trace.etc[0, max(0, fx.i0 - 29) : fx.i0 + 1].sum())


@feature("crop_demand_extra_30", group="uprawa", unit="mm", label="Nadwyżka ETc nad ET0 (30 dni)")
def _extra(fx: FeatureContext) -> float | None:
    e, et0 = fx.get("etc_30"), fx.get("et0_30")
    return None if e is None or et0 is None else e - et0


@feature("ky", group="uprawa", unit="", label="Wrażliwość plonu na niedobór wody Ky")
def _ky(fx: FeatureContext) -> float | None:
    return fx.inputs.crop.ky


# ======================================================================================
# Root-zone balance state
# ======================================================================================
@feature("dr_mm", group="stan", unit="mm", label="Niedobór wody w strefie korzeniowej Dr")
def _dr(fx: FeatureContext) -> float | None:
    return float(fx.trace.dr[0, fx.i0])


@feature("raw_mm", group="stan", unit="mm", label="Woda łatwo dostępna RAW")
def _raw(fx: FeatureContext) -> float | None:
    return float(fx.trace.raw[0, fx.i0])


@feature("dr_over_taw", group="stan", unit="", label="Niedobór jako część TAW")
def _drt(fx: FeatureContext) -> float | None:
    return float(fx.trace.dr[0, fx.i0] / fx.inputs.taw_mm)


@feature("storage_pct", group="stan", unit="%", label="Zapas wody (% TAW)")
def _storage(fx: FeatureContext) -> float | None:
    return float(fx.trace.storage_pct(0)[fx.i0])


@feature("days_to_stress", group="stan", unit="dni", label="Dni do stresu wodnego (projekcja)")
def _dts(fx: FeatureContext) -> float | None:
    return days_to_stress(fx.trace, 0)


@feature("kbw_smsr_margin", group="stan", unit="mm", label="Zapas KBW nad progiem suszy SMSR")
def _kbw_margin(fx: FeatureContext) -> float | None:
    done = [p for p in fx.inputs.kbw_periods if p.is_complete and p.margin_mm is not None]
    return done[-1].margin_mm if done else None


# ======================================================================================
# Satellite
# ======================================================================================
@feature("ndmi", group="satelita", unit="", label="NDMI – wilgotność łanu")
def _ndmi(fx: FeatureContext) -> float | None:
    return fx.inputs.satellite.ndmi


@feature("ndvi", group="satelita", unit="", label="NDVI – kondycja łanu")
def _ndvi(fx: FeatureContext) -> float | None:
    return fx.inputs.satellite.ndvi


@feature("ndmi_anomaly_z", group="satelita", unit="σ", label="Odchylenie NDMI od lat poprzednich")
def _ndmi_z(fx: FeatureContext) -> float | None:
    return fx.inputs.satellite.ndmi_anomaly_z


@feature("ndmi_history_mean", group="satelita", unit="", label="NDMI działki w tym samym terminie poprzednich lat")
def _ndmi_hist(fx: FeatureContext) -> float | None:
    return fx.inputs.satellite.ndmi_history_mean


@feature("ndmi_prev", group="satelita", unit="", label="NDMI z ostatniego bezchmurnego zdjęcia sprzed 16–60 dni")
def _ndmi_prev(fx: FeatureContext) -> float | None:
    return fx.inputs.satellite.previous_ndmi


@feature("ndmi_prev_lag", group="satelita", unit="dni", label="Wiek tego zdjęcia")
def _ndmi_prev_lag(fx: FeatureContext) -> float | None:
    acq = fx.inputs.satellite.previous_acquired
    return None if acq is None else float((fx.inputs.analysis_date - acq).days)


@feature("s2_age_days", group="satelita", unit="dni", label="Wiek ostatniego bezchmurnego zdjęcia")
def _s2_age(fx: FeatureContext) -> float | None:
    acq = fx.inputs.satellite.acquired
    return None if acq is None else float((fx.inputs.analysis_date - acq).days)
