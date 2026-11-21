"""E9: ISRIC SoilGrids v2.0 water-retention profiles for the dataset points (training + hold-out).

Per point and depth (0-5, 5-15, 15-30, 30-60, 60-100, 100-200 cm): volumetric water content at
field capacity (wv0033, -33 kPa) and wilting point (wv1500, -1500 kPa), plus sand, clay and SOC.
Available water of a layer: AWC_z = (theta_FC - theta_WP) * dz [mm].

ISRIC asks for at most 5 requests per minute -> one request per point, 13 s apart, cached on disk.
Output: data/soils/soilgrids_profiles.json (data/holdout points included).

    uv run --group train python ml/rl_irrigator/scripts/fetch_soilgrids.py
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kowr_rl import DATA_DIR  # noqa: E402
from kowr_rl.data import DataUnavailableError, HttpClient  # noqa: E402

URL = "https://rest.isric.org/soilgrids/v2.0/properties/query"
PROPS = ["wv0033", "wv1500", "sand", "clay", "soc"]
DEPTHS = ["0-5cm", "5-15cm", "15-30cm", "30-60cm", "60-100cm", "100-200cm"]
log = logging.getLogger("soilgrids")


def parse(payload: dict) -> dict:
    out: dict = {}
    for layer in payload["properties"]["layers"]:
        factor = float(layer["unit_measure"]["d_factor"])
        vals = {}
        for d in layer["depths"]:
            v = d["values"].get("mean")
            vals[d["label"]] = None if v is None else v / factor
        out[layer["name"]] = {"unit": layer["unit_measure"]["target_units"], "by_depth": vals}
    return out


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    client = HttpClient(timeout=60.0, retries=4, backoff_s=15.0, min_interval_s={"rest.isric.org": 13.0})
    points = []
    for set_name, d in (("train", DATA_DIR), ("holdout", DATA_DIR / "holdout")):
        for p in json.loads((d / "MANIFEST.json").read_text(encoding="utf-8"))["points"]:
            points.append((set_name, p))
    out = {"source": URL, "license": "ISRIC SoilGrids v2.0, CC BY 4.0", "properties": PROPS, "depths": DEPTHS,
           "fetched_utc": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(), "points": {}}
    for i, (set_name, p) in enumerate(points):
        params = {"lon": p["lon"], "lat": p["lat"], "property": PROPS, "depth": DEPTHS, "value": "mean"}
        try:
            res = client.get(URL, params)
            out["points"][p["id"]] = {"set": set_name, "lat": p["lat"], "lon": p["lon"], "retrieved_at": res.retrieved_at, **parse(res.payload)}
        except (DataUnavailableError, KeyError) as err:
            log.warning("%s: %s", p["id"], err)
        if (i + 1) % 10 == 0:
            log.info("%d/%d points", i + 1, len(points))
            (DATA_DIR / "soils" / "soilgrids_profiles.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    (DATA_DIR / "soils" / "soilgrids_profiles.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    log.info("saved %d profiles", len(out["points"]))
    client.close()


if __name__ == "__main__":
    main()
