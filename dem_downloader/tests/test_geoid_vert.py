"""ジオイドモデル選択の簡易テスト。"""
from __future__ import annotations

import numpy as np
import geoid_vert


def test_parse_defaults():
    assert geoid_vert.parse_geoid_model(None) == "GSIGEO2011"
    assert geoid_vert.parse_ellipsoid_model(None) == "wgs84"


def test_geoid_undulation_points_gsi(monkeypatch):
    monkeypatch.setattr(pyproj.network, "set_network_enabled", lambda *a, **k: None)
    lons = np.array([140.08])
    lats = np.array([36.09])
    n = geoid_vert._geoid_undulation_points(lons, lats, "GSIGEO2011")
    assert 35 < n[0] < 45


if __name__ == "__main__":
    import pyproj

    test_parse_defaults()
    print("ok (network test skipped in __main__)")