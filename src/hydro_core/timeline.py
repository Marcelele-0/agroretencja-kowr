"""Unified daily weather timeline: history, recent days, forecast and climatology in ONE series.

As-of principle: the timeline contains what was known on the morning of the analysis date.

==============  ============================================================================
kind            meaning
==============  ============================================================================
observed        reanalysis archive (ERA5 / ERA5-Land via Open-Meteo, or NASA POWER fallback)
recent          last days not yet in the archive, filled by the forecast model's analysis
forecast        ECMWF IFS forecast after the analysis date (only when analysis date ~ today)
climatology     mean of the other years for the same day of year: days after a historical
                analysis date (no look-ahead) and gaps in the data
==============  ============================================================================

Every model downstream (FAO-56 balance, KBW, irrigation agent, farmer advice) reads this
object, so history and projection are computed by the same code path.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

from hydro_core.schemas import DayKind

_KIND_CODES = {DayKind.OBSERVED: 0, DayKind.RECENT: 1, DayKind.FORECAST: 2, DayKind.CLIMATOLOGY: 3}
_CODE_KINDS = {v: k for k, v in _KIND_CODES.items()}


def _arr(values: Sequence[float | None] | None, n: int) -> np.ndarray:
    if values is None:
        return np.full(n, np.nan)
    return np.array([np.nan if v is None else float(v) for v in values], dtype=np.float64)


def _doy(dates: np.ndarray) -> np.ndarray:
    return (dates - dates.astype("datetime64[Y]")).astype(int)


@dataclass
class DailyTimeline:
    """Daily P, ET0, Tmax with a provenance code per day (oldest first, no date gaps)."""

    dates: np.ndarray  # datetime64[D]
    precip: np.ndarray
    et0: np.ndarray
    tmax: np.ndarray
    kind: np.ndarray  # int codes, see _KIND_CODES
    analysis_date: dt.date
    providers: list[str] = field(default_factory=list)
    forecast_model: str | None = None

    # -- access -------------------------------------------------------------------------
    def __len__(self) -> int:
        return len(self.dates)

    def index_of(self, day: dt.date) -> int | None:
        i = int((np.datetime64(day, "D") - self.dates[0]).astype(int)) if len(self) else -1
        return i if 0 <= i < len(self) else None

    @property
    def analysis_index(self) -> int:
        idx = self.index_of(self.analysis_date)
        if idx is None:
            raise ValueError("Data analizy poza osią czasu.")
        return idx

    def kind_of(self, i: int) -> DayKind:
        return _CODE_KINDS[int(self.kind[i])]

    def is_projection(self) -> np.ndarray:
        """True for days after the analysis date (forecast or climatology)."""
        return self.dates > np.datetime64(self.analysis_date, "D")

    def known_mask(self) -> np.ndarray:
        """Observed or recent days up to the analysis date (what really happened)."""
        return (self.kind <= _KIND_CODES[DayKind.RECENT]) & ~self.is_projection()

    def window_idx(self, start: dt.date, end: dt.date) -> slice:
        s = max(0, int((np.datetime64(start, "D") - self.dates[0]).astype(int)))
        e = min(len(self), int((np.datetime64(end, "D") - self.dates[0]).astype(int)) + 1)
        return slice(s, max(s, e))

    def sum_before(self, values: np.ndarray, days: int, end_index: int | None = None) -> float | None:
        """Sum of ``values`` over ``days`` days ending at ``end_index`` (default: analysis date)."""
        end = self.analysis_index if end_index is None else end_index
        start = end - days + 1
        if start < 0:
            return None
        chunk = values[start : end + 1]
        return float(np.nansum(chunk)) if np.isfinite(chunk).any() else None

    def years(self) -> list[int]:
        return sorted(set((self.dates.astype("datetime64[Y]").astype(int) + 1970).tolist()))

    def date_list(self) -> list[dt.date]:
        return [d.astype(dt.date) for d in self.dates]

    # -- climatology ----------------------------------------------------------------------
    def climatology(self, target: np.ndarray, exclude_year: int | None = None) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Mean P, ET0, Tmax by day of year over known days of other years."""
        known = self.known_mask()
        if exclude_year is not None:
            known &= (self.dates.astype("datetime64[Y]").astype(int) + 1970) != exclude_year
        doy = _doy(self.dates)
        out = []
        for values in (self.precip, self.et0, self.tmax):
            acc = np.zeros(367)
            cnt = np.zeros(367)
            ok = known & np.isfinite(values)
            np.add.at(acc, doy[ok], values[ok])
            np.add.at(cnt, doy[ok], 1)
            # smooth with a +-7 day circular window (10 years is a short sample per day)
            kernel = np.ones(15)
            acc_s = np.convolve(np.concatenate([acc[-7:], acc, acc[:7]]), kernel, "valid")
            cnt_s = np.convolve(np.concatenate([cnt[-7:], cnt, cnt[:7]]), kernel, "valid")
            mean = np.where(cnt_s > 0, acc_s / np.maximum(cnt_s, 1), np.nan)
            out.append(mean[_doy(np.asarray(target, dtype="datetime64[D]"))])
        return out[0], out[1], out[2]


