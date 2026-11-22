"""API tests with a fake data collector (no network): steps 1-5, What-If, PDF, out-of-scope parcel."""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient
from synthetic_data import ANALYSIS_DATE, make_bundle

from backend.analysis import AnalysisService
from backend.clients.pipeline import ParcelDataBundle
from backend.main import app
from hydro_core.schemas import AnalysisRequest


class FakeCollector:
    def __init__(self, iung_values: list[int | None] | None = None) -> None:
        self.iung_values = iung_values
        self.calls = 0

    async def collect(self, request: AnalysisRequest, today: dt.date | None = None) -> ParcelDataBundle:
        self.calls += 1
        return make_bundle(request.analysis_date, iung_values=self.iung_values)


@pytest.fixture
def client_factory():
    clients = []

    def make(iung_values: list[int | None] | None = None) -> tuple[TestClient, FakeCollector]:
        c = TestClient(app)
        c.__enter__()
        fake = FakeCollector(iung_values)
        app.state.collector = fake
        app.state.service = AnalysisService(fake)  # type: ignore[arg-type]
        clients.append(c)
        return c, fake

    yield make
    for c in clients:
        c.__exit__(None, None, None)


BODY = {"parcel_id": "302111_5.0015.72", "crop": "kukurydza", "analysis_date": ANALYSIS_DATE.isoformat()}


def test_analyze_returns_all_kowr_steps(client_factory) -> None:
    client, fake = client_factory()
    r = client.post("/api/analyze", json=BODY)
    assert r.status_code == 200, r.text
    d = r.json()
    a = d["assessment"]
    assert -5 <= a["water_state"] <= 5
    assert len(a["water_state_timeline"]) > 30
    assert any(p["forecast"] for p in a["water_state_timeline"])
    assert len(a["history_10y"]) == 11
    shares = sum(d["causes"][k] for k in ("climate_pct", "soil_pct", "runoff_pct", "crop_pct"))
    assert abs(shares - (100 if d["causes"]["has_deficit"] else 0)) < 0.5
    assert d["retention"]["recommendations"]
    assert d["irrigation"]["policy_used"] in ("rl", "fao_rule")
    assert d["irrigation"]["strategies_10y"]
    assert d["advice"]
    assert {"parcel", "zones", "flows"} <= set(d["map_layers"])
    from shapely.geometry import shape
    from shapely.ops import unary_union

    parcel_geom = shape(d["parcel"]["geojson"])
    z_feats = d["map_layers"]["zones"]["features"]
    assert z_feats
    assert 99.0 <= sum(f["properties"]["area_pct"] for f in z_feats) <= 101.0
    z_union = unary_union([shape(f["geometry"]) for f in z_feats])
    assert 100.0 * z_union.difference(parcel_geom).area / max(z_union.area, 1e-12) < 0.5
    hf_label = d["audit"]["model_versions"]["hydrofusion"]
    assert hf_label.startswith(("brak wag", "HydroFusion-PL"))
    # the synthetic bundle has no Sentinel-2 at all -> nothing to reconstruct from, no fake value
    if hf_label.startswith("HydroFusion-PL"):
        assert a["ndmi_value"] is None or a["ndmi_is_reconstructed"]

    # cached: What-If and PDF do not collect again
    w = client.post("/api/what-if", json={**BODY, "enabled_measures": ["mulcz", "materia_organiczna"]})
    assert w.status_code == 200 and w.json()["enabled_measures"] == ["mulcz", "materia_organiczna"]
    p = client.get("/api/report.pdf", params=BODY)
    assert p.status_code == 200 and p.headers["x-report-renderer"] in ("weasyprint", "matplotlib", "html")
    if p.headers["content-type"] == "application/pdf":
        assert p.content[:4] == b"%PDF"
    assert fake.calls == 1


def test_non_agricultural_parcel_gets_message_not_analysis(client_factory) -> None:
    client, _ = client_factory(iung_values=[11, 11, 11])
    r = client.post("/api/analyze", json=BODY)
    assert r.status_code == 422
    assert r.json()["status"] == "not_arable" and "las" in r.json()["message"]


def test_date_out_of_range_and_registries(client_factory) -> None:
    client, _ = client_factory()
    assert client.post("/api/analyze", json={**BODY, "analysis_date": "2015-06-01"}).status_code == 422
    reg = client.get("/api/registries").json()
    assert "zapas_wody_w_glebie" in [c["name"] for c in reg["score_components"]]
    assert reg["irrigation_providers"] == ["rl", "fao"]
    assert "weather_history" in reg["data_sources"]
