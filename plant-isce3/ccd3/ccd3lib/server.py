"""
GUI 用のローカル HTTP サーバー（標準ライブラリ http.server）。

ローカル利用前提。ファイルブラウザはサーバーを動かしているマシンの
ファイルシステムを見せるため、既定では 127.0.0.1 のみで待ち受ける。

不正操作対策:
  - POST は Content-Type: application/json のみ受け付ける（他サイトのページからは
    preflight が必要になり、本サーバーは応じないため実行できない）
  - ループバックで待ち受けるときは Host ヘッダーもループバックに限る（DNS リバインディング対策）
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import threading
import traceback
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from pydantic import ValidationError

from . import __version__, jobs
from .config import STEP_LABELS, STEP_ORDER, ProjectConfig, check_runnable, step_signatures
from .projects import APP_DIR, PROJECTS_DIR, Project, ProjectError, create_project, delete_project, list_projects

STATIC_DIR = os.path.join(APP_DIR, "static")
STATIC_PREFIX = "/ccd3-static/"
LOOPBACK_NAMES = {"127.0.0.1", "localhost", "[::1]", "::1"}


class ApiError(Exception):
    def __init__(self, message, status=400, details=None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.details = details


def _validation_details(e: ValidationError) -> list[dict]:
    out = []
    for err in e.errors():
        loc = ".".join(str(x) for x in err.get("loc", ()))
        msg = err.get("msg", "")
        msg = re.sub(r"^Value error, ", "", msg)
        out.append({"field": loc, "message": msg})
    return out


# ---------------------------------------------------------------- API 実装
def api_info(_q, _b):
    return {
        "version": __version__,
        "projects_dir": PROJECTS_DIR,
        "steps": [{"id": s, "label": STEP_LABELS[s]} for s in STEP_ORDER],
        "defaults": ProjectConfig(name="_").model_dump(mode="json"),
        "home": os.path.expanduser("~"),
    }


def api_projects(_q, _b):
    return {"projects": list_projects()}


def api_create_project(_q, body):
    try:
        proj = create_project(body.get("name", ""), body.get("description", ""))
    except ProjectError as e:
        raise ApiError(str(e))
    return {"name": proj.name}


def _project(name: str) -> Project:
    try:
        proj = Project(unquote(name))
    except ProjectError as e:
        raise ApiError(str(e), 404)
    if not proj.exists():
        raise ApiError(f"プロジェクトがありません: {proj.name}", 404)
    return proj


def _project_payload(proj: Project) -> dict:
    cfg = proj.load()
    state = jobs.refresh(proj)
    sigs = step_signatures(cfg)
    for s in STEP_ORDER:
        st = state["steps"][s]
        st["stale"] = st.get("status") == "done" and st.get("signature") != sigs[s]
    errors, warnings = check_runnable(cfg)
    return {
        "name": proj.name,
        "root": proj.root,
        "config": cfg.model_dump(mode="json"),
        "state": state,
        "scenes": proj.read_json("work", "scenes.json"),
        "grid": proj.read_json("work", "grid.json"),
        "previews": proj.read_json("output", "preview", "preview.json"),
        "summary": proj.read_json("output", "summary.json"),
        "check": {"errors": errors, "warnings": warnings},
        "running": jobs.is_running(proj),
    }


def api_get_project(name):
    return lambda _q, _b: _project_payload(_project(name))


def api_save_config(name):
    def handler(_q, body):
        proj = _project(name)
        if jobs.is_running(proj):
            raise ApiError("実行中は設定を保存できません。中止するか完了を待ってください")
        data = body.get("config") or {}
        data["name"] = proj.name
        old = proj.load()
        data.setdefault("created", old.created)
        try:
            cfg = ProjectConfig.model_validate(data)
        except ValidationError as e:
            raise ApiError("設定に誤りがあります", details=_validation_details(e))
        proj.save(cfg)
        return _project_payload(proj)

    return handler


def api_inspect(name):
    """シーン情報だけをその場で読む（地図表示・ROI 設定の補助用）。"""

    def handler(_q, body):
        from .inputs import inspect_scenes

        proj = _project(name)
        cfg = proj.load()
        i = cfg.inputs
        if not all(i.scenes):
            raise ApiError("3 シーンすべてのパスを指定して保存してください")
        try:
            res = inspect_scenes(i.scenes, i.sensor, i.polarization, i.nisar_frequency)
        except Exception as e:
            raise ApiError(f"シーン情報を読めませんでした: {e}")
        proj.write_json(res, "work", "scenes.json")
        # GUI から読んだ結果も inspect ステップの完了として扱う
        proj.update_step("inspect", status="done", signature=step_signatures(cfg)["inspect"], message="GUI から読み込み")
        return _project_payload(proj)

    return handler


def api_run(name):
    def handler(_q, body):
        proj = _project(name)
        try:
            job = jobs.start(proj, body.get("from_step") or None, body.get("only") or None,
                             body.get("credentials") or {})
        except (RuntimeError, ValueError) as e:
            raise ApiError(str(e), 409)
        return {"job": job}

    return handler


def api_cancel(name):
    def handler(_q, _b):
        try:
            jobs.cancel(_project(name))
        except RuntimeError as e:
            raise ApiError(str(e), 409)
        return {"ok": True}

    return handler


def api_log(name):
    def handler(q, _b):
        proj = _project(name)
        offset = int(q.get("offset", ["0"])[0] or 0)
        log_name = q.get("name", [None])[0]
        return jobs.read_log(proj, offset, log_name)

    return handler


def api_logs(name):
    def handler(_q, _b):
        proj = _project(name)
        files = sorted((f for f in os.listdir(proj.logs) if f.endswith(".log")), reverse=True) \
            if os.path.isdir(proj.logs) else []
        return {"logs": files}

    return handler


def api_outputs(name):
    def handler(_q, _b):
        proj = _project(name)
        out = []
        for base in ("output",):
            d = proj.path(base)
            if not os.path.isdir(d):
                continue
            for f in sorted(os.listdir(d)):
                fp = os.path.join(d, f)
                if os.path.isfile(fp):
                    out.append({"name": f, "path": fp, "size": os.path.getsize(fp)})
        return {"files": out, "dir": proj.output}

    return handler


def api_delete(name):
    def handler(_q, body):
        proj = _project(name)
        if body.get("confirm") != proj.name:
            raise ApiError("確認のため、プロジェクト名を正確に入力してください")
        if jobs.is_running(proj):
            raise ApiError("実行中のプロジェクトは削除できません", 409)
        delete_project(proj.name)
        return {"ok": True}

    return handler


def api_fs(q, _b):
    path = q.get("path", [""])[0] or os.path.expanduser("~")
    exts = [e.lower() for e in (q.get("ext", [""])[0] or "").split(",") if e]
    path = os.path.abspath(os.path.expanduser(path))
    if os.path.isfile(path):
        path = os.path.dirname(path)
    if not os.path.isdir(path):
        raise ApiError(f"フォルダがありません: {path}", 404)
    dirs, files = [], []
    try:
        names = sorted(os.listdir(path), key=str.lower)
    except PermissionError:
        raise ApiError(f"フォルダを読めません: {path}", 403)
    for n in names:
        if n.startswith("."):
            continue
        fp = os.path.join(path, n)
        if os.path.isdir(fp):
            # .SAFE は通常フォルダとして辿れるようにする
            dirs.append(n)
        elif not exts or any(n.lower().endswith(e) for e in exts):
            try:
                size = os.path.getsize(fp)
            except OSError:
                size = None
            files.append({"name": n, "size": size})
    return {"path": path, "parent": os.path.dirname(path) if path != "/" else None, "dirs": dirs, "files": files}


# ---------------------------------------------------------------- ルーティング
_ROUTES_GET = [
    (re.compile(r"^/api/info$"), lambda m: api_info),
    (re.compile(r"^/api/projects$"), lambda m: api_projects),
    (re.compile(r"^/api/projects/([^/]+)$"), lambda m: api_get_project(m[1])),
    (re.compile(r"^/api/projects/([^/]+)/log$"), lambda m: api_log(m[1])),
    (re.compile(r"^/api/projects/([^/]+)/logs$"), lambda m: api_logs(m[1])),
    (re.compile(r"^/api/projects/([^/]+)/outputs$"), lambda m: api_outputs(m[1])),
    (re.compile(r"^/api/fs$"), lambda m: api_fs),
]

_ROUTES_POST = [
    (re.compile(r"^/api/projects$"), lambda m: api_create_project),
    (re.compile(r"^/api/projects/([^/]+)/config$"), lambda m: api_save_config(m[1])),
    (re.compile(r"^/api/projects/([^/]+)/inspect$"), lambda m: api_inspect(m[1])),
    (re.compile(r"^/api/projects/([^/]+)/run$"), lambda m: api_run(m[1])),
    (re.compile(r"^/api/projects/([^/]+)/cancel$"), lambda m: api_cancel(m[1])),
    (re.compile(r"^/api/projects/([^/]+)/delete$"), lambda m: api_delete(m[1])),
]

_PREVIEW_RE = re.compile(r"^/api/projects/([^/]+)/preview/([A-Za-z0-9_.\-]+\.png)$")


class Handler(BaseHTTPRequestHandler):
    server_version = f"ccd3/{__version__}"
    loopback_only = True
    on_shutdown = None

    def log_message(self, fmt, *args):
        if os.environ.get("CCD3_HTTP_LOG"):
            super().log_message(fmt, *args)

    # ---- 応答 ----
    def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, data):
        self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8",
                   {"Cache-Control": "no-store"})

    def _error(self, status: int, message: str, details=None):
        self._json(status, {"error": message, "details": details})

    def _file(self, path: str, cache: str = "no-cache"):
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        with open(path, "rb") as f:
            data = f.read()
        self._send(200, data, ctype, {"Cache-Control": cache})

    # ---- ガード ----
    def _host_ok(self) -> bool:
        if not self.loopback_only:
            return True
        host = self.headers.get("Host", "")
        hostname = host[: host.index("]") + 1] if host.startswith("[") else host.split(":")[0]
        return hostname in LOOPBACK_NAMES

    # ---- メソッド ----
    def do_GET(self):
        if not self._host_ok():
            return self._error(403, "不正なホストです")
        url = urlparse(self.path)
        path = url.path
        try:
            if path in ("/", "/index.html"):
                return self._file(os.path.join(STATIC_DIR, "index.html"))
            if path.startswith(STATIC_PREFIX):
                rel = os.path.normpath(unquote(path[len(STATIC_PREFIX):])).lstrip("/")
                fp = os.path.realpath(os.path.join(STATIC_DIR, rel))
                if not fp.startswith(os.path.realpath(STATIC_DIR) + os.sep) or not os.path.isfile(fp):
                    return self._error(404, "見つかりません")
                return self._file(fp)
            m = _PREVIEW_RE.match(path)
            if m:
                proj = _project(m[1])
                fp = proj.path("output", "preview", m[2])
                if not os.path.isfile(fp):
                    return self._error(404, "プレビューがありません")
                return self._file(fp, "no-store")
            for rx, factory in _ROUTES_GET:
                m = rx.match(path)
                if m:
                    return self._json(200, factory(m)(parse_qs(url.query), None))
            return self._error(404, "見つかりません")
        except ApiError as e:
            return self._error(e.status, e.message, e.details)
        except Exception as e:
            traceback.print_exc()
            return self._error(500, f"サーバー内部エラー: {e}")

    def do_HEAD(self):
        return self.do_GET()

    def do_POST(self):
        if not self._host_ok():
            return self._error(403, "不正なホストです")
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            return self._error(415, "Content-Type は application/json にしてください")
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > 5_000_000:
                return self._error(413, "リクエストが大きすぎます")
            raw = self.rfile.read(length) if length else b"{}"
            body = json.loads(raw.decode("utf-8") or "{}")
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            return self._error(400, "JSON を解釈できません")
        path = urlparse(self.path).path
        try:
            if path == "/api/shutdown":
                self._json(200, {"ok": True})
                if self.on_shutdown:
                    threading.Thread(target=self.on_shutdown, daemon=True).start()
                return
            for rx, factory in _ROUTES_POST:
                m = rx.match(path)
                if m:
                    return self._json(200, factory(m)(None, body))
            return self._error(404, "見つかりません")
        except ApiError as e:
            return self._error(e.status, e.message, e.details)
        except Exception as e:
            traceback.print_exc()
            return self._error(500, f"サーバー内部エラー: {e}")


def make_server(host: str, port: int) -> ThreadingHTTPServer:
    Handler.loopback_only = host in LOOPBACK_NAMES
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    Handler.on_shutdown = srv.shutdown
    return srv