def build_timeline(
    *,
    dates: Sequence[dt.date],
    precipitation_mm: Sequence[float | None],
    et0_mm: Sequence[float | None],
    tmax_c: Sequence[float | None] | None,
    analysis_date: dt.date,
    today: dt.date,
    archive_delay_days: int = 5,
    projection_days: int = 14,
    forecast_dates: Sequence[dt.date] | None = None,
    forecast_precip_mm: Sequence[float | None] | None = None,
    forecast_et0_mm: Sequence[float | None] | None = None,
    forecast_tmax_c: Sequence[float | None] | None = None,
    providers: list[str] | None = None,
    forecast_model: str | None = None,
) -> DailyTimeline:
    """Merge history (<= analysis date) and projection (forecast, else climatology) into one series."""
    if not dates:
        raise ValueError("Brak danych pogodowych do zbudowania osi czasu.")
    hist_dates = np.array(dates, dtype="datetime64[D]")
    keep = hist_dates <= np.datetime64(analysis_date, "D")
    n_hist = len(hist_dates)
    hp = _arr(precipitation_mm, n_hist)[keep]
    he = _arr(et0_mm, n_hist)[keep]
    ht = _arr(tmax_c, n_hist)[keep]
    hist_dates = hist_dates[keep]

    start = hist_dates[0]
    end = np.datetime64(analysis_date, "D") + projection_days
    all_dates = start + np.arange(int((end - start).astype(int)) + 1)
    n = len(all_dates)
    precip = np.full(n, np.nan)
    et0 = np.full(n, np.nan)
    tmax = np.full(n, np.nan)
    kind = np.full(n, _KIND_CODES[DayKind.CLIMATOLOGY], dtype=np.int8)

    idx = (hist_dates - start).astype(int)
    precip[idx], et0[idx], tmax[idx] = hp, he, ht
    recent_from = np.datetime64(today, "D") - archive_delay_days
    has_data = np.isfinite(precip) & np.isfinite(et0)
    is_hist = all_dates <= np.datetime64(analysis_date, "D")
    kind[has_data & is_hist] = _KIND_CODES[DayKind.OBSERVED]
    kind[has_data & is_hist & (all_dates > recent_from)] = _KIND_CODES[DayKind.RECENT]

    if forecast_dates:
        fd = np.array(forecast_dates, dtype="datetime64[D]")
        nf = len(fd)
        fp, fe, ft = _arr(forecast_precip_mm, nf), _arr(forecast_et0_mm, nf), _arr(forecast_tmax_c, nf)
        for j, d in enumerate(fd):
            i = int((d - start).astype(int))
            if 0 <= i < n and all_dates[i] > np.datetime64(analysis_date, "D") and np.isfinite(fp[j]) and np.isfinite(fe[j]):
                precip[i], et0[i], tmax[i] = fp[j], fe[j], ft[j]
                kind[i] = _KIND_CODES[DayKind.FORECAST]

    timeline = DailyTimeline(
        dates=all_dates,
        precip=precip,
        et0=et0,
        tmax=tmax,
        kind=kind,
        analysis_date=analysis_date,
        providers=list(providers or []),
        forecast_model=forecast_model,
    )
    # Fill everything still empty (gaps + projection without forecast) with climatology.
    gap = kind == _KIND_CODES[DayKind.CLIMATOLOGY]
    if gap.any():
        cp, ce, ct = timeline.climatology(all_dates[gap], exclude_year=analysis_date.year)
        precip[gap] = np.nan_to_num(cp, nan=0.0)
        et0[gap] = np.nan_to_num(ce, nan=1.0)
        tmax[gap] = ct
    return timeline


def kind_is_forecast(kind: DayKind) -> bool:
    """UI flag: anything that is not a measurement-based value."""
    return kind in (DayKind.FORECAST, DayKind.CLIMATOLOGY)
