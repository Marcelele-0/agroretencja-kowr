"""Live inference: ``recommend()`` and ``compare_strategies()`` (torch-free, NumPy only).

Both functions fetch data live (with an on-disk cache), rebuild the FAO-56 water balance of the
parcel's season up to the analysis date and return JSON-serialisable dicts with numbers,
sources (with retrieval dates), uncertainty and limitations.

Forecast seen by the policy on the analysis date ``D``:
* ``D == today``          -> live ECMWF IFS 0.25 forecast (Open-Meteo Forecast API),
* ``2024-01-01 <= D``     -> real archived ECMWF forecast issued the day before (Previous Runs API),
* ``D < 2024-01-01``      -> climatology of the point (mean of the other years) - no look-ahead.
"""

from __future__ import annotations

import datetime as dt
import time
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from . import ARTIFACT_MODELS_DIR, __version__
from .baselines import baseline_policies, run_oracle
from .config import (
    STAGE_LABELS_PL,
    STAGES,
    CropParams,
    accepted_crop_codes,
    load_crops,
    load_rl_config,
    load_soils,
    resolve_crop,
)
from .data import (
    PREVIOUS_RUNS_START,
    DailySeries,
    DataUnavailableError,
    HttpClient,
    SourceRecord,
    fetch_archive,
    fetch_forecast,
    fetch_iung_code,
    fetch_parcel,
    fetch_previous_runs,
)
from .forecast import align_archived_forecast, climatology_forecast, noisy_forecast
from .policy import EnsemblePolicy, NumpyPolicy, load_policy
from .simulator import (
    N_FC,
    N_LEADS,
    SeasonBatch,
    SeasonSpec,
    SimState,
    action_mask,
    balance_step,
    build_batch,
    default_actions,
    legalize,
    simulate,
)

ARCHIVE_START = dt.date(2016, 1, 1)
POLAND_BBOX = (48.9, 55.1, 14.0, 24.3)  # lat_min, lat_max, lon_min, lon_max
RL_WEIGHTS = ARTIFACT_MODELS_DIR / "ppo_irrigator_weights.npz"
ROMAN = {1: "I", 2: "II", 3: "III", 4: "IV"}
STRATEGY_LABELS = {
    "none": "Bez nawadniania",
    "calendar": "Kalendarz (stala dawka co N dni)",
    "fao_rule": "Regula FAO-56 (Dr > RAW)",
    "fao_forecast": "Regula FAO-56 + prognoza opadu",
    "fao_small": "Regula FAO-56, male dawki 10 mm + prognoza opadu",
    "rl": "Agent RL (MaskablePPO)",
    "oracle": "Wyrocznia (znana pogoda, gorna granica)",
}

STATIC_LIMITATIONS = [
    "Model bilansu jest jednowarstwowy (FAO-56 'wiadro'): nie uwzglednia podsiaku wod gruntowych, splywu powierzchniowego ani drenazu.",
    "Pogoda pochodzi z reanalizy i modelu ECMWF na siatce ok. 9-25 km, a nie z pomiaru na dzialce; lokalne burze moga sie roznic.",
    "Pojemnosc wodna gleby (TAW) to wartosc reprezentatywna dla kategorii IUNG-PIB, a nie pomiar na dzialce; nie zalezy od glebokosci korzeni uprawy.",
    "Parametry upraw (Kc, p, Ky, terminy siewu i dlugosci faz) sa z FAO-56/FAO-33 i wymagaja weryfikacji z ekspertem dla warunkow Polski.",
    "Plon wzgledny wg FAO-33 to wskaznik straty z niedoboru wody, a nie prognoza plonu w t/ha (nie uwzglednia nawozenia, chorob, odmiany).",
    "Nawadnianie jest ostatnim krokiem po dzialaniach retencyjnych i agrotechnicznych - ta rekomendacja ich nie zastepuje.",
]


# ======================================================================================
# Helpers
# ======================================================================================
_CLIENT: HttpClient | None = None


def _today() -> dt.date:
    """Analysis 'today' (separate function so tests can pin it)."""
    return dt.date.today()


#: Runtime HTTP settings: worst case per source ~ 4 + 0.5 + 4 s, sources fetched in parallel,
#: so a cold recommendation stays under ~10 s even when one public service is slow.
RUNTIME_HTTP = {"timeout": 5.0, "retries": 2, "backoff_s": 0.5, "max_wait_s": 2.0}  # worst case ~17 s < 20 s frontend timeout


def get_client() -> HttpClient:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = HttpClient(**RUNTIME_HTTP)
    return _CLIENT


@lru_cache(maxsize=2)
def load_rl_policy(path: str = str(RL_WEIGHTS)) -> NumpyPolicy | EnsemblePolicy | None:
    p = Path(path)
    return load_policy(p) if p.exists() else None


def get_policy(name: str) -> tuple[str, Any]:
    """'default' | 'rl' | 'fao_rule' | other baseline -> (resolved name, policy callable)."""
    cfg = load_rl_config()
    if name == "default":
        name = cfg["default_policy"]
    if name == "rl":
        pol = load_rl_policy()
        if pol is None:
            return "fao_rule", baseline_policies()["fao_rule"]
        return "rl", pol
    return name, baseline_policies()[name]


def _r(x: float, nd: int = 1) -> float:
    return float(round(float(x), nd))


def _soil_message(code: int | None) -> tuple[str, str]:
    soils = load_soils()
    if code is None or code in soils["no_data"]:
        return "no_data", soils["no_data"].get(code, soils["no_data"][0]) if code is not None else soils["no_data"][0]
    if code in soils["non_arable"]:
        return "not_arable", soils["non_arable"][code]
    return "not_arable", f"Kod IUNG {code}: nieznana klasa - brak rekomendacji"


def _in_poland(lat: float, lon: float) -> bool:
    a, b, c, d = POLAND_BBOX
    return a <= lat <= b and c <= lon <= d


