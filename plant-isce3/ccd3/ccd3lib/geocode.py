"""
SLC を共通の地図グリッドへ複素ジオコード（GSLC 化）する。

  Sentinel-1: plant-isce3 の plant_isce3_geocode（--gslc --interp）を使う。
              plant-isce3 は S1 の入力を強度に変換してしまうため、バースト SLC を
              TOPS の方位キャリアで deramp した複素 SLC を --input-raster で渡す
              （sinc 補間の前に信号をベースバンドへ移す必要があるため）。
  NISAR RSLC: isce3.geocode.geocode_slc を使う（ネイティブドップラーでベースバンド化）。
              プロダクトの読み込みは NISAR リーダー（plant-isce3 と共通）。

どちらも距離位相を除去（flatten）するため、GSLC 同士の干渉位相には地形縞が
ほとんど残らず、コヒーレンス推定が偏りにくい。
"""

from __future__ import annotations

import os
import warnings

import numpy as np

_SLC_OPTS = ["TILED=YES", "BIGTIFF=YES"]


def _gdal():
    from osgeo import gdal

    gdal.UseExceptions()
    return gdal


def _write_complex(path: str, width: int, length: int):
    gdal = _gdal()
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    return gdal.GetDriverByName("GTiff").Create(path, width, length, 1, gdal.GDT_CFloat32, options=_SLC_OPTS)


# ---------------------------------------------------------------- Sentinel-1
def load_s1_burst(zip_path: str, orbit_file: str | None, burst_id: str, polarization: str):
    from s1reader.s1_reader import load_bursts

    swath = int(burst_id.split("_")[-1].replace("iw", ""))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        bursts = load_bursts(zip_path, orbit_file, swath, polarization.lower(),
                             burst_ids=[burst_id], flag_apply_eap=False)
    if not bursts:
        raise RuntimeError(f"バースト {burst_id} が見つかりません: {zip_path}")
    return bursts[0]


