from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

CONFIG_VERSION = 1


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class RoiConfig(_Base):
    west: Optional[float] = None
    south: Optional[float] = None
    east: Optional[float] = None
    north: Optional[float] = None

    def is_set(self) -> bool:
        return self.west is not None

    @model_validator(mode="after")
    def _order(self) -> "RoiConfig":
        vals = [self.west, self.south, self.east, self.north]
        if all(v is None for v in vals):
            return self
        if any(v is None for v in vals):
            raise ValueError("ROI は西・南・東・北をすべて指定してください")
        if not (-180 <= self.west < self.east <= 180):
            raise ValueError("経度が不正です")
        if not (-90 <= self.south < self.north <= 90):
            raise ValueError("緯度が不正です")
        return self


class GridConfig(_Base):
    epsg: Optional[int] = None
    spacing: Optional[float] = Field(
        default=None,
        description="空欄相当は None＝NISAR の地上ピクセル間隔から自動",
    )

    @model_validator(mode="after")
    def _spacing_range(self) -> "GridConfig":
        if self.spacing is not None and not (0.25 < self.spacing <= 500):
            raise ValueError("グリッド間隔は 0.25〜500 m、または空（自動）にしてください")
        return self


class OutputConfig(_Base):
    geocode: str = ""
    rtc: str = ""


class GeocodeConfig(_Base):
    """NISAR ジオコードの出力方式。"""

    output: Literal["complex_gslc", "rtc_gamma"] = "rtc_gamma"


class DemConfig(_Base):
    source: Literal["gsi", "copernicus", "file"] = "gsi"
    path: str = ""
    gsi_mode: Literal["auto", "dem5a", "dem5b", "dem10b"] = "auto"
    vertical_datum: Literal["ellipsoid", "egm2008", "egm96", "geoid"] = "ellipsoid"
    margin_deg: float = Field(0.05, ge=0, le=1)


class ExplorerConfig(_Base):
    version: int = CONFIG_VERSION
    name: str
    description: str = ""
    created: str = ""
    input_h5: str = ""
    frequency: Literal["A", "B"] = "A"
    polarization: str = "HH"
    roi: RoiConfig = Field(default_factory=RoiConfig)
    grid: GridConfig = Field(default_factory=GridConfig)
    geocode: GeocodeConfig = Field(default_factory=GeocodeConfig)
    outputs: OutputConfig = Field(default_factory=OutputConfig)
    dem: DemConfig = Field(default_factory=DemConfig)
    tools: dict[str, dict[str, Any]] = Field(default_factory=lambda: {"geocode": {}, "rtc": {}})


def load_config(path: str) -> ExplorerConfig:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return ExplorerConfig.model_validate(data)


def save_config(cfg: ExplorerConfig, path: str) -> None:
    if not cfg.created:
        cfg = cfg.model_copy(update={"created": datetime.now().isoformat(timespec="seconds")})
    d = cfg.model_dump(mode="json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(d, f, allow_unicode=True, sort_keys=False, default_flow_style=False)


def check_runnable(cfg: ExplorerConfig) -> tuple[list[str], list[str]]:
    err, warn = [], []
    if not (cfg.input_h5 or "").strip():
        err.append("NISAR RSLC（HDF5）のパスを指定してください")
    elif not os.path.isfile(cfg.input_h5):
        err.append(f"入力ファイルがありません: {cfg.input_h5}")
    if not cfg.roi.is_set():
        err.append("ROI を地図または数値で指定してください")
    if cfg.dem.source == "file" and not (cfg.dem.path or "").strip():
        err.append("DEM ソースがファイルのとき、DEM パスを指定してください")
    elif cfg.dem.source == "file" and not os.path.isfile(cfg.dem.path):
        err.append(f"DEM ファイルがありません: {cfg.dem.path}")
    return err, warn