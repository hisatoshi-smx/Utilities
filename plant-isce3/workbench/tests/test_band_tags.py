import os
import tempfile

from worklib.band_tags import band_descriptions, tag_geotiff_bands
from worklib.config import ExplorerConfig, GeocodeConfig


def test_band_descriptions_rtc():
    cfg = ExplorerConfig(name="t", polarization="HH_HV", geocode=GeocodeConfig(output="rtc_gamma"))
    assert band_descriptions(cfg, "geocode") == ["HH_gamma0", "HV_gamma0"]
    assert band_descriptions(cfg, "rtc") == ["HH_gamma0", "HV_gamma0"]


def test_band_descriptions_gslc():
    cfg = ExplorerConfig(name="t", polarization="HV", geocode=GeocodeConfig(output="complex_gslc"))
    assert band_descriptions(cfg) == ["HV_cpx"]


def test_tag_geotiff_roundtrip():
    from osgeo import gdal

    gdal.UseExceptions()
    path = tempfile.mktemp(suffix=".tif")
    try:
        ds = gdal.GetDriverByName("GTiff").Create(path, 4, 4, 2, gdal.GDT_Float32)
        ds = None
        tag_geotiff_bands(path, ["HH_gamma0", "HV_gamma0"])
        ds = gdal.Open(path)
        assert ds.GetRasterBand(1).GetDescription() == "HH_gamma0"
        assert ds.GetRasterBand(2).GetDescription() == "HV_gamma0"
        ds = None
    finally:
        if os.path.isfile(path):
            os.remove(path)