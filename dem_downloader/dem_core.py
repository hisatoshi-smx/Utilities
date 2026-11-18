#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
国土地理院 標高タイル(DEM)をダウンロードして GeoTIFF を作る処理の本体。

GUI(run_dem_gui.py)から呼び出される。進捗・ログ・キャンセルはコールバック/イベントで受け渡す。

出力するDEMの考え方:
  1. 出力グリッド(座標系・範囲・解像度)を決める。
  2. 出力範囲を覆う Web メルカトルのタイルを取得し、モザイクにする。
  3. 解像度が高いデータセットを優先し、欠損部分を次点のデータセットで補う
     (自動モード: DEM5A -> DEM5B -> DEM10B)。
  4. モザイクを出力グリッドへ再投影し、帯状(バンド)に分けて GeoTIFF へ書き出す。

SNAP 用 DEM 仕様 (ESA SNAP External DEM):
  - float32 / nodata=-9999 / LZW 圧縮 / タイル 256×256 / BigTIFF IF_SAFER
  - predictor=1 (none) のみ使用。predictor=3 は SNAP (Java ImageIO) で読めないため禁止
  - 書き出し後に assert_snap_dem_geotiff() で Predictor を検証する
"""

from __future__ import annotations

import math
import os
import re
import shutil
import subprocess
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np
import rasterio
import requests
from pyproj import CRS, Transformer
from rasterio.transform import Affine, array_bounds, from_origin
from rasterio.warp import Resampling, calculate_default_transform, reproject, transform as warp_transform, transform_bounds
from rasterio.windows import Window

# ----------------------------------------------------------------------------
# 定数
# ----------------------------------------------------------------------------

TILE = 256
NODATA = -9999.0
EARTH_RADIUS = 6378137.0
ORIGIN_SHIFT = math.pi * EARTH_RADIUS  # Webメルカトルの範囲の半分 (m)
WEB_MERCATOR = "EPSG:3857"

# 取得を許す最大タイル数 (モザイクをメモリ上に作るため。1タイル = 256KB)
MAX_TILES = 16000
# 出力ピクセル数の上限
MAX_PIXELS = 4.0e9

GSI_BASE = "https://cyberjapandata.gsi.go.jp/xyz"

# 利用可能な標高タイル
SOURCES = {
    "dem5a": {
        "url": GSI_BASE + "/dem5a/{z}/{x}/{y}.txt",
        "zoom": 15,
        "label": "DEM5A（航空レーザ測量 5m）",
    },
    "dem5b": {
        "url": GSI_BASE + "/dem5b/{z}/{x}/{y}.txt",
        "zoom": 15,
        "label": "DEM5B（航空レーザ測量 5m）",
    },
    "dem10b": {
        "url": GSI_BASE + "/dem/{z}/{x}/{y}.txt",
        "zoom": 14,
        "label": "DEM10B（基盤地図情報 10m）",
    },
}

# モード → レイヤ構成。レイヤは (ズーム, [優先順のデータセット]) のリストで、
# 先頭のレイヤを全域取得し、後ろのレイヤは前レイヤで欠損が残ったタイルの周辺だけ取得する。
LAYER_PLANS = {
    "auto": [(15, ["dem5a", "dem5b"]), (14, ["dem10b"])],
    "dem5a": [(15, ["dem5a"])],
    "dem5b": [(15, ["dem5b"])],
    "dem10b": [(14, ["dem10b"])],
}

SOURCE_MODE_LABELS = {
    "auto": "自動（高解像度優先: DEM5A → DEM5B → DEM10B で欠損を補完）",
    "dem5a": "DEM5A のみ",
    "dem5b": "DEM5B のみ",
    "dem10b": "DEM10B のみ（10m）",
}

# 国土地理院タイルの標高はジオイド2011 に基づく正常高（ジオイド高）。
HEIGHT_MODE_LABELS = {
    "geoid": "ジオイド高（標高・正常高／GSI タイルそのまま）",
    "ellipsoid": "楕円体高（WGS 84 楕円体上の高さへ変換）",
}
_ORTHO_COMPOUND = CRS.from_epsg(6697)  # JGD2024 + JGD2011 (vertical) height
_ELLIPSOID_3D = CRS.from_epsg(4327)  # WGS 84 geographic 3D
_ORTHO_TO_ELLIP = Transformer.from_crs(_ORTHO_COMPOUND, _ELLIPSOID_3D, always_xy=True)

DEFAULT_CACHE_DIR = os.environ.get(
    "DEM_CACHE_DIR", os.path.join(os.path.expanduser("~"), ".cache", "dem_downloader", "tiles")
)

# ESA SNAP の External DEM (gpt Terrain-Correction 等) 向け GeoTIFF プロファイル。
# Java ImageIO は float TIFF の Predictor=3 (浮動小数点差分) を解釈できず
# javax.imageio.IIOException: Illegal value for Predictor in TIFF file となるため predictor=1 のみ。
SNAP_DEM_GTIFF_PROFILE: dict = {
    "driver": "GTiff",
    "count": 1,
    "dtype": "float32",
    "nodata": NODATA,
    "compress": "lzw",
    "predictor": 1,
    "tiled": True,
    "blockxsize": 256,
    "blockysize": 256,
    "BIGTIFF": "IF_SAFER",
}


def dem_gtiff_profile(grid: Grid, *, file_nodata: Optional[float] = NODATA) -> dict:
    """出力グリッド向けの SNAP 互換 GeoTIFF プロファイル。"""
    profile = {
        **SNAP_DEM_GTIFF_PROFILE,
        "height": grid.height,
        "width": grid.width,
        "crs": grid.crs,
        "transform": grid.transform,
    }
    if file_nodata is None:
        profile.pop("nodata", None)
    else:
        profile["nodata"] = file_nodata
    return profile


def parse_nodata_fill(value) -> float:
    """出力 GeoTIFF に書き込む欠損ピクセルの埋め値（既定 0）。"""
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return 0.0
    try:
        fill = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"欠損の埋め値が数値ではありません: {value!r}")
    if not math.isfinite(fill):
        raise ValueError("欠損の埋め値は有限の数値を指定してください。")
    return fill


def apply_nodata_fill(heights: np.ndarray, nodata_fill: float) -> tuple[np.ndarray, Optional[float]]:
    """
    内部欠損値 NODATA を出力用に処理する。
    nodata_fill が NODATA と同じならそのまま残し、GeoTIFF の nodata タグも -9999。
    それ以外は欠損セルを埋め値で置換し、nodata タグは付けない。
    """
    if nodata_fill == NODATA:
        return heights, NODATA
    mask = heights == NODATA
    if not mask.any():
        return heights, None
    out = heights.copy()
    out[mask] = np.float32(nodata_fill)
    return out, None


_RE_PREDICTOR_BAD = re.compile(r"Predictor:\s*(?:float|horizontal difference|3)\b", re.IGNORECASE)
_RE_PREDICTOR_OK = re.compile(r"Predictor:\s*(?:none|1)\b", re.IGNORECASE)


def assert_snap_dem_geotiff(path: str) -> None:
    """
    生成した DEM GeoTIFF が SNAP External DEM として読める形式か検証する。
    predictor=3 が検出された場合は RuntimeError。
    """
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        raise RuntimeError(f"DEM ファイルが存在しません: {path}")

    tiffinfo = shutil.which("tiffinfo")
    if tiffinfo:
        proc = subprocess.run([tiffinfo, path], capture_output=True, text=True, check=False)
        text = (proc.stdout or "") + (proc.stderr or "")
        if _RE_PREDICTOR_BAD.search(text):
            raise RuntimeError(
                f"GeoTIFF の Predictor が SNAP 非互換です (predictor=3 等): {path}\n{tiffinfo} の出力を確認してください。"
            )
        if "Predictor:" in text and not _RE_PREDICTOR_OK.search(text) and "0x1" not in text:
            raise RuntimeError(f"GeoTIFF の Predictor が 1 (none) ではありません: {path}\n{text[-800:]}")
    else:
        with rasterio.open(path) as ds:
            pred = int(ds.profile.get("predictor", 1) or 1)
            if pred == 3:
                raise RuntimeError(f"GeoTIFF predictor={pred} (SNAP 非互換): {path}")
            if ds.dtypes[0] != "float32":
                raise RuntimeError(f"dtype が float32 ではありません: {ds.dtypes[0]}")


class Cancelled(Exception):
    """ユーザー操作により処理が中断された。"""


class NoDataError(Exception):
    """指定範囲に標高データが存在しなかった。"""


# ----------------------------------------------------------------------------
# グリッド / タイル計算
# ----------------------------------------------------------------------------


@dataclass
class Grid:
    """出力ラスタのグリッド定義。"""

    crs: CRS
    transform: Affine
    width: int
    height: int

    @property
    def bounds(self):
        """(west, south, east, north)"""
        return array_bounds(self.height, self.width, self.transform)


def tile_resolution(zoom: int) -> float:
    """ズームレベルにおけるタイル1ピクセルのサイズ (Webメルカトル座標系の単位 = m)。"""
    return 2 * ORIGIN_SHIFT / (TILE * 2**zoom)


def tile_origin(tx: int, ty: int, zoom: int):
    """タイル左上の Webメルカトル座標 (min_x, max_y)。"""
    size = TILE * tile_resolution(zoom)
    return tx * size - ORIGIN_SHIFT, ORIGIN_SHIFT - ty * size


def tile_range_for_bounds(mx0, my0, mx1, my1, zoom, pad_px=4):
    """Webメルカトル座標の範囲を覆うタイル範囲 (tx0, tx1, ty0, ty1) を返す。"""
    res = tile_resolution(zoom)
    size = TILE * res
    pad = pad_px * res  # 再投影の補間用に少し余白をとる
    n = 2**zoom
    tx0 = math.floor((mx0 - pad + ORIGIN_SHIFT) / size)
    tx1 = math.floor((mx1 + pad + ORIGIN_SHIFT) / size)
    ty0 = math.floor((ORIGIN_SHIFT - (my1 + pad)) / size)
    ty1 = math.floor((ORIGIN_SHIFT - (my0 - pad)) / size)
    clamp = lambda v: min(max(v, 0), n - 1)
    return clamp(tx0), clamp(tx1), clamp(ty0), clamp(ty1)


def bounds_in_mercator(grid: Grid):
    """出力グリッドの範囲を Webメルカトルへ変換して返す。"""
    b = transform_bounds(grid.crs, WEB_MERCATOR, *grid.bounds, densify_pts=21)
    if not all(math.isfinite(v) for v in b):
        raise ValueError("指定した座標系では範囲を Webメルカトルへ変換できませんでした。座標系または範囲を見直してください。")
    mx0, my0, mx1, my1 = b
    lim = ORIGIN_SHIFT
    return max(mx0, -lim), max(my0, -lim), min(mx1, lim), min(my1, lim)


def roi_to_mercator(roi):
    """WGS84 の (west, south, east, north) を Webメルカトル範囲へ。"""
    b = transform_bounds("EPSG:4326", WEB_MERCATOR, *roi, densify_pts=21)
    return b


def auto_gsd(roi, crs: CRS, source_mode: str = "auto") -> float:
    """
    元データ(タイル)の解像度を落とさない出力ピクセルサイズ(出力座標系の単位)を返す。
    GDAL の calculate_default_transform に Webメルカトルのタイルピクセル数を渡して求める。
    """
    zoom = LAYER_PLANS[source_mode][0][0]
    res = tile_resolution(zoom)
    mx0, my0, mx1, my1 = roi_to_mercator(roi)
    w = max(1, int(round((mx1 - mx0) / res)))
    h = max(1, int(round((my1 - my0) / res)))
    t, _, _ = calculate_default_transform(
        WEB_MERCATOR, crs, w, h, left=mx0, bottom=my0, right=mx1, top=my1
    )
    gsd = abs(t.a)
    if not math.isfinite(gsd) or gsd <= 0:
        raise ValueError("出力解像度を自動算出できませんでした。GSD を手動で指定してください。")
    return gsd


def grid_from_roi(roi, crs: CRS, gsd: Optional[float] = None, source_mode: str = "auto") -> Grid:
    """ROI(WGS84 の west, south, east, north)と座標系から出力グリッドを作る。"""
    west, south, east, north = roi
    left, bottom, right, top = transform_bounds("EPSG:4326", crs, west, south, east, north, densify_pts=21)
    if not all(math.isfinite(v) for v in (left, bottom, right, top)):
        raise ValueError("指定した座標系では ROI を変換できませんでした（適用範囲外の可能性があります）。")
    if gsd is None:
        gsd = auto_gsd(roi, crs, source_mode)
    if gsd <= 0:
        raise ValueError("GSD は正の値を指定してください。")
    # 原点を GSD の整数倍にそろえる（他の出力と重ねやすくなる）
    left = math.floor(left / gsd) * gsd
    top = math.ceil(top / gsd) * gsd
    width = max(1, int(math.ceil((right - left) / gsd - 1e-9)))
    height = max(1, int(math.ceil((top - bottom) / gsd - 1e-9)))
    if width * height > MAX_PIXELS:
        raise ValueError(f"出力サイズが大きすぎます ({width} x {height} px)。ROI を小さくするか GSD を大きくしてください。")
    return Grid(crs=crs, transform=from_origin(left, top, gsd, gsd), width=width, height=height)


def grid_from_reference(path: str) -> Grid:
    """参照 GeoTIFF のグリッド(座標系・範囲・解像度)をそのまま使う。"""
    with rasterio.open(path) as src:
        if src.crs is None:
            raise ValueError("参照ラスタに座標系情報がありません。")
        crs = CRS.from_user_input(src.crs.to_wkt())  # rasterio の CRS を pyproj の CRS に統一
        return Grid(crs=crs, transform=src.transform, width=src.width, height=src.height)


# ----------------------------------------------------------------------------
# 実行計画 / 見積もり
# ----------------------------------------------------------------------------


@dataclass
class Plan:
    grid: Grid
    mode: str
    layers: list  # [(zoom, [source,...]), ...]
    merc_bounds: tuple
    tx0: int
    tx1: int
    ty0: int
    ty1: int

    @property
    def n_tiles(self) -> int:
        return (self.tx1 - self.tx0 + 1) * (self.ty1 - self.ty0 + 1)

    @property
    def tiles(self):
        return [(x, y) for y in range(self.ty0, self.ty1 + 1) for x in range(self.tx0, self.tx1 + 1)]


def build_plan(grid: Grid, mode: str = "auto") -> Plan:
    if mode not in LAYER_PLANS:
        raise ValueError(f"不明な DEM ソースです: {mode}")
    layers = LAYER_PLANS[mode]
    mb = bounds_in_mercator(grid)
    tx0, tx1, ty0, ty1 = tile_range_for_bounds(*mb, zoom=layers[0][0])
    return Plan(grid=grid, mode=mode, layers=layers, merc_bounds=mb, tx0=tx0, tx1=tx1, ty0=ty0, ty1=ty1)


def estimate(grid: Grid, mode: str = "auto", workers: int = 4) -> dict:
    """ダウンロード前の見積もり(タイル数・出力サイズ・所要時間の目安)。"""
    plan = build_plan(grid, mode)
    n_src = len(plan.layers[0][1])
    # 1リクエスト 0.3秒程度、欠損があると次点データセットも要求する、という大雑把な仮定
    est_seconds = plan.n_tiles * 0.3 * (1 + 0.3 * (n_src - 1)) / max(1, workers)
    if len(plan.layers) > 1:
        est_seconds *= 1.15
    return {
        "n_tiles": plan.n_tiles,
        "zoom": plan.layers[0][0],
        "width": grid.width,
        "height": grid.height,
        "gsd_x": abs(grid.transform.a),
        "gsd_y": abs(grid.transform.e),
        "bounds": list(grid.bounds),
        "est_bytes": grid.width * grid.height * 4,
        "est_seconds": est_seconds,
        "too_many_tiles": plan.n_tiles > MAX_TILES,
        "max_tiles": MAX_TILES,
    }


def format_duration(seconds: Optional[float]) -> str:
    if seconds is None or not math.isfinite(seconds):
        return "--"
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


# ----------------------------------------------------------------------------
# タイル取得
# ----------------------------------------------------------------------------


@dataclass
class Mosaic:
    array: np.ndarray  # (H, W) float32, 欠損は NODATA
    transform: Affine  # EPSG:3857
    zoom: int
    holes: list  # 欠損セルを含むタイル [(x, y)]
    sources_used: dict  # データセット名 -> 寄与したタイル数


class TileFetcher:
    def __init__(self, workers, use_cache, cache_dir, delay, cancel: threading.Event, log):
        self.workers = workers
        self.use_cache = use_cache
        self.cache_dir = cache_dir
        self.delay = delay
        self.cancel = cancel
        self.log = log
        self._local = threading.local()
        self.lock = threading.Lock()
        self.stats = {"cached": 0, "network": 0, "missing": 0, "errors": 0}

    # --- 低レベル -----------------------------------------------------------
    def _session(self) -> requests.Session:
        s = getattr(self._local, "session", None)
        if s is None:
            s = requests.Session()
            s.headers["User-Agent"] = "dem_downloader/1.0 (GSI elevation tile client)"
            self._local.session = s
        return s

    def _check_cancel(self):
        if self.cancel.is_set():
            raise Cancelled()

    def _cache_path(self, src, z, x, y):
        return os.path.join(self.cache_dir, src, str(z), str(x), str(y))

    @staticmethod
    def _parse_dem_text(text: str) -> np.ndarray:
        # 'e' は欠損値。NODATA に置換する。
        values = text.strip().replace("\n", ",").replace("e", "nan").split(",")
        arr = np.array(values, dtype=np.float32)
        if arr.size != TILE * TILE:
            raise ValueError(f"タイルのサイズが不正です ({arr.size} 値)")
        arr = arr.reshape(TILE, TILE)
        return np.where(np.isnan(arr), np.float32(NODATA), arr).astype(np.float32)

    def get_tile(self, src, z, x, y):
        """1タイルを取得。戻り値: (ndarray|None, status) status: ok / missing / error"""
        base = self._cache_path(src, z, x, y)
        if self.use_cache:
            if os.path.exists(base + ".npy"):
                try:
                    arr = np.load(base + ".npy")
                    with self.lock:
                        self.stats["cached"] += 1
                    return arr, "ok"
                except Exception:
                    pass  # 壊れたキャッシュは取り直す
            elif os.path.exists(base + ".none"):
                with self.lock:
                    self.stats["cached"] += 1
                return None, "missing"

        url = SOURCES[src]["url"].format(z=z, x=x, y=y)
        last_err = ""
        for attempt in range(3):
            self._check_cancel()
            try:
                resp = self._session().get(url, timeout=(5, 30))
            except requests.RequestException as e:
                last_err = f"{type(e).__name__}: {e}"
                time.sleep(1.0 * (attempt + 1))
                continue

            if resp.status_code == 200:
                try:
                    arr = self._parse_dem_text(resp.text)
                except Exception as e:
                    last_err = f"パース失敗: {e}"
                    break
                if self.use_cache:
                    self._save_cache(base, arr)
                with self.lock:
                    self.stats["network"] += 1
                time.sleep(self.delay)
                return arr, "ok"
            if resp.status_code == 404:
                if self.use_cache:
                    self._save_cache(base, None)
                with self.lock:
                    self.stats["missing"] += 1
                time.sleep(self.delay)
                return None, "missing"
            last_err = f"HTTP {resp.status_code}"
            time.sleep(1.0 * (attempt + 1))

        with self.lock:
            self.stats["errors"] += 1
        self.log(f"タイル取得失敗 {src} z={z} x={x} y={y}: {last_err}", "WARN")
        return None, "error"

    @staticmethod
    def _save_cache(base, arr):
        try:
            os.makedirs(os.path.dirname(base), exist_ok=True)
            if arr is None:
                open(base + ".none", "w").close()
            else:
                tmp = base + ".tmp.npy"
                np.save(tmp, arr)
                os.replace(tmp, base + ".npy")
        except OSError:
            pass  # キャッシュに失敗しても処理は継続

    # --- タイル単位 / レイヤ単位 ---------------------------------------------
    def fetch_tile(self, sources, z, x, y):
        """優先順にデータセットを試し、欠損セルを次点で埋めた 256x256 配列を返す。"""
        arr = np.full((TILE, TILE), NODATA, dtype=np.float32)
        used = []
        for src in sources:
            self._check_cancel()
            tile, _ = self.get_tile(src, z, x, y)
            if tile is None:
                continue
            fill = (arr == NODATA) & (tile != NODATA)
            if fill.any():
                arr[fill] = tile[fill]
                used.append(src)
            if not (arr == NODATA).any():
                break
        return arr, used

    def fetch_mosaic(self, zoom, tiles, sources, label, progress: Callable, prog_range):
        """tiles を取得してモザイクにする。progress(fraction, stage, eta) を随時呼ぶ。"""
        xs = [t[0] for t in tiles]
        ys = [t[1] for t in tiles]
        tx0, tx1, ty0, ty1 = min(xs), max(xs), min(ys), max(ys)
        nx, ny = tx1 - tx0 + 1, ty1 - ty0 + 1
        mosaic = np.full((ny * TILE, nx * TILE), NODATA, dtype=np.float32)
        holes = []
        used_count = {s: 0 for s in sources}

        total = len(tiles)
        done = 0
        stamps = deque(maxlen=40)
        started = time.time()
        step = max(1, total // 20)
        lo, hi = prog_range
        self.log(f"[{label}] ズーム{zoom} のタイル {total} 枚を取得します（データセット: {', '.join(sources)}）")

        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            futs = {ex.submit(self.fetch_tile, sources, zoom, x, y): (x, y) for x, y in tiles}
            try:
                for fut in as_completed(futs):
                    self._check_cancel()
                    x, y = futs[fut]
                    arr, used = fut.result()
                    j, i = y - ty0, x - tx0
                    mosaic[j * TILE:(j + 1) * TILE, i * TILE:(i + 1) * TILE] = arr
                    if (arr == NODATA).any():
                        holes.append((x, y))
                    for s in used:
                        used_count[s] += 1

                    done += 1
                    stamps.append(time.time())
                    eta = None
                    if done < total:
                        if len(stamps) >= 5:
                            per = (stamps[-1] - stamps[0]) / (len(stamps) - 1)
                        else:
                            per = (time.time() - started) / done
                        eta = per * (total - done)
                    progress(lo + (hi - lo) * done / total, f"{label}: タイル取得 {done}/{total}", eta)
                    if done % step == 0 or done == total:
                        self.log(
                            f"[{label}] {done}/{total} ({100 * done // total}%) "
                            f"残り約 {format_duration(eta)}"
                        )
            finally:
                for f in futs:
                    f.cancel()

        tx_min_x, tx_max_y = tile_origin(tx0, ty0, zoom)
        res = tile_resolution(zoom)
        return Mosaic(
            array=mosaic,
            transform=from_origin(tx_min_x, tx_max_y, res, res),
            zoom=zoom,
            holes=holes,
            sources_used=used_count,
        )


# ----------------------------------------------------------------------------
# メイン処理
# ----------------------------------------------------------------------------


def _warp_band(mosaic: Mosaic, shape, band_transform, dst_crs):
    dst = np.full(shape, NODATA, dtype=np.float32)
    reproject(
        source=mosaic.array,
        destination=dst,
        src_transform=mosaic.transform,
        src_crs=WEB_MERCATOR,
        dst_transform=band_transform,
        dst_crs=dst_crs,
        src_nodata=NODATA,
        dst_nodata=NODATA,
        resampling=Resampling.bilinear,
        num_threads=min(4, os.cpu_count() or 1),
    )
    return dst


def parse_height_mode(value: str) -> str:
    mode = (value or "geoid").strip().lower()
    if mode not in HEIGHT_MODE_LABELS:
        raise ValueError(f"高さの種類は geoid または ellipsoid を指定してください (指定値: {value!r})")
    return mode


def apply_height_mode(
    heights: np.ndarray,
    band_transform: Affine,
    dst_crs: CRS,
    height_mode: str,
) -> np.ndarray:
    """国土地理院のジオイド高を、必要に応じて楕円体高 (WGS 84) へ変換する。"""
    if height_mode == "geoid":
        return heights
    rows, cols = np.indices(heights.shape)
    xs, ys = rasterio.transform.xy(band_transform, rows.ravel(), cols.ravel(), offset="center")
    lon, lat = warp_transform(dst_crs, "EPSG:6668", xs, ys)
    flat = heights.ravel()
    valid = flat != NODATA
    if not np.any(valid):
        return heights
    lon_v = np.asarray(lon, dtype=np.float64)[valid]
    lat_v = np.asarray(lat, dtype=np.float64)[valid]
    h_v = flat[valid].astype(np.float64, copy=False)
    _, _, h_ell = _ORTHO_TO_ELLIP.transform(lon_v, lat_v, h_v)
    out = flat.copy()
    out[valid] = h_ell.astype(np.float32)
    return out.reshape(heights.shape)


def run_download(
    grid: Grid,
    mode: str,
    output_path: str,
    *,
    workers: int = 4,
    use_cache: bool = True,
    cache_dir: Optional[str] = None,
    delay: float = 0.05,
    height_mode: str = "geoid",
    nodata_fill: float = 0.0,
    cancel: Optional[threading.Event] = None,
    log: Callable = lambda msg, level="INFO": print(f"[{level}] {msg}"),
    progress: Callable = lambda fraction, stage, eta: None,
) -> dict:
    """DEM をダウンロードして output_path に GeoTIFF を書き出す。結果の概要を dict で返す。"""
    height_mode = parse_height_mode(height_mode)
    nodata_fill = parse_nodata_fill(nodata_fill)
    cancel = cancel or threading.Event()
    cache_dir = cache_dir or DEFAULT_CACHE_DIR
    t_start = time.time()

    plan = build_plan(grid, mode)
    if plan.n_tiles > MAX_TILES:
        raise ValueError(
            f"取得するタイル数が多すぎます ({plan.n_tiles} 枚 > 上限 {MAX_TILES} 枚)。ROI を小さくするか、GSD を見直してください。"
        )

    west, south, east, north = grid.bounds
    log(f"出力座標系: {grid.crs.to_string()}")
    log(f"出力範囲: W={west:.6f} S={south:.6f} E={east:.6f} N={north:.6f}")
    log(
        f"出力サイズ: {grid.width} x {grid.height} px / "
        f"GSD: {abs(grid.transform.a):.8g} x {abs(grid.transform.e):.8g} (座標系の単位)"
    )
    log(f"DEM ソース: {SOURCE_MODE_LABELS[mode]}")
    log(f"標高の種類: {HEIGHT_MODE_LABELS[height_mode]}")
    if nodata_fill == NODATA:
        log(f"欠損画素: NoData ({NODATA:g}) のまま出力します")
    else:
        log(f"欠損画素: {nodata_fill:g} で埋めて出力します（GeoTIFF の nodata タグは付けません）")
    log(f"取得タイル数(第1レイヤ): {plan.n_tiles} 枚 / 並列数: {workers} / キャッシュ: {'使用' if use_cache else '不使用'}")

    fetcher = TileFetcher(workers, use_cache, cache_dir, delay, cancel, log)

    # --- 第1レイヤ(最高解像度) ---
    z0, srcs0 = plan.layers[0]
    has_second = len(plan.layers) > 1
    mosaics = []
    m0 = fetcher.fetch_mosaic(z0, plan.tiles, srcs0, "高解像度", progress, (0.0, 0.70))
    mosaics.append(m0)

    # --- 第2レイヤ(欠損補完) ---
    if has_second and m0.holes:
        z1, srcs1 = plan.layers[1]
        shift = z0 - z1
        parents = sorted({(x >> shift, y >> shift) for x, y in m0.holes})
        log(f"第1レイヤで欠損が残ったタイル {len(m0.holes)} 枚 → ズーム{z1} のタイル {len(parents)} 枚で補完します")
        m1 = fetcher.fetch_mosaic(z1, parents, srcs1, "補完", progress, (0.70, 0.85))
        mosaics.append(m1)
    elif has_second:
        log("第1レイヤだけで全域が埋まったため、補完レイヤは取得しません")

    st = fetcher.stats
    log(f"タイル取得完了: ネットワーク {st['network']} / キャッシュ {st['cached']} / データなし(404) {st['missing']} / 失敗 {st['errors']}")
    if st["errors"]:
        log(f"{st['errors']} 件のタイル取得に失敗しました。該当部分は欠損になります（再実行でキャッシュ済み分は再取得されません）。", "WARN")
    for i, m in enumerate(mosaics):
        contrib = ", ".join(f"{s}: {n}枚" for s, n in m.sources_used.items())
        log(f"レイヤ{i + 1} (ズーム{m.zoom}) で採用されたタイル: {contrib}")

    if not any((m.array != NODATA).any() for m in mosaics):
        raise NoDataError(
            "指定範囲に標高データがありませんでした。国土地理院の標高タイルは日本国内のみ提供されています。"
        )

    # --- 再投影 + 書き出し(帯状に処理) ---
    out_dir = os.path.dirname(os.path.abspath(output_path))
    os.makedirs(out_dir, exist_ok=True)
    tmp_path = output_path + ".part"

    # 1帯あたり約 2500 万ピクセル以内、行数は 256 の倍数(タイル構造に合わせる)
    band_rows = 256 * max(1, min(8, int(2.5e7 // (grid.width * 256))))
    n_bands = math.ceil(grid.height / band_rows)
    file_nodata = NODATA if nodata_fill == NODATA else None
    profile = dem_gtiff_profile(grid, file_nodata=file_nodata)
    log(f"再投影して書き出します（{n_bands} 帯 / 1帯 {band_rows} 行）")

    valid_total = 0
    t_warp = time.time()
    try:
        with rasterio.open(tmp_path, "w", **profile) as dst:
            dst.update_tags(
                SOURCE="Geospatial Information Authority of Japan (GSI) elevation tiles",
                DEM_MODE=mode,
                HEIGHT_MODE=height_mode,
                VERTICAL_REFERENCE=(
                    "JGD2011 geoid height (orthometric)"
                    if height_mode == "geoid"
                    else "WGS 84 ellipsoidal height"
                ),
                NODATA_FILL=str(nodata_fill),
            )
            for b in range(n_bands):
                if cancel.is_set():
                    raise Cancelled()
                row0 = b * band_rows
                rows = min(band_rows, grid.height - row0)
                band_tf = grid.transform * Affine.translation(0, row0)
                shape = (rows, grid.width)

                out = _warp_band(mosaics[0], shape, band_tf, grid.crs)
                for extra in mosaics[1:]:
                    hole = out == NODATA
                    if hole.any():
                        fill = _warp_band(extra, shape, band_tf, grid.crs)
                        out[hole] = fill[hole]
                out = apply_height_mode(out, band_tf, grid.crs, height_mode)
                valid_total += int((out != NODATA).sum())
                out, _ = apply_nodata_fill(out, nodata_fill)
                dst.write(out, 1, window=Window(0, row0, grid.width, rows))

                done = b + 1
                eta = (time.time() - t_warp) / done * (n_bands - done) if done < n_bands else None
                progress(0.85 + 0.15 * done / n_bands, f"再投影・書き出し {done}/{n_bands}", eta)
        if valid_total == 0:
            raise NoDataError("再投影の結果、有効な標高値がありませんでした。")
        os.replace(tmp_path, output_path)
        assert_snap_dem_geotiff(output_path)
    except BaseException:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        raise

    total_px = grid.width * grid.height
    ratio = valid_total / total_px
    size = os.path.getsize(output_path)
    elapsed = time.time() - t_start
    if nodata_fill == NODATA:
        log(f"有効画素率: {100 * ratio:.1f}% （欠損は NoData = {NODATA:g}）")
    else:
        log(f"有効画素率: {100 * ratio:.1f}% （欠損 {100 * (1 - ratio):.1f}% は {nodata_fill:g} で埋め済み）")
    if ratio < 0.999:
        log("欠損が含まれます。海域・データ未整備地域、または日本国外の可能性があります。", "WARN")
    log(f"保存しました: {output_path} ({size / 1e6:.1f} MB)")
    log(f"完了（所要時間 {format_duration(elapsed)}）")

    return {
        "output": output_path,
        "size_bytes": size,
        "width": grid.width,
        "height": grid.height,
        "valid_ratio": ratio,
        "elapsed": elapsed,
        "tile_stats": dict(st),
        "height_mode": height_mode,
        "nodata_fill": nodata_fill,
    }
