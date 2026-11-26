#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
国土地理院正標高 → 楕円体高への変換（InSAR_Dolphin gsi_dem と同系のジオイドモデル）。

h = H + N 。GSI タイルの H はジオイド2011 正常高。
"""

from __future__ import annotations

import math
import os
import urllib.request
from typing import Optional

import numpy as np
import pyproj
import rasterio
from pyproj import CRS, Transformer
from rasterio.transform import Affine
from rasterio.warp import Resampling, reproject, transform as warp_transform

_NODATA = -9999.0

pyproj.network.set_network_enabled(True)

GEOID_MODELS = ("GSIGEO2011", "JPGEO2024", "EGM2008", "EGM96")
ELLIPSOID_MODELS = ("wgs84", "grs80")

DEFAULT_GEOID_MODEL = "GSIGEO2011"
DEFAULT_ELLIPSOID_MODEL = "wgs84"
DEFAULT_GEOID_GRID_STEP_DEG = 0.01

GEOID_MODEL_LABELS = {
    "GSIGEO2011": "GSIGEO2011 — 現行 GSI 標高タイル向け（推奨・高精度格子）",
    "JPGEO2024": "JPGEO2024 — 2024 標高体系・重力ジオイド",
    "EGM2008": "EGM2008 — 全球（COP DEM 等と同一系）",
    "EGM96": "EGM96 — 全球（レガシー）",
}

ELLIPSOID_MODEL_LABELS = {
    "wgs84": "WGS 84 (GRS80 同等) — SNAP / isce3 向け（推奨）",
    "grs80": "GRS 1980 (JGD2024) — 国土地理院ジオイドと同一楕円体",
}

# 点計算用 (orthometric z → ellipsoidal z)。JPGEO2024 は格子 GTiff のみ。
_GEOID_POINT_CRS: dict[str, tuple[str, str]] = {
    "EGM2008": ("EPSG:4326+3855", "EPSG:4979"),
    "EGM96": ("EPSG:4326+5773", "EPSG:4979"),
    "GSIGEO2011": ("EPSG:6668+6695", "EPSG:6667"),
}

_GSI_GEOID_GRIDS: dict[str, tuple[str, str]] = {
    "GSIGEO2011": ("jp_gsi_gsigeo2011.tif", "https://cdn.proj.org/jp_gsi_gsigeo2011.tif"),
    "JPGEO2024": ("jp_gsi_jpgeo2024.tif", "https://cdn.proj.org/jp_gsi_jpgeo2024.tif"),
}

# GSI 正標高 (JGD2011) → 楕円体高。楕円体モデルはタグ用（GRS80/WGS84 は実質同一パラメータ）。
_ORTHO_COMPOUND_WGS84 = "EPSG:6697"
_ELLIPSOID_DST = {
    ("GSIGEO2011", "wgs84"): ("EPSG:6697", "EPSG:4979"),
    ("GSIGEO2011", "grs80"): ("EPSG:6668+6695", "EPSG:6667"),
    ("JPGEO2024", "wgs84"): ("EPSG:6697", "EPSG:4979"),
    ("JPGEO2024", "grs80"): ("EPSG:6697", "EPSG:6667"),
    ("EGM2008", "wgs84"): ("EPSG:4326+3855", "EPSG:4979"),
    ("EGM2008", "grs80"): ("EPSG:6697", "EPSG:6667"),
    ("EGM96", "wgs84"): ("EPSG:4326+5773", "EPSG:4979"),
    ("EGM96", "grs80"): ("EPSG:6697", "EPSG:6667"),
}


def parse_geoid_model(value: str) -> str:
    g = (value or DEFAULT_GEOID_MODEL).strip().upper()
    if g not in GEOID_MODELS:
        raise ValueError(f"ジオイドモデルは {', '.join(GEOID_MODELS)} のいずれかを指定してください (指定値: {value!r})")
    return g


def parse_ellipsoid_model(value: str) -> str:
    e = (value or DEFAULT_ELLIPSOID_MODEL).strip().lower()
    if e not in ELLIPSOID_MODELS:
        raise ValueError(f"楕円体モデルは wgs84 または grs80 を指定してください (指定値: {value!r})")
    return e


def parse_geoid_grid_step(value) -> float:
    if value is None or value == "":
        return DEFAULT_GEOID_GRID_STEP_DEG
    step = float(value)
    if not math.isfinite(step) or step <= 0 or step > 0.5:
        raise ValueError("ジオイド格子間隔は 0 より大きく 0.5 度以下で指定してください。")
    return step


def vertical_reference_tag(geoid: str, ellipsoid: str) -> str:
    ell = "WGS 84 ellipsoidal height" if ellipsoid == "wgs84" else "GRS 1980 (JGD2024) ellipsoidal height"
    return f"{ell}; geoid model {geoid}"


def ensure_geoid_grid(cache_dir: str, geoid: str) -> str:
    name, url = _GSI_GEOID_GRIDS[geoid]
    out = os.path.join(os.path.abspath(cache_dir), "geoid", name)
    if os.path.isfile(out) and os.path.getsize(out) > 0:
        return out
    os.makedirs(os.path.dirname(out), exist_ok=True)
    tmp = out + ".part"
    urllib.request.urlretrieve(url, tmp)
    os.replace(tmp, out)
    return out


def _geoid_undulation_points(lons: np.ndarray, lats: np.ndarray, geoid: str) -> np.ndarray:
    """点群のジオイド高 N (m)。EGM / GSIGEO2011 は PROJ、JPGEO2024 は未対応。"""

    def calc(name: str) -> np.ndarray:
        if name not in _GEOID_POINT_CRS:
            raise ValueError(name)
        src, dst = _GEOID_POINT_CRS[name]
        t = Transformer.from_crs(src, dst, always_xy=True)
        _, _, z = t.transform(lons, lats, np.zeros_like(lons, dtype=np.float64))
        return np.asarray(z, dtype=np.float64)

    if geoid == "JPGEO2024":
        raise ValueError("JPGEO2024 は格子モデルのみ対応です。")
    n = calc(geoid)
    if geoid == "GSIGEO2011":
        outside = ~np.isfinite(n) | (n == 0.0)
        if outside.any():
            n[outside] = calc("EGM2008")[outside]
    if not np.isfinite(n).all():
        raise RuntimeError(f"ジオイド高 ({geoid}) を計算できません。PROJ のネットワーク取得を確認してください。")
    return n


def _geoid_grid_on_band(
    shape: tuple[int, int],
    band_transform: Affine,
    dst_crs: CRS,
    geoid: str,
    cache_dir: str,
) -> np.ndarray:
    path = ensure_geoid_grid(cache_dir, geoid)
    dest = np.full(shape, np.nan, dtype=np.float32)
    with rasterio.open(path) as src:
        reproject(
            source=rasterio.band(src, 1),
            destination=dest,
            src_transform=src.transform,
            src_crs=src.crs,
            dst_transform=band_transform,
            dst_crs=dst_crs,
            resampling=Resampling.bilinear,
        )
    return dest


def _geoid_coarse_on_band(
    shape: tuple[int, int],
    band_transform: Affine,
    dst_crs: CRS,
    geoid: str,
    step_deg: float,
) -> np.ndarray:
    rows, cols = np.indices(shape)
    xs, ys = rasterio.transform.xy(band_transform, rows.ravel(), cols.ravel(), offset="center")
    lon, lat = warp_transform(dst_crs, "EPSG:4326", xs, ys)
    lon = np.asarray(lon, dtype=np.float64)
    lat = np.asarray(lat, dtype=np.float64)
    # 粗い格子で補間（EGM 系）
    b = rasterio.transform.array_bounds(shape[0], shape[1], band_transform)
    if dst_crs != CRS.from_epsg(4326):
        w, s, e, n = warp_transform(dst_crs, "EPSG:4326", [b[0], b[2]], [b[1], b[3]])
        west, east = min(w), max(w)
        south, north = min(s), max(s)
    else:
        west, south, east, north = b[0], b[1], b[2], b[3]
    pad = step_deg
    w0, n0 = west - pad, north + pad
    nx = max(2, int(math.ceil((east + pad - w0) / step_deg)) + 1)
    ny = max(2, int(math.ceil((n0 - (south - pad)) / step_deg)) + 1)
    glon = w0 + (np.arange(nx) + 0.5) * step_deg
    glat = n0 - (np.arange(ny) + 0.5) * step_deg
    lon2, lat2 = np.meshgrid(glon, glat)
    ng = _geoid_undulation_points(lon2.ravel(), lat2.ravel(), geoid).reshape(ny, nx)
    # 各出力画素中心を最近傍の粗格子値で近似（双線形）
    fi = (lon - w0) / step_deg - 0.5
    fj = (n0 - lat) / step_deg - 0.5
    gi = np.clip(np.floor(fi).astype(np.int32), 0, nx - 2)
    gj = np.clip(np.floor(fj).astype(np.int32), 0, ny - 2)
    tlx = (fi - gi).astype(np.float64)
    tly = (fj - gj).astype(np.float64)
    n00 = ng[gj, gi]
    n10 = ng[gj, gi + 1]
    n01 = ng[gj + 1, gi]
    n11 = ng[gj + 1, gi + 1]
    n = (1 - tlx) * (1 - tly) * n00 + tlx * (1 - tly) * n10 + (1 - tlx) * tly * n01 + tlx * tly * n11
    return n.reshape(shape).astype(np.float32)


def geoid_undulation_band(
    shape: tuple[int, int],
    band_transform: Affine,
    dst_crs: CRS,
    geoid: str,
    *,
    cache_dir: str,
    step_deg: float,
) -> np.ndarray:
    if geoid in _GSI_GEOID_GRIDS:
        try:
            return _geoid_grid_on_band(shape, band_transform, dst_crs, geoid, cache_dir)
        except OSError as e:
            if geoid != "GSIGEO2011":
                raise RuntimeError(f"ジオイド格子 {geoid} の取得に失敗しました: {e}") from e
    return _geoid_coarse_on_band(shape, band_transform, dst_crs, geoid, step_deg)


def orthometric_to_ellipsoidal(
    heights: np.ndarray,
    band_transform: Affine,
    dst_crs: CRS,
    *,
    geoid: str,
    ellipsoid: str,
    cache_dir: str,
    geoid_grid_step_deg: float,
) -> np.ndarray:
    """
    正標高 H を楕円体高 h へ。基本は h = H + N。
    GSI 格子モデル利用時は PROJ GTiff をバンド解像度へリサンプル。
    """
    geoid = parse_geoid_model(geoid)
    ellipsoid = parse_ellipsoid_model(ellipsoid)
    flat = heights.ravel()
    valid = flat != _NODATA
    if not np.any(valid):
        return heights

    shape = heights.shape
    use_grid = geoid in _GSI_GEOID_GRIDS
    if use_grid:
        n_arr = geoid_undulation_band(shape, band_transform, dst_crs, geoid, cache_dir=cache_dir, step_deg=geoid_grid_step_deg)
        out = flat.copy()
        n_flat = n_arr.ravel()
        ok = valid & np.isfinite(n_flat)
        out[ok] = (flat[ok].astype(np.float64) + n_flat[ok]).astype(np.float32)
        return out.reshape(shape)

    # EGM 系: 粗格子または点計算
    n_arr = _geoid_coarse_on_band(shape, band_transform, dst_crs, geoid, geoid_grid_step_deg)
    out = flat.copy()
    n_flat = n_arr.ravel()
    ok = valid & np.isfinite(n_flat)
    out[ok] = (flat[ok].astype(np.float64) + n_flat[ok]).astype(np.float32)
    return out.reshape(shape)


def transform_orthometric_to_ellipsoid_crs(
    lon: np.ndarray,
    lat: np.ndarray,
    h_ortho: np.ndarray,
    geoid: str,
    ellipsoid: str,
) -> np.ndarray:
    """従来の PROJ コンパウンド変換（点列）。検証・フォールバック用。"""
    key = (parse_geoid_model(geoid), parse_ellipsoid_model(ellipsoid))
    src, dst = _ELLIPSOID_DST.get(key, (_ORTHO_COMPOUND_WGS84, "EPSG:4979"))
    t = Transformer.from_crs(src, dst, always_xy=True)
    _, _, h = t.transform(lon, lat, h_ortho)
    return np.asarray(h, dtype=np.float32)