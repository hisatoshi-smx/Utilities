"""ccd3/worklib と同系の出力グリッド（plant --bbox-file 用）。"""

from __future__ import annotations

import math
import os
from dataclasses import asdict, dataclass

import numpy as np
import shapely
from pyproj import CRS, Transformer
from shapely.geometry import box


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


def _is_geographic(epsg: int) -> bool:
    return CRS.from_epsg(epsg).is_geographic


def spacing_for(epsg: int, spacing_m: float, lat: float) -> tuple[float, float]:
    if _is_geographic(epsg):
        dlat = spacing_m / 111320.0
        dlon = spacing_m / (111320.0 * max(math.cos(math.radians(lat)), 0.1))
        return dlon, -dlat
    return spacing_m, -spacing_m


@dataclass
class Grid:
    epsg: int
    x0: float
    y0: float
    dx: float
    dy: float
    width: int
    length: int

    @property
    def geotransform(self) -> tuple:
        return (self.x0, self.dx, 0.0, self.y0, 0.0, self.dy)

    def to_dict(self) -> dict:
        return asdict(self)


def roi_bounds(roi, margin_deg: float) -> list[float]:
    return [roi.west - margin_deg, roi.south - margin_deg, roi.east + margin_deg, roi.north + margin_deg]


def grid_from_roi(roi, epsg: int | None, spacing_m: float) -> Grid:
    bounds = [roi.west, roi.south, roi.east, roi.north]
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


def write_template(path: str, grid: Grid) -> str:
    from osgeo import gdal, osr

    gdal.UseExceptions()
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    ds = gdal.GetDriverByName("VRT").Create(path, grid.width, grid.length, 1, gdal.GDT_Byte)
    ds.SetGeoTransform(grid.geotransform)
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(grid.epsg)
    ds.SetProjection(srs.ExportToWkt())
    ds.FlushCache()
    ds = None
    return path