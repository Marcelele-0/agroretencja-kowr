"""Tests of IUNG KBW thresholds (Dz.U. 2019 poz. 739) and six-dekad KBW computation."""

from __future__ import annotations

from datetime import date, timedelta

from hydro_core.kbw import (
    KBW_THRESHOLDS,
    SIX_DEKAD_PERIODS,
    compute_kbw_season,
    current_period,
    kbw_threshold,
    period_bounds,
    period_label,
)
from hydro_core.schemas import CropCode, IungSoilCategory


def _season(year: int, p: float, et0: float) -> tuple[list[date], list[float], list[float]]:
    start = date(year, 1, 1)
    days = [start + timedelta(days=i) for i in range(365)]
    return days, [p] * len(days), [et0] * len(days)


def test_table_has_14_groups_4_categories_14_periods() -> None:
    assert len(SIX_DEKAD_PERIODS) == 14
    assert len(KBW_THRESHOLDS) == 14
    for rows in KBW_THRESHOLDS.values():
        assert set(rows) == set(IungSoilCategory)
        assert all(len(r) == 14 for r in rows.values())


def test_known_threshold_values() -> None:
    assert kbw_threshold(CropCode.PSZENICA_OZIMA, IungSoilCategory.I, 0) == (-129.0, None)
    assert kbw_threshold(CropCode.KUKURYDZA, IungSoilCategory.IV, 13) == (-267.0, None)
    value, note = kbw_threshold(CropCode.BURAK_CUKROWY, IungSoilCategory.I, 5)
    assert value is None and note and "niewskazana" in note
    value, note = kbw_threshold(CropCode.PSZENICA_OZIMA, IungSoilCategory.I, 12)
    assert value is None and note and "nie powoduje" in note
    value, note = kbw_threshold(CropCode.UZYTKI_ZIELONE, IungSoilCategory.II, 3)
    assert value is None and note and "TUZ" in note


def test_period_labels_and_bounds() -> None:
    assert period_label(0) == "21.III-20.V"
    assert period_label(13) == "1.VIII-30.IX"
    assert period_bounds(2025, 1) == (date(2025, 4, 1), date(2025, 5, 31))


def test_kbw_without_drought() -> None:
    days, p, e = _season(2025, 1.0, 3.0)  # -2 mm/day, 61 days -> -122 mm > -129 mm
    periods = compute_kbw_season(days, p, e, 2025, CropCode.PSZENICA_OZIMA, IungSoilCategory.I)
    first = periods[0]
    assert first.kbw_mm == -122.0
    assert first.is_drought is False
    assert first.margin_mm == 7.0


def test_kbw_with_drought() -> None:
    days, p, e = _season(2025, 0.0, 4.0)  # -4 mm/day -> -244 mm < -129 mm
    periods = compute_kbw_season(days, p, e, 2025, CropCode.PSZENICA_OZIMA, IungSoilCategory.I)
    assert periods[0].is_drought is True
    assert periods[12].is_drought is None  # 'x' - no yield loss in this period


def test_running_period_is_partial() -> None:
    days, p, e = _season(2025, 0.0, 4.0)
    periods = compute_kbw_season(
        days, p, e, 2025, CropCode.PSZENICA_OZIMA, IungSoilCategory.I, as_of=date(2025, 5, 10)
    )
    assert [x.period_index for x in periods] == [0, 1, 2, 3, 4]
    assert all(not x.is_complete for x in periods)
    assert all(x.is_drought is None for x in periods)
    assert current_period(periods) is not None


def test_missing_weather_days_give_no_value() -> None:
    days, p, e = _season(2025, 1.0, 3.0)
    p = [None if date(2025, 4, 1) <= d <= date(2025, 4, 10) else v for d, v in zip(days, p, strict=True)]
    periods = compute_kbw_season(days, p, e, 2025, CropCode.PSZENICA_OZIMA, IungSoilCategory.I)
    assert periods[0].kbw_mm is None
    assert periods[0].missing_days == 10