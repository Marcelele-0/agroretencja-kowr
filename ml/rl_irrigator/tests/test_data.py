"""Offline tests of the data layer (parsing, cache, retries) - no network access."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import httpx
import numpy as np
import pytest

from kowr_rl.data import (
    DailySeries,
    DataUnavailableError,
    HttpClient,
    fetch_iung_code,
    parse_daily,
    parse_iung,
    parse_previous_runs,
    parse_uldk,
)

FIX = Path(__file__).parent / "fixtures"


def _load(name: str):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def test_parse_daily_nulls_become_nan() -> None:
    s = parse_daily(_load("archive_small.json"))
    assert len(s) == 5
    assert s.dates[0] == np.datetime64("2024-06-01")
    assert np.isnan(s.precip[3]) and not np.isnan(s.et0[3])
    assert s.precip[1] == pytest.approx(15.4)
    assert s.last_valid_date() == dt.date(2024, 6, 5)


def test_window_and_merge() -> None:
    a = DailySeries(np.arange("2024-06-01", "2024-06-04", dtype="datetime64[D]"), [1.0, np.nan, 3.0], [1.0, 1.0, 1.0])
    b = DailySeries(np.arange("2024-06-02", "2024-06-06", dtype="datetime64[D]"), [9.0, 9.0, 9.0, 9.0], [2.0, 2.0, 2.0, 2.0])
    m = a.merged_with(b)
    assert len(m) == 5
    # a has priority where valid; NaN in a is filled from b; dates only in b are appended
    np.testing.assert_allclose(m.precip, [1.0, 9.0, 3.0, 9.0, 9.0])
    np.testing.assert_allclose(m.et0, [1.0, 1.0, 1.0, 2.0, 2.0])
    w = m.window(dt.date(2024, 6, 2), dt.date(2024, 6, 3))
    assert len(w) == 2 and w.precip[0] == 9.0


def test_parse_previous_runs_daily_sums_and_incomplete_days() -> None:
    hours = np.arange("2024-06-01T00", "2024-06-03T00", dtype="datetime64[h]")
    payload = {"hourly": {"time": [str(h) for h in hours]}}
    for k in range(1, 8):
        vals = [0.5 * k] * len(hours)
        if k == 3:
            vals[30] = None  # one missing hour on day 2 for lead 3
        payload["hourly"][f"precipitation_previous_day{k}"] = vals
    dates, f = parse_previous_runs(payload)
    assert list(dates.astype(str)) == ["2024-06-01", "2024-06-02"]
    assert f.shape == (2, 7)
    assert f[0, 0] == pytest.approx(24 * 0.5)
    assert f[0, 6] == pytest.approx(24 * 3.5)
    assert np.isnan(f[1, 2]) and np.isfinite(f[0, 2])


def test_parse_iung_codes() -> None:
    assert parse_iung(_load("iung_arable.json")) == 2
    assert parse_iung(_load("iung_urban.json")) == 13
    assert parse_iung(_load("iung_empty.json")) is None


def test_parse_uldk() -> None:
    wkt, teryt = parse_uldk((FIX / "uldk_ok.txt").read_text(encoding="utf-8"))
    assert wkt.startswith("POLYGON((") and teryt == "141201_1.0001.6509"
    with pytest.raises(DataUnavailableError):
        parse_uldk((FIX / "uldk_missing.txt").read_text(encoding="utf-8"))


def test_offline_cache_roundtrip(tmp_path: Path) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=_load("iung_arable.json"))

    with HttpClient(tmp_path, transport=httpx.MockTransport(handler)) as live:
        code, src = fetch_iung_code(live, 52.0, 18.0)
        assert code == 2 and src.mode == "live"
        code, src = fetch_iung_code(live, 52.0, 18.0)
        assert src.mode == "cache" and calls["n"] == 1
    with HttpClient(tmp_path, offline=True) as off:
        code, src = fetch_iung_code(off, 52.0, 18.0)
        assert code == 2 and src.mode == "cache"
        with pytest.raises(DataUnavailableError):
            fetch_iung_code(off, 50.0, 20.0)


def test_retry_on_429_then_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("kowr_rl.data.time.sleep", lambda s: None)
    seq = iter([httpx.Response(429, text="Too many"), httpx.Response(503, text="busy"), httpx.Response(200, json={"ok": 1})])
    with HttpClient(tmp_path, transport=httpx.MockTransport(lambda r: next(seq))) as c:
        res = c.get("https://example.org/x", {"a": 1})
    assert res.payload == {"ok": 1} and not res.from_cache


def test_open_meteo_error_payload_raises(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": True, "reason": "bad variable"})

    with HttpClient(tmp_path, transport=httpx.MockTransport(handler)) as c:
        with pytest.raises(DataUnavailableError, match="bad variable"):
            c.get("https://example.org/y")
