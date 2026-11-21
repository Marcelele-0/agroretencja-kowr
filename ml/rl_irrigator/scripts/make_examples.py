"""Generate example API responses (live data) into ml/rl_irrigator/examples/.

Usage: uv run python ml/rl_irrigator/scripts/make_examples.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from kowr_rl import MODULE_ROOT  # noqa: E402
from kowr_rl.api import app  # noqa: E402

EXAMPLES = [
    ("recommend_kukurydza_lipiec_2025.json", "post", "/rl/recommend", {"lat": 52.60, "lon": 17.00, "crop": "kukurydza", "date": "2025-07-15", "budget_mm": 150, "budget_used_mm": 30}),
    ("recommend_ziemniak_dzialka_2024.json", "post", "/rl/recommend", {"parcel_id": "302111_5.0015.72", "crop": "ziemniak", "date": "2024-07-02", "budget_mm": 120, "include_history": True}),
    ("recommend_burak_2019_klimatologia.json", "post", "/rl/recommend", {"lat": 50.50, "lon": 17.70, "crop": "burak_cukrowy", "date": "2019-06-25", "budget_mm": 200}),
    ("recommend_rl_eksperymentalny_2025.json", "post", "/rl/recommend", {"lat": 52.60, "lon": 17.00, "crop": "kukurydza", "date": "2025-07-15", "budget_mm": 150, "budget_used_mm": 30, "policy": "rl"}),
    ("recommend_listopad_poza_sezonem.json", "post", "/rl/recommend", {"lat": 52.60, "lon": 17.00, "crop": "pszenica_ozima", "date": "2026-11-21", "budget_mm": 150}),
    ("recommend_las_nie_grunt_orny.json", "post", "/rl/recommend", {"lat": 52.735, "lon": 23.86, "crop": "kukurydza", "date": "2025-07-15", "budget_mm": 150}),
    ("compare_kukurydza_wielkopolska.json", "post", "/rl/compare", {"lat": 52.60, "lon": 17.00, "crop": "kukurydza", "budget_mm": 150}),
]


def main() -> None:
    out_dir = MODULE_ROOT / "examples"
    out_dir.mkdir(exist_ok=True)
    client = TestClient(app)
    for name, method, path, body in EXAMPLES:
        r = getattr(client, method)(path, json=body)
        payload = {"request": {"method": method.upper(), "path": path, "body": body}, "status_code": r.status_code, "response": r.json()}
        (out_dir / name).write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
        resp = r.json()
        print(f"{name}: HTTP {r.status_code} status={resp.get('status')} t={resp.get('timing_s')} s :: {str(resp.get('message', ''))[:110]}")


if __name__ == "__main__":
    main()