def _weather_climatology(arch: DailySeries, dates: np.ndarray, exclude_year: int) -> tuple[np.ndarray, np.ndarray]:
    """Mean P and ET0 by day-of-year over the other years (for days with no data/forecast yet)."""
    doy_all = (arch.dates - arch.dates.astype("datetime64[Y]")).astype(int)
    yrs = arch.dates.astype("datetime64[Y]").astype(int) + 1970
    m = yrs != exclude_year
    p_doy = np.zeros(367)
    e_doy = np.zeros(367)
    n_doy = np.zeros(367)
    ok = m & np.isfinite(arch.precip) & np.isfinite(arch.et0)
    np.add.at(p_doy, doy_all[ok], arch.precip[ok])
    np.add.at(e_doy, doy_all[ok], arch.et0[ok])
    np.add.at(n_doy, doy_all[ok], 1)
    doy = (dates - dates.astype("datetime64[Y]")).astype(int)
    n = np.maximum(n_doy[doy], 1)
    return p_doy[doy] / n, e_doy[doy] / n


# ======================================================================================
# Data assembly
# ======================================================================================
def _fetch_common(client: HttpClient, lat: float, lon: float, today: dt.date, need_live: bool,
                  zones_for: dt.date | None = None) -> dict[str, Any]:
    """Parallel fetch: IUNG soil, 10-year archive, (live forecast); with ``zones_for`` (14-day plan)
    also the SoilGrids profile and the ECMWF ensemble (live for today, archive if enabled)."""
    from . import forecast14_runtime as f14

    jobs: dict[str, Any] = {}
    optional = {"soilgrids", "ensemble"}
    with ThreadPoolExecutor(max_workers=5) as ex:
        jobs["soil"] = ex.submit(fetch_iung_code, client, lat, lon)
        jobs["archive"] = ex.submit(fetch_archive, client, lat, lon, ARCHIVE_START, today - dt.timedelta(days=1))
        if need_live:
            jobs["forecast"] = ex.submit(fetch_forecast, client, lat, lon)
        if zones_for is not None:
            jobs["soilgrids"] = ex.submit(f14.fetch_soil_profile, client, lat, lon)
            if zones_for >= today:
                jobs["ensemble"] = ex.submit(f14.fetch_ensemble_live, client, lat, lon, zones_for)
            elif f14.archive_enabled():
                jobs["ensemble"] = ex.submit(f14.fetch_ensemble_archive, lat, lon, zones_for)
        out = {}
        for k, fut in jobs.items():
            try:
                out[k] = fut.result()
            except DataUnavailableError as err:
                out[k] = err
            except Exception as err:  # optional sources never break the recommendation
                if k not in optional:
                    raise
                out[k] = DataUnavailableError(f"{k}: {err}")
    return out


def _observed_weather(common: dict[str, Any], today: dt.date) -> tuple[DailySeries, list[SourceRecord], DailySeries | None]:
    arch_res = common["archive"]
    if isinstance(arch_res, Exception):
        raise DataUnavailableError(f"Brak danych pogodowych z archiwum Open-Meteo: {arch_res}")
    arch, src = arch_res
    sources = [src]
    fc_series = None
    fc_res = common.get("forecast")
    if fc_res is not None and not isinstance(fc_res, Exception):
        fc_series, fsrc = fc_res
        sources.append(fsrc)
        # fill the archive delay (last days) with the forecast model's recent days
        arch = arch.merged_with(fc_series.window(ARCHIVE_START, today - dt.timedelta(days=1)))
    return arch, sources, fc_series


def _issued_forecast(
    client: HttpClient,
    lat: float,
    lon: float,
    date: dt.date,
    today: dt.date,
    obs: DailySeries,
    fc_series: DailySeries | None,
    horizon: int,
) -> tuple[np.ndarray, np.ndarray, str, list[SourceRecord]]:
    """(P[h], ET0[h]) assumed for days date..date+h-1 as known on the morning of ``date``."""
    days = np.datetime64(date, "D") + np.arange(horizon)
    clim_p, clim_e = _weather_climatology(obs, days, date.year)
    e7 = obs.window(date - dt.timedelta(days=7), date - dt.timedelta(days=1)).et0
    et0_recent = float(np.nanmean(e7)) if len(e7) and np.isfinite(e7).any() else float(clim_e[0])
    if date >= today and fc_series is not None:
        w = fc_series.window(date, date + dt.timedelta(days=horizon - 1))
        p = clim_p.copy()
        e = clim_e.copy()
        idx = np.searchsorted(days, w.dates)
        okp, oke = np.isfinite(w.precip), np.isfinite(w.et0)
        p[idx[okp]] = w.precip[okp]
        e[idx[oke]] = w.et0[oke]
        return p, e, "live_ecmwf", []
    if date >= PREVIOUS_RUNS_START:
        try:
            f_dates, f, src = fetch_previous_runs(client, lat, lon, date, date + dt.timedelta(days=N_LEADS - 1))
            mat, missing = align_archived_forecast(date, 1, f_dates, f)
            if missing < 0.5:
                p = clim_p.copy()
                p[:N_LEADS] = mat[0]
                e = np.full(horizon, et0_recent)
                return p, e, "archived_ecmwf", [src]
        except DataUnavailableError:
            pass
    return clim_p, np.full(horizon, et0_recent), "climatology", []


def _season_spec(
    crop: CropParams, year: int, taw: float, budget: float, obs: DailySeries, assumed_p: np.ndarray, assumed_e: np.ndarray, date: dt.date
) -> SeasonSpec:
    """Weather for 1 March .. season end + 7: observed before ``date``, assumed afterwards."""
    pre_start = dt.date(year, 3, 1)
    end = crop.season_end(year) + dt.timedelta(days=N_FC)
    dates = np.datetime64(pre_start, "D") + np.arange((end - pre_start).days + 1)
    clim_p, clim_e = _weather_climatology(obs, dates, year)
    p, e = clim_p.copy(), clim_e.copy()
    w = obs.window(pre_start, date - dt.timedelta(days=1))
    idx = np.searchsorted(dates, w.dates)
    okp, oke = np.isfinite(w.precip), np.isfinite(w.et0)
    p[idx[okp]] = w.precip[okp]
    e[idx[oke]] = w.et0[oke]
    i0 = int((np.datetime64(date, "D") - dates[0]).astype(int))
    n = min(len(assumed_p), len(dates) - i0)
    if n > 0:
        p[i0 : i0 + n] = assumed_p[:n]
        e[i0 : i0 + n] = assumed_e[:n]
    return SeasonSpec(DailySeries(dates, p, e), year, crop, taw, budget)


