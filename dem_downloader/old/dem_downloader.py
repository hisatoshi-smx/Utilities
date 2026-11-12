#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import argparse
import math
import time
import numpy as np
import rasterio
from rasterio.warp import transform_bounds, reproject, Resampling
from rasterio.transform import from_origin
import requests
import pandas as pd
from io import BytesIO

# --- 定数設定 ---
# 国土地理院タイル URLテンプレート
URL_TEMPLATES = {
    "dem5a": "https://cyberjapandata.gsi.go.jp/xyz/dem5a/{z}/{x}/{y}.txt",
    "dem5b": "https://cyberjapandata.gsi.go.jp/xyz/dem5b/{z}/{x}/{y}.txt",
    "dem10b": "https://cyberjapandata.gsi.go.jp/xyz/dem/{z}/{x}/{y}.txt",
    "rgb": "https://cyberjapandata.gsi.go.jp/xyz/seamlessphoto/{z}/{x}/{y}.jpg"
}

# Webメルカトル (EPSG:3857) の定数
EARTH_RADIUS = 6378137.0
ORIGIN_SHIFT = 2 * math.pi * EARTH_RADIUS / 2.0

def lonlat_to_tile(lon, lat, zoom):
    """緯度経度からタイル座標(x, y)を算出"""
    mx = lon * ORIGIN_SHIFT / 180.0
    my = math.log(math.tan((90 + lat) * math.pi / 360.0)) / (math.pi / 180.0)
    my = my * ORIGIN_SHIFT / 180.0
    
    res = (2 * math.pi * EARTH_RADIUS) / (256 * 2**zoom)
    px = (mx + ORIGIN_SHIFT) / res
    py = (ORIGIN_SHIFT - my) / res
    
    tx = int(math.ceil(px / 256.0) - 1)
    ty = int(math.ceil(py / 256.0) - 1)
    return tx, ty

def tile_to_web_mercator(tx, ty, zoom):
    """タイル座標からWebメルカトル(EPSG:3857)の左上座標と解像度を算出"""
    res = (2 * math.pi * EARTH_RADIUS) / (256 * 2**zoom)
    min_x = tx * 256 * res - ORIGIN_SHIFT
    max_y = ORIGIN_SHIFT - ty * 256 * res
    return min_x, max_y, res

def download_tile(url_template, x, y, z, is_dem=True):
    """
    タイルをダウンロードしてnumpy配列として返す
    dem: float32 array (256x256)
    rgb: uint8 array (3, 256, 256)
    """
    url = url_template.format(z=z, x=x, y=y)
    print(f"Downloading tile: z={z}, x={x}, y={y} -> {url}")
    
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code != 200:
            print(f"Error: {resp.status_code} (Not Found or Error)")
            return None

        if is_dem:
            # テキスト形式: 'e'は欠損値なのでNaNに置換
            content = resp.text.replace("e", "NaN")
            try:
                # pandasを使って高速にパース
                df = pd.read_csv(BytesIO(content.encode()), header=None)
                arr = df.to_numpy(dtype=np.float32)
                # 欠損値を -9999 に置換
                arr = np.nan_to_num(arr, nan=-9999.0)
                return arr
            except Exception as parse_err:
                print(f"Parse Error: {parse_err}")
                return None

        else: # rgb
            # 画像形式: RasterioのMemoryFileで開く
            with rasterio.io.MemoryFile(resp.content) as memfile:
                with memfile.open() as dataset:
                    return dataset.read() # (bands, h, w)

    except Exception as e:
        print(f"Download failed: {e}")
        return None

