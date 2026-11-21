"""SoilGrids v2.0 texture, organic carbon and available water at the 100 training points.

Uses the runtime parser (backend.clients.soilgrids.parse_soilgrids), so the soil features in
training are computed exactly like in the service. ISRIC fair use: <= 5 requests per minute.
Resumable: results are written after every point to ml/hydrofusion/data/soilgrids_points.json.

    uv run python ml/hydrofusion/fetch_soilgrids.py
"""

from __future__ import annotations

import json
import logging
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import SOIL_FILE, load_points  # noqa: E402

from backend.clients.base import SourceUnavailableError  # noqa: E402
from backend.clients.soilgrids import _DEPTHS, _PROPERTIES, parse_soilgrids  # noqa: E402

URL = "https://rest.isric.org/soilgrids/v2.0/properties/query"
SPACING_S = 13.0
log = logging.getLogger("fetch_soilgrids")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    SOIL_FILE.parent.mkdir(parents=True, exist_ok=True)
    done: dict[str, dict] = json.loads(SOIL_FILE.read_text(encoding="utf-8")) if SOIL_FILE.exists() else {}
    points = [p for p in load_points() if p.id not in done]
    with httpx.Client(headers={"User-Agent": "AgroRetencja-KOWR/0.1 (HydroFusion-PL training)"}, timeout=40) as client:
        for k, p in enumerate(points, start=1):
            params = [("lon", f"{p.lon:.5f}"), ("lat", f"{p.lat:.5f}"), ("value", "mean")]
            params += [("property", x) for x in _PROPERTIES] + [("depth", d) for d in _DEPTHS]
            record: dict = {"retrieved_at": datetime.now(UTC).isoformat(timespec="seconds"), "url": URL}
            for attempt in range(3):
                try:
                    r = client.get(URL, params=params)
                    if r.status_code == 429:
                        time.sleep(60)
                        continue
                    r.raise_for_status()
                    prof = parse_soilgrids(r.json())
                    record.update(sand_pct=prof.topsoil_sand_pct, soc_g_kg=prof.topsoil_soc_g_kg,
                                  awc_mm_0_100=prof.awc_mm_0_100, estimated_category=prof.estimated_category.value)
                    break
                except SourceUnavailableError as exc:
                    record["error"] = str(exc)
                    break
                except httpx.HTTPError as exc:
                    record["error"] = f"{type(exc).__name__}"
                    time.sleep(10)
            done[p.id] = record
            SOIL_FILE.write_text(json.dumps(done, indent=1, ensure_ascii=False), encoding="utf-8")
            log.info("%d/%d %s %s", k, len(points), p.id, {k2: record.get(k2) for k2 in ("sand_pct", "soc_g_kg", "awc_mm_0_100", "error")})
            time.sleep(SPACING_S)


if __name__ == "__main__":
    main()