# ======================================================================================
# Plan simulation
# ======================================================================================
def _forecast_rows(batch: SeasonBatch, t_date: int, issued: np.ndarray, past_rng: np.random.Generator, days: int = 7) -> None:
    """Fill batch.forecast: synthetic for past days, issued forecast (shifted) for plan days."""
    batch.forecast = noisy_forecast(batch.fut, past_rng)
    L = int(batch.length[0])
    width = batch.forecast.shape[2]
    for k in range(min(max(N_LEADS, days) + 1, L - t_date)):
        row = issued[k : k + width]
        if len(row) < width:
            row = np.concatenate([row, np.zeros(width - len(row))])
        batch.forecast[0, t_date + k] = row


def _run_plan(
    batch: SeasonBatch,
    policy: Any,
    t_date: int,
    budget_used: float,
    budget_left: float,
    history: dict[int, float] | None,
    days: int = 7,
) -> dict[str, Any]:
    actions = default_actions()
    dr = batch.dr0.copy()
    bl_past = np.array([budget_used])
    past_irr = 0.0
    past_doses = np.zeros(t_date)
    for t in range(t_date):
        if history is not None:
            irr = np.array([history.get(t, 0.0)])
        else:
            irr = legalize(policy(SimState(t, dr, bl_past, batch)), bl_past, actions)
            bl_past = bl_past - irr
        dr = balance_step(dr, batch.precip[:, t], irr, batch.et0[:, t], batch.kc[:, t], batch.taw, batch.p_tab)[0]
        past_irr += float(irr[0])
        past_doses[t] = float(irr[0])
    state0 = SimState(t_date, dr.copy(), np.array([budget_left]), batch)
    bl = np.array([budget_left])
    plan = []
    L = int(batch.length[0])
    for k in range(min(days, L - t_date)):
        t = t_date + k
        st = SimState(t, dr, bl, batch)
        irr = legalize(policy(st), bl, actions)
        dr_new, eta, etc, dp, ks, raw = balance_step(dr, batch.precip[:, t], irr, batch.et0[:, t], batch.kc[:, t], batch.taw, batch.p_tab)
        plan.append(
            {
                "day": k,
                "dose_mm": float(irr[0]),
                "precip_assumed_mm": _r(batch.precip[0, t]),
                "et0_assumed_mm": _r(batch.et0[0, t]),
                "storage_pct_taw_start": _r(100 * (1 - dr[0] / batch.taw[0])),
                "stress_expected": bool(ks[0] < 1.0 - 1e-9),
            }
        )
        dr, bl = dr_new, bl - irr
    return {"plan": plan, "state0": state0, "past_irrigation_mm": past_irr, "past_doses": past_doses}


# Confidence of plan day k (1-based), from the E8 test (reports/plan14_holdout_prev7.json):
# Brier skill of the irrigation-day probability vs the base rate 0.43 / 0.27 on days 2 / 3,
# 0.10-0.17 on days 4-7 and below 0.1 from day 8 on.
PLAN_DAY_CONFIDENCE = {**dict.fromkeys(range(1, 4), "wysoka"), **dict.fromkeys(range(4, 8), "srednia"), **dict.fromkeys(range(8, 15), "niska")}


def _analog_arrays(obs: DailySeries, start: dt.date, n: int, skip_year: int) -> tuple[np.ndarray, np.ndarray]:
    """(Y, 1, n) precipitation and ET0 of the same calendar days in the other archive years."""
    dates = np.asarray(obs.dates, dtype="datetime64[D]")
    P, E = [], []
    for y in range(ARCHIVE_START.year, skip_year + 1):
        if y == skip_year:
            continue
        try:
            d0 = np.datetime64(start.replace(year=y), "D")
        except ValueError:
            continue
        want = d0 + np.arange(n)
        idx = np.clip(np.searchsorted(dates, want), 0, len(dates) - 1)
        if not (dates[idx] == want).all():
            continue
        p, e = np.asarray(obs.precip)[idx], np.asarray(obs.et0)[idx]
        if np.isfinite(p).all() and np.isfinite(e).all():
            P.append(p)
            E.append(e)
    return np.asarray(P)[:, None, :], np.asarray(E)[:, None, :]


def _edge_pad(x: np.ndarray, n: int) -> np.ndarray:
    return x[:n] if len(x) >= n else np.pad(x, (0, n - len(x)), mode="edge")