def write_deramped_burst(burst, out_path: str, block: int = 256) -> str:
    """バースト SLC から TOPS 方位キャリア位相を除去して GeoTIFF に保存する。"""
    gdal = _gdal()
    vrt = out_path + ".src.vrt"
    burst.slc_to_vrt_file(vrt)
    src = gdal.Open(vrt)
    width, length = src.RasterXSize, src.RasterYSize
    # kt と eta_ref は距離方向のみに依存するので 1 行分だけ計算する
    x = np.arange(width)[None, :]
    comps = burst.az_carrier_components(offset=0.0, position=(np.zeros_like(x), x))
    kt = np.asarray(comps.kt, dtype=np.float64).reshape(1, width)
    eta_ref = np.asarray(comps.eta_ref, dtype=np.float64).reshape(1, width)
    dt = burst.azimuth_time_interval
    n_lines = burst.shape[0]

    out = _write_complex(out_path, width, length)
    ob = out.GetRasterBand(1)
    sb = src.GetRasterBand(1)
    for r0 in range(0, length, block):
        n = min(block, length - r0)
        slc = sb.ReadAsArray(0, r0, width, n)
        eta = ((np.arange(r0, r0 + n) - n_lines // 2) * dt)[:, None]
        carrier = np.pi * kt * (eta - eta_ref) ** 2
        ob.WriteArray((slc * np.exp(-1j * carrier)).astype(np.complex64), 0, r0)
    ob.FlushCache()
    out = None
    src = None
    os.remove(vrt)
    return out_path


# ---------------------------------------------------------------- plant-isce3
def run_plant_geocode(input_file: str, input_raster: str, dem: str, template: str, epsg: int,
                      out_path: str, interp: str, extra: list[str] | None = None) -> str:
    from plant_isce3 import plant_isce3_geocode

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    argv = [
        input_file,
        "--input-raster", input_raster,
        "--no-transform-input-raster",
        "--dem", dem,
        "--bbox-file", template,
        "--epsg", str(epsg),
        "--gslc",
        "--interp",
        "--data-interp-method", interp,
        # plant-isce3 は既定で RTC を掛けるが、複素 GSLC の振幅を画素ごとに変えてしまうので無効にする
        "--terrain", "none",
        "--output", out_path,
        "--force",
        "--no-log",
        "--no-text-color",
    ] + (extra or [])
    print("plant_isce3_geocode " + " ".join(argv), flush=True)
    plant_isce3_geocode.main(argv)
    if not os.path.isfile(out_path):
        raise RuntimeError(f"plant_isce3_geocode が出力を作成しませんでした: {out_path}")
    return out_path


def geocode_s1_burst(zip_path: str, orbit_file: str | None, burst_id: str, polarization: str,
                     dem: str, template: str, epsg: int, out_path: str, slc_dir: str,
                     interp: str, keep_slc: bool) -> str:
    burst = load_s1_burst(zip_path, orbit_file, burst_id, polarization)
    slc = os.path.join(slc_dir, f"{burst_id}_deramped.tif")
    print(f"  deramp: {burst_id} ({burst.shape[1]} x {burst.shape[0]})", flush=True)
    write_deramped_burst(burst, slc)
    extra = ["--burst-ids", burst_id]
    if orbit_file:
        extra += ["--orbit-files", orbit_file]
    try:
        run_plant_geocode(zip_path, slc, dem, template, epsg, out_path, interp, extra)
    finally:
        if not keep_slc and os.path.exists(slc):
            os.remove(slc)
    return out_path


# ---------------------------------------------------------------- NISAR
def _open_nisar(h5_path: str, frequency: str):
    from nisar.products.readers import SLC

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        slc = SLC(hdf5file=h5_path)
    # h5py で開く前にメタデータを読み切る（同じファイルを SWMR 有無の違うモードで開けないため）
    return slc, slc.getRadarGrid(frequency), slc.getOrbit()


def _radar_crop_start(radar_grid, orbit, dem_raster, epsg: int, x: float, y: float, half: float = 320.0):
    """地図座標 (x, y) 付近の小グリッドに対応するレーダー範囲の先頭 (行, 列)。"""
    import isce3
    from isce3.core.rdr_geo_block_generator import block_generator

    gg = isce3.product.GeoGridParameters(x - half, y + half, 10.0, -10.0,
                                         int(2 * half / 10), int(2 * half / 10), epsg)
    for rdr_slice, *_ in block_generator(gg, radar_grid, orbit, dem_raster, gg.length, gg.width, 100):
        return rdr_slice[0].start, rdr_slice[1].start
    return None


def _subpixel_peak(c: np.ndarray, i: int, j: int) -> tuple[float, float]:
    def para(m, z, p):
        d = m - 2 * z + p
        return 0.0 if d == 0 else 0.5 * (m - p) / d
    n0, n1 = c.shape
    return (i + para(c[(i - 1) % n0, j], c[i, j], c[(i + 1) % n0, j]),
            j + para(c[i, (j - 1) % n1], c[i, j], c[i, (j + 1) % n1]))


def estimate_nisar_offset(ref_h5: str, sec_h5: str, frequency: str, polarization: str, dem: str,
                          epsg: int, x: float, y: float, size: int = 1024) -> dict:
    """
    幾何計算だけで決まるレーダー位置に対する、参照シーン基準の残差オフセットを推定する。

    各シーンで地図座標 (x, y) に対応するレーダー位置から size×size を切り出し、
    振幅の相互相関ピーク（放物線で小数画素化）を求める。日付間の位置ずれは
    数 m（分解能と同程度）残ることがあり、補正しないとコヒーレンスが大きく下がる。
    戻り値: az_lines / rg_samples（副シーンの画素単位）, az_time_s / slant_range_m, ncc
    """
    import h5py
    import isce3

    dem_raster = isce3.io.Raster(dem)
    opened = [_open_nisar(p, frequency) for p in (ref_h5, sec_h5)]
    size = min([size] + [n for _, rg, _ in opened for n in (rg.length, rg.width)])
    crops, grids = [], []
    for path, (slc, rg, orbit) in zip((ref_h5, sec_h5), opened):
        start = _radar_crop_start(rg, orbit, dem_raster, epsg, x, y)
        if start is None:
            raise RuntimeError(f"オフセット推定点がシーン範囲外です: {path}")
        a0 = int(np.clip(start[0] - size // 2, 0, rg.length - size))
        r0 = int(np.clip(start[1] - size // 2, 0, rg.width - size))
        key = slc.slcPath(frequency, polarization)
        with h5py.File(path, "r") as h5:
            ds = h5[key]
            arr = ds[a0:a0 + size, r0:r0 + size]
        if arr.dtype.names:
            arr = arr["r"].astype(np.float32) + 1j * arr["i"].astype(np.float32)
        crops.append(np.abs(arr).astype(np.float64))
        grids.append((rg, a0 - start[0], r0 - start[1]))
    a, b = (np.nan_to_num(c) for c in crops)
    a -= a.mean()
    b -= b.mean()
    c = np.fft.ifft2(np.fft.fft2(b) * np.conj(np.fft.fft2(a))).real
    c /= np.sqrt((a ** 2).sum() * (b ** 2).sum()) + 1e-30
    i, j = np.unravel_index(int(np.argmax(c)), c.shape)
    di, dj = _subpixel_peak(c, i, j)
    di = di - size if di > size / 2 else di
    dj = dj - size if dj > size / 2 else dj
    # 切り出し開始位置の丸め差（クリップ時）を戻す
    di += grids[1][1] - grids[0][1]
    dj += grids[1][2] - grids[0][2]
    rg = grids[1][0]
    return {
        "az_lines": float(di), "rg_samples": float(dj),
        "az_time_s": float(di / rg.prf), "slant_range_m": float(dj * rg.range_pixel_spacing),
        "ncc": float(c.max()),
    }


def _constant_lut(radar_grid, value: float):
    import isce3

    if value == 0.0:
        return isce3.core.LUT2d()
    xs = np.array([radar_grid.starting_range, radar_grid.end_range])
    ys = np.array([radar_grid.sensing_start, radar_grid.sensing_stop])
    return isce3.core.LUT2d(xs, ys, np.full((2, 2), value))


def geocode_nisar(h5_path: str, frequency: str, polarization: str, dem: str, template: str,
                  epsg: int, out_path: str, az_time_s: float = 0.0, slant_range_m: float = 0.0,
                  block: int = 1024) -> str:
    """
    NISAR RSLC を isce3.geocode.geocode_slc でジオコードする（NISAR GSLC ワークフローと同じ方式）。

    RSLC はゼロドップラー幾何で処理されているが、方位スペクトルの中心はネイティブドップラー
    （数百 Hz 以上になることがある）にある。plant-isce3 の geocode はベースバンド化に
    幾何用のゼロドップラー LUT を使うため sinc 補間で信号が壊れる。geocode_slc は
    幾何（image_grid_doppler）とベースバンド化（native_doppler）を分けて扱える。
    入力は出力ブロックに必要な範囲だけを HDF5 から読む。
    az_time_s / slant_range_m は geo2rdr 結果への加算補正（estimate_nisar_offset の値）。
    """
    import h5py
    import isce3
    from isce3.core.rdr_geo_block_generator import block_generator
    from isce3.core.types import read_c4_dataset_as_c8

    gdal = _gdal()
    print(f"  RSLC: frequency{frequency}/{polarization}", flush=True)
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
    except Exception:  # サブスワス情報がないプロダクト
        sub_swaths = None
    dem_raster = isce3.io.Raster(dem)
    ellipsoid = isce3.core.make_projection(epsg).ellipsoid
    az_corr = _constant_lut(radar_grid, az_time_s)
    rg_corr = _constant_lut(radar_grid, slant_range_m)

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    out = gdal.GetDriverByName("GTiff").Create(out_path, width, length, 1, gdal.GDT_CFloat32,
                                               options=["TILED=YES", "COMPRESS=DEFLATE", "BIGTIFF=IF_SAFER"])
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
                flatten=True, az_time_correction=az_corr, srange_correction=rg_corr)
            band.WriteArray(geo, geo_slice[1].start, geo_slice[0].start)
            n_blocks += 1
    band.FlushCache()
    out = None
    if n_blocks == 0:
        raise RuntimeError(f"出力グリッドと RSLC の範囲が重なりません: {h5_path}")
    print(f"  geocode_slc: {n_blocks} ブロック", flush=True)
    return out_path
