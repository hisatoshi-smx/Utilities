#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
国土地理院 DEM を ROI から取得する CLI（plant-isce3 エクスプローラ等から subprocess 用）。

    conda activate dem_downloader
    python run_dem_cli.py --west 130.6 --south 32.5 --east 130.9 --north 32.8 \\
        --output /tmp/dem.tif --mode auto --height-mode ellipsoid
"""

from __future__ import annotations

import argparse
import os
import sys

# 親プロセス（plant-isce3 等）の PROJ_DATA が残っていると pyproj が壊れる
for _k in ("GDAL_DRIVER_PATH", "GDAL_DATA", "PROJ_DATA", "PROJ_LIB"):
    os.environ.pop(_k, None)
_prefix = os.path.dirname(os.path.dirname(os.path.abspath(sys.executable)))
for _var, _sub in (("GDAL_DATA", ("share", "gdal")), ("PROJ_DATA", ("share", "proj"))):
    _p = os.path.join(_prefix, *_sub)
    if os.path.isdir(_p):
        os.environ[_var] = _p
os.environ.setdefault("PROJ_NETWORK", "ON")

import dem_core
from pyproj import CRS


def main() -> int:
    ap = argparse.ArgumentParser(description="国土地理院 DEM タイル取得（CLI）")
    ap.add_argument("--west", type=float, required=True)
    ap.add_argument("--south", type=float, required=True)
    ap.add_argument("--east", type=float, required=True)
    ap.add_argument("--north", type=float, required=True)
    ap.add_argument("--output", required=True, help="出力 GeoTIFF のパス")
    ap.add_argument("--mode", default="auto", choices=list(dem_core.LAYER_PLANS))
    ap.add_argument("--height-mode", default="ellipsoid", choices=["ellipsoid", "geoid"])
    ap.add_argument("--crs", default="EPSG:4326", help="出力グリッドの座標系")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()
    roi = (args.west, args.south, args.east, args.north)
    crs = CRS.from_user_input(args.crs)
    grid = dem_core.grid_from_roi(roi, crs, source_mode=args.mode)
    dem_core.run_download(
        grid,
        args.mode,
        args.output,
        workers=args.workers,
        height_mode=args.height_mode,
        log=lambda msg, level="INFO": print(f"[{level}] {msg}", flush=True),
    )
    print(args.output, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())