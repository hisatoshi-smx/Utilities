"""ROI（中心＋一辺 km）と標高種別の簡易テスト。"""
from __future__ import annotations

import numpy as np
import dem_core
import geo_io
from pyproj import CRS
from rasterio.transform import from_origin


def test_geodesic_distance_km():
    d = geo_io.geodesic_distance_km(140.0, 36.0, 140.01, 36.0)
    assert 0.8 < d < 1.2


def test_roi_from_center_km():
    w, s, e, n = geo_io.roi_from_center_km(140.08, 36.09, 2.0)
    assert w < e and s < n
    assert abs((w + e) / 2 - 140.08) < 0.02
    assert abs((s + n) / 2 - 36.09) < 0.02


def test_ellipsoid_raises_geoid_unchanged():
    h = np.array([[32.0]], dtype=np.float32)
    tf = from_origin(140.08, 36.1, 0.0001, 0.0001)
    ell = dem_core.apply_height_mode(h, tf, CRS.from_epsg(4326), "ellipsoid")
    assert ell[0, 0] > h[0, 0] + 30
    geo = dem_core.apply_height_mode(h, tf, CRS.from_epsg(4326), "geoid")
    assert geo[0, 0] == h[0, 0]


def test_apply_nodata_fill_zero():
    h = np.array([[1.0, dem_core.NODATA]], dtype=np.float32)
    out, tag = dem_core.apply_nodata_fill(h, 0.0)
    assert tag is None and out[0, 1] == 0.0


def test_apply_nodata_fill_keep_sentinel():
    h = np.array([[dem_core.NODATA]], dtype=np.float32)
    out, tag = dem_core.apply_nodata_fill(h, dem_core.NODATA)
    assert tag == dem_core.NODATA and out[0, 0] == dem_core.NODATA


if __name__ == "__main__":
    test_roi_from_center_km()
    test_ellipsoid_raises_geoid_unchanged()
    test_apply_nodata_fill_zero()
    test_apply_nodata_fill_keep_sentinel()
    print("ok")