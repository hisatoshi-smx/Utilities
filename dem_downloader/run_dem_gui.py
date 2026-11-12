#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
国土地理院 DEM ダウンローダー（ブラウザ GUI）

    python run_dem_gui.py                 # http://127.0.0.1:8780 で起動し、ブラウザを開く
    python run_dem_gui.py --port 9000 --no-browser

既定のポートが使用中の場合は、次の番号(最大 30 個先まで)を順に試し、
実際に使ったアドレスを表示してブラウザもそのアドレスで開く。

ローカル利用を想定している。ファイルブラウザは本プログラムを動かしているマシンの
ファイルシステムを参照するため、既定では 127.0.0.1 のみで待ち受ける。
"""

from __future__ import annotations

import argparse
import math
import os
import socket
import threading
import time
import traceback
import uuid
import webbrowser
from datetime import datetime

from flask import Flask, Response, jsonify, request, send_from_directory
from werkzeug.serving import make_server

import dem_core
import geo_io

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUTPUT_DIR = os.path.join(APP_DIR, "output")

# 他のブラウザ GUI(例: InSAR_MintPy は 8765)と既定ポートが重ならないようにする
DEFAULT_PORT = 8780
PORT_TRIES = 30

# 静的ファイルのパスを他のアプリと変える。同じ origin(127.0.0.1:ポート)を別のアプリが
# 使ったときに、ブラウザのキャッシュ上の /static/app.js などと混ざらないようにするため。
STATIC_URL = "/dem-static"

app = Flask(__name__, static_folder=os.path.join(APP_DIR, "static"), static_url_path=STATIC_URL)


@app.after_request
def _no_cache(resp):
    """画面本体・静的ファイルは毎回サーバーに確認させる（古い/他アプリのキャッシュを使わない）。"""
    if request.path == "/" or request.path.startswith(STATIC_URL + "/"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


class ApiError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


LOOPBACK_NAMES = {"127.0.0.1", "localhost", "[::1]", "::1"}
app.config["LOOPBACK_ONLY"] = False  # main() が、ループバックのみで待ち受けるときに True にする
_server = None  # main() で作る HTTP サーバー（/api/shutdown で停止するために保持）


@app.before_request
def _guard():
    """
    他サイトからの不正な操作を防ぐ。
      - POST は Content-Type: application/json のみ受け付ける。ブラウザは他サイトからこの形式を
        送るときに事前確認(preflight)を要求し、本サーバーはそれに応じないため、他サイトのページから
        ジョブ開始・フォルダ作成・サーバー終了などを実行できない。
      - ローカルのみで待ち受けている場合、Host ヘッダーがローカルのアドレスであること（DNS リバインディング対策）。
    """
    if app.config["LOOPBACK_ONLY"]:
        host = request.host  # 例: "127.0.0.1:8780" / "[::1]:8780"
        hostname = host[: host.index("]") + 1] if host.startswith("[") else host.split(":")[0]
        if hostname not in LOOPBACK_NAMES:
            raise ApiError("このホスト名ではアクセスできません。http://127.0.0.1:ポート番号/ を使ってください。", 403)
    if request.method == "POST" and request.mimetype != "application/json":
        raise ApiError("リクエストの形式が正しくありません。", 415)


@app.errorhandler(ApiError)
def _handle_api_error(e):
    return jsonify(error=e.message), e.status


@app.errorhandler(ValueError)
def _handle_value_error(e):
    return jsonify(error=str(e)), 400


@app.errorhandler(Exception)
def _handle_unexpected(e):
    if hasattr(e, "code") and isinstance(e.code, int):  # Flask/Werkzeug の HTTPException
        return jsonify(error=getattr(e, "description", str(e))), e.code
    traceback.print_exc()
    return jsonify(error=f"{type(e).__name__}: {e}"), 500


# ----------------------------------------------------------------------------
# ジョブ管理（同時実行は 1 件。GSI サーバーへの負荷にも配慮）
# ----------------------------------------------------------------------------


class Job:
    def __init__(self, params: dict, log_path: str):
        self.id = uuid.uuid4().hex[:12]
        self.params = params
        self.status = "running"  # running / done / error / cancelled
        self.progress = 0.0
        self.stage = "準備中"
        self.eta = None
        self.started = time.time()
        self.finished = None
        self.logs = []  # [{"t": "...", "level": "INFO", "msg": "..."}]
        self.result = None
        self.error = None
        self.cancel_event = threading.Event()
        self.lock = threading.Lock()
        self.log_path = log_path
        self._log_file = open(log_path, "a", encoding="utf-8", buffering=1)

    def log(self, msg, level="INFO"):
        t = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self.lock:
            for line in str(msg).splitlines() or [""]:
                self.logs.append({"t": t, "level": level, "msg": line})
                try:
                    self._log_file.write(f"{t} [{level}] {line}\n")
                except (OSError, ValueError):
                    pass

    def set_progress(self, fraction, stage, eta):
        with self.lock:
            self.progress = max(self.progress, min(1.0, fraction))
            self.stage = stage
            self.eta = eta

    def finish(self, status, error=None, result=None):
        with self.lock:
            self.status = status
            self.error = error
            self.result = result
            self.finished = time.time()
            if status == "done":
                self.progress, self.stage, self.eta = 1.0, "完了", 0
            elif status == "cancelled":
                self.stage, self.eta = "キャンセルしました", None
            else:
                self.stage, self.eta = "エラー", None
            try:
                self._log_file.close()
            except OSError:
                pass

    def snapshot(self, since=0):
        with self.lock:
            end = self.finished or time.time()
            return {
                "id": self.id,
                "status": self.status,
                "progress": self.progress,
                "stage": self.stage,
                "eta": self.eta,
                "elapsed": end - self.started,
                "logs": self.logs[since:],
                "next": len(self.logs),
                "result": self.result,
                "error": self.error,
                "log_path": self.log_path,
            }


_jobs = {}
_current_job_id = None
_jobs_lock = threading.Lock()


def _roi_wgs84(body: dict, grid) -> tuple[float, float, float, float]:
    """KML に書き出す矩形範囲（WGS84）。参照グリッド時は出力ラスタの外接範囲、それ以外は地図で指定した ROI。"""
    if body.get("use_reference_grid"):
        return geo_io.bounds_to_wgs84(grid.crs, grid.bounds)
    roi = body.get("roi") or {}
    return float(roi["west"]), float(roi["south"]), float(roi["east"]), float(roi["north"])


def _kml_output_path(output_path: str) -> str:
    out_dir = os.path.dirname(os.path.abspath(output_path))
    stem = os.path.splitext(os.path.basename(output_path))[0]
    return os.path.join(out_dir, "roi", stem + ".kml")


def _export_roi_kml(job: Job, grid, output_path: str) -> str:
    """DEM 取得の前に ROI を KML へ書き出す。"""
    kml_path = _kml_output_path(output_path)
    w, s, e, n = _roi_wgs84(job.params, grid)
    job.set_progress(0.0, "ROI の KML を保存中", None)
    job.log("ROI の KML を保存します（DEM 取得より先に実行）")
    geo_io.write_roi_kml(kml_path, w, s, e, n, title=os.path.splitext(os.path.basename(output_path))[0])
    job.log(f"ROI の KML を保存しました: {kml_path}")
    return kml_path


def _run_job(job: Job, grid, mode, output_path, workers, use_cache, export_roi_kml: bool):
    kml_path = None
    try:
        job.log(f"ジョブ開始 (ID: {job.id})")
        if export_roi_kml:
            kml_path = _export_roi_kml(job, grid, output_path)
        result = dem_core.run_download(
            grid,
            mode,
            output_path,
            workers=workers,
            use_cache=use_cache,
            cancel=job.cancel_event,
            log=job.log,
            progress=job.set_progress,
        )
        if kml_path:
            result["roi_kml"] = kml_path
        job.finish("done", result=result)
    except dem_core.Cancelled:
        if kml_path:
            job.log(f"KML は既に保存済みです: {kml_path}", "WARN")
        job.log("ユーザー操作により中断しました。途中のファイルは削除しています。", "WARN")
        job.finish("cancelled", result={"roi_kml": kml_path} if kml_path else None)
    except dem_core.NoDataError as e:
        if kml_path:
            job.log(f"KML は既に保存済みです: {kml_path}", "WARN")
        job.log(str(e), "ERROR")
        job.finish("error", error=str(e), result={"roi_kml": kml_path} if kml_path else None)
    except Exception as e:
        if kml_path:
            job.log(f"KML は既に保存済みです: {kml_path}", "WARN")
        job.log(f"エラー: {type(e).__name__}: {e}", "ERROR")
        job.log(traceback.format_exc(), "DEBUG")
        job.finish("error", error=f"{type(e).__name__}: {e}", result={"roi_kml": kml_path} if kml_path else None)


# ----------------------------------------------------------------------------
# リクエストのパラメータ解釈
# ----------------------------------------------------------------------------


def _float(body, key, required=True):
    v = body.get(key)
    if v is None or v == "":
        if required:
            raise ApiError(f"{key} が指定されていません。")
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ApiError(f"{key} は数値で指定してください。")
    if not math.isfinite(f):
        raise ApiError(f"{key} が不正な値です。")
    return f


def _parse_grid(body):
    """リクエストから出力グリッド・DEM ソース・並列数などを解釈する。"""
    mode = body.get("source", "auto")
    if mode not in dem_core.LAYER_PLANS:
        raise ApiError(f"不明な DEM ソースです: {mode}")

    workers = int(body.get("workers", 4) or 4)
    workers = max(1, min(8, workers))

    if body.get("use_reference_grid"):
        ref = body.get("reference_path")
        if not ref:
            raise ApiError("参照 GeoTIFF が指定されていません。")
        ref = os.path.abspath(os.path.expanduser(ref))
        if not os.path.isfile(ref):
            raise ApiError(f"参照ファイルが見つかりません: {ref}")
        grid = dem_core.grid_from_reference(ref)
    else:
        roi = body.get("roi") or {}
        west, south = _float(roi, "west"), _float(roi, "south")
        east, north = _float(roi, "east"), _float(roi, "north")
        if not (-180 <= west < east <= 180):
            raise ApiError("ROI の経度の範囲が不正です（-180 ≤ 西 < 東 ≤ 180）。")
        if not (-90 <= south < north <= 90):
            raise ApiError("ROI の緯度の範囲が不正です（-90 ≤ 南 < 北 ≤ 90）。")
        crs = geo_io.parse_crs(body.get("crs"))
        gsd = _float(body, "gsd", required=False)
        if gsd is not None and gsd <= 0:
            raise ApiError("GSD は正の値を指定してください。")
        grid = dem_core.grid_from_roi((west, south, east, north), crs, gsd, mode)
    return grid, mode, workers


# ----------------------------------------------------------------------------
# API
# ----------------------------------------------------------------------------


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/config")
def api_config():
    return jsonify(
        default_output_dir=DEFAULT_OUTPUT_DIR,
        home=os.path.expanduser("~"),
        sources=[{"id": k, "label": v} for k, v in dem_core.SOURCE_MODE_LABELS.items()],
        max_tiles=dem_core.MAX_TILES,
        cache_dir=dem_core.DEFAULT_CACHE_DIR,
    )


@app.get("/api/fs")
def api_fs():
    return jsonify(
        geo_io.list_dir(
            request.args.get("path", "~"),
            dirs_only=request.args.get("dirs_only") == "1",
            show_all=request.args.get("all") == "1",
            show_hidden=request.args.get("hidden") == "1",
        )
    )


@app.post("/api/fs/mkdir")
def api_mkdir():
    body = request.get_json(force=True, silent=True) or {}
    path = geo_io.make_dir(body.get("parent", ""), body.get("name", ""))
    return jsonify(path=path)


@app.post("/api/inspect")
def api_inspect():
    body = request.get_json(force=True, silent=True) or {}
    path = (body.get("path") or "").strip()
    if not path:
        raise ApiError("ファイルパスが指定されていません。")
    return jsonify(geo_io.inspect_file(path))


@app.get("/api/crs/search")
def api_crs_search():
    return jsonify(geo_io.search_crs(request.args.get("q", ""), limit=int(request.args.get("limit", 60))))


@app.get("/api/crs/info")
def api_crs_info():
    return jsonify(geo_io.crs_info(request.args.get("code", "")))


@app.get("/api/geocode")
def api_geocode():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify([])
    try:
        return jsonify(geo_io.geocode_places(q, limit=int(request.args.get("limit", 8))))
    except ValueError as e:
        raise ApiError(str(e))


@app.post("/api/estimate")
def api_estimate():
    body = request.get_json(force=True, silent=True) or {}
    grid, mode, workers = _parse_grid(body)
    est = dem_core.estimate(grid, mode, workers)
    est["crs_name"] = grid.crs.name
    est["is_geographic"] = bool(grid.crs.is_geographic)
    est["unit"] = geo_io._unit_label(grid.crs)
    return jsonify(est)


@app.post("/api/jobs")
def api_start_job():
    global _current_job_id
    body = request.get_json(force=True, silent=True) or {}
    grid, mode, workers = _parse_grid(body)

    out_dir = (body.get("output_dir") or "").strip() or DEFAULT_OUTPUT_DIR
    name = (body.get("output_name") or "").strip() or "dem.tif"
    if os.path.basename(name) != name:
        raise ApiError("出力ファイル名にはフォルダを含めないでください（保存先フォルダは別に指定します）。")
    if not name.lower().endswith((".tif", ".tiff")):
        name += ".tif"
    out_dir = os.path.abspath(os.path.expanduser(out_dir))
    output_path = os.path.join(out_dir, name)
    if not body.get("overwrite") and os.path.exists(output_path):
        raise ApiError(f"出力ファイルが既に存在します: {output_path}\n別のファイル名にするか、上書きにチェックを入れてください。", 409)

    export_roi_kml = bool(body.get("export_roi_kml") or body.get("export_roi_kmz"))
    if export_roi_kml:
        kml_path = _kml_output_path(output_path)
        if not body.get("overwrite") and os.path.exists(kml_path):
            raise ApiError(
                f"ROI の KML が既に存在します: {kml_path}\n別のファイル名にするか、上書きにチェックを入れてください。",
                409,
            )

    plan_est = dem_core.estimate(grid, mode, workers)
    if plan_est["too_many_tiles"]:
        raise ApiError(f"取得するタイル数が多すぎます（{plan_est['n_tiles']} 枚 > 上限 {plan_est['max_tiles']} 枚）。")

    try:
        os.makedirs(out_dir, exist_ok=True)
    except OSError as e:
        raise ApiError(f"保存先フォルダを作成できません: {e}")
    stem = os.path.splitext(output_path)[0]
    log_path = f"{stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    try:
        job = Job(body, log_path)
    except OSError as e:
        raise ApiError(f"ログファイルを作成できません: {e}")

    with _jobs_lock:
        cur = _jobs.get(_current_job_id)
        if cur and cur.status == "running":
            job._log_file.close()
            os.remove(log_path)
            raise ApiError("別のダウンロードが実行中です。完了またはキャンセルしてから開始してください。", 409)
        _jobs[job.id] = job
        _current_job_id = job.id

    job.log(f"保存先: {output_path}")
    job.log(f"ログファイル: {log_path}")
    threading.Thread(
        target=_run_job,
        args=(job, grid, mode, output_path, workers, bool(body.get("use_cache", True)), export_roi_kml),
        daemon=True,
    ).start()
    return jsonify(job.snapshot())


def _get_job(job_id) -> Job:
    job = _jobs.get(job_id)
    if job is None:
        raise ApiError("ジョブが見つかりません。", 404)
    return job


@app.get("/api/jobs/current")
def api_current_job():
    job = _jobs.get(_current_job_id)
    if job is None:
        return jsonify(job=None)
    return jsonify(job=job.snapshot(0))


@app.get("/api/jobs/<job_id>")
def api_job(job_id):
    return jsonify(_get_job(job_id).snapshot(int(request.args.get("since", 0))))


@app.post("/api/jobs/<job_id>/cancel")
def api_cancel(job_id):
    job = _get_job(job_id)
    if job.status == "running":
        job.cancel_event.set()
        job.log("キャンセルを要求しました…", "WARN")
    return jsonify(job.snapshot(int(request.args.get("since", 0))))


@app.get("/api/jobs/<job_id>/log")
def api_job_log(job_id):
    job = _get_job(job_id)
    with job.lock:
        text = "\n".join(f"{l['t']} [{l['level']}] {l['msg']}" for l in job.logs) + "\n"
    fname = os.path.basename(job.log_path)
    return Response(
        text,
        mimetype="text/plain; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{fname}"},
    )


def _port_is_free(host: str, port: int) -> bool:
    """そのポートで待ち受けられるか（実際に bind できるか）を調べる。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # werkzeug と同じ条件
        try:
            s.bind((host, port))
        except OSError:
            return False
    return True


