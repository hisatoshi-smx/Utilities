"""
GDAL / PROJ の環境変数を、実行中の Python（conda 環境）に合わせて補正する。

conda 環境を activate せずにその環境の python を直接起動すると、base 環境の
GDAL_DRIVER_PATH などが残り、HDF5 プラグインの読み込みに失敗することがある。
osgeo / pyproj / isce3 を import する前に apply() を呼ぶこと。
"""

from __future__ import annotations

import os
import sys

_VARS = {
    "GDAL_DRIVER_PATH": ("lib", "gdalplugins"),
    "GDAL_DATA": ("share", "gdal"),
    "PROJ_DATA": ("share", "proj"),
}


def apply() -> None:
    prefix = os.path.realpath(sys.prefix)
    for name, parts in _VARS.items():
        expected = os.path.join(prefix, *parts)
        if not os.path.isdir(expected):
            continue
        current = os.environ.get(name)
        if current and os.path.realpath(current).startswith(prefix + os.sep):
            continue
        os.environ[name] = expected
    os.environ.pop("PROJ_LIB", None)
    # Copernicus DEM のジオイド補正で PROJ のグリッドをネットワーク取得するため
    os.environ.setdefault("PROJ_NETWORK", "ON")
