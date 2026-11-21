"""Optional server warm-up: extract BDOT10k hydrography for all counties into data/cache/bdot10k/.

End users never run this. Without it, each county is fetched on its first analysis (~3-5 s).
Usage:
    uv run python scripts/prefetch_bdot10k.py            # all counties (~380)
    uv run python scripts/prefetch_bdot10k.py --woj 14   # one voivodeship
"""

from __future__ import annotations

import argparse
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx

from backend.clients.bdot10k import download_and_store
from backend.config import get_settings

_ENVELOPE_RE = re.compile(
    r"<ms:BDOT10k_powiaty>.*?<gml:lowerCorner>([\d.]+) ([\d.]+)</gml:lowerCorner>\s*"
    r"<gml:upperCorner>([\d.]+) ([\d.]+)</gml:upperCorner>.*?<ms:TERYT>(\d{4})</ms:TERYT>",
    re.DOTALL,
)


def list_counties() -> dict[str, tuple[float, float]]:
    """Return county TERYT -> reference point (easting, northing) in EPSG:2180."""
    s = get_settings()
    params = {"SERVICE": "WFS", "VERSION": "1.1.0", "REQUEST": "GetFeature", "TYPENAME": "ms:BDOT10k_powiaty"}
    text = httpx.get(s.bdot10k_counties_wfs_url, params=params, timeout=120).text
    counties: dict[str, tuple[float, float]] = {}
    for n1, e1, n2, e2, teryt in _ENVELOPE_RE.findall(text):
        # WFS envelope is (northing, easting); the reference point is the envelope centre.
        counties[teryt] = ((float(e1) + float(e2)) / 2, (float(n1) + float(n2)) / 2)
    return counties


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--woj", help="Dwucyfrowy kod województwa, np. 14")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--force", action="store_true", help="Pobierz ponownie istniejące pliki")
    args = parser.parse_args()

    s = get_settings()
    counties = list_counties()
    if args.woj:
        counties = {k: v for k, v in counties.items() if k.startswith(args.woj)}
    todo = {
        k: v for k, v in counties.items()
        if args.force or not (s.bdot10k_cache_dir / f"{k}.json").exists()
    }
    print(f"Powiaty: {len(counties)}, do pobrania: {len(todo)}")

    def job(teryt: str, ref: tuple[float, float]) -> str:
        url = s.bdot10k_zip_url_template.format(woj=teryt[:2], powiat=teryt)
        county = download_and_store(
            url, teryt, ref[0], ref[1], 60.0, s.http_user_agent, s.bdot10k_cache_dir / f"{teryt}.json"
        )
        n = sum(len(v) for v in county["classes"].values())
        return f"{teryt}: {n} obiektów"

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(job, k, v): k for k, v in todo.items()}
        for fut in as_completed(futures):
            try:
                print(fut.result())
            except Exception as exc:  # noqa: BLE001
                print(f"{futures[fut]}: BŁĄD {exc}")


if __name__ == "__main__":
    main()