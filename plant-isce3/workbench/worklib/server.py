from __future__ import annotations

import json
import mimetypes
import os
import re
import traceback
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from pydantic import ValidationError

from . import __version__, jobs
from .catalog import TOOLS, all_tools, tool_schema
from .config import ExplorerConfig, check_runnable
from .inspect import inspect_nisar
from .output_names import suggest_output_basenames
from .projects import APP_DIR, PROJECTS_DIR, Project, ProjectError, create_project, delete_project, list_projects

STATIC_DIR = os.path.join(APP_DIR, "static")
STATIC_PREFIX = "/explorer-static/"
LOOPBACK = {"127.0.0.1", "localhost", "[::1]", "::1"}


class ApiError(Exception):
    def __init__(self, message, status=400, details=None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.details = details


def _proj(name: str) -> Project:
    p = Project(unquote(name))
    if not p.exists():
        raise ApiError("プロジェクトがありません", 404)
    return p


def _payload(proj: Project) -> dict:
    cfg = proj.load()
    errs, warns = check_runnable(cfg)
    state = jobs.refresh(proj)
    return {
        "name": proj.name,
        "config": cfg.model_dump(mode="json"),
        "state": state,
        "runtime": proj.read_json("work", "runtime.json"),
        "check": {"errors": errs, "warnings": warns},
        "running": jobs.is_running(proj),
    }


def api_info(_q, _b):
    return {"version": __version__, "app": "Plant-ISCE3 エクスプローラ",
            "projects_dir": PROJECTS_DIR, "tools": all_tools(), "home": os.path.expanduser("~")}


def api_tools_schema(q, _b):
    tool = q.get("tool", [""])[0]
    if tool not in TOOLS:
        raise ApiError("不明なツール")
    return tool_schema(tool)


def api_projects(_q, _b):
    return {"projects": list_projects()}


def api_create(_q, body):
    try:
        proj = create_project(body.get("name", ""), body.get("description", ""))
    except ProjectError as e:
        raise ApiError(str(e))
    return {"name": proj.name}


class Handler(BaseHTTPRequestHandler):
    server_version = "PlantExplorerHTTP/0.1"
    on_shutdown = None

    def log_message(self, *_):
        pass

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0].lower()
        return self.server.server_name not in LOOPBACK or host in LOOPBACK

    def _json(self, status: int, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        return json.loads(raw.decode("utf-8"))

    def do_GET(self):
        if not self._host_ok():
            self._json(403, {"error": "Forbidden"})
            return
        path = urlparse(self.path).path
        q = parse_qs(urlparse(self.path).query)
        try:
            if path == "/" or path == "/index.html":
                self._serve_static("index.html")
                return
            if path.startswith(STATIC_PREFIX):
                rel = path[len(STATIC_PREFIX):]
                self._serve_static(rel)
                return
            if path == "/api/info":
                self._json(200, api_info(q, None))
                return
            if path == "/api/tools/schema":
                self._json(200, api_tools_schema(q, None))
                return
            if path == "/api/projects":
                self._json(200, api_projects(q, None))
                return
            m = re.match(r"^/api/projects/([^/]+)$", path)
            if m:
                self._json(200, _payload(_proj(m[1])))
                return
            m = re.match(r"^/api/projects/([^/]+)/log$", path)
            if m:
                proj = _proj(m[1])
                off = int(q.get("offset", ["0"])[0] or 0)
                self._json(200, jobs.read_log(proj, off, q.get("name", [None])[0]))
                return
            if path == "/api/fs":
                self._json(200, _api_fs(q))
                return
            self._json(404, {"error": "Not found"})
        except ApiError as e:
            self._json(e.status, {"error": e.message, "details": e.details})
        except Exception as e:
            traceback.print_exc()
            self._json(500, {"error": str(e)})

    def do_POST(self):
        if not self._host_ok():
            self._json(403, {"error": "Forbidden"})
            return
        if "application/json" not in (self.headers.get("Content-Type") or ""):
            self._json(415, {"error": "application/json のみ"})
            return
        path = urlparse(self.path).path
        try:
            body = self._read_json()
            if path == "/api/projects":
                self._json(200, api_create(None, body))
                return
            m = re.match(r"^/api/projects/([^/]+)/config$", path)
            if m:
                proj = _proj(m[1])
                if jobs.is_running(proj):
                    raise ApiError("実行中は保存できません", 409)
                data = body.get("config") or {}
                data.pop("check", None)
                for _k in (
                    "polarizations_available",
                    "suggested_outputs",
                    "spacing",
                    "footprint",
                    "bounds",
                    "path",
                    "platform",
                    "datetime",
                    "track",
                    "frame",
                    "direction",
                    "frequencies",
                ):
                    data.pop(_k, None)
                data["name"] = proj.name
                old = proj.load()
                data.setdefault("created", old.created)
                cfg = ExplorerConfig.model_validate(data)
                proj.save(cfg)
                self._json(200, _payload(proj))
                return
            m = re.match(r"^/api/projects/([^/]+)/inspect$", path)
            if m:
                proj = _proj(m[1])
                cfg = proj.load()
                if not cfg.input_h5 or not os.path.isfile(cfg.input_h5):
                    raise ApiError("入力 HDF5 を指定して保存してください")
                info = inspect_nisar(cfg.input_h5, cfg.frequency)
                info["suggested_outputs"] = suggest_output_basenames(
                    info,
                    frequency=cfg.frequency,
                    polarization=cfg.polarization,
                    geocode_mode=cfg.geocode.output,
                )
                proj.write_json(info, "work", "product.json")
                self._json(200, {"product": info, "suggested_outputs": info["suggested_outputs"], "project": _payload(proj)})
                return
            m = re.match(r"^/api/projects/([^/]+)/run$", path)
            if m:
                proj = _proj(m[1])
                tool = body.get("tool")
                errs, _ = check_runnable(proj.load())
                if errs:
                    raise ApiError("設定が不完全です", details=errs)
                try:
                    job = jobs.start(proj, tool)
                except (RuntimeError, ValueError) as e:
                    raise ApiError(str(e), 409)
                self._json(200, {"job": job})
                return
            m = re.match(r"^/api/projects/([^/]+)/cancel$", path)
            if m:
                try:
                    jobs.cancel(_proj(m[1]))
                except RuntimeError as e:
                    raise ApiError(str(e), 409)
                self._json(200, {"ok": True})
                return
            m = re.match(r"^/api/projects/([^/]+)/delete$", path)
            if m:
                proj = _proj(m[1])
                if body.get("confirm") != proj.name:
                    raise ApiError("確認名が一致しません")
                if jobs.is_running(proj):
                    raise ApiError("実行中は削除できません", 409)
                delete_project(proj.name)
                self._json(200, {"ok": True})
                return
            if path == "/api/shutdown":
                self._json(200, {"ok": True, "message": "サーバーを終了します"})
                if self.on_shutdown:
                    threading.Thread(target=self.on_shutdown, daemon=True).start()
                return
            self._json(404, {"error": "Not found"})
        except ApiError as e:
            self._json(e.status, {"error": e.message, "details": e.details})
        except ValidationError as e:
            self._json(400, {"error": "設定に誤りがあります", "details": e.errors()})
        except Exception as e:
            traceback.print_exc()
            self._json(500, {"error": str(e)})

    def _serve_static(self, rel: str):
        rel = unquote(rel).lstrip("/")
        if ".." in rel.split("/"):
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        fp = os.path.join(STATIC_DIR, rel)
        if not os.path.isfile(fp):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        ctype = mimetypes.guess_type(fp)[0] or "application/octet-stream"
        with open(fp, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        # 修正後の JS/CSS が古いキャッシュで隠れないようにする
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


import threading  # noqa: E402 — shutdown で使用


def _api_fs(q) -> dict:
    path = os.path.abspath(os.path.expanduser(q.get("path", [""])[0] or os.path.expanduser("~")))
    exts = [e.lower() for e in (q.get("ext", [""])[0] or "").split(",") if e]
    if os.path.isfile(path):
        path = os.path.dirname(path)
    if not os.path.isdir(path):
        raise ApiError("フォルダがありません", 404)
    dirs, files = [], []
    for n in sorted(os.listdir(path), key=str.lower):
        if n.startswith("."):
            continue
        fp = os.path.join(path, n)
        if os.path.isdir(fp):
            dirs.append(n)
        elif not exts or any(n.lower().endswith(e) for e in exts):
            files.append({"name": n, "size": os.path.getsize(fp) if os.path.isfile(fp) else None})
    return {"path": path, "parent": os.path.dirname(path) if path != "/" else None, "dirs": dirs, "files": files}


def make_server(host: str, port: int) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    Handler.on_shutdown = srv.shutdown
    return srv