"""
CCD 成果物とブラウザ表示用プレビューの作成。

成果物（output/）:
    gamma_pre.tif   災害前ペア γ(t1, t2)
    gamma_co.tif    災害を挟むペア γ(t2, t3)
    dgamma.tif      Δγ = γpre − γco（正の大きな値ほどコヒーレンスが低下＝変化の可能性）
    ratio.tif       γco / γpre（1 より小さいほど低下）
    ccd_rgb.tif     RGB 合成（R=γpre, G=γco, B=γco）。変化域は赤く見える
    preview/*.png   Web メルカトルに再投影した PNG（Leaflet 重ね合わせ用）
"""

from __future__ import annotations

import os

import numpy as np

from .coherence import read_float, write_float

PREVIEW_MAX_PX = 2048


def _gdal():
    from osgeo import gdal

    gdal.UseExceptions()
    return gdal


def make_products(out_dir: str, dgamma_range: float, log=print) -> dict:
    pre, gt, proj = read_float(os.path.join(out_dir, "gamma_pre.tif"))
    co, _, _ = read_float(os.path.join(out_dir, "gamma_co.tif"))

    dg = pre - co
    write_float(os.path.join(out_dir, "dgamma.tif"), dg, gt, proj, "gamma_pre - gamma_co")
    with np.errstate(invalid="ignore", divide="ignore"):
        ratio = np.where(pre > 0.01, co / pre, np.nan)
    write_float(os.path.join(out_dir, "ratio.tif"), ratio, gt, proj, "gamma_co / gamma_pre")

    gdal = _gdal()
    h, w = pre.shape
    rgb_path = os.path.join(out_dir, "ccd_rgb.tif")
    ds = gdal.GetDriverByName("GTiff").Create(
        rgb_path, w, h, 4, gdal.GDT_Byte, options=["COMPRESS=DEFLATE", "TILED=YES", "PHOTOMETRIC=RGB", "ALPHA=YES"]
    )
    ds.SetGeoTransform(gt)
    ds.SetProjection(proj)
    valid = np.isfinite(pre) & np.isfinite(co)
    for i, (arr, name) in enumerate([(pre, "R: gamma_pre"), (co, "G: gamma_co"), (co, "B: gamma_co")], 1):
        b = ds.GetRasterBand(i)
        b.WriteArray(np.where(valid, np.clip(np.nan_to_num(arr) * 255, 0, 255), 0).astype(np.uint8))
        b.SetDescription(name)
    ds.GetRasterBand(4).WriteArray((valid * 255).astype(np.uint8))
    ds = None

    def _stats(a):
        v = a[np.isfinite(a)]
        if v.size == 0:
            return None
        return {"mean": float(v.mean()), "median": float(np.median(v)), "p05": float(np.percentile(v, 5)),
                "p95": float(np.percentile(v, 95)), "valid_pixels": int(v.size)}

    summary = {
        "width": w, "length": h, "geotransform": list(gt),
        "gamma_pre": _stats(pre), "gamma_co": _stats(co), "dgamma": _stats(dg),
        "dgamma_range": dgamma_range,
    }
    for k in ("gamma_pre", "gamma_co", "dgamma"):
        s = summary[k]
        if s:
            log(f"  {k}: 平均 {s['mean']:.3f}, 中央値 {s['median']:.3f}, 有効画素 {s['valid_pixels']}")
    return summary


# ---------------------------------------------------------------- プレビュー
def _cmap(name: str):
    import matplotlib

    return matplotlib.colormaps[name]


