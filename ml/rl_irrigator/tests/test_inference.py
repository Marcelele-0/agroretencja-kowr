"""recommend() / compare_strategies() / FastAPI router against a mocked network (no internet)."""

from __future__ import annotations

import datetime as dt
import json
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import numpy as np
import pytest

import kowr_rl.inference as inf
from kowr_rl.data import HttpClient

FIX = Path(__file__).parent / "fixtures"
TODAY = dt.date(2026, 7, 15)


def _p(d: dt.date) -> float:
    rng = np.random.default_rng(d.toordinal())
    return float(rng.exponential(7.0)) if rng.random() < 0.28 else 0.0


def _e(d: dt.date) -> float:
    doy = d.timetuple().tm_yday
    return round(max(0.3, 0.5 + 4.2 * np.sin(np.pi * (doy - 60) / 245.0)), 2)


def _days(a: dt.date, b: dt.date) -> list[dt.date]:
    return [a + dt.timedelta(days=i) for i in range((b - a).days + 1)]


def make_handler(today: dt.date, iung_code: int = 2):
    calls: dict[str, int] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        calls[host] = calls.get(host, 0) + 1
        q = {k: v[0] for k, v in parse_qs(urlparse(str(request.url)).query).items()}
        if host == "archive-api.open-meteo.com":
            ds = _days(dt.date.fromisoformat(q["start_date"]), dt.date.fromisoformat(q["end_date"]))
            return httpx.Response(200, json={"latitude": 52.1, "longitude": 18.2, "daily": {"time": [d.isoformat() for d in ds], "precipitation_sum": [_p(d) for d in ds], "et0_fao_evapotranspiration": [_e(d) for d in ds]}})
        if host == "api.open-meteo.com":
            ds = _days(today - dt.timedelta(days=int(q["past_days"])), today + dt.timedelta(days=int(q["forecast_days"]) - 1))
            return httpx.Response(200, json={"daily": {"time": [d.isoformat() for d in ds], "precipitation_sum": [_p(d) * 0.9 for d in ds], "et0_fao_evapotranspiration": [_e(d) for d in ds]}})
        if host == "previous-runs-api.open-meteo.com":
            ds = _days(dt.date.fromisoformat(q["start_date"]), dt.date.fromisoformat(q["end_date"]))
            times = [f"{d.isoformat()}T{h:02d}:00" for d in ds for h in range(24)]
            hourly = {"time": times}
            for k in range(1, 8):
                hourly[f"precipitation_previous_day{k}"] = [_p(dt.date.fromisoformat(t[:10])) / 24 * 0.8 for t in times]
            return httpx.Response(200, json={"hourly": hourly})
        if host == "ensemble-api.open-meteo.com":
            ds = _days(today, today + dt.timedelta(days=int(q["forecast_days"]) - 1))
            times = [f"{d.isoformat()}T{h:02d}:00" for d in ds for h in range(24)]
            rng = np.random.default_rng(7)
            hourly: dict = {"time": times}
            for m in range(51):
                sfx = "" if m == 0 else f"_member{m:02d}"
                f = float(rng.uniform(0.3, 1.7))
                hourly["precipitation" + sfx] = [_p(dt.date.fromisoformat(t[:10])) / 24 * f for t in times]
                hourly["temperature_2m" + sfx] = [18.0 + 6 * np.sin(np.pi * (int(t[11:13]) - 6) / 12) + rng.normal(0, 1) for t in times]
                hourly["shortwave_radiation" + sfx] = [max(0.0, 600 * np.sin(np.pi * (int(t[11:13]) - 5) / 15)) if 5 <= int(t[11:13]) <= 20 else 0.0 for t in times]
            return httpx.Response(200, json={"hourly": hourly})
        if host == "rest.isric.org":
            fc = {"0-5cm": 380, "5-15cm": 360, "15-30cm": 340, "30-60cm": 300, "60-100cm": 280, "100-200cm": 260}
            wp = {"0-5cm": 160, "5-15cm": 150, "15-30cm": 150, "30-60cm": 140, "60-100cm": 140, "100-200cm": 130}
            layers = [{"name": n, "unit_measure": {"d_factor": 10}, "depths": [{"label": k, "values": {"mean": v}} for k, v in src.items()]}
                      for n, src in (("wv0033", fc), ("wv1500", wp))]
            return httpx.Response(200, json={"properties": {"layers": layers}})
        if host == "sgeo.iung.pl":
            return httpx.Response(200, json={"type": "FeatureCollection", "features": [{"properties": {"GRAY_INDEX": iung_code}}]})
        if host == "uldk.gugik.gov.pl":
            return httpx.Response(200, text=(FIX / "uldk_ok.txt").read_text(encoding="utf-8"))
        return httpx.Response(404, text="unknown host")

    return handler, calls