def _shutdown_server():
    """実行中のジョブを中断してから、サーバーを停止する（別スレッドで呼ぶ）。"""
    time.sleep(0.5)  # 呼び出し元へ HTTP 応答を返し終えるのを待つ
    job = _jobs.get(_current_job_id)
    if job is not None and job.status == "running":
        job.log("サーバー終了のため、実行中のダウンロードを中断します。", "WARN")
        job.cancel_event.set()
        for _ in range(150):  # 一時ファイルの削除まで最大 15 秒待つ
            if job.status != "running":
                break
            time.sleep(0.1)
    print("画面からの操作でサーバーを終了します。")
    if _server is not None:
        _server.shutdown()


@app.post("/api/shutdown")
def api_shutdown():
    """画面の「サーバーを終了」ボタンから呼ばれる。"""
    if _server is None:
        raise ApiError("サーバーを停止できません（このプログラムは run_dem_gui.py から起動されていません）。", 409)
    threading.Thread(target=_shutdown_server, daemon=True).start()
    return jsonify(ok=True)


def main():
    global _server
    ap = argparse.ArgumentParser(description="国土地理院 DEM ダウンローダー (ブラウザ GUI)")
    ap.add_argument("--host", default="127.0.0.1", help="待ち受けアドレス (既定: 127.0.0.1)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT,
                    help=f"ポート番号 (既定: {DEFAULT_PORT}。使用中なら次の番号を試す)")
    ap.add_argument("--no-browser", action="store_true", help="ブラウザを自動で開かない")
    args = ap.parse_args()

    # 使えるポートを探す。bind に成功したものをそのまま使うので、確認と起動の間に奪われることがない。
    server = None
    for candidate in range(args.port, args.port + PORT_TRIES):
        if not _port_is_free(args.host, candidate):
            print(f"ポート {candidate} は使用中のため、次の番号を試します。")
            continue
        try:
            server = make_server(args.host, candidate, app, threaded=True)
            break
        except (OSError, SystemExit):
            # 空き確認の直後に別プログラムに取られた場合。werkzeug は SystemExit で終了しようとする。
            print(f"ポート {candidate} を確保できなかったため、次の番号を試します。")
    if server is None:
        raise SystemExit(f"空いているポートが見つかりません（{args.port}〜{args.port + PORT_TRIES - 1}）。--port で別の番号を指定してください。")

    _server = server
    app.config["LOOPBACK_ONLY"] = args.host in ("127.0.0.1", "localhost", "::1")
    port = server.server_port
    url = f"http://{args.host}:{port}/"
    print(f"DEM ダウンローダーを起動しました: {url}")
    print("終了するには Ctrl+C を押してください。")
    if args.host not in ("127.0.0.1", "localhost"):
        print("【注意】外部から接続できる設定です。ファイルブラウザからこのマシンのファイルが見えてしまいます。")
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("終了します。")


if __name__ == "__main__":
    main()
