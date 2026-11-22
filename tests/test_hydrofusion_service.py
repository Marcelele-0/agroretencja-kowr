"""With the deployed HydroFusion-PL artifacts, a cloudy day gets a reconstructed NDMI with an interval."""

from __future__ import annotations

import datetime as dt

import pytest
from synthetic_data import ANALYSIS_DATE, TODAY, make_bundle

from backend.analysis import AnalysisService
from backend.clients.sentinel2 import S2ParcelSeries, S2SceneStats
from backend.inference.hydrofusion import MODELS_DIR, load_hydrofusion
from hydro_core.schemas import AnalysisRequest, CropCode

pytestmark = pytest.mark.skipif(not (MODELS_DIR / "hydrofusion_meta.json").exists(), reason="model not deployed")


class CloudyCollector:
    """No clear scene in the last 15 days, but one 30 days back and a 9-year history."""

    async def collect(self, request: AnalysisRequest, today: dt.date | None = None):
        bundle = make_bundle(request.analysis_date)
        prev_day = request.analysis_date - dt.timedelta(days=30)
        bundle.sentinel2 = S2ParcelSeries(
            current=None,
            previous=S2SceneStats(scene_id="S2_prev", acquired=prev_day, scene_cloud_pct=5.0, parcel_clear_fraction=1.0, ndmi_mean=0.31),
            history=[], years_without_data=[], years_timed_out=[], ndmi_history_mean=0.28,
        )
        return bundle


def test_cloudy_day_gets_reconstructed_ndmi() -> None:
    load_hydrofusion.cache_clear()
    service = AnalysisService(CloudyCollector())  # type: ignore[arg-type]
    req = AnalysisRequest(parcel_id="302111_5.0015.72", crop=CropCode.KUKURYDZA, analysis_date=ANALYSIS_DATE)
    import asyncio

    state = asyncio.run(service.analyze(req, today=TODAY))
    a = state.response.assessment
    if a.confidence.inside_aoa:
        assert a.ndmi_is_reconstructed
        assert a.ndmi_interval is not None and a.ndmi_interval[0] <= a.ndmi_value <= a.ndmi_interval[1]
        sat = next(c for c in a.water_score.components if c.name == "wilgotnosc_lanu_ndmi")
        assert sat.value is not None and sat.weight_used > 0
        assert any("HydroFusion" in w for w in a.confidence.resolution_warnings)
    else:
        assert not a.ndmi_is_reconstructed and a.ndmi_value is None
