import os

import pytest

from worklib.roi_vector import bounds_from_vector, roi_to_kml, vector_path_allowed


def test_vector_path_allowed():
    assert vector_path_allowed("/x/file.kml")
    assert not vector_path_allowed("/x/file.tif")


def test_roi_to_kml_roundtrip(tmp_path):
    roi = {"west": 139.0, "south": 35.0, "east": 139.1, "north": 35.1}
    kml = roi_to_kml(roi, name="test")
    p = tmp_path / "r.kml"
    p.write_text(kml, encoding="utf-8")
    pytest.importorskip("osgeo")
    got = bounds_from_vector(str(p))
    assert abs(got["west"] - 139.0) < 1e-6
    assert abs(got["north"] - 35.1) < 1e-6


def test_bounds_from_vector_kml(tmp_path):
    pytest.importorskip("osgeo")
    p = tmp_path / "box.kml"
    p.write_text(
        roi_to_kml({"west": 130.5, "south": 33.2, "east": 130.6, "north": 33.3}),
        encoding="utf-8",
    )
    b = bounds_from_vector(str(p))
    assert b["west"] == pytest.approx(130.5)
    assert b["east"] == pytest.approx(130.6)