@pytest.fixture
def mock_client(tmp_path, monkeypatch):
    handler, calls = make_handler(TODAY)
    client = HttpClient(tmp_path / "cache", transport=httpx.MockTransport(handler))
    monkeypatch.setattr(inf, "_CLIENT", client)
    # hermetic: the national weather grid in data/cache would bypass the mocked archive
    monkeypatch.setattr("kowr_rl.data._local_grid_archive", lambda *a, **k: None)
    client.calls = calls  # type: ignore[attr-defined]
    return client


def _check_ok(out: dict, budget_left: float) -> None:
    assert out["status"] == "ok", out.get("message")
    plan = out["recommendation"]["plan_7d"]
    assert 1 <= len(plan) <= 7
    assert all(d["dose_mm"] in (0.0, 10.0, 20.0, 30.0) for d in plan)
    assert out["recommendation"]["total_7d_mm"] <= budget_left + 1e-9
    r = out["rationale"]
    for k in ("soil_water_storage_pct_taw", "stress_threshold_storage_pct_taw", "forecast_precip_7d_mm", "budget_left_mm", "days_to_stress_without_rain"):
        assert k in r
    assert 0 <= r["soil_water_storage_pct_taw"] <= 100
    assert out["crop"]["stage"] in ("ini", "dev", "mid", "late")
    names = " ".join(s["name"] for s in out["sources"])
    assert "Historical Weather" in names and "IUNG" in names
    assert all(s["retrieved_at"] for s in out["sources"])
    assert out["limitations"] and out["uncertainty"]
    json.dumps(out)  # JSON-serialisable


def test_recommend_historical_climatology(mock_client) -> None:
    out = inf.recommend(52.1, 18.2, "kukurydza", "2023-07-10", 150, 40, client=mock_client, today=TODAY)
    _check_ok(out, 110)
    assert out["forecast_mode"] == "climatology"
    assert set(out["recommendation"]["scenarios_7d_mm"]) == {"bez_opadu", "wg_prognozy", "opad_x1_5"}
    s = out["recommendation"]["scenarios_7d_mm"]
    assert s["bez_opadu"] >= s["opad_x1_5"]


def test_recommend_archived_forecast(mock_client) -> None:
    out = inf.recommend(52.1, 18.2, "ziemniak", "2025-06-20", 200, client=mock_client, today=TODAY)
    _check_ok(out, 200)
    assert out["forecast_mode"] == "archived_ecmwf"
    assert any("Previous Runs" in s["name"] for s in out["sources"])


def test_recommend_today_live_forecast(mock_client) -> None:
    out = inf.recommend(52.1, 18.2, "burak_cukrowy", TODAY, 150, client=mock_client, today=TODAY)
    _check_ok(out, 150)
    assert out["forecast_mode"] == "live_ecmwf"
    assert out["recommendation"]["plan_7d"][0]["date"] == TODAY.isoformat()


def test_budget_exhausted_means_no_irrigation(mock_client) -> None:
    out = inf.recommend(52.1, 18.2, "kukurydza", "2023-07-25", 100, 100, client=mock_client, today=TODAY)
    _check_ok(out, 0)
    assert out["recommendation"]["total_7d_mm"] == 0
    assert "wyczerpany" in out["recommendation"]["action_now"] or out["recommendation"]["dose_today_mm"] == 0


def test_out_of_season_gives_history(mock_client) -> None:
    out = inf.recommend(52.1, 18.2, "pszenica_ozima", "2025-11-10", 150, client=mock_client, today=TODAY)
    assert out["status"] == "out_of_season"
    assert "po sezonie uprawy" in out["message"] and "Brak rekomendacji dawek" in out["message"]
    assert out["history"] and {s["strategy"] for s in out["history"]} >= {"none", "fao_rule", "oracle"}
    assert "recommendation" not in out


