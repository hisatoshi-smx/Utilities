#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
GUI から使う補助機能:
  - 地理情報付きファイル(GeoTIFF / Shapefile / GeoJSON / GPKG など)の範囲・輪郭の取得
  - 座標系(CRS)の検索・プリセット
  - サーバー側のファイル/フォルダブラウザ
"""

from __future__ import annotations

import math
import os
import threading
import xml.etree.ElementTree as ET
import xml.sax.saxutils as xml_escape
import zipfile

import fiona
import rasterio
import requests
from pyproj import CRS, Geod, Transformer
from pyproj.database import query_crs_info
from pyproj.enums import PJType
from shapely.geometry import mapping, shape
from shapely.ops import transform as shp_transform

RASTER_EXTS = {".tif", ".tiff", ".vrt", ".img", ".jp2", ".nc", ".asc"}
VECTOR_EXTS = {".shp", ".geojson", ".json", ".gpkg", ".kml", ".kmz", ".gml", ".fgb"}
GEO_EXTS = RASTER_EXTS | VECTOR_EXTS

MAX_DISPLAY_FEATURES = 5000


class FileProblem(ValueError):
    """ファイルは開けたが、ROI の入力としては使えない（座標系なし等）。利用者へそのまま伝える。"""

# ----------------------------------------------------------------------------
# 座標系
# ----------------------------------------------------------------------------

# 日本でよく使う座標系。平面直角座標系の系番号と対象地域は、検索用の別名としてのみ使う
# (リストには表示しない)。表示名は PROJ データベースの定義(JGD2011 / JGD2024 など)に従う。
_PLANE = [
    ("I", "長崎県・鹿児島県の一部"),
    ("II", "福岡・佐賀・熊本・大分・宮崎・鹿児島(一部)"),
    ("III", "山口・島根・広島"),
    ("IV", "香川・愛媛・徳島・高知"),
    ("V", "兵庫・鳥取・岡山"),
    ("VI", "京都・大阪・福井・滋賀・三重・奈良・和歌山"),
    ("VII", "石川・富山・岐阜・愛知"),
    ("VIII", "新潟・長野・山梨・静岡"),
    ("IX", "東京・福島・栃木・茨城・埼玉・千葉・群馬・神奈川"),
    ("X", "青森・秋田・山形・岩手・宮城"),
    ("XI", "北海道(小樽・函館・伊達・北斗・後志・渡島・檜山ほか)"),
    ("XII", "北海道(札幌・旭川・稚内・留萌・上川ほか)"),
    ("XIII", "北海道(網走・北見・釧路・帯広ほか)"),
    ("XIV", "東京都(小笠原諸島・南方諸島)"),
    ("XV", "沖縄県(本島周辺)"),
    ("XVI", "沖縄県(先島諸島)"),
    ("XVII", "沖縄県(大東諸島)"),
    ("XVIII", "東京都(沖ノ鳥島)"),
    ("XIX", "東京都(南鳥島)"),
]

GROUP_GEO = "地理座標系 / Webメルカトル"
GROUP_PLANE = "日本の平面直角座標系"
GROUP_UTM_JGD = "UTM — 日本測地系 (JGD2024)"
GROUP_UTM_WGS = "UTM — WGS 84（日本周辺の帯）"

# 日本付近で使う UTM 帯（東経 120°〜156° を 51N〜56N で覆う）
_UTM_BANDS = [
    (51, "120°〜126°（南西諸島西・九州西など）"),
    (52, "126°〜132°"),
    (53, "132°〜138°"),
    (54, "138°〜144°"),
    (55, "144°〜150°"),
    (56, "150°〜156°（北海道東・小笠原など）"),
]

# (コード, グループ, 検索用の別名)
JP_PRESETS = [
    ("EPSG:6668", GROUP_GEO, "日本 世界測地系 jgd2011 jgd2024 緯度経度 地理座標"),
    ("EPSG:4326", GROUP_GEO, "世界 wgs84 緯度経度 地理座標 gps"),
    ("EPSG:3857", GROUP_GEO, "web mercator pseudo-mercator ウェブメルカトル"),
]
for _i, (_zone, _area) in enumerate(_PLANE):
    JP_PRESETS.append(
        (f"EPSG:{6669 + _i}", GROUP_PLANE, f"日本 平面直角 平面直角座標系 第{_zone}系 jgd2011 jgd2024 {_area}")
    )
for _z, _desc in _UTM_BANDS:
    JP_PRESETS.append(
        (f"EPSG:{6688 + _z - 51}", GROUP_UTM_JGD, f"日本 jgd2024 utm {_z}n 東経 {_desc}"),
    )
    JP_PRESETS.append(
        (f"EPSG:{32600 + _z}", GROUP_UTM_WGS, f"日本 wgs84 utm {_z}n 東経 {_desc}"),
    )
_PRESET_BY_CODE = {c: (order, group, alias) for order, (c, group, alias) in enumerate(JP_PRESETS)}

_index = None
_index_lock = threading.Lock()


def _build_index():
    infos = query_crs_info(
        auth_name="EPSG",
        pj_types=[PJType.PROJECTED_CRS, PJType.GEOGRAPHIC_2D_CRS],
        allow_deprecated=False,
    )
    items = []
    for i in infos:
        code = f"{i.auth_name}:{i.code}"
        area = i.area_of_use.name if i.area_of_use else ""
        order, group, alias = _PRESET_BY_CODE.get(code, (None, None, ""))
        items.append(
            {
                "code": code,
                "num": str(i.code),
                "name": i.name,
                "preset": order is not None,
                "order": order,
                "group": group,
                # 適用地域・別名は表示しないが、検索対象には含める
                "text": f"{code} {i.name} {area} {alias}".lower(),
            }
        )
    return items


def _get_index():
    global _index
    with _index_lock:
        if _index is None:
            _index = _build_index()
        return _index


def _item(e, with_group=False):
    """リスト表示用の項目。code と name を別々に返し、画面側で列を揃えて表示する。"""
    d = {"code": e["code"], "name": e["name"], "label": f"{e['code']} — {e['name']}"}
    if with_group:
        d["group"] = e["group"]
    return d


def crs_presets():
    """検索語が空のときに表示する日本向けプリセット(グループ順)。"""
    by_code = {e["code"]: e for e in _get_index()}
    return [_item(by_code[c], with_group=True) for c, _, _ in JP_PRESETS if c in by_code]


def search_crs(query: str, limit: int = 60):
    """コード・名称・適用地域・別名の部分一致(空白区切りは AND)で座標系を検索する。"""
    q = query.strip().lower()
    if not q:
        return crs_presets()
    tokens = q.split()
    bare = q.replace("epsg:", "").strip()
    hits = []
    for e in _get_index():
        if all(t in e["text"] for t in tokens):
            if e["code"].lower() == q or e["num"] == bare:
                score = 0.0
            elif bare.isdigit() and e["num"].startswith(bare):
                score = 1.0 + len(e["num"]) / 100
            elif e["preset"]:
                score = 2.0 + e["order"] / 1000
            else:
                score = 3.0 + len(e["name"]) / 1000
            hits.append((score, e))
    hits.sort(key=lambda h: (h[0], h[1]["code"]))
    return [_item(e) for _, e in hits[:limit]]


def parse_crs(value: str) -> CRS:
    """'EPSG:6677' / '6677' / WKT など、ユーザー入力から CRS を作る。"""
    value = (value or "").strip()
    if not value:
        raise ValueError("座標系が指定されていません。")
    if value.isdigit():
        value = f"EPSG:{value}"
    try:
        return CRS.from_user_input(value)
    except Exception as e:
        raise ValueError(f"座標系を解釈できません: {value} ({e})")


def _unit_label(crs: CRS) -> str:
    if crs.is_geographic:
        return "度"
    try:
        name = crs.axis_info[0].unit_name
    except Exception:
        name = ""
    return {"metre": "m", "meter": "m", "US survey foot": "ft(US)", "foot": "ft"}.get(name, name or "m")


def crs_info(value: str) -> dict:
    crs = parse_crs(value)
    epsg = crs.to_epsg()
    return {
        "value": f"EPSG:{epsg}" if epsg else crs.to_wkt(),
        "epsg": epsg,
        "name": crs.name,
        "is_geographic": bool(crs.is_geographic),
        "unit": _unit_label(crs),
    }


# ----------------------------------------------------------------------------
# 地理情報付きファイルの読み込み
# ----------------------------------------------------------------------------


def _wgs84_transformer(crs: CRS) -> Transformer:
    return Transformer.from_crs(crs, "EPSG:4326", always_xy=True)


def _bounds_to_wgs84(crs: CRS, bounds):
    tr = _wgs84_transformer(crs)
    b = tr.transform_bounds(*bounds, densify_pts=21)
    if not all(math.isfinite(v) for v in b):
        raise FileProblem("範囲を緯度経度へ変換できませんでした。ファイルの座標系を確認してください。")
    return list(b)


def _crs_summary(crs: CRS) -> dict:
    epsg = crs.to_epsg()
    return {
        "crs_value": f"EPSG:{epsg}" if epsg else crs.to_wkt(),
        "crs_epsg": epsg,
        "crs_name": crs.name,
        "is_geographic": bool(crs.is_geographic),
        "unit": _unit_label(crs),
    }


def _inspect_raster(path: str) -> dict:
    with rasterio.open(path) as src:
        if src.crs is None:
            raise FileProblem("このラスタには座標系情報がありません。")
        crs = CRS.from_user_input(src.crs.to_wkt())
        tr = _wgs84_transformer(crs)

        # ピクセル境界(外周)をサンプリングして緯度経度の輪郭にする
        n = 24
        w, h = src.width, src.height
        cols_rows = (
            [(w * i / n, 0) for i in range(n)]
            + [(w, h * i / n) for i in range(n)]
            + [(w * (n - i) / n, h) for i in range(n)]
            + [(0, h * (n - i) / n) for i in range(n)]
        )
        xs, ys = zip(*[src.transform * (c, r) for c, r in cols_rows])
        lons, lats = tr.transform(list(xs), list(ys))
        ring = [[float(a), float(b)] for a, b in zip(lons, lats)]
        ring.append(ring[0])
        if not all(math.isfinite(v) for p in ring for v in p):
            raise FileProblem("範囲を緯度経度へ変換できませんでした。")
        bounds = [min(p[0] for p in ring), min(p[1] for p in ring), max(p[0] for p in ring), max(p[1] for p in ring)]

        return {
            "kind": "raster",
            "path": path,
            "width": w,
            "height": h,
            "bands": src.count,
            "res": [abs(src.res[0]), abs(src.res[1])],
            "bounds_wgs84": bounds,
            "geojson": {"type": "Feature", "properties": {}, "geometry": {"type": "Polygon", "coordinates": [ring]}},
            **_crs_summary(crs),
        }


def _inspect_shapefile(path: str) -> dict:
    """ESRI Shapefile（.shp + 付随ファイル）を検査する。"""
    base, _ = os.path.splitext(path)
    missing = [s for s in (".shx", ".dbf") if not os.path.isfile(base + s)]
    if missing:
        raise FileProblem(
            "Shapefile に必要な付随ファイルがありません: "
            + ", ".join(base + s for s in missing)
            + "（同じフォルダに .shp / .shx / .dbf / .prj があるか確認してください）。"
        )
    return _inspect_vector(path)


def _kml_local_tag(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _parse_kml_coords_text(text: str) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    for token in (text or "").replace("\n", " ").split():
        parts = token.split(",")
        if len(parts) < 2:
            continue
        try:
            lon, lat = float(parts[0]), float(parts[1])
        except ValueError:
            continue
        if math.isfinite(lon) and math.isfinite(lat):
            pts.append((lon, lat))
    return pts


def _read_kml_root(path: str) -> ET.Element:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".kmz":
        try:
            with zipfile.ZipFile(path) as zf:
                names = [
                    n
                    for n in zf.namelist()
                    if n.lower().endswith(".kml") and not n.split("/")[-1].startswith(".")
                ]
                if not names:
                    raise FileProblem("KMZ 内に KML ファイルがありません。")
                names.sort(key=lambda n: (0 if os.path.basename(n).lower() == "doc.kml" else 1, n))
                data = zf.read(names[0])
        except zipfile.BadZipFile as e:
            raise FileProblem(f"KMZ を ZIP として開けません: {e}") from e
    else:
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError as e:
            raise FileProblem(f"KML を読めません: {e}") from e
    try:
        return ET.fromstring(data)
    except ET.ParseError as e:
        raise FileProblem(f"KML の XML が不正です: {e}") from e


def _kml_latlon_boxes(root: ET.Element) -> list[tuple[float, float, float, float]]:
    boxes: list[tuple[float, float, float, float]] = []
    for elem in root.iter():
        if _kml_local_tag(elem.tag) != "LatLonBox":
            continue
        vals = {}
        for child in elem:
            name = _kml_local_tag(child.tag)
            if name in ("north", "south", "east", "west") and child.text:
                try:
                    vals[name] = float(child.text.strip())
                except ValueError:
                    pass
        if len(vals) == 4:
            west, east = vals["west"], vals["east"]
            south, north = vals["south"], vals["north"]
            if west < east and south < north:
                boxes.append((west, south, east, north))
    return boxes


def _inspect_kml(path: str) -> dict:
    """KML / KMZ を WGS84 の範囲・GeoJSON として返す（座標は KML 2.2 の lon,lat を想定）。"""
    root = _read_kml_root(path)
    lonlats: list[tuple[float, float]] = []
    features = []
    n_features = 0

    for elem in root.iter():
        tag = _kml_local_tag(elem.tag)
        if tag == "Placemark":
            n_features += 1
        if tag == "coordinates" and elem.text:
            pts = _parse_kml_coords_text(elem.text)
            if not pts:
                continue
            lonlats.extend(pts)
            if len(pts) >= 2:
                ring = pts if pts[0] == pts[-1] else pts
                if len(ring) >= 3:
                    if ring[0] != ring[-1]:
                        ring = ring + [ring[0]]
                    geom = {"type": "Polygon", "coordinates": [ring]}
                else:
                    geom = {"type": "LineString", "coordinates": pts}
                features.append({"type": "Feature", "properties": {}, "geometry": geom})

    for west, south, east, north in _kml_latlon_boxes(root):
        lonlats.extend([(west, south), (east, north)])
        features.append(
            {
                "type": "Feature",
                "properties": {},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[west, south], [east, south], [east, north], [west, north], [west, south]]],
                },
            }
        )

    if not lonlats:
        raise FileProblem("KML / KMZ に利用できる座標がありません。")

    west = min(p[0] for p in lonlats)
    east = max(p[0] for p in lonlats)
    south = min(p[1] for p in lonlats)
    north = max(p[1] for p in lonlats)
    if west >= east:
        pad = max(1e-5, 0.0001)
        west, east = west - pad, east + pad
    if south >= north:
        pad = max(1e-5, 0.0001)
        south, north = south - pad, north + pad
    west = max(-180.0, west)
    east = min(180.0, east)
    south = max(-90.0, south)
    north = min(90.0, north)
    if west >= east or south >= north:
        raise FileProblem("KML の座標範囲が不正です。")

    if not features:
        features = [
            {
                "type": "Feature",
                "properties": {},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [[west, south], [east, south], [east, north], [west, north], [west, south]]
                    ],
                },
            }
        ]

    if n_features == 0:
        n_features = len(features)

    crs = CRS.from_epsg(4326)
    return {
        "kind": "vector",
        "path": path,
        "n_features": n_features,
        "n_displayed": min(len(features), MAX_DISPLAY_FEATURES),
        "bounds_wgs84": [west, south, east, north],
        "geojson": {"type": "FeatureCollection", "features": features[:MAX_DISPLAY_FEATURES]},
        "format": "kml" if path.lower().endswith(".kml") else "kmz",
        **_crs_summary(crs),
    }


def _inspect_vector(path: str) -> dict:
    with fiona.open(path) as src:
        if not src.crs_wkt:
            raise FileProblem(
                "このベクタには座標系情報がありません（Shapefile の場合は .prj ファイルが同じフォルダに必要です）。"
            )
        crs = CRS.from_user_input(src.crs_wkt)
        n_features = len(src)
        if n_features == 0:
            raise FileProblem("フィーチャが1件も含まれていません。")
        raw_bounds = src.bounds
        if not all(math.isfinite(v) for v in raw_bounds):
            raise FileProblem("ファイルの範囲を取得できませんでした。")
        bounds = _bounds_to_wgs84(crs, raw_bounds)

        tr = _wgs84_transformer(crs)
        tol = max(bounds[2] - bounds[0], bounds[3] - bounds[1]) / 1500.0
        features = []
        for i, f in enumerate(src):
            if i >= MAX_DISPLAY_FEATURES:
                break
            if f.geometry is None:
                continue
            try:
                geom = shape(f.geometry)
                geom = shp_transform(lambda x, y, z=None: tr.transform(x, y), geom)
                geom = geom.simplify(tol, preserve_topology=False)
                if geom.is_empty:
                    continue
                features.append({"type": "Feature", "properties": {}, "geometry": mapping(geom)})
            except Exception:
                continue

        return {
            "kind": "vector",
            "path": path,
            "n_features": n_features,
            "n_displayed": len(features),
            "bounds_wgs84": bounds,
            "geojson": {"type": "FeatureCollection", "features": features},
            **_crs_summary(crs),
        }


_GEO_WGS84 = Geod(ellps="WGS84")


def roi_from_center_km(lon: float, lat: float, side_km: float) -> tuple[float, float, float, float]:
    """
    中心点 (lon, lat) と正方形の一辺の長さ side_km から WGS84 の ROI (west, south, east, north) を求める。
    距離は WGS84 楕円体上の測地線で半辺分ずつ東西南北に伸ばす。
    """
    if not (-180 <= lon <= 180 and -90 <= lat <= 90):
        raise ValueError("中心点の緯度・経度が不正です。")
    if not math.isfinite(side_km) or side_km <= 0:
        raise ValueError("一辺の長さは正の値 (km) を指定してください。")
    half_m = side_km * 500.0
    lon_w, _, _ = _GEO_WGS84.fwd(lon, lat, 270, half_m)
    lon_e, _, _ = _GEO_WGS84.fwd(lon, lat, 90, half_m)
    _, lat_n, _ = _GEO_WGS84.fwd(lon, lat, 0, half_m)
    _, lat_s, _ = _GEO_WGS84.fwd(lon, lat, 180, half_m)
    west, east = min(lon_w, lon_e), max(lon_w, lon_e)
    south, north = min(lat_s, lat_n), max(lat_s, lat_n)
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise ValueError("算出した ROI が有効範囲を超えています。中心位置または一辺の長さを見直してください。")
    return west, south, east, north


def geodesic_distance_km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """WGS84 測地線上の 2 点間距離 (km)。"""
    if not all(math.isfinite(x) for x in (lon1, lat1, lon2, lat2)):
        raise ValueError("緯度・経度が不正です。")
    _, _, dist_m = _GEO_WGS84.inv(lon1, lat1, lon2, lat2)
    return dist_m / 1000.0


def bounds_to_wgs84(crs: CRS, bounds) -> tuple[float, float, float, float]:
    """(west, south, east, north) を EPSG:4326 の外接範囲へ変換する。"""
    west, south, east, north = bounds
    if crs.to_epsg() == 4326:
        return float(west), float(south), float(east), float(north)
    tr = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    w, s, e, n = tr.transform_bounds(west, south, east, north, densify_pts=21)
    return float(w), float(s), float(e), float(n)


def _roi_kml_text(
    west: float,
    south: float,
    east: float,
    north: float,
    *,
    title: str = "ROI",
) -> str:
    title = xml_escape.escape(title)
    coords = " ".join(
        f"{lon},{lat},0"
        for lon, lat in (
            (west, south),
            (east, south),
            (east, north),
            (west, north),
            (west, south),
        )
    )
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


def write_roi_kml(
    path: str,
    west: float,
    south: float,
    east: float,
    north: float,
    *,
    title: str = "ROI",
) -> None:
    """矩形 ROI を KML 2.2 の Polygon として書き出す。座標は WGS84（経度・緯度）。"""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(_roi_kml_text(west, south, east, north, title=title))


def geocode_places(
    query: str,
    *,
    limit: int = 8,
    countrycodes: str = "jp",
) -> list[dict]:
    """
    OpenStreetMap Nominatim で地名検索し、ROI 用の範囲候補を返す。
    boundingbox が無い場合は中心から約 0.02° の矩形を返す。
    """
    q = (query or "").strip()
    if not q:
        return []
    if len(q) < 2:
        return []
    limit = max(1, min(15, limit))
    try:
        resp = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": q, "format": "json", "limit": limit, "countrycodes": countrycodes},
            headers={"User-Agent": "dem_downloader/1.0 (local GIS; Nominatim fair use)"},
            timeout=(5, 20),
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        raise ValueError(f"地名検索に失敗しました: {e}") from e
    if not isinstance(data, list):
        return []

    out: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        name = item.get("display_name") or item.get("name") or ""
        bb = item.get("boundingbox")
        if isinstance(bb, (list, tuple)) and len(bb) >= 4:
            south, north, west, east = (float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3]))
        else:
            lat = float(item.get("lat", 0))
            lon = float(item.get("lon", 0))
            pad = 0.01
            south, north, west, east = lat - pad, lat + pad, lon - pad, lon + pad
        if west >= east or south >= north:
            continue
        west = max(-180.0, min(180.0, west))
        east = max(-180.0, min(180.0, east))
        south = max(-90.0, min(90.0, south))
        north = max(-90.0, min(90.0, north))
        if west >= east or south >= north:
            continue
        out.append(
            {
                "label": name,
                "lat": float(item.get("lat", (south + north) / 2)),
                "lon": float(item.get("lon", (west + east) / 2)),
                "bounds": {"west": west, "south": south, "east": east, "north": north},
            }
        )
    return out


def inspect_file(path: str) -> dict:
    """ファイルの種別・座標系・WGS84 範囲・地図表示用 GeoJSON を返す。"""
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isfile(path):
        raise ValueError(f"ファイルが見つかりません: {path}")
    ext = os.path.splitext(path)[1].lower()
    if ext == ".shp":
        return _inspect_shapefile(path)
    if ext in (".kml", ".kmz"):
        return _inspect_kml(path)
    order = [_inspect_raster, _inspect_vector] if ext in RASTER_EXTS else [_inspect_vector, _inspect_raster]
    errors = []
    for fn in order:
        try:
            return fn(path)
        except FileProblem:
            raise  # 座標系なし等、ファイルは開けたが使えないケースはそのまま伝える
        except Exception as e:
            errors.append(f"{type(e).__name__}: {e}")
    raise ValueError("ファイルを地理データとして開けませんでした。\n" + "\n".join(errors))


# ----------------------------------------------------------------------------
# ファイルブラウザ
# ----------------------------------------------------------------------------


def make_dir(parent: str, name: str) -> str:
    """parent の直下に新しいフォルダを作り、そのパスを返す（階層を含む名前は不可）。"""
    name = (name or "").strip()
    if not name:
        raise ValueError("フォルダ名を入力してください。")
    if name in (".", "..") or any(c in name for c in ("/", "\\", "\0")):
        raise ValueError("フォルダ名に「/」「\\」は使えません。また「.」「..」は指定できません。")
    parent = os.path.abspath(os.path.expanduser(parent or "~"))
    if not os.path.isdir(parent):
        raise ValueError(f"作成先のフォルダが存在しません: {parent}")
    path = os.path.join(parent, name)
    if os.path.exists(path):
        raise ValueError(f"同じ名前のファイル/フォルダが既に存在します: {name}")
    try:
        os.mkdir(path)
    except PermissionError:
        raise ValueError("このフォルダに作成する権限がありません。")
    except OSError as e:
        raise ValueError(f"フォルダを作成できません: {e}")
    return path


def list_dir(path: str, dirs_only: bool = False, show_all: bool = False, show_hidden: bool = False) -> dict:
    path = os.path.abspath(os.path.expanduser(path or "~"))
    # 存在しなければ、存在する親ディレクトリまでさかのぼる
    while not os.path.isdir(path):
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent

    entries, error = [], None
    try:
        with os.scandir(path) as it:
            for e in it:
                if not show_hidden and e.name.startswith("."):
                    continue
                try:
                    is_dir = e.is_dir()
                    if is_dir:
                        entries.append({"name": e.name, "is_dir": True, "size": None})
                    elif not dirs_only:
                        ext = os.path.splitext(e.name)[1].lower()
                        if show_all or ext in GEO_EXTS:
                            entries.append({"name": e.name, "is_dir": False, "size": e.stat().st_size})
                except OSError:
                    continue
    except PermissionError:
        error = "このフォルダを読む権限がありません。"
    except OSError as ex:
        error = str(ex)

    entries.sort(key=lambda x: (not x["is_dir"], x["name"].casefold()))
    shortcuts = [{"name": "ホーム", "path": os.path.expanduser("~")}]
    here = os.path.dirname(os.path.abspath(__file__))
    shortcuts.append({"name": "このプログラムのフォルダ", "path": here})
    if os.path.isdir("/mnt"):
        for d in sorted(os.listdir("/mnt")):
            p = os.path.join("/mnt", d)
            if os.path.isdir(p) and len(d) == 1:
                shortcuts.append({"name": f"Windows {d.upper()}:", "path": p})
    shortcuts.append({"name": "/", "path": "/"})

    parent = os.path.dirname(path)
    return {
        "path": path,
        "parent": parent if parent != path else None,
        "entries": entries,
        "error": error,
        "shortcuts": shortcuts,
    }
