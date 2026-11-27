"""
GSLC ペアからのコヒーレンス推定とモザイク。

    γ = |Σ s1·s2*| / sqrt(Σ|s1|² · Σ|s2|²)      （矩形窓、地図グリッド上）

欠損（NaN / 0）の画素は窓内の和から除外し、窓内の有効画素率が
min_valid_fraction 未満の画素は NaN にする。
"""

from __future__ import annotations

import os

import numpy as np
from scipy import ndimage

_FLOAT_OPTS = ["COMPRESS=DEFLATE", "PREDICTOR=3", "TILED=YES", "BIGTIFF=IF_SAFER"]


def _gdal():
    from osgeo import gdal

    gdal.UseExceptions()
    return gdal


def coherence_array(s1: np.ndarray, s2: np.ndarray, win_y: int, win_x: int,
                    min_valid_fraction: float = 0.5) -> np.ndarray:
    valid = np.isfinite(s1) & np.isfinite(s2) & (s1 != 0) & (s2 != 0)
    a = np.where(valid, s1, 0).astype(np.complex128)
    b = np.where(valid, s2, 0).astype(np.complex128)
    size = (win_y, win_x)
    ifg = a * np.conj(b)
    num_r = ndimage.uniform_filter(ifg.real, size, mode="constant")
    num_i = ndimage.uniform_filter(ifg.imag, size, mode="constant")
    p1 = ndimage.uniform_filter(np.abs(a) ** 2, size, mode="constant")
    p2 = ndimage.uniform_filter(np.abs(b) ** 2, size, mode="constant")
    frac = ndimage.uniform_filter(valid.astype(np.float64), size, mode="constant")
    with np.errstate(invalid="ignore", divide="ignore"):
        gamma = np.hypot(num_r, num_i) / np.sqrt(p1 * p2)
    gamma[~valid | (frac < min_valid_fraction - 1e-9) | ~np.isfinite(gamma)] = np.nan
    return np.clip(gamma, 0.0, 1.0).astype(np.float32)


def coherence_file(gslc1: str, gslc2: str, out_path: str, win_y: int, win_x: int,
                   min_valid_fraction: float, block: int = 1024) -> str:
    """2 つの GSLC（同一グリッド）からコヒーレンスを計算する。行方向にブロック処理する。"""
    gdal = _gdal()
    d1, d2 = gdal.Open(gslc1), gdal.Open(gslc2)
    if (d1.RasterXSize, d1.RasterYSize) != (d2.RasterXSize, d2.RasterYSize):
        raise RuntimeError(f"GSLC のサイズが一致しません: {gslc1} / {gslc2}")
    if not np.allclose(d1.GetGeoTransform(), d2.GetGeoTransform()):
        raise RuntimeError(f"GSLC のグリッドが一致しません: {gslc1} / {gslc2}")
    w, h = d1.RasterXSize, d1.RasterYSize
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    out = gdal.GetDriverByName("GTiff").Create(out_path, w, h, 1, gdal.GDT_Float32, options=_FLOAT_OPTS)
    out.SetGeoTransform(d1.GetGeoTransform())
    out.SetProjection(d1.GetProjection())
    ob = out.GetRasterBand(1)
    ob.SetNoDataValue(float("nan"))
    halo = win_y // 2
    b1, b2 = d1.GetRasterBand(1), d2.GetRasterBand(1)
    for r0 in range(0, h, block):
        r1 = min(r0 + block, h)
        a0, a1 = max(r0 - halo, 0), min(r1 + halo, h)
        s1 = b1.ReadAsArray(0, a0, w, a1 - a0)
        s2 = b2.ReadAsArray(0, a0, w, a1 - a0)
        g = coherence_array(s1, s2, win_y, win_x, min_valid_fraction)
        ob.WriteArray(g[r0 - a0: r0 - a0 + (r1 - r0)], 0, r0)
    ob.FlushCache()
    out = None
    return out_path


def mosaic(parts: list[tuple[str, int, int]], width: int, length: int, geotransform, projection: str,
           out_path: str, edge_erosion: int = 0) -> str:
    """
    部分グリッドのコヒーレンスを ROI グリッドにモザイクする（重なりは平均）。
    parts: [(path, col_off, row_off), ...]
    """
    gdal = _gdal()
    acc = np.zeros((length, width), dtype=np.float64)
    cnt = np.zeros((length, width), dtype=np.uint16)
    for path, col, row in parts:
        ds = gdal.Open(path)
        g = ds.GetRasterBand(1).ReadAsArray().astype(np.float64)
        ds = None
        valid = np.isfinite(g)
        if edge_erosion > 0:
            valid = ndimage.binary_erosion(valid, iterations=edge_erosion, border_value=0)
        h, w = g.shape
        sl = (slice(row, row + h), slice(col, col + w))
        acc[sl][valid] += g[valid]
        cnt[sl][valid] += 1
    with np.errstate(invalid="ignore", divide="ignore"):
        res = np.where(cnt > 0, acc / np.maximum(cnt, 1), np.nan).astype(np.float32)
    write_float(out_path, res, geotransform, projection)
    return out_path


def write_float(path: str, arr: np.ndarray, geotransform, projection: str, description: str = "") -> str:
    gdal = _gdal()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    h, w = arr.shape
    out = gdal.GetDriverByName("GTiff").Create(path, w, h, 1, gdal.GDT_Float32, options=_FLOAT_OPTS)
    out.SetGeoTransform(tuple(geotransform))
    out.SetProjection(projection)
    b = out.GetRasterBand(1)
    b.SetNoDataValue(float("nan"))
    if description:
        b.SetDescription(description)
    b.WriteArray(arr.astype(np.float32))
    b.FlushCache()
    out = None
    return path


def read_float(path: str) -> tuple[np.ndarray, tuple, str]:
    gdal = _gdal()
    ds = gdal.Open(path)
    arr = ds.GetRasterBand(1).ReadAsArray().astype(np.float32)
    return arr, ds.GetGeoTransform(), ds.GetProjection()