def _long_plan(batch: SeasonBatch, pol: Any, state0: SimState, t_date: int, date: dt.date, long_plan: list[dict[str, Any]],
               issued_p: np.ndarray, fmode: str, obs: DailySeries, days: int, *, crop: CropParams | None = None,
               taw: float | None = None, common: dict[str, Any] | None = None, sources: list[SourceRecord] | None = None,
               past_doses: np.ndarray | None = None) -> dict[str, Any]:
    """Plan for up to 14 days + irrigation probability and water need from weather scenarios: the 51
    ECMWF ensemble members when available (E9), otherwise analog years (E8); plus the three-zone
    soil-water forecast (``soil_zones``) with classes and probabilities."""
    from . import forecast14_runtime as f14
    from .forecast14 import N_DAYS, EnsCalibration
    from .plan import make_plan, scenario_plans
    from .soil3 import zone_awc_from_profile

    common = common or {}
    sources = sources if sources is not None else []
    n_fc = {"live_ecmwf": 14, "archived_ecmwf": N_LEADS}.get(fmode, 0)
    season_start = date - dt.timedelta(days=t_date)
    T = batch.horizon + N_FC
    span = days + N_FC
    ana_p, ana_e = _analog_arrays(obs, season_start, T, date.year)
    ens = common.get("ensemble")
    ens = None if ens is None or isinstance(ens, Exception) else ens
    calib = EnsCalibration.load()
    ens_p = ens_e = None
    if ens is not None:
        ens_p, ens_e = f14.ens_weather(ens, calib, min(N_DAYS, ens["precip"].shape[1]))
        sources.append(SourceRecord(name=ens["name"], url=ens["url"], retrieved_at=ens["retrieved_at"] or "",
                                    mode="cache" if ens.get("from_cache") else "live", license=f14.ENS_LICENSE,
                                    detail=f"{ens_p.shape[0]} czlonkow: opad, temperatura 2 m, promieniowanie; {ens_p.shape[1]} dni"))
    out_days = []
    prob = np.full(days, np.nan)
    water = None
    prob_source = None
    sp = None
    if ens_p is not None:
        M, H = ens_p.shape
        ap = np.repeat(_edge_pad(batch.clim_p[0, t_date:], span)[None], M, axis=0)
        ae = np.repeat(_edge_pad(batch.clim_e[0, t_date:], span)[None], M, axis=0)
        ap[:, :H], ae[:, :H] = ens_p, ens_e
        bb = batch.subset(np.zeros(M, dtype=int))
        sp = make_plan(bb, pol, t_date, np.repeat(state0.dr, M), np.repeat(state0.budget_left, M), days,
                       precip_issued=ap, et0_issued=ae, keep_today_forecast=True)  # (M, days)
        prob_source = "ecmwf_ens"
    elif len(ana_p):
        issued = np.full((1, span), np.nan)
        issued[0, : min(n_fc, len(issued_p))] = issued_p[: min(n_fc, span)]
        sp = scenario_plans(batch, pol, t_date, state0.dr, state0.budget_left, ana_p, ana_e, days,
                            forecast_days=n_fc, precip_issued=issued)[:, 0]  # (Y, days)
        prob_source = "analog_years"
    if sp is not None:
        prob = (sp > 0).mean(axis=0)
        tot = sp.sum(axis=1)
        water = {"expected_mm": _r(tot.mean()), "p10_mm": _r(np.percentile(tot, 10)), "p90_mm": _r(np.percentile(tot, 90)),
                 "n_scenarios": len(tot)}

    soil_zones = None
    if crop is not None and taw is not None:
        prof_res = common.get("soilgrids")
        profile, psrc = None, "rownomierny rozklad wody z glebokoscia (profil SoilGrids niedostepny)"
        if prof_res is not None and not isinstance(prof_res, Exception):
            profile, pres = prof_res
            sources.append(SourceRecord(name="ISRIC SoilGrids 2.0 (wv0033, wv1500)", url=pres.url, retrieved_at=pres.retrieved_at,
                                        mode="cache" if pres.from_cache else "live", license=f14.SOILGRIDS_LICENSE,
                                        detail="woda przy pojemnosci polowej i punkcie trwalego wiedniecia, 6 warstw 0-200 cm"))
            psrc = "SoilGrids 2.0 (250 m), skalowane do TAW kategorii IUNG-PIB"
        awc = zone_awc_from_profile(profile, taw)
        zs = f14.zone_state(obs.dates, obs.precip, obs.et0, crop, date, awc, past_doses)
        Hz = min(days, N_DAYS, crop.length - t_date)
        if Hz >= 1:
            sl = slice(t_date, t_date + Hz)
            frac, kc, ft = zs["frac"][sl], zs["kc"][sl], zs["f_t"][sl]
            za_p, za_e = _analog_arrays(obs, date, Hz, date.year)
            za_p, za_e = (za_p[:, 0], za_e[:, 0]) if len(za_p) else (np.zeros((0, Hz)), np.zeros((0, Hz)))
            post = f14.load_postproc()
            ep = None if ens_p is None else ens_p[:, :Hz]
            ee = None if ens_e is None else ens_e[:, :Hz]
            fc = f14.zone_forecast(zs["d0"], awc, frac, kc, ft, crop.p, ep, ee, za_p, za_e, post)
            doses = np.array([d["dose_mm"] for d in long_plan[:Hz]] + [0.0] * max(0, Hz - len(long_plan)))
            fc_plan = f14.zone_forecast(zs["d0"], awc, frac, kc, ft, crop.p, ep, ee, za_p, za_e, post, irr=doses) if doses.sum() > 0 else None
            if fc:
                w_ens = np.asarray((post or {}).get("w_ens", [1.0] * N_DAYS)[:Hz])
                mixed = [i + 1 for i, w in enumerate(w_ens) if w < 1.0]
                wsrc = (f"ECMWF ENS ({ep.shape[0]} czlonkow)" + (f", od dnia {mixed[0]} laczony z latami analogicznymi (wagi z roku walidacyjnego 2024)" if mixed else "")
                        if ep is not None else f"lata analogiczne: te same dni w {len(za_p)} latach archiwum")
                soil_zones = f14.soil_zones_block(date, zs["d0"], awc, frac, fc, fc_plan, psrc, wsrc)
    for k, d in enumerate(long_plan[:days]):
        out_days.append({
            "day": k + 1,
            "date": (date + dt.timedelta(days=k)).isoformat(),
            "dose_mm": d["dose_mm"],
            "irrigation_probability": None if not np.isfinite(prob[k]) else _r(prob[k], 2),
            "precip_assumed_mm": d["precip_assumed_mm"],
            "confidence": PLAN_DAY_CONFIDENCE.get(k + 1, "niska"),
        })
    return {
        "days": out_days,
        "total_plan_mm": float(sum(d["dose_mm"] for d in long_plan[:days])),
        "water_need_mm": water,
        "forecast_days_used": n_fc,
        "probability_source": prob_source,
        "method": "Plan: ta sama polityka dzien po dniu na pogodzie zakladanej dzis (prognoza, dalej klimatologia). "
                  + ("Prawdopodobienstwo podlewania i zakres wody: plan powtorzony dla kazdego z czlonkow ensemble ECMWF (14 dni)."
                     if prob_source == "ecmwf_ens" else
                     "Prawdopodobienstwo i zakres wody: scenariusze z pogody tych samych dni w innych latach archiwum."),
        "advice": "Wiazaca jest dawka na dzis; plan aktualizuje sie codziennie wraz z nowa prognoza.",
        **({"soil_zones": soil_zones} if soil_zones else {}),
    }


