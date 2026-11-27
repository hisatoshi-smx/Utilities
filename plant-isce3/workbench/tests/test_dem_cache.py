from worklib.dem import dem_prepare_signature


def test_dem_signature_changes_with_bounds():
    base = dict(source="gsi", path="", gsi_mode="auto", vertical_datum="ellipsoid")
    a = dem_prepare_signature(**base, bounds=[139.0, 35.0, 140.0, 36.0])
    b = dem_prepare_signature(**base, bounds=[139.0, 35.0, 140.01, 36.0])
    assert a != b


def test_dem_signature_ignores_file_path_when_not_file_source():
    a = dem_prepare_signature("gsi", "/tmp/x.tif", "auto", "ellipsoid", [0, 0, 1, 1])
    b = dem_prepare_signature("gsi", "/other.tif", "auto", "ellipsoid", [0, 0, 1, 1])
    assert a == b


def test_dem_signature_file_source_uses_abspath():
    a = dem_prepare_signature("file", "dem.tif", "auto", "egm2008", [0, 0, 1, 1])
    assert a["source"] == "file"
    assert a["vertical_datum"] == "egm2008"