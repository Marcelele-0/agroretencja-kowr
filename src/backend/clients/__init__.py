"""Data source clients.

Importing the package installs a narrow warning filter (see ``silence_inmemory_georef_race``).
"""

from __future__ import annotations

import warnings

from rasterio.errors import NotGeoreferencedWarning

# Modules whose in-memory datasets always receive an explicit transform from our code.
_INMEMORY_MODULES = r"rasterio\.(features|warp)\Z"


def silence_inmemory_georef_race() -> None:
    """Ignore the spurious NotGeoreferencedWarning raised by rasterio in-memory datasets under threads.

    rasterize/geometry_mask, shapes and array reproject wrap a GDAL MEM dataset (``MemoryDataset``).
    Its constructor reads the geotransform before writing the given one and hides the resulting
    warning with ``warnings.catch_warnings()``. That context manager swaps the process-global filter
    list and is not thread-safe: when two threads overlap, one restores the list without the
    ``ignore`` entry while the other is inside, and the warning reaches the log (attributed to
    rasterio/features.py). The transform written afterwards is the correct one, so the mask or
    polygons are unaffected. The filter matches only warnings attributed to those rasterio modules;
    a real source file without georeferencing (opened in our code) still warns.
    Installed at import, before worker threads start, so every copy of the filter list keeps it.
    """
    warnings.filterwarnings("ignore", category=NotGeoreferencedWarning, module=_INMEMORY_MODULES)


silence_inmemory_georef_race()
