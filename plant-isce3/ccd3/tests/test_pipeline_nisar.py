"""
plant-isce3 同梱の envisat.h5（NISAR RSLC 形式）を使った通しテスト。

同じシーンを 3 時期として扱うため inspect ステップは使わず、scenes.json を直接作る。
DEM 準備 → グリッド → 日付間オフセット推定 → geocode_slc によるジオコード →
コヒーレンス → 成果物・プレビューを実行し、オフセット ≈ 0・γ ≈ 1 になること、
グリッドが一致することを確認する。
"""

import os

import numpy as np
import pytest
from osgeo import gdal

from conftest import PLANT_TEST_DATA
from ccd3lib.config import RoiConfig
from ccd3lib.pipeline import Pipeline
from ccd3lib.projects import create_project

RSLC = os.path.join(PLANT_TEST_DATA, "envisat.h5")
DEM = os.path.join(PLANT_TEST_DATA, "constant_height.vrt")

pytestmark = pytest.mark.skipif(not os.path.exists(RSLC), reason="plant-isce3 のテストデータがありません")


def test_pipeline_nisar(tmp_path):
    from ccd3lib.inputs import inspect_scene

    proj = create_project("envisat", root=str(tmp_path))
    cfg = proj.load()
    cfg.inputs.sensor = "nisar_rslc"
    cfg.inputs.polarization = "HH"
    cfg.inputs.scenes = [RSLC, RSLC + "#2", RSLC + "#3"]
    cfg.roi = RoiConfig(west=-115.60, south=34.815, east=-115.54, north=34.835)
    cfg.grid.spacing = 20.0
    cfg.dem.source = "file"
    cfg.dem.path = DEM
    cfg.dem.vertical_datum = "ellipsoid"
    cfg.ccd.window_x = cfg.ccd.window_y = 3
    cfg.processing.keep_intermediate = False
    proj.save(cfg)

    info = inspect_scene(RSLC, "nisar_rslc", "HH", "A")
    assert info["polarizations"] == ["HH"]
    proj.write_json({"scenes": {t: info for t in ("t1", "t2", "t3")}, "warnings": [], "reordered": False,
                     "common_footprint": info["footprint"]}, "work", "scenes.json")
    proj.write_json({t: None for t in ("t1", "t2", "t3")}, "work", "orbits.json")

    pl = Pipeline(proj)
    pl.step_dem()
    pl.step_grid()
    pl.step_geocode()
    pl.step_coherence()
    pl.step_products()

    gi = proj.read_json("work", "grid.json")
    assert gi["grid"]["epsg"] == 32611
    unit = gi["units"][0]["id"]
    ds = [gdal.Open(proj.path("work", "gslc", t, f"{unit}.tif")) for t in ("t1", "t2", "t3")]
    assert len({d.GetGeoTransform() for d in ds}) == 1
    assert gdal.GetDataTypeName(ds[0].GetRasterBand(1).DataType) == "CFloat32"
    assert not os.path.exists(proj.path("work", "slc"))

    coreg = proj.read_json("work", "coreg.json")
    for t in ("t1", "t3"):
        assert coreg[t]["applied"] and coreg[t]["ncc"] > 0.99
        assert abs(coreg[t]["az_lines"]) < 0.05 and abs(coreg[t]["rg_samples"]) < 0.05

    pre = gdal.Open(proj.path("output", "gamma_pre.tif")).ReadAsArray()
    assert np.isfinite(pre).mean() > 0.5
    assert np.nanmedian(pre) == pytest.approx(1.0, abs=1e-3)
    dg = gdal.Open(proj.path("output", "dgamma.tif")).ReadAsArray()
    assert np.nanmax(np.abs(dg)) < 1e-3

    rgb = gdal.Open(proj.path("output", "ccd_rgb.tif"))
    assert rgb.RasterCount == 4
    previews = proj.read_json("output", "preview", "preview.json")
    assert set(previews) == {"gamma_pre", "gamma_co", "dgamma", "ratio", "rgb"}
    for v in previews.values():
        assert os.path.exists(proj.path("output", "preview", v["png"]))
        (s, w), (n, e) = v["bounds"]
        assert -115.7 < w < e < -115.4 and 34.7 < s < n < 34.9
