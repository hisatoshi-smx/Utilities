"""
GUI サーバーからパイプラインを別プロセスで実行・中止・監視する。

isce3 の処理は長時間かつ大量のメモリを使うので、サーバーとは別プロセスにし、
プロセスグループごと終了できるようにする。ログは projects/<名前>/logs/ に保存する。
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime

from .config import STEP_ORDER
from .projects import APP_DIR, PROJECTS_DIR, Project

_lock = threading.Lock()
_procs: dict[str, subprocess.Popen] = {}

# GUI で入力された認証情報だけをワーカーへ渡す（設定ファイルには保存しない）
CREDENTIAL_ENV = {
    "cdse_user": "CCD3_CDSE_USER",
    "cdse_password": "CCD3_CDSE_PASSWORD",
    "asf_user": "CCD3_ASF_USER",
    "asf_password": "CCD3_ASF_PASSWORD",
}


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (OSError, TypeError):
        return False
    return True


def is_running(project: Project) -> bool:
    with _lock:
        p = _procs.get(project.name)
        if p is not None and p.poll() is None:
            return True
    job = project.read_state().get("job") or {}
    return job.get("status") == "running" and _pid_alive(job.get("pid"))


def start(project: Project, from_step: str | None = None, only: str | None = None,
          credentials: dict | None = None) -> dict:
    if is_running(project):
        raise RuntimeError("このプロジェクトは実行中です")
    if from_step and from_step not in STEP_ORDER:
        raise ValueError(f"不明なステップ: {from_step}")
    if only and only not in STEP_ORDER:
        raise ValueError(f"不明なステップ: {only}")
    project.ensure_dirs()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(project.logs, f"run_{ts}.log")
    cmd = [sys.executable, "-u", "-m", "ccd3lib.pipeline", "--project", project.name,
           "--projects-dir", os.path.dirname(project.root)]
    if from_step:
        cmd += ["--from-step", from_step]
    if only:
        cmd += ["--only", only]
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    for key, var in CREDENTIAL_ENV.items():
        val = (credentials or {}).get(key) or ""
        if val:
            env[var] = val
    logf = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(cmd, cwd=APP_DIR, stdout=logf, stderr=subprocess.STDOUT, env=env,
                            start_new_session=True)
    logf.close()
    job = {"status": "running", "pid": proc.pid, "log": os.path.basename(log_path), "started": ts,
           "from_step": from_step, "only": only, "returncode": None}
    project.update_job(job)
    with _lock:
        _procs[project.name] = proc
    threading.Thread(target=_watch, args=(project, proc), daemon=True).start()
    return job


def _watch(project: Project, proc: subprocess.Popen) -> None:
    rc = proc.wait()
    state = project.read_state()
    job = state.get("job") or {}
    if job.get("pid") == proc.pid:
        if job.get("status") == "cancelling":
            job["status"] = "cancelled"
        else:
            job["status"] = "finished" if rc == 0 else "failed"
        job["returncode"] = rc
        job["ended"] = datetime.now().strftime("%Y%m%d_%H%M%S")
        state["job"] = job
        for s in STEP_ORDER:
            if state["steps"].get(s, {}).get("status") == "running":
                state["steps"][s]["status"] = "cancelled" if job["status"] == "cancelled" else "failed"
        project.write_state(state)
    with _lock:
        if _procs.get(project.name) is proc:
            del _procs[project.name]


def cancel(project: Project) -> None:
    state = project.read_state()
    job = state.get("job") or {}
    pid = job.get("pid")
    if not pid or not _pid_alive(pid):
        raise RuntimeError("実行中の処理はありません")
    job["status"] = "cancelling"
    state["job"] = job
    project.write_state(state)
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return

    def _force():
        time.sleep(8)
        if _pid_alive(pid):
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    threading.Thread(target=_force, daemon=True).start()


def refresh(project: Project) -> dict:
    """
    このサーバーが監視していないワーカー（サーバー再起動前に起動したもの）が
    終了していたら、状態を確定させる。最新の state を返す。
    """
    state = project.read_state()
    job = state.get("job") or {}
    if job.get("status") not in ("running", "cancelling"):
        return state
    with _lock:
        watched = project.name in _procs
    if watched or _pid_alive(job.get("pid")):
        return state
    if job["status"] == "cancelling":
        job["status"] = "cancelled"
    elif all(state["steps"][s].get("status") == "done" for s in STEP_ORDER):
        job["status"] = "finished"
    else:
        job["status"] = "interrupted"
    state["job"] = job
    for s in STEP_ORDER:
        if state["steps"][s].get("status") == "running":
            state["steps"][s]["status"] = "failed"
            state["steps"][s]["message"] = "処理が途中で終了しました"
    project.write_state(state)
    return state


def recover_stale(root: str | None = None) -> None:
    """サーバー起動時、終了済みなのに実行中のまま残った状態を直す。"""
    root = root or PROJECTS_DIR
    if not os.path.isdir(root):
        return
    for name in os.listdir(root):
        try:
            proj = Project(name, root)
        except Exception:
            continue
        if proj.exists():
            refresh(proj)


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
        offset = 0
    if size - offset > max_bytes:
        offset = size - max_bytes
    with open(path, "rb") as f:
        f.seek(offset)
        data = f.read(size - offset)
    return {"name": name, "text": data.decode("utf-8", errors="replace"), "offset": offset + len(data), "size": size}
