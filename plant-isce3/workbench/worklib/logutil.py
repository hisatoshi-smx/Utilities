"""ログ行の整形（長行の折り返し・\\r 進捗の改行化）。"""

from __future__ import annotations

import subprocess
import sys
from typing import Callable

DEFAULT_MAX_LINE = 200


def split_log_buffer(pending: str) -> tuple[list[str], str]:
    """\\n / \\r で行に分割し、未完了断片を返す。"""
    lines: list[str] = []
    start = 0
    i = 0
    while i < len(pending):
        if pending[i] in "\r\n":
            if i > start:
                lines.append(pending[start:i])
            if pending[i] == "\r" and i + 1 < len(pending) and pending[i + 1] == "\n":
                i += 1
            start = i + 1
        i += 1
    return lines, pending[start:]


def wrap_log_line(line: str, max_len: int = DEFAULT_MAX_LINE) -> list[str]:
    line = line.rstrip()
    if not line:
        return []
    if len(line) <= max_len:
        return [line]
    out: list[str] = []
    while len(line) > max_len:
        out.append(line[:max_len] + " …")
        line = "  " + line[max_len:]
    out.append(line)
    return out


def emit_log_line(log: Callable[..., None], line: str, max_len: int = DEFAULT_MAX_LINE) -> None:
    for part in wrap_log_line(line, max_len):
        log(part)


def sanitize_log_text(text: str, max_len: int = DEFAULT_MAX_LINE) -> str:
    """保存済み・一括キャプチャのログを画面向けに整形。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    parts: list[str] = []
    for raw in text.split("\n"):
        parts.extend(wrap_log_line(raw, max_len))
    return "\n".join(parts) + ("\n" if parts else "")


def run_process_logging(
    cmd: list[str],
    log: Callable[..., None],
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    max_line: int = DEFAULT_MAX_LINE,
) -> int:
    """subprocess を逐次読み、\\r 含む進捗も行単位で log に流す。"""
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        cwd=cwd,
        env=env,
    )
    assert proc.stdout is not None
    pending = ""
    try:
        while True:
            chunk = proc.stdout.read(4096)
            if chunk:
                pending += chunk
                complete, pending = split_log_buffer(pending)
                for line in complete:
                    emit_log_line(log, line, max_line)
            elif proc.poll() is not None:
                break
        if pending.strip():
            emit_log_line(log, pending, max_line)
    finally:
        proc.stdout.close()
    return proc.wait()


def log_cmd_multiline(log: Callable[..., None], cmd: list[str], indent: str = "  ") -> None:
    log(f"{indent}コマンド: {cmd[0]}")
    i = 1
    while i < len(cmd):
        if cmd[i].startswith("-"):
            if i + 1 < len(cmd) and not cmd[i + 1].startswith("-"):
                log(f"{indent}  {cmd[i]} {cmd[i + 1]}")
                i += 2
            else:
                log(f"{indent}  {cmd[i]}")
                i += 1
        else:
            log(f"{indent}  {cmd[i]}")
            i += 1