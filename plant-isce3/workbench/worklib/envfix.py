"""conda の base から漏れた GDAL/PROJ 環境変数を外し、HDF5 プラグインを有効にする。"""

from __future__ import annotations

import os
import sys


def apply() -> None:
    for k in list(os.environ):
        if k in ("GDAL_DRIVER_PATH", "GDAL_DATA", "PROJ_DATA", "PROJ_LIB"):
            os.environ.pop(k, None)
    os.environ.setdefault("PROJ_NETWORK", "ON")
    prefix = os.path.dirname(os.path.dirname(sys.executable))
    gdal_data = os.path.join(prefix, "share", "gdal")
    proj_data = os.path.join(prefix, "share", "proj")
    if os.path.isdir(gdal_data):
        os.environ["GDAL_DATA"] = gdal_data
    if os.path.isdir(proj_data):
        os.environ["PROJ_DATA"] = proj_data