from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime

from .logutil import sanitize_log_text
from .projects import APP_DIR, PROJECTS_DIR, Project

_lock = threading.Lock()
_procs: dict[str, subprocess.Popen] = {}


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (OSError, TypeError):
        return False
    return True


def is_running(project: Project) -> bool:
    with _lock:
        p = _procs.get(project.name)
        if p and p.poll() is None:
            return True
    job = project.read_state().get("job") or {}
    return job.get("status") == "running" and _alive(job.get("pid"))


def start(project: Project, tool: str) -> dict:
    if is_running(project):
        raise RuntimeError("実行中です")
    if tool not in ("geocode", "rtc"):
        raise ValueError(tool)
    project.ensure_dirs()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_name = f"run_{ts}_{tool}.log"
    log_path = os.path.join(project.logs, log_name)
    cmd = [sys.executable, "-u", "-m", "worklib.worker", "--project", project.name,
           "--projects-dir", os.path.dirname(project.root), "--tool", tool]
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONPATH": APP_DIR}
    f = open(log_path, "w", encoding="utf-8", buffering=1)
    proc = subprocess.Popen(cmd, cwd=APP_DIR, stdout=f, stderr=subprocess.STDOUT, env=env,
                            start_new_session=True)
    # 子プロセスが fd を継承している間は親が閉じてよい（POSIX）
    f.close()
    job = {"status": "running", "tool": tool, "pid": proc.pid, "log": log_name, "started": ts,
           "returncode": None}
    project.update_job(job)
    with _lock:
        _procs[project.name] = proc
    threading.Thread(target=_watch, args=(project, proc), daemon=True).start()
    return job


def _watch(project: Project, proc: subprocess.Popen) -> None:
    rc = proc.wait()
    job = project.read_state().get("job") or {}
    if job.get("pid") == proc.pid:
        job["status"] = "finished" if rc == 0 else "failed"
        job["returncode"] = rc
        job["ended"] = datetime.now().strftime("%Y%m%d_%H%M%S")
        project.update_job(job)
    with _lock:
        _procs.pop(project.name, None)


def cancel(project: Project) -> None:
    job = project.read_state().get("job") or {}
    pid = job.get("pid")
    if not pid or not _alive(pid):
        raise RuntimeError("実行中の処理がありません")
    job["status"] = "cancelling"
    project.update_job(job)
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass


def refresh(project: Project) -> dict:
    state = project.read_state()
    job = state.get("job") or {}
    if job.get("status") != "running":
        return state
    with _lock:
        if project.name in _procs or _alive(job.get("pid")):
            return state
    job["status"] = "interrupted"
    state["job"] = job
    project.write_state(state)
    return state


def recover_stale(root: str | None = None) -> None:
    root = root or PROJECTS_DIR
    if not os.path.isdir(root):
        return
    for name in os.listdir(root):
        try:
            refresh(Project(name, root))
        except Exception:
            pass


def read_log(project: Project, offset: int = 0, name: str | None = None, max_bytes: int = 512_000) -> dict:
    job = project.read_state().get("job") or {}
    name = name or job.get("log")
    if not name:
        return {"name": None, "text": "", "offset": 0, "size": 0}
    path = os.path.join(project.logs, os.path.basename(name))
    if not os.path.isfile(path):
        return {"name": name, "text": "", "offset": 0, "size": 0}
    size = os.path.getsize(path)
    if offset > size:
        offset = max(0, size - max_bytes)
    elif size - offset > max_bytes:
        offset = size - max_bytes
    with open(path, "rb") as f:
        f.seek(offset)
        data = f.read()
    text = sanitize_log_text(data.decode("utf-8", errors="replace"))
    return {"name": name, "text": text, "offset": offset + len(data), "size": size}