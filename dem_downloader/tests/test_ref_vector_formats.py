"""参照ファイル: KML/KMZ/SHP の inspect テスト。"""
from __future__ import annotations

import os
import tempfile
import zipfile

import fiona
import geo_io
from fiona.crs import from_epsg
from shapely.geometry import mapping, shape


def test_inspect_kml():
    kml = b"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
<Placemark><Polygon><outerBoundaryIs><LinearRing>
<coordinates>139.0,36.0,0 139.1,36.0,0 139.1,36.1,0 139.0,36.1,0 139.0,36.0,0</coordinates>
</LinearRing></outerBoundaryIs></Polygon></Placemark>
</Document></kml>"""
    path = os.path.join(tempfile.mkdtemp(), "r.kml")
    with open(path, "wb") as f:
        f.write(kml)
    info = geo_io.inspect_file(path)
    assert info["kind"] == "vector"
    w, s, e, n = info["bounds_wgs84"]
    assert w == 139.0 and e == 139.1 and s == 36.0 and n == 36.1


def test_inspect_kmz():
    kml = b"""<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2">
<Placemark><Point><coordinates>140.5,35.5,0</coordinates></Point></Placemark></kml>"""
    path = os.path.join(tempfile.mkdtemp(), "r.kmz")
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("doc.kml", kml)
    info = geo_io.inspect_file(path)
    assert info["format"] == "kmz"
    w, s, e, n = info["bounds_wgs84"]
    assert abs((w + e) / 2 - 140.5) < 1e-4


def test_inspect_shapefile():
    path = os.path.join(tempfile.mkdtemp(), "poly.shp")
    poly = {"type": "Polygon", "coordinates": [[[0.0, 0.0], [0.01, 0.0], [0.01, 0.01], [0.0, 0.0]]]}
    with fiona.open(
        path,
        "w",
        driver="ESRI Shapefile",
        schema={"geometry": "Polygon", "properties": {"id": "int"}},
        crs=from_epsg(4326),
    ) as dst:
        dst.write({"geometry": mapping(shape(poly)), "properties": {"id": 1}})
    info = geo_io.inspect_file(path)
    assert info["kind"] == "vector"
    assert info["bounds_wgs84"][2] >= 0.01


if __name__ == "__main__":
    test_inspect_kml()
    test_inspect_kmz()
    test_inspect_shapefile()
    print("ok")