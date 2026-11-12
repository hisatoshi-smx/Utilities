"""SNAP 互換 GeoTIFF プロファイルの単体テスト。"""

from __future__ import annotations

import os
import tempfile

import numpy as np
import rasterio
from pyproj import CRS
from rasterio.transform import from_origin

import dem_core


def test_snap_dem_gtiff_profile_predictor_is_one():
    assert dem_core.SNAP_DEM_GTIFF_PROFILE["predictor"] == 1
    assert dem_core.SNAP_DEM_GTIFF_PROFILE["dtype"] == "float32"
    assert dem_core.SNAP_DEM_GTIFF_PROFILE["compress"] == "lzw"


def test_dem_gtiff_profile_merges_grid():
    grid = dem_core.Grid(crs=CRS.from_epsg(4326), transform=from_origin(0, 1, 0.001, 0.001), width=10, height=20)
    p = dem_core.dem_gtiff_profile(grid)
    assert p["predictor"] == 1
    assert p["height"] == 20 and p["width"] == 10


def test_assert_snap_dem_geotiff_accepts_predictor_one():
    grid = dem_core.Grid(crs=CRS.from_epsg(4326), transform=from_origin(139.0, 36.0, 0.001, 0.001), width=4, height=4)
    path = os.path.join(tempfile.mkdtemp(), "snap_test.tif")
    data = np.arange(16, dtype=np.float32).reshape(4, 4)
    with rasterio.open(path, "w", **dem_core.dem_gtiff_profile(grid)) as dst:
        dst.write(data, 1)
    dem_core.assert_snap_dem_geotiff(path)