def test_not_arable_and_outside(tmp_path, monkeypatch) -> None:
    handler, _ = make_handler(TODAY, iung_code=11)
    client = HttpClient(tmp_path, transport=httpx.MockTransport(handler))
    out = inf.recommend(52.1, 18.2, "kukurydza", "2024-07-01", 150, client=client, today=TODAY)
    assert out["status"] == "not_arable" and "Las" in out["message"]
    out = inf.recommend(48.0, 10.0, "kukurydza", "2024-07-01", 150, client=client, today=TODAY)
    assert out["status"] == "outside_poland"
    out = inf.recommend(52.1, 18.2, "ryz", "2024-07-01", 150, client=client, today=TODAY)
    assert out["status"] == "invalid_input"


def test_compare_strategies(mock_client) -> None:
    out = inf.compare_strategies(52.1, 18.2, "kukurydza", 150, client=mock_client, today=TODAY)
    assert out["status"] == "ok"
    assert out["seasons"] == list(range(2016, 2026))  # 2026 season not complete on TODAY
    by = {s["strategy"]: s for s in out["strategies"]}
    assert by["none"]["avg_water_mm"] == 0
    assert by["fao_rule"]["avg_water_mm"] <= 150
    assert by["oracle"]["avg_yield_rel"] >= by["none"]["avg_yield_rel"]
    assert out["forecast_modes"]["2025"] == "archived_ecmwf" and out["forecast_modes"]["2019"] == "synthetic_calibrated"
    assert len(by["fao_rule"]["per_year"]) == 10


def test_parcel_id_and_contract_adapters(mock_client) -> None:
    schemas = pytest.importorskip("hydro_core.schemas")
    out = inf.recommend(None, None, "kukurydza", "2024-07-15", 150, parcel_id="141201_1.0001.6509", include_history=True, client=mock_client, today=TODAY)
    _check_ok(out, 150)
    assert out["location_parcel"]["teryt"] == "141201_1.0001.6509"
    advice = schemas.IrrigationAdvice(**inf.to_irrigation_advice(out))
    assert advice.strategies_10y and advice.recommended_mm >= 0
    for e in inf.to_source_audit_entries(out["sources"], "2024-07-15"):
        schemas.SourceAuditEntry(**e)


def test_cached_call_is_fast(mock_client) -> None:
    inf.recommend(52.1, 18.2, "kukurydza", "2024-07-15", 150, client=mock_client, today=TODAY)
    n_calls = sum(mock_client.calls.values())
    t0 = time.perf_counter()
    out = inf.recommend(52.1, 18.2, "kukurydza", "2024-07-15", 150, client=mock_client, today=TODAY)
    assert time.perf_counter() - t0 < 1.0
    assert sum(mock_client.calls.values()) == n_calls  # everything from cache
    assert all(s["mode"] == "cache" for s in out["sources"])


