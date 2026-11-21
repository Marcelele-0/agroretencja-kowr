"""IUNG-PIB climatic water balance (KBW) in six-dekad periods and legal drought thresholds.

Source of thresholds: Rozporzadzenie MRiRW z dnia 11 kwietnia 2019 r. (Dz.U. 2019 poz. 739),
table published by IUNG-PIB SMSR: https://susza.iung.pulawy.pl/progi/ (retrieved 2026-11-21).
KBW = precipitation - potential evapotranspiration (IUNG uses the Penman method; this module
accepts any Penman-type ET0, e.g. FAO-56 Penman-Monteith).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from hydro_core.schemas import CropCode, IungSoilCategory, KbwPeriodResult

THRESHOLD_SOURCE = "Dz.U. 2019 poz. 739 (tabela IUNG-PIB SMSR, stan na 2026-11-21)"

# (start_month, start_day, end_month, end_day) for the 14 six-dekad periods 21.III-30.IX.
SIX_DEKAD_PERIODS: tuple[tuple[int, int, int, int], ...] = (
    (3, 21, 5, 20),
    (4, 1, 5, 31),
    (4, 11, 6, 10),
    (4, 21, 6, 20),
    (5, 1, 6, 30),
    (5, 11, 7, 10),
    (5, 21, 7, 20),
    (6, 1, 7, 31),
    (6, 11, 8, 10),
    (6, 21, 8, 20),
    (7, 1, 8, 31),
    (7, 11, 9, 10),
    (7, 21, 9, 20),
    (8, 1, 9, 30),
)

_ROMAN_MONTHS = ("I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII")

# Tokens: number = threshold in mm, "x" = no yield loss from drought in this period,
# "nr" = crop not recommended on this soil category.
_RAW_THRESHOLDS: dict[str, dict[str, str]] = {
    "zboza_ozime": {
        "I": "-129 -132 -137 -145 -154 -165 -180 -199 -220 x x x x x",
        "II": "-150 -153 -158 -166 -175 -186 -201 -220 -241 x x x x x",
        "III": "-188 -191 -196 -204 -213 -224 -239 -258 -279 x x x x x",
        "IV": "-215 -218 -223 -231 -240 -251 -266 -285 -306 x x x x x",
    },
    "zboza_jare": {
        "I": "-125 -127 -130 -135 -142 -153 -169 -186 -207 x x x x x",
        "II": "-144 -146 -149 -154 -161 -172 -188 -205 -226 x x x x x",
        "III": "-174 -176 -179 -184 -191 -202 -218 -235 -256 x x x x x",
        "IV": "-194 -196 -199 -204 -211 -222 -238 -255 -276 x x x x x",
    },
    "kukurydza_ziarno": {
        "I": "x x x -168 -161 -156 -153 -152 -153 -154 -156 -158 -161 -164",
        "II": "x x x -200 -193 -188 -185 -184 -185 -186 -188 -190 -193 -196",
        "III": "x x x -236 -229 -224 -221 -220 -221 -222 -224 -226 -229 -232",
        "IV": "x x x -271 -264 -259 -256 -255 -256 -257 -259 -261 -264 -267",
    },
    "kukurydza_kiszonka": {
        "I": "x x x -168 -161 -156 -153 -152 -152 -152 -153 -155 -157 -160",
        "II": "x x x -200 -193 -188 -185 -184 -184 -184 -185 -187 -189 -192",
        "III": "x x x -236 -229 -224 -221 -220 -220 -220 -221 -223 -225 -228",
        "IV": "x x x -271 -264 -259 -256 -255 -255 -255 -256 -258 -260 -263",
    },
    "rzepak_rzepik": {
        "I": "-207 -187 -170 -155 -142 -132 x x x x x x -138 -138",
        "II": "-232 -211 -193 -177 -165 -155 x x x x x x -158 -158",
        "III": "-275 -254 -236 -220 -208 -198 x x x x x x -205 -205",
        "IV": "-297 -276 -258 -242 -230 -220 x x x x x x -230 -230",
    },
    "ziemniak": {
        "I": "x x -238 -232 -226 -219 -210 -200 -189 -178 -168 -157 -147 x",
        "II": "x x -257 -251 -245 -238 -229 -219 -208 -197 -187 -176 -166 x",
        "III": "x x -286 -280 -274 -267 -258 -248 -237 -226 -216 -205 -195 x",
        "IV": "x x -306 -300 -294 -287 -278 -268 -257 -246 -236 -225 -215 x",
    },
    "burak_cukrowy": {
        "I": "nr nr nr nr nr nr nr nr nr nr nr nr nr nr",
        "II": "-250 -248 -245 -241 -237 -232 -226 -220 -212 -202 -192 -182 -169 -155",
        "III": "-277 -275 -272 -268 -264 -259 -253 -247 -239 -229 -219 -209 -196 -182",
        "IV": "-296 -294 -291 -287 -283 -278 -272 -266 -258 -248 -238 -228 -215 -201",
    },
    "chmiel": {
        "I": "x x x -230 -220 -212 -205 -199 -194 -190 -186 -182 x x",
        "II": "x x x -248 -238 -230 -223 -217 -212 -208 -204 -200 x x",
        "III": "x x x -285 -275 -267 -260 -254 -249 -245 -241 -237 x x",
        "IV": "x x x -302 -292 -284 -277 -271 -266 -262 -258 -254 x x",
    },
    "tyton": {
        "I": "x x x -189 -187 -186 -185 -186 -187 -190 -195 -200 x x",
        "II": "x x x -201 -199 -198 -197 -198 -199 -202 -207 -212 x x",
        "III": "x x x -227 -225 -224 -223 -224 -225 -228 -233 -238 x x",
        "IV": "x x x -240 -238 -237 -236 -237 -238 -241 -246 -251 x x",
    },
    "warzywa_gruntowe": {
        "I": "x x x -189 -187 -185 -184 -184 -184 -185 -187 -188 x x",
        "II": "x x x -207 -205 -203 -202 -202 -202 -203 -205 -206 x x",
        "III": "x x x -243 -241 -239 -238 -238 -238 -239 -241 -242 x x",
        "IV": "x x x -263 -261 -259 -258 -258 -258 -259 -261 -262 x x",
    },
    "krzewy_owocowe": {
        "I": "-149 -148 -147 -147 -149 -154 -159 -162 -163 -162 -160 -157 x x",
        "II": "-166 -165 -164 -164 -166 -171 -176 -179 -180 -179 -177 -174 x x",
        "III": "-200 -199 -198 -198 -200 -205 -210 -213 -214 -213 -211 -208 x x",
        "IV": "-217 -216 -215 -215 -217 -222 -227 -230 -231 -230 -228 -225 x x",
    },
    "drzewa_owocowe": {
        "I": "-168 -171 -175 -180 -187 -193 -199 -205 -210 -215 -220 -223 x x",
        "II": "-190 -193 -197 -202 -209 -215 -221 -227 -232 -237 -242 -245 x x",
        "III": "-229 -232 -236 -241 -248 -254 -260 -266 -271 -276 -281 -284 x x",
        "IV": "-249 -252 -256 -261 -268 -274 -280 -286 -291 -296 -301 -304 x x",
    },
    "truskawki": {
        "I": "-133 -137 -143 -152 -163 -178 -190 -198 -202 x x x x x",
        "II": "-150 -154 -160 -169 -180 -195 -207 -215 -219 x x x x x",
        "III": "-183 -187 -193 -202 -213 -228 -240 -248 -252 x x x x x",
        "IV": "-204 -208 -214 -223 -234 -249 -261 -269 -273 x x x x x",
    },
    "rosliny_straczkowe": {
        "I": "x x x -161 -161 -163 -165 -166 -167 -168 -170 -172 -174 -176",
        "II": "x x x -182 -182 -184 -186 -187 -188 -189 -191 -193 -195 -197",
        "III": "x x x -213 -213 -215 -217 -218 -219 -220 -222 -224 -226 -228",
        "IV": "x x x -234 -234 -236 -238 -239 -240 -241 -243 -245 -247 -249",
    },
}

NOT_RECOMMENDED = "nr"
NO_DROUGHT_LOSS = "x"


def _parse_row(row: str) -> tuple[float | str, ...]:
    """Parse one threshold row into floats or marker tokens."""
    tokens = row.split()
    if len(tokens) != len(SIX_DEKAD_PERIODS):
        raise ValueError(f"Threshold row must have {len(SIX_DEKAD_PERIODS)} values: {row}")
    parsed: list[float | str] = []
    for token in tokens:
        if token in (NOT_RECOMMENDED, NO_DROUGHT_LOSS):
            parsed.append(token)
        else:
            parsed.append(float(token))
    return tuple(parsed)


KBW_THRESHOLDS: dict[str, dict[IungSoilCategory, tuple[float | str, ...]]] = {
    group: {IungSoilCategory(cat): _parse_row(row) for cat, row in rows.items()}
    for group, rows in _RAW_THRESHOLDS.items()
}

# Mapping of supported crops to IUNG crop groups. None = no legal threshold exists.
CROP_TO_IUNG_GROUP: dict[CropCode, str | None] = {
    CropCode.PSZENICA_OZIMA: "zboza_ozime",
    CropCode.PSZENICA_JARA: "zboza_jare",
    CropCode.KUKURYDZA: "kukurydza_ziarno",
    CropCode.RZEPAK_OZIMY: "rzepak_rzepik",
    CropCode.BURAK_CUKROWY: "burak_cukrowy",
    CropCode.ZIEMNIAK: "ziemniak",
    CropCode.UZYTKI_ZIELONE: None,
}


def period_bounds(year: int, period_index: int) -> tuple[date, date]:
    """Return start and end dates of a six-dekad period in a given year."""
    sm, sd, em, ed = SIX_DEKAD_PERIODS[period_index]
    return date(year, sm, sd), date(year, em, ed)


def period_label(period_index: int) -> str:
    """Return the period label used by IUNG, e.g. '21.III-20.V'."""
    sm, sd, em, ed = SIX_DEKAD_PERIODS[period_index]
    return f"{sd}.{_ROMAN_MONTHS[sm - 1]}-{ed}.{_ROMAN_MONTHS[em - 1]}"


def kbw_threshold(
    crop: CropCode, category: IungSoilCategory, period_index: int
) -> tuple[float | None, str | None]:
    """Return (threshold_mm, note). threshold_mm is None when no legal threshold applies."""
    group = CROP_TO_IUNG_GROUP.get(crop)
    if group is None:
        return None, "Rozporządzenie nie wyznacza progu KBW dla tej uprawy (np. TUZ)."
    value = KBW_THRESHOLDS[group][category][period_index]
    if value == NOT_RECOMMENDED:
        return None, "Uprawa niewskazana na tej kategorii gleby (wg rozporządzenia)."
    if value == NO_DROUGHT_LOSS:
        return None, "W tym okresie susza nie powoduje spadku plonu tej uprawy."
    return float(value), None


def compute_kbw_season(
    dates: Sequence[date],
    precipitation_mm: Sequence[float | None],
    et0_mm: Sequence[float | None],
    year: int,
    crop: CropCode,
    category: IungSoilCategory,
    as_of: date | None = None,
    max_missing_days: int = 3,
) -> list[KbwPeriodResult]:
    """Compute KBW for all 14 six-dekad periods of one season and compare with thresholds.

    Periods ending after ``as_of`` are computed from available days only and flagged
    ``is_complete=False`` (no drought verdict). Periods starting after ``as_of`` are skipped.
    """
    daily: dict[date, float] = {}
    for d, p, e in zip(dates, precipitation_mm, et0_mm, strict=True):
        if p is not None and e is not None:
            daily[d] = float(p) - float(e)

    results: list[KbwPeriodResult] = []
    for idx in range(len(SIX_DEKAD_PERIODS)):
        start, end = period_bounds(year, idx)
        if as_of is not None and start > as_of:
            break
        effective_end = end if as_of is None or end <= as_of else as_of
        n_days = (effective_end - start).days + 1
        values = [
            daily[d]
            for d in (date.fromordinal(start.toordinal() + i) for i in range(n_days))
            if d in daily
        ]
        missing = n_days - len(values)
        is_complete = effective_end == end
        threshold, note = kbw_threshold(crop, category, idx)

        kbw: float | None = round(sum(values), 1) if values else None
        if missing > max_missing_days:
            kbw = None
            note = f"Brak danych meteorologicznych dla {missing} dni okresu."

        margin = round(kbw - threshold, 1) if kbw is not None and threshold is not None else None
        is_drought = (kbw < threshold) if (is_complete and margin is not None) else None
        if not is_complete and note is None:
            note = "Okres w toku – wynik częściowy, bez rozstrzygnięcia o suszy."

        results.append(
            KbwPeriodResult(
                period_index=idx,
                label=period_label(idx),
                start=start,
                end=end,
                kbw_mm=kbw,
                threshold_mm=threshold,
                margin_mm=margin,
                is_drought=is_drought,
                is_complete=is_complete,
                missing_days=missing,
                note=note,
            )
        )
    return results


def current_period(periods: Sequence[KbwPeriodResult]) -> KbwPeriodResult | None:
    """Return the latest complete period with a KBW value, or the running one if none is complete."""
    complete = [p for p in periods if p.is_complete and p.kbw_mm is not None]
    if complete:
        return complete[-1]
    running = [p for p in periods if p.kbw_mm is not None]
    return running[-1] if running else None