"""
入力シーン（Sentinel-1 SLC zip / NISAR RSLC HDF5）のメタデータ読み取り。

戻り値は JSON にそのまま保存できる dict:
    {
      "path", "sensor", "datetime"(ISO), "platform", "track", "direction",
      "polarizations": [...],
      "footprint": [[lon, lat], ...],
      "bursts": [{"id", "swath", "border": [[lon, lat], ...]}]   # Sentinel-1 のみ
    }
"""

from __future__ import annotations

import os
import warnings
from datetime import datetime

from shapely.geometry import MultiPolygon, Polygon
from shapely.ops import unary_union


def _ring(poly) -> list[list[float]]:
    # NISAR の boundingPolygon は高さ付き（経度 緯度 高さ）なので先頭 2 成分だけ使う
    return [[round(c[0], 6), round(c[1], 6)] for c in poly.exterior.coords]


def _as_polygon(border) -> Polygon:
    """s1reader の border（shapely Polygon のリスト等）を 1 つのポリゴンにする。"""
    if isinstance(border, (Polygon, MultiPolygon)):
        geom = border
    elif isinstance(border, (list, tuple)) and border and hasattr(border[0], "exterior"):
        geom = unary_union(list(border))
    else:
        geom = Polygon(border)
    if isinstance(geom, MultiPolygon):
        geom = max(geom.geoms, key=lambda g: g.area)
    return geom


def inspect_sentinel1(path: str, polarization: str) -> dict:
    from s1reader.s1_reader import load_bursts

    pol = polarization.lower()
    bursts_out = []
    first = None
    for swath in (1, 2, 3):
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                bursts = load_bursts(path, None, swath, pol, flag_apply_eap=False)
        except ValueError as e:
            if "pol" in str(e).lower() or "not found" in str(e).lower():
                raise ValueError(f"{os.path.basename(path)} に偏波 {polarization} が含まれていません") from e
            raise
        for b in bursts:
            if first is None or b.sensing_start < first.sensing_start:
                first = b
            bursts_out.append(
                {
                    "id": str(b.burst_id).lower(),
                    "swath": swath,
                    "sensing_start": b.sensing_start.isoformat(),
                    "border": _ring(_as_polygon(b.border)),
                }
            )
    if not bursts_out:
        raise ValueError(f"バーストを読み込めませんでした: {path}")

    footprint = unary_union([Polygon(b["border"]) for b in bursts_out]).convex_hull
    track = bursts_out[0]["id"].split("_")[0]
    return {
        "path": path,
        "sensor": "sentinel1",
        "datetime": first.sensing_start.isoformat(),
        "platform": first.platform_id,
        "track": track,
        "direction": first.orbit_direction,
        "polarizations": [polarization],
        "footprint": _ring(footprint),
        "bursts": bursts_out,
    }


def inspect_nisar_rslc(path: str, frequency: str, polarization: str) -> dict:
    import shapely.wkt
    from plant_isce3.readers import open_product

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        prod = open_product(path)
    if prod.productType not in ("RSLC", "SLC"):
        raise ValueError(f"RSLC ではありません（{prod.productType}）: {path}")
    pols = prod.polarizations.get(frequency)
    if not pols:
        raise ValueError(f"周波数 {frequency} がありません: {path}（含まれる周波数: {list(prod.polarizations)}）")
    if polarization not in pols:
        raise ValueError(f"偏波 {polarization} がありません: {path}（周波数 {frequency} の偏波: {pols}）")

    ident = prod.identification
    poly = shapely.force_2d(shapely.wkt.loads(ident.boundingPolygon))
    if isinstance(poly, MultiPolygon):
        poly = max(poly.geoms, key=lambda g: g.area)
    dt = str(ident.zdStartTime)
    # ナノ秒まであると datetime.fromisoformat が読めないので切り詰める
    if "." in dt:
        head, frac = dt.split(".", 1)
        dt = f"{head}.{frac[:6]}"

    def _get(attr, default=None):
        try:
            v = getattr(ident, attr)
            return v.decode() if isinstance(v, bytes) else v
        except Exception:
            return default

    track = None
    try:
        import h5py

        with h5py.File(path, "r") as h5:
            for root in ("science/LSAR", "science/SSAR"):
                key = f"{root}/identification/trackNumber"
                if key in h5:
                    track = int(h5[key][()])
                    break
    except Exception:
        pass

    return {
        "path": path,
        "sensor": "nisar_rslc",
        "datetime": datetime.fromisoformat(dt).isoformat(),
        "platform": _get("missionId", "NISAR"),
        "track": track,
        "direction": _get("orbitPassDirection"),
        "polarizations": list(pols),
        "frequency": frequency,
        "footprint": _ring(poly),
        "bursts": [],
    }


def inspect_scene(path: str, sensor: str, polarization: str, frequency: str = "A") -> dict:
    if not os.path.exists(path):
        raise FileNotFoundError(f"ファイルが見つかりません: {path}")
    if sensor == "sentinel1":
        return inspect_sentinel1(path, polarization)
    if sensor == "nisar_rslc":
        return inspect_nisar_rslc(path, frequency, polarization)
    raise ValueError(f"未対応のセンサー: {sensor}")


def inspect_scenes(paths: list[str], sensor: str, polarization: str, frequency: str = "A") -> dict:
    """
    3 シーンを読み、観測日時順に t1, t2, t3 を割り当てる。
    戻り値: {"scenes": {"t1": {...}, ...}, "reordered": bool, "warnings": [...], "common_footprint": [...]}
    """
    infos = [inspect_scene(p, sensor, polarization, frequency) for p in paths]
    order = sorted(range(3), key=lambda k: infos[k]["datetime"])
    warns = []
    reordered = order != [0, 1, 2]
    if reordered:
        warns.append("入力順と観測日時の順が異なるため、日時順に並べ替えました")
    dts = [infos[k]["datetime"] for k in order]
    if len(set(dts)) != 3:
        raise ValueError("観測日時が同じシーンが含まれています")

    tracks = {str(i.get("track")) for i in infos}
    if len(tracks) > 1:
        raise ValueError(f"3 シーンの軌道（トラック）が一致しません: {sorted(tracks)}")
    dirs = {i.get("direction") for i in infos if i.get("direction")}
    if len(dirs) > 1:
        raise ValueError(f"3 シーンの軌道方向（昇交/降交）が一致しません: {sorted(dirs)}")

    common = None
    for i in infos:
        p = Polygon(i["footprint"])
        common = p if common is None else common.intersection(p)
    if common is None or common.is_empty:
        raise ValueError("3 シーンのフットプリントに共通範囲がありません")
    if isinstance(common, MultiPolygon):
        common = max(common.geoms, key=lambda g: g.area)

    if sensor == "sentinel1":
        ids = [set(b["id"] for b in i["bursts"]) for i in infos]
        n_common = len(ids[0] & ids[1] & ids[2])
        if n_common == 0:
            raise ValueError("3 シーンに共通するバーストがありません")

    scenes = {tag: infos[k] for tag, k in zip(("t1", "t2", "t3"), order)}
    return {
        "scenes": scenes,
        "reordered": reordered,
        "warnings": warns,
        "common_footprint": _ring(common),
        "common_bounds": list(common.bounds),
    }
