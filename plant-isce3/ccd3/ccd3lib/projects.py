"""
プロジェクトディレクトリの作成・一覧・状態管理。

projects/<名前>/
    project.yaml        設定
    state.json          各ステップの実行状態
    logs/               実行ログ
    work/               中間生成物（シーン情報、軌道、DEM、GSLC、コヒーレンス）
    output/             最終成果物（GeoTIFF、プレビュー PNG）
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
from datetime import datetime

from .config import STEP_ORDER, ProjectConfig, load_config, save_config

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECTS_DIR = os.environ.get("CCD3_PROJECTS_DIR", os.path.join(APP_DIR, "projects"))

# \w は日本語も含む。パスとして安全な文字だけを許可する
_NAME_RE = re.compile(r"^\w[\w.\-]{0,63}$")

_state_lock = threading.Lock()


class ProjectError(Exception):
    pass


def validate_name(name: str) -> str:
    name = (name or "").strip()
    if not _NAME_RE.match(name) or name in (".", ".."):
        raise ProjectError(
            "プロジェクト名は英数字・日本語・アンダースコア・ハイフン・ピリオドで 64 文字以内にしてください"
            "（先頭は記号以外）"
        )
    return name


class Project:
    def __init__(self, name: str, root: str | None = None):
        self.name = validate_name(name)
        # geocode ステップは作業ディレクトリを移すので絶対パスにしておく
        self.root = os.path.abspath(os.path.join(root or PROJECTS_DIR, self.name))

    # ---- パス ----
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

    # ---- 設定 ----
    def load(self) -> ProjectConfig:
        if not self.exists():
            raise ProjectError(f"プロジェクトがありません: {self.name}")
        cfg = load_config(self.config_path)
        if cfg.name != self.name:
            cfg = cfg.model_copy(update={"name": self.name})
        return cfg

    def save(self, cfg: ProjectConfig) -> None:
        if cfg.name != self.name:
            cfg = cfg.model_copy(update={"name": self.name})
        self.ensure_dirs()
        save_config(cfg, self.config_path)

    # ---- 状態 ----
    def read_state(self) -> dict:
        with _state_lock:
            try:
                with open(self.state_path, encoding="utf-8") as f:
                    state = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                state = {}
        state.setdefault("steps", {})
        for s in STEP_ORDER:
            state["steps"].setdefault(s, {"status": "pending"})
        state.setdefault("job", None)
        return state

    def write_state(self, state: dict) -> None:
        with _state_lock:
            tmp = self.state_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.state_path)

    def update_step(self, step: str, **fields) -> None:
        state = self.read_state()
        state["steps"].setdefault(step, {}).update(fields)
        self.write_state(state)

    def update_job(self, job: dict | None) -> None:
        state = self.read_state()
        state["job"] = job
        self.write_state(state)

    # ---- JSON 補助（work/ 内のメタデータ） ----
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
        raise ProjectError(f"同名のプロジェクト（またはディレクトリ）が既にあります: {proj.name}")
    proj.ensure_dirs()
    proj.save(ProjectConfig(name=proj.name, description=description))
    proj.write_state(proj.read_state())
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
            desc, created = "（設定ファイルを読めません）", ""
        state = proj.read_state()
        done = sum(1 for s in STEP_ORDER if state["steps"][s].get("status") == "done")
        out.append(
            {
                "name": name,
                "description": desc,
                "created": created,
                "steps_done": done,
                "steps_total": len(STEP_ORDER),
                "running": bool(state.get("job") and state["job"].get("status") == "running"),
                "modified": datetime.fromtimestamp(os.path.getmtime(proj.config_path)).isoformat(timespec="seconds"),
            }
        )
    return out


def delete_project(name: str, root: str | None = None) -> None:
    proj = Project(name, root)
    base = os.path.realpath(root or PROJECTS_DIR)
    target = os.path.realpath(proj.root)
    if os.path.dirname(target) != base or not proj.exists():
        raise ProjectError("削除対象が不正です")
    shutil.rmtree(target)
