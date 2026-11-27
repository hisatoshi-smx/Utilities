"""
プロジェクト設定（project.yaml）のスキーマと読み書き。

設定は途中まで入力した状態でも保存できるようにし、実行可能かどうかの
チェックは check_runnable() で別に行う。
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CONFIG_VERSION = 1

SCENE_TAGS = ("t1", "t2", "t3")
PAIRS = {"pre": ("t1", "t2"), "co": ("t2", "t3")}


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class InputsConfig(_Base):
    sensor: Literal["sentinel1", "nisar_rslc"] = "sentinel1"
    # 3 シーンのパス。実行時に観測日時で並べ替えて t1 < t2 < t3 とする
    scenes: list[str] = Field(default_factory=lambda: ["", "", ""])
    polarization: Literal["VV", "VH", "HH", "HV"] = "VV"
    nisar_frequency: Literal["A", "B"] = "A"
    # Sentinel-1 のバースト ID（例: t046_097412_iw2）。空なら ROI にかかるものを自動選択
    burst_ids: list[str] = Field(default_factory=list)

    @field_validator("scenes")
    @classmethod
    def _three_scenes(cls, v: list[str]) -> list[str]:
        v = [str(s).strip() for s in v]
        if len(v) != 3:
            raise ValueError("scenes は 3 件指定してください")
        return v

    @field_validator("burst_ids")
    @classmethod
    def _norm_bursts(cls, v: list[str]) -> list[str]:
        return [s.strip().lower() for s in v if s.strip()]


class RoiConfig(_Base):
    west: Optional[float] = None
    south: Optional[float] = None
    east: Optional[float] = None
    north: Optional[float] = None

    @model_validator(mode="after")
    def _order(self) -> "RoiConfig":
        vals = [self.west, self.south, self.east, self.north]
        if all(v is None for v in vals):
            return self
        if any(v is None for v in vals):
            raise ValueError("ROI は西・南・東・北の 4 つすべてを指定してください")
        if not (-180 <= self.west < self.east <= 180):
            raise ValueError("ROI の経度が不正です（西 < 東、-180〜180）")
        if not (-90 <= self.south < self.north <= 90):
            raise ValueError("ROI の緯度が不正です（南 < 北、-90〜90）")
        return self

    def is_set(self) -> bool:
        return self.west is not None


class GridConfig(_Base):
    # None なら ROI 中心から UTM を自動選択
    epsg: Optional[int] = None
    spacing: float = Field(10.0, gt=0, le=1000)


class DemConfig(_Base):
    source: Literal["copernicus", "file"] = "copernicus"
    path: str = ""
    # 入力 DEM の高さの基準。isce3 は楕円体高を前提にするため、必要なら変換する
    vertical_datum: Literal["egm2008", "egm96", "ellipsoid"] = "egm2008"
    margin_deg: float = Field(0.05, ge=0, le=2)


class OrbitConfig(_Base):
    # auto: POEORB（なければ RESORB）を自動取得 / files: 手動指定 / annotation: SAFE 内の軌道
    mode: Literal["auto", "files", "annotation"] = "auto"
    files: list[str] = Field(default_factory=lambda: ["", "", ""])
    provider: Literal["auto", "cdse", "asf"] = "auto"
    netrc_file: str = ""

    @field_validator("files")
    @classmethod
    def _three_files(cls, v: list[str]) -> list[str]:
        v = [str(s).strip() for s in v]
        if len(v) != 3:
            raise ValueError("軌道ファイルは 3 件（各シーン分）で指定してください")
        return v


class CcdConfig(_Base):
    window_x: int = Field(7, ge=1, le=101)
    window_y: int = Field(7, ge=1, le=101)
    min_valid_fraction: float = Field(0.5, gt=0, le=1)
    # バースト／フレーム端の補間誤差を避けるため、有効域を内側に削る画素数
    edge_erosion: int = Field(2, ge=0, le=50)
    dgamma_range: float = Field(0.5, gt=0, le=1)

    @field_validator("window_x", "window_y")
    @classmethod
    def _odd(cls, v: int) -> int:
        if v % 2 == 0:
            raise ValueError("窓サイズは奇数にしてください")
        return v


class ProcessingConfig(_Base):
    data_interp_method: Literal["sinc", "bicubic", "biquintic", "bilinear", "nearest"] = "sinc"
    keep_intermediate: bool = True
    omp_threads: Optional[int] = Field(None, ge=1, le=256)


class ProjectConfig(_Base):
    version: int = CONFIG_VERSION
    name: str
    description: str = ""
    created: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    inputs: InputsConfig = Field(default_factory=InputsConfig)
    roi: RoiConfig = Field(default_factory=RoiConfig)
    grid: GridConfig = Field(default_factory=GridConfig)
    dem: DemConfig = Field(default_factory=DemConfig)
    orbit: OrbitConfig = Field(default_factory=OrbitConfig)
    ccd: CcdConfig = Field(default_factory=CcdConfig)
    processing: ProcessingConfig = Field(default_factory=ProcessingConfig)


def load_config(path: str) -> ProjectConfig:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return ProjectConfig.model_validate(data)


def save_config(cfg: ProjectConfig, path: str) -> None:
    data = cfg.model_dump(mode="json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("# plant-isce3 三時期 CCD プロジェクト設定\n")
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
    os.replace(tmp, path)


def check_runnable(cfg: ProjectConfig) -> tuple[list[str], list[str]]:
    """実行前チェック。(エラー, 警告) を返す。"""
    errors: list[str] = []
    warnings: list[str] = []

    for i, p in enumerate(cfg.inputs.scenes, 1):
        if not p:
            errors.append(f"シーン {i} のパスが未指定です")
        elif not os.path.exists(p):
            errors.append(f"シーン {i} が見つかりません: {p}")
        elif cfg.inputs.sensor == "sentinel1" and not p.lower().endswith(".zip"):
            errors.append(f"Sentinel-1 は zip ファイルを指定してください（plant-isce3 の制約）: {p}")
        elif cfg.inputs.sensor == "nisar_rslc" and not p.lower().endswith(".h5"):
            errors.append(f"NISAR RSLC は .h5 ファイルを指定してください: {p}")
    if len(set(cfg.inputs.scenes)) != 3 and all(cfg.inputs.scenes):
        errors.append("3 シーンに同じファイルが含まれています")

    if not cfg.roi.is_set():
        warnings.append("ROI が未指定のため、3 シーンの共通範囲全体を処理します（時間がかかる場合があります）")

    if cfg.dem.source == "file":
        if not cfg.dem.path:
            errors.append("DEM ファイルが未指定です")
        elif not os.path.exists(cfg.dem.path):
            errors.append(f"DEM ファイルが見つかりません: {cfg.dem.path}")

    if cfg.inputs.sensor == "sentinel1":
        if cfg.orbit.mode == "files":
            for i, p in enumerate(cfg.orbit.files, 1):
                if not p:
                    errors.append(f"軌道ファイル {i} が未指定です")
                elif not os.path.exists(p):
                    errors.append(f"軌道ファイル {i} が見つかりません: {p}")
        elif cfg.orbit.mode == "annotation":
            warnings.append("SAFE 内の軌道を使います。精密軌道より位置精度が落ちます")
        if cfg.orbit.netrc_file and not os.path.exists(cfg.orbit.netrc_file):
            errors.append(f"netrc ファイルが見つかりません: {cfg.orbit.netrc_file}")

    if cfg.inputs.sensor == "nisar_rslc" and cfg.inputs.polarization in ("VV", "VH"):
        warnings.append("NISAR の偏波が VV/VH になっています。データに含まれるか確認してください")

    return errors, warnings


# 各処理ステップの結果に影響する設定項目。前のステップの署名も含めて連鎖させ、
# 設定を変えたときに影響を受けるステップだけ再実行されるようにする。
STEP_ORDER = ("inspect", "orbit", "dem", "grid", "geocode", "coherence", "products")

STEP_LABELS = {
    "inspect": "シーン情報の読み込み",
    "orbit": "軌道ファイルの準備",
    "dem": "DEM の準備",
    "grid": "出力グリッドの決定",
    "geocode": "ジオコード（GSLC 作成）",
    "coherence": "コヒーレンス計算",
    "products": "CCD 成果物の作成",
}


def _step_inputs(cfg: ProjectConfig, step: str) -> dict:
    i = cfg.inputs
    if step == "inspect":
        return {"sensor": i.sensor, "scenes": i.scenes, "pol": i.polarization, "freq": i.nisar_frequency}
    if step == "orbit":
        o = cfg.orbit
        return {"mode": o.mode, "files": o.files, "provider": o.provider}
    if step == "dem":
        return {"dem": cfg.dem.model_dump(), "roi": cfg.roi.model_dump()}
    if step == "grid":
        return {"roi": cfg.roi.model_dump(), "grid": cfg.grid.model_dump(), "bursts": i.burst_ids}
    if step == "geocode":
        return {"interp": cfg.processing.data_interp_method}
    if step == "coherence":
        c = cfg.ccd
        return {"wx": c.window_x, "wy": c.window_y, "mvf": c.min_valid_fraction, "ero": c.edge_erosion}
    if step == "products":
        return {"drange": cfg.ccd.dgamma_range}
    raise KeyError(step)


_STEP_DEPENDS = {
    "inspect": (),
    "orbit": ("inspect",),
    "dem": ("inspect",),
    "grid": ("inspect",),
    "geocode": ("orbit", "dem", "grid"),
    "coherence": ("geocode",),
    "products": ("coherence",),
}


def step_signatures(cfg: ProjectConfig) -> dict[str, str]:
    sigs: dict[str, str] = {}
    for step in STEP_ORDER:
        payload = {"self": _step_inputs(cfg, step), "deps": [sigs[d] for d in _STEP_DEPENDS[step]]}
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
        sigs[step] = hashlib.sha1(raw).hexdigest()[:16]
    return sigs
