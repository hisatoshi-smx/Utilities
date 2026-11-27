from __future__ import annotations

import json
import os
import re
import shutil
import threading
from datetime import datetime

from .config import ExplorerConfig, load_config, save_config

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECTS_DIR = os.environ.get("EXPLORER_PROJECTS_DIR", os.path.join(APP_DIR, "projects"))
_NAME_RE = re.compile(r"^\w[\w.\-]{0,63}$")
_lock = threading.Lock()


class ProjectError(Exception):
    pass


def validate_name(name: str) -> str:
    name = (name or "").strip()
    if not _NAME_RE.match(name) or name in (".", ".."):
        raise ProjectError("プロジェクト名が不正です")
    return name


class Project:
    def __init__(self, name: str, root: str | None = None):
        self.name = validate_name(name)
        self.root = os.path.abspath(os.path.join(root or PROJECTS_DIR, self.name))

    @property
    def config_path(self) -> str:
        return os.path.join(self.root, "project.yaml")

    @property
    def state_path(self) -> str:
        return os.path.join(self.root, "state.json")

    def path(self, *parts: str) -> str:
        return os.path.join(self.root, *parts)

    @property
    def work(self) -> str:
        return self.path("work")

    @property
    def output(self) -> str:
        return self.path("output")

    @property
    def logs(self) -> str:
        return self.path("logs")

    def exists(self) -> bool:
        return os.path.isfile(self.config_path)

    def ensure_dirs(self) -> None:
        for d in (self.root, self.work, self.output, self.logs):
            os.makedirs(d, exist_ok=True)

    def load(self) -> ExplorerConfig:
        if not self.exists():
            raise ProjectError(f"プロジェクトがありません: {self.name}")
        cfg = load_config(self.config_path)
        if cfg.name != self.name:
            cfg = cfg.model_copy(update={"name": self.name})
        return cfg

    def save(self, cfg: ExplorerConfig) -> None:
        if cfg.name != self.name:
            cfg = cfg.model_copy(update={"name": self.name})
        self.ensure_dirs()
        save_config(cfg, self.config_path)

    def read_state(self) -> dict:
        with _lock:
            try:
                with open(self.state_path, encoding="utf-8") as f:
                    state = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                state = {}
        state.setdefault("job", None)
        return state

    def write_state(self, state: dict) -> None:
        with _lock:
            tmp = self.state_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.state_path)

    def update_job(self, job: dict | None) -> None:
        state = self.read_state()
        state["job"] = job
        self.write_state(state)

    def read_json(self, *parts: str, default=None):
        try:
            with open(self.path(*parts), encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return default

    def write_json(self, data, *parts: str) -> None:
        p = self.path(*parts)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, p)


def create_project(name: str, description: str = "", root: str | None = None) -> Project:
    proj = Project(name, root)
    if os.path.exists(proj.root):
        raise ProjectError(f"既に存在します: {proj.name}")
    proj.ensure_dirs()
    proj.save(ExplorerConfig(name=proj.name, description=description))
    proj.write_state({"job": None})
    return proj


def list_projects(root: str | None = None) -> list[dict]:
    root = root or PROJECTS_DIR
    out = []
    if not os.path.isdir(root):
        return out
    for name in sorted(os.listdir(root)):
        try:
            proj = Project(name, root)
        except ProjectError:
            continue
        if not proj.exists():
            continue
        try:
            cfg = proj.load()
            desc, created = cfg.description, cfg.created
        except Exception:
            desc, created = "", ""
        state = proj.read_state()
        job = state.get("job") or {}
        out.append({
            "name": name,
            "description": desc,
            "created": created,
            "running": job.get("status") == "running",
            "modified": datetime.fromtimestamp(os.path.getmtime(proj.config_path)).isoformat(timespec="seconds"),
        })
    return out


def delete_project(name: str, root: str | None = None) -> None:
    proj = Project(name, root)
    base = os.path.realpath(root or PROJECTS_DIR)
    target = os.path.realpath(proj.root)
    if os.path.dirname(target) != base or not proj.exists():
        raise ProjectError("削除対象が不正です")
    shutil.rmtree(target)