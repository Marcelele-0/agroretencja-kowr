"""Basic API protections: per-client rate limit and security headers (no network)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.security import RateLimiter


def test_rate_limiter_blocks_over_limit_and_recovers() -> None:
    limiter = RateLimiter(per_minute=2)
    assert limiter.check("a", now=0.0) is None
    assert limiter.check("a", now=1.0) is None
    assert limiter.check("a", now=2.0) == 58
    assert limiter.check("b", now=2.0) is None  # other clients have their own window
    assert limiter.check("a", now=60.5) is None  # the first request left the window


def test_rate_limiter_off_with_zero() -> None:
    limiter = RateLimiter(per_minute=0)
    assert all(limiter.check("a", now=float(i)) is None for i in range(500))


def test_api_sends_security_headers_and_429(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "limiter", RateLimiter(per_minute=1))
    client = TestClient(main.app)
    health = client.get("/api/health")
    assert health.headers["X-Content-Type-Options"] == "nosniff"
    assert health.headers["X-Frame-Options"] == "DENY"
    assert client.get("/rl/health").status_code == 200
    blocked = client.get("/rl/health")
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) >= 1
    assert blocked.headers["X-Content-Type-Options"] == "nosniff"
