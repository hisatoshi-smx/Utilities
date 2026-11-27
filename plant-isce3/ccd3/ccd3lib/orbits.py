"""
Sentinel-1 軌道ファイル（EOF）の準備。

自動取得には sentineleof を使う。認証は次のいずれか:
  - ~/.netrc（または指定 netrc）に dataspace.copernicus.eu / urs.earthdata.nasa.gov を登録
  - GUI で入力した CDSE / Earthdata のユーザー名・パスワード（環境変数で受け渡し、保存しない）
"""

from __future__ import annotations

import glob
import os
import re
from datetime import datetime, timedelta

_EOF_RE = re.compile(
    r"(?P<mission>S1[A-D])_OPER_AUX_(?P<kind>POEORB|RESORB)_OPOD_\d{8}T\d{6}_V(?P<start>\d{8}T\d{6})_(?P<stop>\d{8}T\d{6})\.EOF$"
)

# シーンの前後にこれだけの余裕がある軌道ファイルを使う
_MARGIN = timedelta(minutes=2)


def _parse(name: str):
    m = _EOF_RE.search(os.path.basename(name))
    if not m:
        return None
    fmt = "%Y%m%dT%H%M%S"
    return m["mission"], m["kind"], datetime.strptime(m["start"], fmt), datetime.strptime(m["stop"], fmt)


def find_existing(orbit_dir: str, platform: str, sensing: datetime) -> str | None:
    best = None
    for f in glob.glob(os.path.join(orbit_dir, "*.EOF")):
        p = _parse(f)
        if not p:
            continue
        mission, kind, start, stop = p
        if mission != platform or not (start + _MARGIN <= sensing <= stop - _MARGIN):
            continue
        # POEORB を優先
        rank = 0 if kind == "POEORB" else 1
        if best is None or rank < best[0]:
            best = (rank, f)
    return best[1] if best else None


def download(scene_path: str, orbit_dir: str, provider: str, netrc_file: str, log=print) -> str:
    from eof.download import download_eofs

    os.makedirs(orbit_dir, exist_ok=True)
    kwargs = {
        "sentinel_file": scene_path,
        "save_dir": orbit_dir,
        "force_asf": provider == "asf",
        "cdse_user": os.environ.get("CCD3_CDSE_USER", ""),
        "cdse_password": os.environ.get("CCD3_CDSE_PASSWORD", ""),
        "asf_user": os.environ.get("CCD3_ASF_USER", ""),
        "asf_password": os.environ.get("CCD3_ASF_PASSWORD", ""),
    }
    if netrc_file:
        kwargs["netrc_file"] = netrc_file
    last_err = None
    for orbit_type in ("precise", "restituted"):
        try:
            log(f"  {os.path.basename(scene_path)}: {orbit_type} 軌道を取得中...")
            files = download_eofs(orbit_type=orbit_type, **kwargs)
        except Exception as e:  # sentineleof は失敗時に様々な例外を投げる
            last_err = e
            log(f"    取得失敗: {e}")
            continue
        if files:
            return str(files[0])
    raise RuntimeError(
        f"軌道ファイルを取得できませんでした: {os.path.basename(scene_path)}\n"
        f"  最後のエラー: {last_err}\n"
        "  CDSE / Earthdata の認証情報（netrc または GUI 入力）を確認するか、"
        "軌道の指定方法を「ファイル指定」または「SAFE 内の軌道」に変更してください。"
    )


def prepare_orbits(scenes: dict, mode: str, files: list[str], scene_order: list[str],
                   provider: str, netrc_file: str, orbit_dir: str, log=print) -> dict:
    """
    scenes: inspect 結果の {"t1": {...}, ...}
    files: 設定の軌道ファイル（入力順 = scene_order の並び）
    scene_order: 入力順のシーンパス（files との対応付けに使う）
    戻り値: {"t1": path|None, ...}
    """
    out = {}
    for tag, info in scenes.items():
        if mode == "annotation":
            out[tag] = None
            log(f"{tag}: SAFE 内の軌道を使用")
            continue
        if mode == "files":
            idx = scene_order.index(info["path"])
            out[tag] = files[idx]
            log(f"{tag}: 指定ファイル {files[idx]}")
            continue
        sensing = datetime.fromisoformat(info["datetime"])
        found = find_existing(orbit_dir, info["platform"], sensing)
        if not found:
            download(info["path"], orbit_dir, provider, netrc_file, log=log)
            found = find_existing(orbit_dir, info["platform"], sensing)
        if not found:
            raise RuntimeError(f"{tag}: 観測時刻を含む軌道ファイルが見つかりません")
        log(f"{tag}: {os.path.basename(found)}")
        out[tag] = found
    return out
