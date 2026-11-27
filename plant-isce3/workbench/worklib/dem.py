from __future__ import annotations

import json
import math
import os

import numpy as np

_META_NAME = "dem_prepare.json"

# ccd3 と同系の Copernicus / ファイル DEM
_COP30_URL = (
    "https://copernicus-dem-30m.s3.amazonaws.com/"
    "Copernicus_DSM_COG_10_{ns}{lat:02d}_00_{ew}{lon:03d}_00_DEM/"
    "Copernicus_DSM_COG_10_{ns}{lat:02d}_00_{ew}{lon:03d}_00_DEM.tif"
)
_VERT = {"egm2008": "EPSG:4326+3855", "egm96": "EPSG:4326+5773"}
_GTIFF = ["COMPRESS=DEFLATE", "PREDICTOR=3", "TILED=YES", "BIGTIFF=IF_SAFER"]


def _gdal():
    from osgeo import gdal

    gdal.UseExceptions()
    return gdal


def cop30_urls(bounds: list[float]) -> list[str]:
    w, s, e, n = bounds
    urls = []
    for lat in range(int(math.floor(s)), int(math.ceil(n))):
        for lon in range(int(math.floor(w)), int(math.ceil(e))):
            urls.append(_COP30_URL.format(ns="N" if lat >= 0 else "S", lat=abs(lat),
                                          ew="E" if lon >= 0 else "W", lon=abs(lon)))
    return urls


def download_copernicus(bounds: list[float], out_path: str, log=print) -> str:
    gdal = _gdal()
    gdal.SetConfigOption("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
    srcs = []
    for url in cop30_urls(bounds):
        try:
            gdal.Open("/vsicurl/" + url)
        except RuntimeError:
            continue
        log(f"  Copernicus: {os.path.basename(url)}")
        srcs.append("/vsicurl/" + url)
    if not srcs:
        raise RuntimeError("Copernicus DEM タイルが見つかりません")
    vrt = out_path + ".vrt"
    gdal.BuildVRT(vrt, srcs)
    w, s, e, n = bounds
    gdal.Translate(out_path, vrt, projWin=[w, n, e, s], creationOptions=_GTIFF, noData=-32767)
    os.remove(vrt)
    return out_path


def crop_file(src: str, bounds: list[float], out_path: str) -> str:
    gdal = _gdal()
    w, s, e, n = bounds
    gdal.Warp(out_path, src, dstSRS="EPSG:4326", outputBounds=[w, s, e, n],
              resampleAlg="bilinear", creationOptions=_GTIFF, dstNodata=-32767)
    return out_path


def geoid_to_ellipsoid(src: str, out_path: str, vertical_datum: str, log=print) -> str:
    gdal = _gdal()
    if vertical_datum == "ellipsoid":
        gdal.Translate(out_path, src, creationOptions=_GTIFF)
        return out_path
    from pyproj import Transformer, network

    network.set_network_enabled(True)
    tr = Transformer.from_crs(_VERT[vertical_datum], "EPSG:4979", always_xy=True, only_best=True)
    ds = gdal.Open(src)
    gt, band = ds.GetGeoTransform(), ds.GetRasterBand(1)
    w, h = ds.RasterXSize, ds.RasterYSize
    out = gdal.GetDriverByName("GTiff").Create(out_path, w, h, 1, gdal.GDT_Float32, options=_GTIFF)
    out.SetGeoTransform(gt)
    out.SetProjection(ds.GetProjection())
    lon = gt[0] + (np.arange(w) + 0.5) * gt[1]
  # ブロック処理
    for r0 in range(0, h, 512):
        n = min(512, h - r0)
        z = band.ReadAsArray(0, r0, w, n).astype(np.float64)
        lat = gt[3] + (np.arange(r0, r0 + n) + 0.5) * gt[5]
        lon2, lat2 = np.meshgrid(lon, lat)
        z = np.nan_to_num(z, nan=0.0)
        _, _, ze = tr.transform(lon2, lat2, z)
        out.GetRasterBand(1).WriteArray(np.asarray(ze, dtype=np.float32), 0, r0)
    out.FlushCache()
    out = ds = None
    return out_path


def download_gsi(bounds: list[float], mode: str, out_path: str, log=print) -> str:
    """国土地理院タイル → EPSG:4326 楕円体高（conda 環境 dem_downloader を subprocess で使用）。"""
    from .dem_downloader_env import run_gsi_download

    return run_gsi_download(bounds, mode, out_path, log=log)


def dem_prepare_signature(
    source: str,
    path: str,
    gsi_mode: str,
    vertical_datum: str,
    bounds: list[float],
) -> dict:
    """取得済み DEM を再利用できるか判定するためのキー（ROI 余白込みの bounds 含む）。"""
    vd = vertical_datum
    if source == "copernicus":
        vd = "egm2008"
    elif source == "gsi":
        vd = "ellipsoid"
    return {
        "source": source,
        "path": os.path.abspath(path) if source == "file" and path else "",
        "gsi_mode": gsi_mode if source == "gsi" else "",
        "vertical_datum": vd,
        "bounds": [round(float(x), 8) for x in bounds],
    }


def _read_prepare_meta(workdir: str) -> dict | None:
    p = os.path.join(workdir, _META_NAME)
    if not os.path.isfile(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _write_prepare_meta(workdir: str, sig: dict) -> None:
    with open(os.path.join(workdir, _META_NAME), "w", encoding="utf-8") as f:
        json.dump(sig, f, ensure_ascii=False, indent=2)


def prepare_dem(source: str, path: str, gsi_mode: str, vertical_datum: str,
                bounds: list[float], workdir: str, log=print) -> str:
    os.makedirs(workdir, exist_ok=True)
    out = os.path.join(workdir, "dem_wgs84_ellipsoid.tif")
    raw = os.path.join(workdir, "dem_raw.tif")
    sig = dem_prepare_signature(source, path, gsi_mode, vertical_datum, bounds)
    if os.path.isfile(out) and _read_prepare_meta(workdir) == sig:
        gdal = _gdal()
        ds = gdal.Open(out)
        if ds is not None:
            log("DEM を再利用します（ROI と取得設定が前回と同じ）")
            log(f"  DEM: {ds.RasterXSize} x {ds.RasterYSize} px")
            ds = None
            return out
    for p in (raw, out):
        if os.path.isfile(p):
            os.remove(p)
    if source == "gsi":
        log("国土地理院 DEM を取得します（楕円体高）")
        download_gsi(bounds, gsi_mode, out, log=log)
    elif source == "copernicus":
        log("Copernicus GLO-30 を取得します")
        download_copernicus(bounds, raw, log=log)
        geoid_to_ellipsoid(raw, out, "egm2008", log=log)
        os.remove(raw)
    else:
        log(f"DEM ファイルを切り出します: {path}")
        crop_file(path, bounds, raw)
        geoid_to_ellipsoid(raw, out, vertical_datum, log=log)
        os.remove(raw)
    gdal = _gdal()
    ds = gdal.Open(out)
    log(f"  DEM: {ds.RasterXSize} x {ds.RasterYSize} px")
    ds = None
    _write_prepare_meta(workdir, sig)
    return out