def download_and_mosaic(input_tiff, output_tiff, map_type="dem5a", zoom=None, gsd=None):
    """
    入力GeoTIFFの範囲に対応するタイルをダウンロードし、結合・リプロジェクトして保存する
    """
    
    # 0. ズームレベルとURLの決定
    if zoom is None:
        if map_type == "dem10b":
            zoom = 14
        elif map_type in ["dem5a", "dem5b"]:
            zoom = 15
        elif map_type == "rgb":
            zoom = 18
        else:
            zoom = 15 # fallback
            
    url_template = URL_TEMPLATES[map_type]
    is_dem = (map_type != "rgb")

    print(f"Target: {map_type.upper()}, Zoom: {zoom}")

    # 1. 入力ファイルの情報を取得し、出力の解像度を決定
    with rasterio.open(input_tiff) as src:
        src_crs = src.crs
        src_bounds = src.bounds
        src_profile = src.profile.copy()
        
        # GSDが指定された場合、サイズとTransformを再計算
        if gsd is not None:
            if src_crs.is_geographic:
                print("【警告】GSDがメートルで指定されましたが、入力ファイルの座標系が地理座標系（度単位）です。意図しない出力結果になる可能性があります。")
            
            # 境界の幅と高さから、指定GSDに基づくピクセル数を計算
            dst_width = int(math.ceil((src_bounds.right - src_bounds.left) / gsd))
            dst_height = int(math.ceil((src_bounds.top - src_bounds.bottom) / gsd))
            # 新しいTransformの生成 (左上座標を基準に、X/Yのピクセルサイズを指定)
            dst_transform = rasterio.transform.from_origin(src_bounds.left, src_bounds.top, gsd, gsd)
            print(f"GSD override: {gsd}m -> Output Size: {dst_width}x{dst_height}")
        else:
            dst_transform = src.transform
            dst_width = src.width
            dst_height = src.height

        # Webメルカトルではなく、緯度経度(WGS84)での範囲が必要
        if src_crs != "EPSG:4326":
            wgs84_bounds = transform_bounds(src_crs, "EPSG:4326", *src_bounds)
        else:
            wgs84_bounds = src_bounds

    min_lon, min_lat, max_lon, max_lat = wgs84_bounds
    
    # 2. 必要なタイル範囲を計算
    # 北端(max_lat)がY最小、南端(min_lat)がY最大
    min_tx, min_ty = lonlat_to_tile(min_lon, max_lat, zoom) 
    max_tx, max_ty = lonlat_to_tile(max_lon, min_lat, zoom) 

    x_range = range(min_tx, max_tx + 1)
    y_range = range(min_ty, max_ty + 1)
    
    print(f"Tile Range: X[{min_tx}-{max_tx}], Y[{min_ty}-{max_ty}]")
    
    if len(x_range) == 0 or len(y_range) == 0:
        print("Error: Tile range is empty. Check coordinate transformation.")
        return

    # 3. モザイク用の空配列を作成 (Webメルカトル)
    tile_w, tile_h = 256, 256
    total_w = len(x_range) * tile_w
    total_h = len(y_range) * tile_h
    
    if is_dem:
        mosaic = np.full((total_h, total_w), -9999.0, dtype=np.float32)
        count = 1
    else:
        mosaic = np.zeros((3, total_h, total_w), dtype=np.uint8)
        count = 3

    # 4. タイルダウンロード & 配置
    for i, tx in enumerate(x_range):
        for j, ty in enumerate(y_range):
            tile_data = download_tile(url_template, tx, ty, zoom, is_dem)
            
            if tile_data is not None:
                y_start = j * tile_h
                y_end = y_start + tile_h
                x_start = i * tile_w
                x_end = x_start + tile_w
                
                if is_dem:
                    mosaic[y_start:y_end, x_start:x_end] = tile_data
                else:
                    mosaic[:, y_start:y_end, x_start:x_end] = tile_data
            
            time.sleep(0.5) 

    # 5. モザイク画像のAffine変換を定義 (EPSG:3857)
    mosaic_min_x, mosaic_max_y, res = tile_to_web_mercator(min_tx, min_ty, zoom)
    mosaic_transform = from_origin(mosaic_min_x, mosaic_max_y, res, res)
    mosaic_crs = "EPSG:3857"

    # 6. リプロジェクト (Warp)
    print(f"Reprojecting from EPSG:3857 to {src_crs}...")
    
    if is_dem:
        dst_data = np.full((dst_height, dst_width), -9999.0, dtype=np.float32)
        resampling = Resampling.bilinear
        nodata = -9999.0
    else:
        dst_data = np.zeros((3, dst_height, dst_width), dtype=np.uint8)
        resampling = Resampling.cubic
        nodata = 0

    reproject(
        source=mosaic,
        destination=dst_data,
        src_transform=mosaic_transform,
        src_crs=mosaic_crs,
        dst_transform=dst_transform,
        dst_crs=src_crs,
        resampling=resampling,
        src_nodata=nodata,
        dst_nodata=nodata
    )

    # 7. 保存
    print(f"Saving to {output_tiff} (BigTIFF support enabled)...")
    src_profile.update({
        'driver': 'GTiff',
        'height': dst_height,
        'width': dst_width,
        'count': count,
        'dtype': str(dst_data.dtype),
        'crs': src_crs,
        'transform': dst_transform,
        'compress': 'lzw',
        'nodata': nodata,
        'BIGTIFF': 'YES'  # ★ BigTIFF対応を追加
    })

    with rasterio.open(output_tiff, 'w', **src_profile) as dst:
        if is_dem:
            dst.write(dst_data, 1)
        else:
            for b in range(count):
                dst.write(dst_data[b], b + 1)

    print("Done.")

def main():
    parser = argparse.ArgumentParser(description="Download GSI DEM/Ortho tiles matching input GeoTIFF extent")
    parser.add_argument("input_tif", help="Input GeoTIFF (Reference extent)")
    parser.add_argument("output_tif", help="Output GeoTIFF")
    
    # タイプの選択肢を拡張 (dem5a, dem5b, dem10b, rgb)
    parser.add_argument("--type", 
                        choices=["dem5a", "dem5b", "dem10b", "rgb"], 
                        default="dem5a", 
                        help="Map Type: dem5a (default, zoom15), dem5b (zoom15), dem10b (zoom14), or rgb")
    
    # ズームレベルの手動指定用（指定がなければデフォルト値が使われる）
    parser.add_argument("--zoom", type=int, help="Specify zoom level manually (Optional)")

    # ★ GSDの指定を追加
    parser.add_argument("--gsd", type=float, help="Output GSD (Ground Sample Distance) in meters (Optional)")
    
    args = parser.parse_args()
    
    if not os.path.exists(args.input_tif):
        print(f"Error: Input file not found: {args.input_tif}")
        return
    
    out_dir = os.path.dirname(args.output_tif)
    if out_dir and not os.path.exists(out_dir):
        os.makedirs(out_dir)

    download_and_mosaic(args.input_tif, args.output_tif, args.type, args.zoom, args.gsd)

if __name__ == "__main__":
    main()
