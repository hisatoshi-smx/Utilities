"""
ジオコード用 DEM の準備。

isce3 は WGS84 楕円体高の DEM を前提とするため、ジオイド高（標高）の DEM は
PROJ のジオイドグリッドで楕円体高に変換する。出力は EPSG:4326 の GeoTIFF。
"""

from __future__ import annotations

import math
import os

import numpy as np

COP30_URL = (
    "https://copernicus-dem-30m.s3.amazonaws.com/"
    "Copernicus_DSM_COG_10_{ns}{lat:02d}_00_{ew}{lon:03d}_00_DEM/"
    "Copernicus_DSM_COG_10_{ns}{lat:02d}_00_{ew}{lon:03d}_00_DEM.tif"
)

# 鉛直基準 → PROJ の複合 CRS
_VERTICAL_CRS = {
    "egm2008": "EPSG:4326+3855",
    "egm96": "EPSG:4326+5773",
}

_GTIFF_OPTS = ["COMPRESS=DEFLATE", "PREDICTOR=3", "TILED=YES", "BIGTIFF=IF_SAFER"]


def _gdal():
    from osgeo import gdal

    gdal.UseExceptions()
    return gdal


def cop30_tile_urls(bounds: list[float]) -> list[str]:
    west, south, east, north = bounds
    urls = []
    for lat in range(int(math.floor(south)), int(math.ceil(north))):
        for lon in range(int(math.floor(west)), int(math.ceil(east))):
            urls.append(
                COP30_URL.format(
                    ns="N" if lat >= 0 else "S", lat=abs(lat), ew="E" if lon >= 0 else "W", lon=abs(lon)
                )
            )
    return urls


def download_copernicus(bounds: list[float], out_path: str, log=print) -> str:
    """Copernicus GLO-30（AWS 公開バケット）から bounds を切り出す。高さは EGM2008。"""
    gdal = _gdal()
    gdal.SetConfigOption("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
    gdal.SetConfigOption("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", ".tif")
    srcs = []
    for url in cop30_tile_urls(bounds):
        try:
            gdal.Open("/vsicurl/" + url)
        except RuntimeError:
            log(f"  タイルなし（海域など）: {os.path.basename(url)}")
            continue
        log(f"  タイル: {os.path.basename(url)}")
        srcs.append("/vsicurl/" + url)
    if not srcs:
        raise RuntimeError("ROI にかかる Copernicus DEM タイルが見つかりません（ネットワーク接続を確認してください）")
    vrt = out_path + ".src.vrt"
    gdal.BuildVRT(vrt, srcs)
    west, south, east, north = bounds
    gdal.Translate(
        out_path,
        vrt,
        projWin=[west, north, east, south],
        creationOptions=_GTIFF_OPTS,
        noData=-32767,
    )
    os.remove(vrt)
    return out_path


def crop_file(src: str, bounds: list[float], out_path: str) -> str:
    """任意座標系の DEM を EPSG:4326 に再投影して bounds で切り出す。"""
    gdal = _gdal()
    west, south, east, north = bounds
    gdal.Warp(
        out_path,
        src,
        dstSRS="EPSG:4326",
        outputBounds=[west, south, east, north],
        resampleAlg="bilinear",
        creationOptions=_GTIFF_OPTS,
        dstNodata=-32767,
        multithread=True,
    )
    return out_path


def geoid_to_ellipsoid(src: str, out_path: str, vertical_datum: str, log=print) -> str:
    """EPSG:4326 の標高 DEM を楕円体高に変換する。"""
    gdal = _gdal()
    from pyproj import Transformer, network

    if vertical_datum == "ellipsoid":
        gdal.Translate(out_path, src, creationOptions=_GTIFF_OPTS)
        return out_path

    network.set_network_enabled(True)
    try:
        tr = Transformer.from_crs(_VERTICAL_CRS[vertical_datum], "EPSG:4979", always_xy=True, only_best=True)
    except Exception as e:
        raise RuntimeError(
            f"ジオイド（{vertical_datum}）→楕円体高の変換を用意できません: {e}\n"
            "PROJ のジオイドグリッドを取得できる必要があります（ネットワーク接続、または projsync で事前取得）。"
        ) from e

    ds = gdal.Open(src)
    gt = ds.GetGeoTransform()
    band = ds.GetRasterBand(1)
    nodata = band.GetNoDataValue()
    w, h = ds.RasterXSize, ds.RasterYSize
    drv = gdal.GetDriverByName("GTiff")
    out = drv.Create(out_path, w, h, 1, gdal.GDT_Float32, options=_GTIFF_OPTS)
    out.SetGeoTransform(gt)
    out.SetProjection(ds.GetProjection())
    ob = out.GetRasterBand(1)
    ob.SetNoDataValue(-32767)

    lon = gt[0] + (np.arange(w) + 0.5) * gt[1]
    checked = False
    block = 512
    for r0 in range(0, h, block):
        n = min(block, h - r0)
        z = band.ReadAsArray(0, r0, w, n).astype(np.float64)
        bad = ~np.isfinite(z)
        if nodata is not None:
            bad |= z == nodata
        # 海域などの欠損は 0 m（ジオイド面）として楕円体高に変換する
        z[bad] = 0.0
        lat = gt[3] + (np.arange(r0, r0 + n) + 0.5) * gt[5]
        lon2, lat2 = np.meshgrid(lon, lat)
        _, _, ze = tr.transform(lon2, lat2, z)
        ze = np.asarray(ze)
        if not checked:
            diff = np.nanmax(np.abs(ze - z))
            if not np.isfinite(diff) or diff < 1e-3:
                raise RuntimeError(
                    "ジオイド補正が適用されていません（PROJ のジオイドグリッドを取得できなかった可能性があります）"
                )
            log(f"  ジオイド高の最大値: {diff:.2f} m")
            checked = True
        ob.WriteArray(ze.astype(np.float32), 0, r0)
    ob.FlushCache()
    out = None
    ds = None
    return out_path


def prepare_dem(source: str, path: str, vertical_datum: str, bounds: list[float], workdir: str, log=print) -> str:
    os.makedirs(workdir, exist_ok=True)
    raw = os.path.join(workdir, "dem_raw.tif")
    out = os.path.join(workdir, "dem_wgs84_ellipsoid.tif")
    for p in (raw, out):
        if os.path.exists(p):
            os.remove(p)
    if source == "copernicus":
        log("Copernicus GLO-30 DEM を取得します")
        download_copernicus(bounds, raw, log=log)
        vertical_datum = "egm2008"
    else:
        log(f"DEM ファイルを切り出します: {path}")
        crop_file(path, bounds, raw)
    log(f"楕円体高に変換します（元の基準: {vertical_datum}）")
    geoid_to_ellipsoid(raw, out, vertical_datum, log=log)
    os.remove(raw)

    gdal = _gdal()
    ds = gdal.Open(out)
    stats = ds.GetRasterBand(1).ComputeStatistics(False)
    log(f"  DEM: {ds.RasterXSize} x {ds.RasterYSize} 画素, 高さ {stats[0]:.1f} 〜 {stats[1]:.1f} m（楕円体高）")
    ds = None
    return out
