"""
出力グリッド（地図座標系・範囲・画素間隔）の計算。

全時期・全バーストで画素位置を完全に一致させるため、グリッドの原点は
画素間隔の整数倍に揃え、バーストごとの部分グリッドも ROI グリッドの
画素境界に一致させる。
"""

from __future__ import annotations

import math
import os
from dataclasses import asdict, dataclass

import numpy as np
import shapely
from pyproj import CRS, Transformer
from shapely.geometry import Polygon, box


def _reproject(geom, transformer):
    return shapely.transform(geom, lambda xy: np.column_stack(transformer.transform(xy[:, 0], xy[:, 1])))


def utm_epsg(lon: float, lat: float) -> int:
    zone = int(math.floor((lon + 180.0) / 6.0)) % 60 + 1
    return (32600 if lat >= 0 else 32700) + zone


def crs_name(epsg: int) -> str:
    try:
        return CRS.from_epsg(epsg).name
    except Exception:
        return f"EPSG:{epsg}"


def _to_crs(epsg: int):
    return Transformer.from_crs(4326, epsg, always_xy=True)


def _from_crs(epsg: int):
    return Transformer.from_crs(epsg, 4326, always_xy=True)


def _is_geographic(epsg: int) -> bool:
    return CRS.from_epsg(epsg).is_geographic


@dataclass
class Grid:
    epsg: int
    x0: float  # 左上隅の X
    y0: float  # 左上隅の Y
    dx: float  # > 0
    dy: float  # < 0
    width: int
    length: int

    @property
    def x1(self) -> float:
        return self.x0 + self.dx * self.width

    @property
    def y1(self) -> float:
        return self.y0 + self.dy * self.length

    @property
    def geotransform(self) -> tuple:
        return (self.x0, self.dx, 0.0, self.y0, 0.0, self.dy)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Grid":
        return cls(**{k: d[k] for k in ("epsg", "x0", "y0", "dx", "dy", "width", "length")})

    def lonlat_bounds(self) -> list[float]:
        """[west, south, east, north]（辺を細かく分割して変換）"""
        t = _from_crs(self.epsg)
        xs = np.linspace(self.x0, self.x1, 21)
        ys = np.linspace(self.y0, self.y1, 21)
        ex = np.concatenate([xs, np.full(21, self.x1), xs[::-1], np.full(21, self.x0)])
        ey = np.concatenate([np.full(21, self.y0), ys, np.full(21, self.y1), ys[::-1]])
        lon, lat = t.transform(ex, ey)
        return [float(np.min(lon)), float(np.min(lat)), float(np.max(lon)), float(np.max(lat))]

    def window(self, sub: "Grid") -> tuple[int, int]:
        """sub の左上が self の何列目・何行目か。"""
        col = int(round((sub.x0 - self.x0) / self.dx))
        row = int(round((sub.y0 - self.y0) / self.dy))
        return col, row


def spacing_for(epsg: int, spacing_m: float, lat: float) -> tuple[float, float]:
    if _is_geographic(epsg):
        dlat = spacing_m / 111320.0
        dlon = spacing_m / (111320.0 * max(math.cos(math.radians(lat)), 0.1))
        return dlon, -dlat
    return spacing_m, -spacing_m


def grid_from_lonlat_bounds(bounds: list[float], epsg: int | None, spacing_m: float) -> Grid:
    west, south, east, north = bounds
    if epsg is None:
        epsg = utm_epsg((west + east) / 2, (south + north) / 2)
    dx, dy = spacing_for(epsg, spacing_m, (south + north) / 2)
    poly = _reproject(box(west, south, east, north).segmentize(0.01), _to_crs(epsg))
    xmin, ymin, xmax, ymax = poly.bounds
    x0 = math.floor(xmin / dx) * dx
    x1 = math.ceil(xmax / dx) * dx
    y0 = math.ceil(ymax / -dy) * -dy
    y1 = math.floor(ymin / -dy) * -dy
    width = int(round((x1 - x0) / dx))
    length = int(round((y0 - y1) / -dy))
    if width <= 0 or length <= 0:
        raise ValueError("ROI が小さすぎます")
    return Grid(epsg, x0, y0, dx, dy, width, length)


def subgrid_for_polygon(grid: Grid, ring_lonlat: list[list[float]], margin_px: int = 2) -> Grid | None:
    """経緯度ポリゴンと grid の重なり部分を、grid の画素境界に揃えた部分グリッドとして返す。"""
    t = _to_crs(grid.epsg)
    poly = Polygon(ring_lonlat).segmentize(0.01)
    poly_xy = _reproject(poly, t)
    inter = poly_xy.intersection(box(grid.x0, grid.y1, grid.x1, grid.y0))
    if inter.is_empty or inter.area <= 0:
        return None
    xmin, ymin, xmax, ymax = inter.bounds
    c0 = max(int(math.floor((xmin - grid.x0) / grid.dx)) - margin_px, 0)
    c1 = min(int(math.ceil((xmax - grid.x0) / grid.dx)) + margin_px, grid.width)
    r0 = max(int(math.floor((ymax - grid.y0) / grid.dy)) - margin_px, 0)
    r1 = min(int(math.ceil((ymin - grid.y0) / grid.dy)) + margin_px, grid.length)
    if c1 - c0 < 1 or r1 - r0 < 1:
        return None
    return Grid(grid.epsg, grid.x0 + c0 * grid.dx, grid.y0 + r0 * grid.dy, grid.dx, grid.dy, c1 - c0, r1 - r0)


def write_template(path: str, grid: Grid) -> str:
    """plant-isce3 の --bbox-file に渡すための、データを持たない VRT を作る。"""
    from osgeo import gdal, osr

    gdal.UseExceptions()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    ds = gdal.GetDriverByName("VRT").Create(path, grid.width, grid.length, 1, gdal.GDT_Byte)
    ds.SetGeoTransform(grid.geotransform)
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(grid.epsg)
    ds.SetProjection(srs.ExportToWkt())
    ds.FlushCache()
    ds = None
    return path
