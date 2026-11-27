"""GeoTIFF のバンド Description（QGIS 表示名）を付与する。"""

from __future__ import annotations

from .config import ExplorerConfig
from .polarizations import polarizations_for


def band_descriptions(cfg: ExplorerConfig, tool: str = "geocode") -> list[str]:
    """偏波選択から QGIS 用ラベル（順: HH → HV）。"""
    pols = polarizations_for(cfg.polarization)
    if tool == "rtc":
        suffix = "_gamma0"
    elif cfg.geocode.output == "rtc_gamma":
        suffix = "_gamma0"
    else:
        suffix = "_cpx"
    return [f"{p}{suffix}" for p in pols]


def tag_geotiff_bands(path: str, descriptions: list[str]) -> None:
    """既存 GeoTIFF の各バンドに Description を書き込む（in-place）。"""
    if not descriptions:
        return
    from osgeo import gdal

    gdal.UseExceptions()
    ds = gdal.Open(path, gdal.GA_Update)
    if ds is None:
        raise RuntimeError(f"GeoTIFF を開けません: {path}")
    try:
        n = ds.RasterCount
        for i in range(1, n + 1):
            if i - 1 >= len(descriptions):
                break
            ds.GetRasterBand(i).SetDescription(descriptions[i - 1])
        ds.FlushCache()
    finally:
        ds = None