def test_api_router(mock_client, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from kowr_rl.api import app

    monkeypatch.setattr(inf, "_today", lambda: TODAY)
    c = TestClient(app)
    h = c.get("/rl/health").json()
    assert h["status"] == "ok" and "kukurydza" in h["crops"]
    r = c.post("/rl/recommend", json={"lat": 52.1, "lon": 18.2, "crop": "kukurydza", "date": "2024-07-15", "budget_mm": 150})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok" and "irrigation_advice" in body
    r = c.get("/rl/compare", params={"lat": 52.1, "lon": 18.2, "crop": "ziemniak", "budget_mm": 100})
    assert r.status_code == 200 and r.json()["status"] == "ok"
    r = c.post("/rl/recommend", json={"crop": "kukurydza", "date": "2024-07-15"})
    assert r.status_code == 422


FRONTEND_CROPS = (
    "zboza_ozime", "zboza_jare", "kukurydza_ziarno", "kukurydza_kiszonka", "rzepak", "ziemniak", "burak_cukrowy",
    "straczkowe", "warzywa", "chmiel", "tyton", "drzewa_owocowe", "krzewy_owocowe", "truskawki", "uzytki_zielone",
)
SCHEMAS_CROPS = ("pszenica_ozima", "pszenica_jara", "kukurydza", "rzepak_ozimy", "burak_cukrowy", "ziemniak", "uzytki_zielone")


def test_every_frontend_and_schema_crop_is_supported() -> None:
    from kowr_rl.config import load_crops, resolve_crop

    crops = load_crops()
    for code in FRONTEND_CROPS + SCHEMAS_CROPS:
        key = resolve_crop(code)
        assert key in crops, code
        c = crops[key]
        # inside the window of archived ECMWF forecasts (15 III - 31 X) and after the 1 III spin-up
        assert (c.season_start(2025).month, c.season_start(2025).day) >= (3, 15), code
        assert (c.season_end(2025).month, c.season_end(2025).day) <= (10, 31), code
        assert 0.1 <= c.p <= 0.8 and 0.5 <= c.ky <= 1.5 and c.kc_mid >= c.kc_ini
    assert resolve_crop("kapusta_wloska") is None


def test_api_accepts_frontend_crop_codes(mock_client, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from kowr_rl.api import app

    monkeypatch.setattr(inf, "_today", lambda: TODAY)
    c = TestClient(app)
    for code in ("zboza_ozime", "truskawki", "pszenica_jara"):
        r = c.post("/rl/recommend", json={"lat": 52.1, "lon": 18.2, "crop": code, "date": "2024-06-20", "budget_mm": 150})
        assert r.status_code == 200, r.text
        assert r.json()["status"] in ("ok", "out_of_season"), r.json().get("message")
    r = c.post("/rl/recommend", json={"lat": 52.1, "lon": 18.2, "crop": "kapusta_wloska", "date": "2024-06-20"})
    assert r.status_code == 422
    assert "zboza_ozime" in c.get("/rl/health").json()["crop_codes_accepted"]


def test_plan_14_days_with_scenarios(mock_client) -> None:
    out = inf.recommend(52.1, 18.2, "kukurydza", "2024-07-10", 150, 0, plan_days=14, client=mock_client, today=TODAY)
    assert out["status"] == "ok", out.get("message")
    assert len(out["recommendation"]["plan_7d"]) == 7
    p14 = out["recommendation"]["plan_14d"]
    assert len(p14["days"]) == 14
    assert [d["confidence"] for d in p14["days"]][:3] == ["wysoka"] * 3 and p14["days"][-1]["confidence"] == "niska"
    assert p14["days"][0]["dose_mm"] == out["recommendation"]["dose_today_mm"]
    probs = [d["irrigation_probability"] for d in p14["days"] if d["irrigation_probability"] is not None]
    assert probs and all(0.0 <= x <= 1.0 for x in probs)
    w = p14["water_need_mm"]
    assert w is None or (0 <= w["p10_mm"] <= w["expected_mm"] + 1e-9 <= w["p90_mm"] + 2e-9)
    assert p14["total_plan_mm"] <= 150 + 1e-9
    assert "plan_14d" not in inf.recommend(52.1, 18.2, "kukurydza", "2024-07-10", 150, 0, client=mock_client, today=TODAY)["recommendation"]


def _check_zones(p14: dict, n_days: int) -> None:
    z = p14["soil_zones"]
    assert [x["zone"] for x in z["zones"]] == ["0-28 cm", "28-100 cm", "100-200 cm"]
    assert len(z["days"]) == n_days and z["data_scale"]
    for d in z["days"]:
        for blk in [d["root_zone"]] + d["zones"]:
            probs = blk["class_probabilities"]
            assert set(probs) == set(z["classes"]) and abs(sum(probs.values()) - 1.0) < 0.03
            assert blk["class"] in z["classes"]
        r = d["root_zone"]
        assert 0 <= r["water_pct_p10"] <= r["water_pct_median"] <= r["water_pct_p90"] <= 100
        assert 0.0 <= d["stress_probability_no_irrigation"] <= 1.0
        if "stress_probability_with_plan" in d:
            assert d["stress_probability_with_plan"] <= d["stress_probability_no_irrigation"] + 1e-9


def test_plan_14_days_ecmwf_ensemble_and_soil_zones(mock_client) -> None:
    out = inf.recommend(52.1, 18.2, "kukurydza", TODAY.isoformat(), 150, 0, plan_days=14, client=mock_client, today=TODAY)
    assert out["status"] == "ok", out.get("message")
    p14 = out["recommendation"]["plan_14d"]
    assert p14["probability_source"] == "ecmwf_ens"
    assert p14["water_need_mm"]["n_scenarios"] == 51
    assert p14["days"][0]["dose_mm"] == out["recommendation"]["dose_today_mm"]
    _check_zones(p14, 14)
    z = p14["soil_zones"]
    assert z["n_scenarios"] >= 51 and "ECMWF ENS" in z["weather_source"] and "SoilGrids" in z["soil_profile_source"]
    assert abs(sum(x["available_water_mm"] for x in z["zones"][:2]) - out["location"]["taw_mm"]) < 0.5
    names = " ".join(s["name"] for s in out["sources"])
    assert "Ensemble" in names and "SoilGrids" in names
    assert mock_client.calls.get("ensemble-api.open-meteo.com") == 1
    json.dumps(out)


def test_plan_14_days_historical_uses_analog_years(mock_client, monkeypatch) -> None:
    monkeypatch.delenv("KOWR_RL_ENS_ARCHIVE", raising=False)
    out = inf.recommend(52.1, 18.2, "ziemniak", "2025-07-01", 150, 0, plan_days=14, client=mock_client, today=TODAY)
    assert out["status"] == "ok", out.get("message")
    p14 = out["recommendation"]["plan_14d"]
    assert p14["probability_source"] == "analog_years"
    _check_zones(p14, 14)
    assert "lata analogiczne" in p14["soil_zones"]["weather_source"]
    assert "ensemble-api.open-meteo.com" not in mock_client.calls


def test_plan_7_days_does_not_fetch_zone_sources(mock_client) -> None:
    out = inf.recommend(52.1, 18.2, "kukurydza", TODAY.isoformat(), 150, 0, client=mock_client, today=TODAY)
    assert out["status"] == "ok"
    assert "rest.isric.org" not in mock_client.calls and "ensemble-api.open-meteo.com" not in mock_client.calls


def test_prefetched_plan_14_fetches_only_the_zone_sources(mock_client) -> None:
    """Backend path (src/backend/irrigation.py): soil and weather are passed in, the 14-day sources are fetched here."""
    obs = inf.fetch_archive(mock_client, 52.1, 18.2, inf.ARCHIVE_START, TODAY - dt.timedelta(days=1))[0]
    fc = inf.fetch_forecast(mock_client, 52.1, 18.2)[0]
    before = dict(mock_client.calls)
    out = inf.recommend(52.1, 18.2, "kukurydza_ziarno", TODAY, 150, 0, plan_days=14, client=mock_client, today=TODAY,
                        _prefetched=(2, obs.merged_with(fc.window(inf.ARCHIVE_START, TODAY - dt.timedelta(days=1))), [], fc))
    assert out["status"] == "ok", out.get("message")
    p14 = out["recommendation"]["plan_14d"]
    assert p14["probability_source"] == "ecmwf_ens" and "soil_zones" in p14
    new = {h: n - before.get(h, 0) for h, n in mock_client.calls.items() if n - before.get(h, 0)}
    assert set(new) == {"ensemble-api.open-meteo.com", "rest.isric.org"}, new


# --------------------------------------------------------------------------- off season, "today"
OFF_TODAY = dt.date(2026, 11, 22)  # after the season of every crop


@pytest.fixture
def off_client(tmp_path, monkeypatch):
    handler, calls = make_handler(OFF_TODAY)
    client = HttpClient(tmp_path / "cache", transport=httpx.MockTransport(handler))
    monkeypatch.setattr(inf, "_CLIENT", client)
    monkeypatch.delenv("KOWR_RL_ENS_ARCHIVE", raising=False)
    monkeypatch.setattr("kowr_rl.data._local_grid_archive", lambda *a, **k: None)
    client.calls = calls  # type: ignore[attr-defined]
    return client


def test_offseason_today_gives_hold_off_and_soil_forecast(off_client, monkeypatch) -> None:
    monkeypatch.setattr(inf, "get_policy", lambda name: pytest.fail("policy must not run off season"))
    out = inf.recommend(52.1, 18.2, "kukurydza", OFF_TODAY, 150, plan_days=14, client=off_client, today=OFF_TODAY)
    assert out["status"] == "out_of_season", out.get("message")
    assert out["message"].startswith("Kukurydza na ziarno jest poza sezonem (25 kwietnia – 11 października).")
    assert "decyzje o dawkach wracają od początku sezonu" in out["message"]
    assert out["history"] and {s["strategy"] for s in out["history"]} >= {"none", "fao_rule"}
    r = out["recommendation"]
    assert r["action_now"] == "Nie podlewaj teraz"
    assert r["dose_today_mm"] == 0 and r["total_7d_mm"] == 0 and r["window"] == []
    assert len(r["plan_7d"]) == 7 and all(d["dose_mm"] == 0 for d in r["plan_7d"])
    assert r["plan_7d"][0]["date"] == OFF_TODAY.isoformat()
    p14 = r["plan_14d"]
    assert len(p14["days"]) == 14
    assert all(d["dose_mm"] == 0 and d["irrigation_probability"] == 0 for d in p14["days"])
    assert p14["total_plan_mm"] == 0 and p14["water_need_mm"] is None
    assert p14["probability_source"] == "ecmwf_ens" and p14["offseason"] is True
    assert p14["offseason_kc"] == inf.load_crops()["kukurydza"].kc_ini == p14["offseason_surface"]["kc"]
    _check_zones(p14, 14)
    z = p14["soil_zones"]
    assert 0 <= z["today"]["root_zone_water_pct"] <= 100 and "ECMWF ENS" in z["weather_source"]
    assert all("stress_probability_with_plan" not in d for d in z["days"])
    assert off_client.calls.get("ensemble-api.open-meteo.com") == 1
    assert any("Kc_ini" in x for x in out["limitations"])
    json.dumps(out)


def test_offseason_yesterday_and_winter_crop(off_client) -> None:
    out = inf.recommend(52.1, 18.2, "zboza_ozime", OFF_TODAY - dt.timedelta(days=1), 150, plan_days=14, client=off_client, today=OFF_TODAY)
    assert out["status"] == "out_of_season"
    p14 = out["recommendation"]["plan_14d"]
    assert p14["probability_source"] == "analog_years"  # no live ensemble for yesterday, archive disabled
    assert p14["offseason_kc"] == 0.4 and all(d["dose_mm"] == 0 for d in p14["days"])
    _check_zones(p14, 14)


def test_offseason_historical_or_short_plan_has_no_plan(off_client) -> None:
    out = inf.recommend(52.1, 18.2, "kukurydza", "2026-11-10", 150, plan_days=14, client=off_client, today=OFF_TODAY)
    assert out["status"] == "out_of_season"
    assert out["message"].startswith("Data 2026-11-10 jest po sezonie") and "recommendation" not in out
    out = inf.recommend(52.1, 18.2, "kukurydza", OFF_TODAY, 150, plan_days=7, client=off_client, today=OFF_TODAY)
    assert out["status"] == "out_of_season" and "recommendation" not in out
    assert "rest.isric.org" not in off_client.calls and "ensemble-api.open-meteo.com" not in off_client.calls


def test_in_season_plan_has_no_offseason_fields(mock_client) -> None:
    out = inf.recommend(52.1, 18.2, "kukurydza", TODAY, 150, 0, plan_days=14, client=mock_client, today=TODAY)
    assert out["status"] == "ok"
    p14 = out["recommendation"]["plan_14d"]
    assert "offseason" not in p14 and "offseason_kc" not in p14
    assert out["recommendation"]["action_now"] != inf.OFFSEASON_ACTION


def test_offseason_zone_state_joins_the_season(off_client) -> None:
    from kowr_rl import forecast14_runtime as f14

    obs = inf.fetch_archive(off_client, 52.1, 18.2, inf.ARCHIVE_START, OFF_TODAY - dt.timedelta(days=1))[0]
    c = inf.load_crops()["kukurydza"]
    awc = np.array([40.0, 100.0, 120.0])
    with pytest.raises(ValueError):
        f14.offseason_zone_state(obs.dates, obs.precip, obs.et0, c, dt.date(2025, 7, 1), awc)
    # before sowing: the pre-sowing spin-up of zone_state, inside the zone capacities
    d_pre = f14.offseason_zone_state(obs.dates, obs.precip, obs.et0, c, c.season_start(2025) - dt.timedelta(days=1), awc)
    assert np.all((d_pre >= 0) & (d_pre <= awc))
    # the day after harvest equals zone_state at the end of the season (no off-season day run yet)
    after = c.season_end(2025) + dt.timedelta(days=1)
    d_after = f14.offseason_zone_state(obs.dates, obs.precip, obs.et0, c, after, awc)
    assert np.allclose(d_after, f14.zone_state(obs.dates, obs.precip, obs.et0, c, after, awc)["d0"])
