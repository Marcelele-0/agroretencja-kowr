"""Basic protections of the public API: per-client rate limit and security response headers.

The prototype has no accounts and stores no personal data: the parcel number (public EGiB data),
crop and date are its only inputs. What remains to protect is the shared quota of the free data
services (Open-Meteo ~10 000 requests/day): a client that floods the heavy endpoints gets HTTP 429
instead of exhausting the quota for everyone.
"""

from __future__ import annotations

import math
import time
from collections import deque

# Endpoints that fetch external data or run the full analysis
RATE_LIMITED_PREFIXES = ("/api/analyze", "/api/what-if", "/api/zones", "/api/report.pdf", "/rl/")

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}

# The HTML fallback of the report is self-contained (inline CSS, images as data: URIs): no scripts
REPORT_CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'"

WINDOW_S = 60.0
_MAX_CLIENTS = 10_000


class RateLimiter:
    """Sliding 60-second window per client address (in memory, one Uvicorn process)."""

    def __init__(self, per_minute: int) -> None:
        self.per_minute = per_minute
        self._hits: dict[str, deque[float]] = {}

    def check(self, client: str, now: float | None = None) -> int | None:
        """Register one request: None when allowed, otherwise seconds to wait (Retry-After)."""
        if self.per_minute <= 0:
            return None
        now = time.monotonic() if now is None else now
        if len(self._hits) > _MAX_CLIENTS:
            self._forget_idle(now)
        hits = self._hits.setdefault(client, deque())
        while hits and now - hits[0] >= WINDOW_S:
            hits.popleft()
        if len(hits) >= self.per_minute:
            return max(1, math.ceil(WINDOW_S - (now - hits[0])))
        hits.append(now)
        return None

    def _forget_idle(self, now: float) -> None:
        for key in [k for k, h in self._hits.items() if not h or now - h[-1] >= WINDOW_S]:
            del self._hits[key]


def is_rate_limited_path(method: str, path: str) -> bool:
    return method != "OPTIONS" and path.startswith(RATE_LIMITED_PREFIXES)