# ======================================================================================
# recommend()
# ======================================================================================
def recommend(
    lat: float | None,
    lon: float | None,
    crop: str,
    date: dt.date | str,
    budget_mm: float,
    budget_used_mm: float = 0.0,
    *,
    policy: str = "default",
    parcel_id: str | None = None,
    irrigation_history: list[tuple[str, float]] | None = None,
    include_history: bool = False,
    plan_days: int = 7,
    client: HttpClient | None = None,
    today: dt.date | None = None,
) -> dict[str, Any]:
    """7-day irrigation recommendation for a point (or parcel) in Poland on ``date``.

    ``plan_days`` = 8..14 adds ``recommendation.plan_14d``: the daily plan continued to ``plan_days``
    days, the probability of an irrigation day and the range of the water need from the plan repeated
    on the 51 ECMWF ensemble members (E9; analog years when no ensemble is available), the confidence
    of every plan day and ``soil_zones``: the three-zone soil-water forecast (0-28 / 28-100 /
    100-200 cm) with water classes and their probabilities, the root-zone P10-P90 range and the
    probability of crop stress without irrigation and with the plan.
    """
    t_start = time.perf_counter()
    client = client or get_client()
    today = today or _today()
    date = dt.date.fromisoformat(date) if isinstance(date, str) else date
    crops = load_crops()
    base: dict[str, Any] = {
        "module": "kowr_rl",
        "version": __version__,
        "input": {"lat": lat, "lon": lon, "parcel_id": parcel_id, "crop": crop, "date": date.isoformat(), "budget_mm": budget_mm, "budget_used_mm": budget_used_mm, "policy": policy},
        "limitations": list(STATIC_LIMITATIONS),
    }
    sources: list[SourceRecord] = []

    def finish(status: str, message: str, **extra: Any) -> dict[str, Any]:
        out = {**base, "status": status, "message": message, **extra}
        out["sources"] = [s.to_dict() for s in sources]
        out["timing_s"] = round(time.perf_counter() - t_start, 3)
        return out

    crop_key = resolve_crop(crop)
    if crop_key is None:
        return finish("invalid_input", f"Nieznana uprawa '{crop}'. Dostepne: {', '.join(accepted_crop_codes())}")
    if date > today or date < ARCHIVE_START:
        return finish("invalid_input", f"Data analizy musi byc z zakresu {ARCHIVE_START} - {today}.")
    if budget_mm < 0 or budget_used_mm < 0:
        return finish("invalid_input", "Budzet wody nie moze byc ujemny.")
    if parcel_id:
        try:
            parcel, psrc = fetch_parcel(client, parcel_id)
            sources.append(psrc)
            lat, lon = parcel.centroid_lat, parcel.centroid_lon
            base["location_parcel"] = {"parcel_id": parcel.parcel_id, "teryt": parcel.teryt, "centroid": [lat, lon]}
        except DataUnavailableError as err:
            return finish("no_data", f"Nie znaleziono dzialki {parcel_id} w ULDK: {err}")
    if lat is None or lon is None:
        return finish("invalid_input", "Podaj wspolrzedne (lat, lon) albo numer dzialki.")
    if not _in_poland(lat, lon):
        return finish("outside_poland", "Punkt lezy poza Polska - modul dziala tylko dla Polski.")

    c = crops[crop_key]
    season_start, season_end = c.season_start(date.year), c.season_end(date.year)
    in_season = season_start <= date <= season_end
    need_live = date >= today - dt.timedelta(days=60)
    want_14 = int(plan_days) > 7 and in_season
    common = _fetch_common(client, lat, lon, today, need_live, zones_for=date if want_14 else None)
    soil_res = common["soil"]
    if isinstance(soil_res, Exception):
        return finish("no_data", f"Brak odpowiedzi warstwy glebowej IUNG-PIB: {soil_res}")
    code, ssrc = soil_res
    sources.append(ssrc)
    if code not in (1, 2, 3, 4):
        status, msg = _soil_message(code)
        return finish(status, msg, location={"lat": lat, "lon": lon, "iung_code": code})
    soil = load_soils()["categories"][code]
    taw = float(soil["taw_mm"])
    try:
        obs, wsrc, fc_series = _observed_weather(common, today)
    except DataUnavailableError as err:
        return finish("no_data", str(err))
    sources.extend(wsrc)
    location = {"lat": _r(lat, 5), "lon": _r(lon, 5), "iung_code": code, "soil_category": ROMAN[code], "soil_label": soil["label"], "taw_mm": taw}
    crop_info = {"key": c.key, "label": c.label_pl, "season_start": season_start.isoformat(), "season_end": season_end.isoformat(), "params": {"kc_ini": c.kc_ini, "kc_mid": c.kc_mid, "kc_end": c.kc_end, "p": c.p, "ky": c.ky}}

    if not in_season:
        hist = compare_strategies(lat, lon, crop, budget_mm, client=client, today=today, _prefetched=(code, obs, list(sources)))
        when = "przed sezonem" if date < season_start else "po sezonie"
        return finish(
            "out_of_season",
            f"Data {date} jest {when} uprawy '{c.label_pl}' ({season_start} - {season_end}). "
            "Brak rekomendacji dawek - ponizej porownanie strategii z ostatnich sezonow.",
            location=location,
            crop=crop_info,
            history=hist.get("strategies"),
            history_years=hist.get("seasons"),
        )

    pname, pol = get_policy(policy)
    issued_p, issued_e, fmode, fsrc = _issued_forecast(client, lat, lon, date, today, obs, fc_series, 16)
    sources.extend(fsrc)
    budget_left = max(0.0, budget_mm - budget_used_mm)
    spec = _season_spec(c, date.year, taw, budget_mm, obs, issued_p, issued_e, date)
    batch = build_batch([spec])
    # day-of-year climatology of the other archive years (the season spec holds one year only)
    clim_days = np.datetime64(season_start, "D") + np.arange(batch.clim_p.shape[1])
    batch.clim_p[0], batch.clim_e[0] = _weather_climatology(obs, clim_days, date.year)
    t_date = (date - season_start).days
    rng = np.random.default_rng(abs(hash((round(lat, 3), round(lon, 3), date.year))) % 2**32)
    plan_days = int(min(max(plan_days, 7), 14))
    _forecast_rows(batch, t_date, issued_p, rng, plan_days)
    history = None
    if irrigation_history:
        history = {}
        for d_s, mm in irrigation_history:
            k = (dt.date.fromisoformat(d_s) - season_start).days
            if 0 <= k < t_date:
                history[k] = history.get(k, 0.0) + float(mm)
    run = _run_plan(batch, pol, t_date, budget_used_mm, budget_left, history, days=plan_days)
    long_plan = run["plan"]
    run = {**run, "plan": long_plan[:7]}

    # scenarios (uncertainty of the forecast): no rain / forecast / 1.5 x forecast
    scen = {}
    for label, factor in (("bez_opadu", 0.0), ("wg_prognozy", 1.0), ("opad_x1_5", 1.5)):
        b2 = batch.subset([0])
        n = min(7, int(b2.length[0]) - t_date)
        b2.precip[0, t_date : t_date + n] = issued_p[:n] * factor
        b2.forecast[0, t_date : t_date + n + 1] *= factor
        scen[label] = float(sum(d["dose_mm"] for d in _run_plan(b2, pol, t_date, budget_used_mm, budget_left, history)["plan"]))
    alt_name = "fao_rule" if pname == "rl" else "rl"
    alt = None
    if alt_name == "fao_rule" or load_rl_policy() is not None:
        _, apol = get_policy(alt_name)
        aplan = _run_plan(batch, apol, t_date, budget_used_mm, budget_left, history)["plan"]
        alt = {"policy": alt_name, "label": STRATEGY_LABELS[alt_name], "dose_today_mm": aplan[0]["dose_mm"] if aplan else 0.0, "total_7d_mm": float(sum(d["dose_mm"] for d in aplan))}

    st = run["state0"]
    b = batch
    t = t_date
    dr0 = float(st.dr[0])
    et0_7 = float(b.et0_hist7[0, t])
    etc_today = float(b.kc[0, t] * et0_7 / 7.0)
    raw = float(st.raw_estimate()[0])
    days_to_stress = max(0.0, (raw - dr0) / max(etc_today, 0.1))
    f = b.forecast[0, t]
    stage_key = STAGES[int(b.stage[0, t])]
    plan = run["plan"]
    for d in plan:
        d["date"] = (date + dt.timedelta(days=d["day"])).isoformat()
    dose_today = plan[0]["dose_mm"] if plan else 0.0
    total_7d = float(sum(d["dose_mm"] for d in plan))
    confidence = None
    if pname == "rl":
        probs = pol.probabilities(st.obs(), action_mask(st.budget_left, pol.actions))[0]
        confidence = {f"{int(a)}_mm": _r(p, 3) for a, p in zip(pol.actions, probs)}

    storage_pct = 100 * (1 - dr0 / taw)
    stress_storage_pct = 100 * (1 - raw / taw)
    if dose_today > 0:
        action_now = f"Nawadniaj dzis: {dose_today:.0f} mm"
        why = f"zapas wody {storage_pct:.0f}% TAW jest {'ponizej' if dr0 > raw else 'blisko'} progu stresu ({stress_storage_pct:.0f}% TAW), prognoza opadu na 3 dni: {f[:3].sum():.0f} mm"
    elif budget_left <= 0:
        action_now = "Nie nawadniaj - budzet wody wyczerpany"
        why = f"pozostaly budzet 0 mm; zapas wody {storage_pct:.0f}% TAW"
    else:
        action_now = "Nie nawadniaj dzis"
        if dr0 <= raw:
            why = f"zapas wody {storage_pct:.0f}% TAW jest powyzej progu stresu ({stress_storage_pct:.0f}% TAW); przy obecnym zuzyciu stres za ok. {days_to_stress:.0f} dni bez deszczu"
        else:
            why = f"mimo niedoboru ({storage_pct:.0f}% TAW) prognozowany opad {f[:3].sum():.0f} mm w 3 dni / {f.sum():.0f} mm w 7 dni lub oszczedzanie budzetu ({budget_left:.0f} mm) na pozniejsza faze"
    next_doses = [d for d in plan[1:] if d["dose_mm"] > 0]
    text = f"{action_now} - {why}."
    if next_doses:
        text += " Plan na kolejne dni (warunkowy, zalezy od opadu): " + ", ".join(f"{d['date']}: {d['dose_mm']:.0f} mm" for d in next_doses) + "."
    else:
        text += " W kolejnych 6 dniach nie przewiduje sie nawadniania (jesli prognoza sie sprawdzi)."

    uncertainty = [
        f"Suma dawek na 7 dni: {scen['bez_opadu']:.0f} mm gdyby nie padalo, {scen['wg_prognozy']:.0f} mm wg prognozy, {scen['opad_x1_5']:.0f} mm przy opadzie 1,5 x prognoza.",
        {
            "live_ecmwf": "Prognoza: ECMWF IFS 0,25 deg na zywo (Open-Meteo). Skutecznosc prognozy opadu spada z wyprzedzeniem (korelacja ok. 0,7 na 1 dzien i 0,3 na 7 dni, kalibracja 2024).",
            "archived_ecmwf": "Prognoza: archiwalna prognoza ECMWF wydana dzien przed data analizy (Open-Meteo Previous Runs).",
            "climatology": "Prognoza: dla dat sprzed 2024 brak archiwalnych prognoz - uzyto klimatologii punktu (srednia z innych lat), bez zagladania w przyszlosc.",
        }[fmode],
    ]
    if history is None and budget_used_mm > 0:
        uncertainty.append(f"Historia nawodnien nie zostala podana: przyjeto, ze zuzyte {budget_used_mm:.0f} mm podano zgodnie z wybrana strategia.")
    elif history is None:
        uncertainty.append("Przyjeto brak nawadniania od poczatku sezonu do daty analizy (budzet zuzyty = 0).")
    if confidence:
        uncertainty.append(f"Pewnosc agenta RL dla dzisiejszej decyzji (prawdopodobienstwa akcji): {confidence}.")
    limitations = list(STATIC_LIMITATIONS)
    rl_status = "domyslny" if load_rl_config()["default_policy"] == "rl" else "eksperymentalny (domyslna jest regula FAO - patrz reports/evaluation.md)"
    if pname == "rl" and rl_status != "domyslny":
        limitations.insert(0, "Uzyto agenta RL oznaczonego jako eksperymentalny - na tescie nie spelnil kryterium gotowosci.")

    plan_14d = None
    if plan_days > 7:
        plan_14d = _long_plan(batch, pol, run["state0"], t_date, date, long_plan, issued_p, fmode, obs, plan_days,
                              crop=c, taw=taw, common=common, sources=sources, past_doses=run["past_doses"])

    result = finish(
        "ok",
        text,
        location=location,
        crop={**crop_info, "day_of_season": t_date + 1, "season_length": c.length, "stage": stage_key, "stage_label": STAGE_LABELS_PL[stage_key], "kc": _r(b.kc[0, t], 2)},
        policy={"used": pname, "label": STRATEGY_LABELS[pname], "default": load_rl_config()["default_policy"], "rl_status": rl_status, "rl_weights": str(RL_WEIGHTS.name) if load_rl_policy() else None},
        recommendation={
            "action_now": action_now,
            "dose_today_mm": dose_today,
            "total_7d_mm": total_7d,
            "plan_7d": plan,
            "window": [d["date"] for d in plan if d["dose_mm"] > 0],
            "scenarios_7d_mm": scen,
            "alternative": alt,
            **({"plan_14d": plan_14d} if plan_14d else {}),
        },
        rationale={
            "soil_water_storage_pct_taw": _r(storage_pct),
            "depletion_mm": _r(dr0),
            "taw_mm": taw,
            "stress_threshold_storage_pct_taw": _r(stress_storage_pct),
            "raw_mm": _r(raw),
            "days_to_stress_without_rain": _r(days_to_stress),
            "etc_estimate_mm_per_day": _r(etc_today, 2),
            "precip_last7_mm": _r(b.p_hist7[0, t]),
            "et0_last7_mm": _r(et0_7),
            "forecast_precip_3d_mm": _r(f[:3].sum()),
            "forecast_precip_7d_mm": _r(f.sum()),
            "budget_mm": budget_mm,
            "budget_used_mm": budget_used_mm,
            "budget_left_mm": budget_left,
            "irrigation_before_date_mm": _r(run["past_irrigation_mm"]),
            "policy_action_probabilities": confidence,
        },
        forecast_mode=fmode,
        uncertainty=uncertainty,
    )
    result["limitations"] = limitations
    if include_history:
        hist = compare_strategies(lat, lon, crop, budget_mm, client=client, today=today, _prefetched=(code, obs, []))
        result["history"] = hist.get("strategies")
        result["history_years"] = hist.get("seasons")
    result["timing_s"] = round(time.perf_counter() - t_start, 3)
    return result