PREVIEWS = {
    "gamma_pre": {"file": "gamma_pre.tif", "label": "γpre（t1–t2）", "cmap": "gray", "vmin": 0.0, "vmax": 1.0},
    "gamma_co": {"file": "gamma_co.tif", "label": "γco（t2–t3）", "cmap": "gray", "vmin": 0.0, "vmax": 1.0},
    "dgamma": {"file": "dgamma.tif", "label": "Δγ = γpre − γco", "cmap": "RdBu_r", "vmin": None, "vmax": None},
    "ratio": {"file": "ratio.tif", "label": "γco / γpre", "cmap": "RdYlBu", "vmin": 0.0, "vmax": 1.5},
    "rgb": {"file": "ccd_rgb.tif", "label": "RGB（R=γpre, G=B=γco）", "cmap": None, "vmin": None, "vmax": None},
}


def _warp_3857(src: str, resample: str):
    gdal = _gdal()
    ds = gdal.Open(src)
    w, h = ds.RasterXSize, ds.RasterYSize
    scale = max(w, h) / PREVIEW_MAX_PX
    kwargs = {}
    if scale > 1:
        kwargs = {"width": int(w / scale), "height": int(h / scale)}
    return gdal.Warp("", ds, format="MEM", dstSRS="EPSG:3857", resampleAlg=resample,
                     dstNodata=None if ds.RasterCount == 4 else float("nan"), **kwargs)


def _bounds_latlon(ds) -> list[list[float]]:
    from pyproj import Transformer

    gt = ds.GetGeoTransform()
    x0, y0 = gt[0], gt[3]
    x1, y1 = x0 + gt[1] * ds.RasterXSize, y0 + gt[5] * ds.RasterYSize
    t = Transformer.from_crs(3857, 4326, always_xy=True)
    w, n = t.transform(x0, y0)
    e, s = t.transform(x1, y1)
    return [[s, w], [n, e]]


def _write_png(path: str, rgba: np.ndarray) -> None:
    gdal = _gdal()
    h, w, _ = rgba.shape
    mem = gdal.GetDriverByName("MEM").Create("", w, h, 4, gdal.GDT_Byte)
    for i in range(4):
        mem.GetRasterBand(i + 1).WriteArray(rgba[:, :, i])
    gdal.GetDriverByName("PNG").CreateCopy(path, mem)
    aux = path + ".aux.xml"
    if os.path.exists(aux):
        os.remove(aux)


def _legend(cmap: str, vmin: float, vmax: float) -> dict:
    cm = _cmap(cmap)
    stops = []
    for t in np.linspace(0, 1, 11):
        r, g, b, _ = cm(t)
        stops.append(f"rgb({int(r * 255)},{int(g * 255)},{int(b * 255)}) {int(t * 100)}%")
    return {"gradient": f"linear-gradient(to right, {', '.join(stops)})", "vmin": vmin, "vmax": vmax}


def make_previews(out_dir: str, dgamma_range: float) -> dict:
    prev_dir = os.path.join(out_dir, "preview")
    os.makedirs(prev_dir, exist_ok=True)
    result = {}
    for key, spec in PREVIEWS.items():
        src = os.path.join(out_dir, spec["file"])
        if not os.path.exists(src):
            continue
        if key == "rgb":
            ds = _warp_3857(src, "average")
            rgba = np.dstack([ds.GetRasterBand(i).ReadAsArray() for i in range(1, 5)]).astype(np.uint8)
            legend = None
        else:
            ds = _warp_3857(src, "bilinear")
            a = ds.GetRasterBand(1).ReadAsArray().astype(np.float32)
            vmin, vmax = spec["vmin"], spec["vmax"]
            if key == "dgamma":
                vmin, vmax = -dgamma_range, dgamma_range
            norm = np.clip((a - vmin) / (vmax - vmin), 0, 1)
            rgba = (_cmap(spec["cmap"])(np.nan_to_num(norm)) * 255).astype(np.uint8)
            rgba[..., 3] = np.where(np.isfinite(a), 255, 0)
            legend = _legend(spec["cmap"], vmin, vmax)
        png = f"{key}.png"
        _write_png(os.path.join(prev_dir, png), rgba)
        result[key] = {"png": png, "label": spec["label"], "bounds": _bounds_latlon(ds), "legend": legend,
                       "file": spec["file"]}
        ds = None
    return result
