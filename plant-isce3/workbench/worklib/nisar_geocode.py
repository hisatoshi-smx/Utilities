"""NISAR RSLC 向け geocode（isce3.geocode_slc）。ccd3 と同系。"""

from __future__ import annotations

import os
import warnings

import numpy as np


def _gdal():
    from osgeo import gdal

    gdal.UseExceptions()
    return gdal


def _open_nisar(h5_path: str, frequency: str):
    from nisar.products.readers import SLC

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        slc = SLC(hdf5file=h5_path)
    return slc, slc.getRadarGrid(frequency), slc.getOrbit()


def geocode_nisar(
    h5_path: str,
    frequency: str,
    polarization: str,
    dem: str,
    template: str,
    epsg: int,
    out_path: str,
    block: int = 1024,
) -> str:
    import h5py
    import isce3
    from isce3.core.rdr_geo_block_generator import block_generator
    from isce3.core.types import read_c4_dataset_as_c8

    gdal = _gdal()
    t = gdal.Open(template)
    x0, dx, _, y0, _, dy = t.GetGeoTransform()
    width, length, proj = t.RasterXSize, t.RasterYSize, t.GetProjection()
    t = None
    geogrid = isce3.product.GeoGridParameters(x0, y0, dx, dy, width, length, epsg)
    slc, radar_grid, orbit = _open_nisar(h5_path, frequency)
    native_doppler = slc.getDopplerCentroid(frequency=frequency)
    native_doppler.bounds_error = False
    try:
        sub_swaths = isce3.product.Swath(h5_path, frequency).sub_swaths()
    except Exception:
        sub_swaths = None
    dem_raster = isce3.io.Raster(dem)
    ellipsoid = isce3.core.make_projection(epsg).ellipsoid
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    out = gdal.GetDriverByName("GTiff").Create(
        out_path, width, length, 1, gdal.GDT_CFloat32,
        options=["TILED=YES", "COMPRESS=DEFLATE", "BIGTIFF=IF_SAFER"],
    )
    out.SetGeoTransform((x0, dx, 0.0, y0, 0.0, dy))
    out.SetProjection(proj)
    band = out.GetRasterBand(1)
    band.SetNoDataValue(float("nan"))
    band.Fill(float("nan"))
    key = slc.slcPath(frequency, polarization)
    n_blocks = 0
    with h5py.File(h5_path, "r") as h5:
        if key not in h5:
            raise RuntimeError(f"データセットがありません: {key}")
        ds = h5[key]
        for rdr_slice, geo_slice, geo_shape, blk_grid in block_generator(
                geogrid, radar_grid, orbit, dem_raster, block, block, 100):
            rdr = read_c4_dataset_as_c8(ds, rdr_slice) if ds.dtype.names else ds[rdr_slice]
            geo = np.zeros(geo_shape, dtype=np.complex64)
            isce3.geocode.geocode_slc(
                [geo], [np.ascontiguousarray(rdr, dtype=np.complex64)], dem_raster,
                radar_grid, blk_grid, orbit, native_doppler, isce3.core.LUT2d(), ellipsoid,
                1e-8, 100, sliced_radargrid=radar_grid, subswaths=sub_swaths,
                first_azimuth_line=rdr_slice[0].start, first_range_sample=rdr_slice[1].start,
                flatten=True)
            band.WriteArray(geo, geo_slice[1].start, geo_slice[0].start)
            n_blocks += 1
    band.FlushCache()
    out = None
    if n_blocks == 0:
        raise RuntimeError("出力グリッドと RSLC が重なりません")
    return out_path