# ======================================================================================
# compare_strategies()
# ======================================================================================
def compare_strategies(
    lat: float | None,
    lon: float | None,
    crop: str,
    budget_mm: float,
    *,
    parcel_id: str | None = None,
    n_seasons: int = 10,
    include_oracle: bool = True,
    client: HttpClient | None = None,
    today: dt.date | None = None,
    _prefetched: tuple[int, DailySeries, list[SourceRecord]] | None = None,
) -> dict[str, Any]:
    """Run the last ``n_seasons`` complete seasons of the point through all strategies."""
    t_start = time.perf_counter()
    client = client or get_client()
    today = today or _today()
    crops = load_crops()
    out: dict[str, Any] = {"module": "kowr_rl", "version": __version__, "input": {"lat": lat, "lon": lon, "parcel_id": parcel_id, "crop": crop, "budget_mm": budget_mm}}
    sources: list[SourceRecord] = []
    crop_key = resolve_crop(crop)
    if crop_key is None:
        return {**out, "status": "invalid_input", "message": f"Nieznana uprawa '{crop}'. Dostepne: {', '.join(accepted_crop_codes())}"}
    if parcel_id:
        parcel, psrc = fetch_parcel(client, parcel_id)
        sources.append(psrc)
        lat, lon = parcel.centroid_lat, parcel.centroid_lon
    if lat is None or lon is None or not _in_poland(lat, lon):
        return {**out, "status": "outside_poland", "message": "Podaj punkt w Polsce."}
    c = crops[crop_key]
    if _prefetched is not None:
        code, obs, sources = _prefetched[0], _prefetched[1], list(_prefetched[2])
    else:
        common = _fetch_common(client, lat, lon, today, need_live=False)
        if isinstance(common["soil"], Exception):
            return {**out, "status": "no_data", "message": str(common["soil"])}
        code, ssrc = common["soil"]
        sources.append(ssrc)
        if code not in (1, 2, 3, 4):
            status, msg = _soil_message(code)
            return {**out, "status": status, "message": msg, "sources": [s.to_dict() for s in sources]}
        obs, wsrc, _ = _observed_weather(common, today)
        sources.extend(wsrc)
    taw = float(load_soils()["categories"][code]["taw_mm"])
    last_complete = [y for y in range(ARCHIVE_START.year, today.year + 1) if c.season_end(y) + dt.timedelta(days=N_LEADS) < today]
    years = last_complete[-n_seasons:]
    specs, fmodes = [], []
    pf_dates = pf = None
    pf_years = [y for y in years if c.season_start(y) >= PREVIOUS_RUNS_START]
    if pf_years:
        try:
            pf_dates, pf, psrc = fetch_previous_runs(client, lat, lon, c.season_start(pf_years[0]), c.season_end(pf_years[-1]) + dt.timedelta(days=N_LEADS))
            sources.append(psrc)
        except DataUnavailableError:
            pf = None
    for y in years:
        spec = SeasonSpec(obs, y, c, taw, float(budget_mm), meta={"year": y})
        if pf is not None and y in pf_years:
            spec.forecast, _ = align_archived_forecast(c.season_start(y), c.length, pf_dates, pf)
            fmodes.append("archived_ecmwf")
        else:
            fmodes.append("synthetic_calibrated")
        specs.append(spec)
    batch = build_batch(specs)
    syn = noisy_forecast(batch.fut, np.random.default_rng(12345))
    for i, m in enumerate(fmodes):
        if m == "synthetic_calibrated":
            batch.forecast[i] = syn[i]
    pols = dict(baseline_policies())
    rl = load_rl_policy()
    if rl is not None:
        pols["rl"] = rl
    res = {name: simulate(batch, p) for name, p in pols.items()}
    if include_oracle:
        res["oracle"] = run_oracle(batch)
    none_y = res["none"].yield_rel
    strategies = []
    for name, r in res.items():
        gain = float((r.yield_rel - none_y).mean())
        water = float(r.water_mm.mean())
        strategies.append(
            {
                "strategy": name,
                "label": STRATEGY_LABELS[name],
                "avg_yield_rel": _r(r.yield_rel.mean(), 4),
                "avg_yield_loss_pct": _r(100 * (1 - r.yield_rel.mean()), 2),
                "worst_yield_rel": _r(r.yield_rel.min(), 4),
                "worst_year": int(years[int(np.argmin(r.yield_rel))]),
                "avg_water_mm": _r(water),
                "avg_deep_percolation_mm": _r(r.dp_mm.mean()),
                "avg_stress_days": _r(r.stress_days.mean()),
                "yield_gain_pp_per_100mm": _r(100 * gain / water * 100, 2) if water > 0 else None,
                "per_year": [{"year": y, "yield_rel": _r(r.yield_rel[i], 4), "water_mm": _r(r.water_mm[i]), "dp_mm": _r(r.dp_mm[i])} for i, y in enumerate(years)],
            }
        )
    return {
        **out,
        "status": "ok",
        "location": {"lat": _r(lat, 5), "lon": _r(lon, 5), "iung_code": code, "soil_category": ROMAN[code], "taw_mm": taw},
        "crop": {"key": c.key, "label": c.label_pl},
        "seasons": years,
        "forecast_modes": dict(zip(map(str, years), fmodes)),
        "default_policy": load_rl_config()["default_policy"],
        "strategies": strategies,
        "notes": [
            "Te same sezony i ta sama pogoda dla kazdej strategii; plon wzgledny wg FAO-33 (1.0 = brak straty z niedoboru wody).",
            "Prognozy: dla sezonow od 2024 prawdziwe archiwalne prognozy ECMWF, dla starszych syntetyczna prognoza skalibrowana na ECMWF 2024.",
            "Wyrocznia zna przyszla pogode - pokazuje gorna granice mozliwego zysku, nie jest strategia do stosowania.",
        ],
        "limitations": STATIC_LIMITATIONS,
        "sources": [s.to_dict() for s in sources],
        "timing_s": round(time.perf_counter() - t_start, 3),
    }


