"""YAML configuration loaders (crops, soils, RL). Values live in ``config/``, not in code."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from . import CONFIG_DIR

STAGES = ("ini", "dev", "mid", "late")
STAGE_LABELS_PL = {
    "ini": "poczatkowa",
    "dev": "rozwoju",
    "mid": "srodkowa (pelnia wzrostu, kwitnienie)",
    "late": "koncowa (dojrzewanie)",
}


def _read_yaml(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@dataclass(frozen=True)
class CropParams:
    key: str
    label_pl: str
    kc_ini: float
    kc_mid: float
    kc_end: float
    l_ini: int
    l_dev: int
    l_mid: int
    l_late: int
    sowing_month_day: tuple[int, int]
    p: float
    ky: float

    @property
    def length(self) -> int:
        return self.l_ini + self.l_dev + self.l_mid + self.l_late

    @property
    def stage_bounds(self) -> tuple[int, int, int, int]:
        """First day index of each stage (0-based)."""
        return (0, self.l_ini, self.l_ini + self.l_dev, self.l_ini + self.l_dev + self.l_mid)

    def season_start(self, year: int) -> dt.date:
        m, d = self.sowing_month_day
        return dt.date(year, m, d)

    def season_end(self, year: int) -> dt.date:
        """Last day of the season (inclusive)."""
        return self.season_start(year) + dt.timedelta(days=self.length - 1)

    def kc_curve(self) -> np.ndarray:
        """Daily Kc for day index 0..length-1 (FAO-56 eq. 66, piecewise linear)."""
        kc = np.empty(self.length)
        i = np.arange(self.length)
        b = self.stage_bounds
        kc[: b[1]] = self.kc_ini
        dev = (i >= b[1]) & (i < b[2])
        kc[dev] = self.kc_ini + (i[dev] - b[1] + 1) / self.l_dev * (self.kc_mid - self.kc_ini)
        kc[(i >= b[2]) & (i < b[3])] = self.kc_mid
        late = i >= b[3]
        kc[late] = self.kc_mid + (i[late] - b[3] + 1) / self.l_late * (self.kc_end - self.kc_mid)
        return kc

    def stage_index(self) -> np.ndarray:
        """Stage id (0=ini, 1=dev, 2=mid, 3=late) per day index."""
        i = np.arange(self.length)
        return np.searchsorted(np.array(self.stage_bounds[1:]), i, side="right")


@dataclass(frozen=True)
class PreSeason:
    start_month_day: tuple[int, int]
    kc: float
    p: float


@lru_cache(maxsize=None)
def load_crops(path: str | None = None) -> dict[str, CropParams]:
    cfg = _read_yaml(Path(path) if path else CONFIG_DIR / "crops.yaml")
    out: dict[str, CropParams] = {}
    for key, c in cfg["crops"].items():
        s = c["stages_days"]
        out[key] = CropParams(
            key=key,
            label_pl=c["label_pl"],
            kc_ini=float(c["kc_ini"]),
            kc_mid=float(c["kc_mid"]),
            kc_end=float(c["kc_end"]),
            l_ini=int(s["ini"]),
            l_dev=int(s["dev"]),
            l_mid=int(s["mid"]),
            l_late=int(s["late"]),
            sowing_month_day=tuple(c["sowing_month_day"]),
            p=float(c["p"]),
            ky=float(c["ky"]),
        )
    return out


@lru_cache(maxsize=None)
@lru_cache(maxsize=None)
def crop_aliases(path: str | None = None) -> dict[str, str]:
    """Other accepted crop codes (frontend SMSR groups, schemas.CropCode) -> crop key in crops.yaml."""
    return dict(_read_yaml(Path(path) if path else CONFIG_DIR / "crops.yaml").get("aliases") or {})


def resolve_crop(code: str) -> str | None:
    """Crop key in crops.yaml for a crop key or an alias; None if unknown."""
    if code in load_crops():
        return code
    key = crop_aliases().get(code)
    return key if key in load_crops() else None


def accepted_crop_codes() -> list[str]:
    """All crop codes accepted by the module: crop keys and their aliases."""
    return sorted(set(load_crops()) | {a for a, k in crop_aliases().items() if k in load_crops()})


@lru_cache(maxsize=None)
def load_pre_season(path: str | None = None) -> PreSeason:
    cfg = _read_yaml(Path(path) if path else CONFIG_DIR / "crops.yaml")["pre_season"]
    return PreSeason(tuple(cfg["start_month_day"]), float(cfg["kc"]), float(cfg["p"]))


@lru_cache(maxsize=None)
def crops_raw(path: str | None = None) -> dict[str, Any]:
    return _read_yaml(Path(path) if path else CONFIG_DIR / "crops.yaml")


@lru_cache(maxsize=None)
def load_soils(path: str | None = None) -> dict[str, Any]:
    cfg = _read_yaml(Path(path) if path else CONFIG_DIR / "soils.yaml")
    cfg["categories"] = {int(k): v for k, v in cfg["categories"].items()}
    cfg["non_arable"] = {int(k): v for k, v in cfg["non_arable"].items()}
    cfg["no_data"] = {int(k): v for k, v in cfg["no_data"].items()}
    return cfg


def taw_for_category(category: int) -> float:
    return float(load_soils()["categories"][int(category)]["taw_mm"])


@lru_cache(maxsize=None)
def load_rl_config(path: str | None = None) -> dict[str, Any]:
    return _read_yaml(Path(path) if path else CONFIG_DIR / "rl.yaml")
