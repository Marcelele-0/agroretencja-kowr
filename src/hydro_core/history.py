"""10-year parcel profile: one row per season from the same continuous balance trace.

Each row: FAO-33 yield loss and stress days of the crop season, SMSR KBW drought windows and
the water state on the same calendar day as the analysis date ("ten sam termin co roku",
mentor finding 8.3). The current season is cut at the analysis date (``is_partial``).
"""

from __future__ import annotations

import datetime as dt

import numpy as np

from hydro_core.fao_balance import season_summary
from hydro_core.features import FeatureContext
from hydro_core.kbw import compute_kbw_season
from hydro_core.schemas import KbwPeriodResult, WaterScoreBreakdown, YearlyDroughtPoint
from hydro_core.scoring import daily_water_states

HISTORY_YEARS = 10


def same_day(day: dt.date, year: int) -> dt.date:
    try:
        return day.replace(year=year)
    except ValueError:  # 29 February
        return day.replace(year=year, day=28)


def kbw_for_year(fx: FeatureContext, year: int) -> list[KbwPeriodResult]:
    """SMSR windows of one season from known (observed/recent) days only."""
    tl = fx.timeline
    known = tl.known_mask()
    dates = tl.date_list()
    precip = [float(p) if k else None for p, k in zip(tl.precip, known, strict=True)]
    et0 = [float(e) if k else None for e, k in zip(tl.et0, known, strict=True)]
    as_of = fx.inputs.analysis_date if year == fx.inputs.analysis_date.year else None
    return compute_kbw_season(dates, precip, et0, year, fx.inputs.crop_code, fx.inputs.soil_category, as_of=as_of)


def build_history(fx: FeatureContext, score: WaterScoreBreakdown) -> list[YearlyDroughtPoint]:
    ad = fx.inputs.analysis_date
    tl = fx.timeline
    rows: list[YearlyDroughtPoint] = []
    for year in range(ad.year - HISTORY_YEARS, ad.year + 1):
        until = ad if year == ad.year else None
        s = season_summary(fx.trace, fx.inputs.crop, year, until=until)
        idx = tl.index_of(same_day(ad, year))
        state = storage = None
        if idx is not None:
            state = float(daily_water_states(fx, score, np.array([idx]))[0])
            storage = round(float(fx.trace.storage_pct(0)[idx]), 1)
        if s is None and idx is None:
            continue
        periods = kbw_for_year(fx, year) if tl.index_of(dt.date(year, 3, 21)) is not None else []
        margins = [p.margin_mm for p in periods if p.margin_mm is not None and p.is_complete]
        rows.append(
            YearlyDroughtPoint(
                year=year,
                drought_days=s.stress_days if s else 0,
                min_kbw_margin_mm=min(margins) if margins else None,
                kbw_drought_periods=sum(1 for p in periods if p.is_drought),
                yield_loss_pct=min(100.0, max(0.0, s.yield_loss_pct)) if s else 0.0,
                same_day_state=state,
                same_day_storage_pct_taw=storage,
                season_precip_mm=s.precip_mm if s else None,
                season_etc_mm=s.etc_mm if s else None,
                is_partial=bool(s.is_partial) if s else True,
            )
        )
    return rows
