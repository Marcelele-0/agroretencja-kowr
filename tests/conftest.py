"""Shared fixtures (helpers live in synthetic_data.py)."""

from __future__ import annotations

import pytest
from synthetic_data import make_inputs

from hydro_core.features import FeatureContext


@pytest.fixture
def fx() -> FeatureContext:
    return make_inputs()