# ======================================================================================
# Adapters to the monorepo contracts (src/hydro_core/schemas.py)
# ======================================================================================
def to_irrigation_advice(rec: dict[str, Any], comparison: dict[str, Any] | None = None) -> dict[str, Any]:
    """Map recommend() (+ compare_strategies()) output to ``hydro_core.schemas.IrrigationAdvice``."""
    strategies = (comparison or {}).get("strategies") or rec.get("history") or []
    rows = [
        {
            "strategy_name": s["label"],
            "avg_yield_loss_pct": max(0.0, min(100.0, s["avg_yield_loss_pct"])),
            "avg_water_mm": s["avg_water_mm"],
            "avg_deep_percolation_mm": s["avg_deep_percolation_mm"],
            "water_use_efficiency": max(0.0, s["yield_gain_pp_per_100mm"] or 0.0),
        }
        for s in strategies
    ]
    recd = rec.get("recommendation") or {}
    return {
        "action_now": recd.get("action_now", rec.get("message", "")),
        "recommended_mm": float(recd.get("dose_today_mm", 0.0)),
        "rationale": rec.get("message", ""),
        "forecast_mode": rec.get("forecast_mode", "n/a"),
        "strategies_10y": rows,
    }


SOURCE_CATEGORY = {"Open-Meteo": "dane meteorologiczne", "IUNG": "dane glebowe", "GUGiK": "dane przestrzenne (dzialki)"}


def to_source_audit_entries(sources: list[dict[str, str]], observation_date: str) -> list[dict[str, Any]]:
    """Map SourceRecord dicts to ``hydro_core.schemas.SourceAuditEntry`` dicts."""
    out = []
    for s in sources:
        cat = next((v for k, v in SOURCE_CATEGORY.items() if s["name"].startswith(k)), "inne")
        res = "ok. 9-25 km (siatka modelu)" if "Open-Meteo" in s["name"] else ("raster IUNG-PIB" if "IUNG" in s["name"] else "dzialka ewidencyjna")
        out.append(
            {
                "category": cat,
                "source_name": s["name"],
                "endpoint_or_ref": s["url"],
                "mode": "live" if s["mode"] == "live" else "pre_fetched",
                "retrieved_at": s["retrieved_at"],
                "observation_date": observation_date,
                "spatial_resolution": res,
                "license": s["license"],
            }
        )
    return out
