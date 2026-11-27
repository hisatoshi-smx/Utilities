"""ROI のベクター（KML/KMZ/SHP/GPKG）読み込みと KML 書き出し。"""

from __future__ import annotations

import os
import xml.sax.saxutils

_VECTOR_EXT = {".kml", ".kmz", ".shp", ".gpkg", ".geojson", ".json"}


def _ogr():
    from osgeo import gdal, ogr

    gdal.UseExceptions()
    ogr.UseExceptions()
    return ogr


def _ogr_open_path(path: str) -> str:
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isfile(path):
        raise FileNotFoundError(f"ファイルがありません: {path}")
    low = path.lower()
    if low.endswith(".kmz"):
        return f"/vsizip/{path}"
    if low.endswith(".shp"):
        return path
    if low.endswith(".gpkg") or low.endswith(".kml"):
        return path
    if low.endswith((".geojson", ".json")):
        return path
    raise ValueError(f"未対応の形式です（{', '.join(sorted(_VECTOR_EXT))}）")


def vector_path_allowed(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in _VECTOR_EXT


def bounds_from_vector(path: str) -> dict[str, float]:
    """ベクターの全レイヤ・全地物の外接矩形を WGS84（西・南・東・北）で返す。"""
    if not vector_path_allowed(path):
        raise ValueError(f"未対応の拡張子です: {path}")
    ogr = _ogr()
    from osgeo import osr

    src = _ogr_open_path(path)
    ds = ogr.Open(src, 0)
    if ds is None:
        raise RuntimeError(f"ベクターを開けません: {path}")
    tgt = osr.SpatialReference()
    tgt.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    tgt.ImportFromEPSG(4326)
    west, south, east, north = 1.0e300, 1.0e300, -1.0e300, -1.0e300
    found = False
    try:
        for i in range(ds.GetLayerCount()):
            lyr = ds.GetLayerByIndex(i)
            if lyr is None:
                continue
            srs = lyr.GetSpatialRef()
            for feat in lyr:
                geom = feat.GetGeometryRef()
                if geom is None:
                    continue
                g = geom.Clone()
                if srs and not srs.IsSame(tgt):
                    ct = osr.CoordinateTransformation(srs, tgt)
                    g.Transform(ct)
                gminx, gmaxx, gminy, gmaxy = g.GetEnvelope()
                west = min(west, gminx)
                east = max(east, gmaxx)
                south = min(south, gminy)
                north = max(north, gmaxy)
                found = True
    finally:
        ds = None
    if not found:
        raise RuntimeError("ポリゴン・線・点などの地物がありません")
    if west >= east or south >= north:
        raise RuntimeError("外接矩形が不正です")
    return {
        "west": float(west),
        "south": float(south),
        "east": float(east),
        "north": float(north),
    }


def roi_to_kml(roi: dict[str, float], name: str = "Plant-ISCE3 Explorer ROI") -> str:
    w, s, e, n = roi["west"], roi["south"], roi["east"], roi["north"]
    if not (-180 <= w < e <= 180 and -90 <= s < n <= 90):
        raise ValueError("ROI の座標が不正です")
    title = xml.sax.saxutils.escape(name)
    coords = f"{w},{s},0 {w},{n},0 {e},{n},0 {e},{s},0 {w},{s},0"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>{title}</name>
    <Placemark>
      <name>{title}</name>
      <Polygon>
        <outerBoundaryIs>
          <LinearRing>
            <coordinates>{coords}</coordinates>
          </LinearRing>
        </outerBoundaryIs>
      </Polygon>
    </Placemark>
  </Document>
</kml>
"""