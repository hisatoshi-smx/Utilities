"""NISAR RSLC から出力グリッド間隔を求める。"""

from __future__ import annotations

import math
import warnings

from .config import ExplorerConfig


def nominal_ground_spacing_m(h5_path: str, frequency: str) -> tuple[float, float, float]:
    """スワス中央の地上方位・地上距離間隔 (m) と推奨値 min(az, rg)。"""
    import isce3
    from nisar.products.readers import SLC

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        slc = SLC(hdf5file=h5_path)
    grid = slc.getRadarGrid(frequency)
    orbit = slc.getOrbit()
    az, rg = isce3.product.get_radar_grid_nominal_ground_spacing(grid, orbit)
    rec = min(az, rg)
    return float(az), float(rg), float(rec)


def resolve_spacing_m(cfg: ExplorerConfig) -> tuple[float, str]:
    """設定の spacing が None なら NISAR から自動。戻り値 (間隔m, 説明文字列)。"""
    if cfg.grid.spacing is not None:
        return float(cfg.grid.spacing), f"手動 {cfg.grid.spacing:g} m"
    if not (cfg.input_h5 or "").strip():
        raise RuntimeError("グリッド間隔を空にするには NISAR HDF5 を指定してください")
    az, rg, rec = nominal_ground_spacing_m(cfg.input_h5, cfg.frequency)
    # 数値誤差を抑え、過剰に細かくしすぎない（約 1 mm 精度）
    spacing = max(0.25, round(rec, 3))
    note = (
        f"自動 {spacing:g} m（スワス中央 地上方位 {az:.3f} m × 地上距離 {rg:.3f} m の細い方）"
    )
    return spacing, note