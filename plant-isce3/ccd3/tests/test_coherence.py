import numpy as np
import pytest
from osgeo import gdal

from ccd3lib.coherence import coherence_array, coherence_file, mosaic, read_float

gdal.UseExceptions()
RNG = np.random.default_rng(0)


def _cplx(shape):
    return (RNG.standard_normal(shape) + 1j * RNG.standard_normal(shape)).astype(np.complex64)


def _theory(gamma_true, shape=(200, 200)):
    """真のコヒーレンス gamma_true を持つ複素ガウス信号のペア。"""
    a, n = _cplx(shape), _cplx(shape)
    b = gamma_true * a + np.sqrt(1 - gamma_true ** 2) * n
    # 共通の位相縞を乗せてもコヒーレンスは変わらない
    ramp = np.exp(1j * np.linspace(0, 0.5, shape[1]))[None, :]
    return a * ramp, (b * ramp).astype(np.complex64)


def test_identical_is_one():
    a = _cplx((50, 60))
    g = coherence_array(a, a, 5, 5)
    assert np.nanmin(g) == pytest.approx(1.0, abs=1e-5)


@pytest.mark.parametrize("gt", [0.3, 0.7, 0.9])
def test_estimate_close_to_truth(gt):
    a, b = _theory(gt)
    g = coherence_array(a, b, 15, 15)
    assert np.nanmedian(g) == pytest.approx(gt, abs=0.07)


def test_invalid_pixels_masked():
    a, b = _theory(0.8, (40, 40))
    a[10:20, 10:20] = np.nan
    b[0:5, :] = 0
    g = coherence_array(a, b, 5, 5, min_valid_fraction=0.5)
    assert np.isnan(g[15, 15]) and np.isnan(g[2, 20])
    assert np.isfinite(g[30, 30])


def _write(path, arr, gt=(0, 10, 0, 0, 0, -10)):
    dt = gdal.GDT_CFloat32 if np.iscomplexobj(arr) else gdal.GDT_Float32
    ds = gdal.GetDriverByName("GTiff").Create(str(path), arr.shape[1], arr.shape[0], 1, dt)
    ds.SetGeoTransform(gt)
    ds.GetRasterBand(1).WriteArray(arr)
    ds = None


def test_blockwise_equals_array(tmp_path):
    a, b = _theory(0.6, (300, 120))
    _write(tmp_path / "a.tif", a)
    _write(tmp_path / "b.tif", b)
    coherence_file(str(tmp_path / "a.tif"), str(tmp_path / "b.tif"), str(tmp_path / "g.tif"), 7, 5, 0.5, block=64)
    g, _, _ = read_float(str(tmp_path / "g.tif"))
    ref = coherence_array(a, b, 7, 5, 0.5)
    np.testing.assert_allclose(g, ref, atol=1e-5, equal_nan=True)


def test_mosaic_average(tmp_path):
    p1 = np.full((4, 4), 0.2, np.float32)
    p2 = np.full((4, 4), 0.6, np.float32)
    _write(tmp_path / "p1.tif", p1)
    _write(tmp_path / "p2.tif", p2)
    out = str(tmp_path / "m.tif")
    mosaic([(str(tmp_path / "p1.tif"), 0, 0), (str(tmp_path / "p2.tif"), 2, 1)], 6, 5, (0, 10, 0, 0, 0, -10), "", out)
    m, _, _ = read_float(out)
    assert m[0, 0] == pytest.approx(0.2)
    assert m[1, 2] == pytest.approx(0.4)  # 重なりは平均
    assert m[4, 5] == pytest.approx(0.6)
    assert np.isnan(m[4, 0])
