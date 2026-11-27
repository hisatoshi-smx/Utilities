"""ログ行の整形（長行の折り返し・\\r 進捗は同一行の上書き）。"""

from __future__ import annotations

import subprocess
import sys
from typing import Callable

DEFAULT_MAX_LINE = 200


def collapse_cr_line(line: str) -> str:
    """行内の \\r は直前の文字列を破棄し、最後の断片のみ残す（ターミナル進捗）。"""
    i = line.rfind("\r")
    return line[i + 1 :] if i >= 0 else line


def split_log_buffer(pending: str) -> tuple[list[str], str]:
    """\\n で行に分割。裸の \\r は行頭へ戻る（未完了断片に残す）。"""
    lines: list[str] = []
    start = 0
    i = 0
    while i < len(pending):
        if pending[i : i + 2] == "\r\n":
            if i > start:
                lines.append(pending[start:i])
            start = i + 2
            i += 2
        elif pending[i] == "\n":
            if i > start:
                lines.append(pending[start:i])
            start = i + 1
            i += 1
        elif pending[i] == "\r":
            start = i + 1
            i += 1
        else:
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
    """保存済み・一括キャプチャのログを画面向けに整形（\\r 進捗は 1 行に畳む）。"""
    text = text.replace("\r\n", "\n")
    parts: list[str] = []
    for raw in text.split("\n"):
        collapsed = collapse_cr_line(raw)
        if collapsed:
            parts.extend(wrap_log_line(collapsed, max_len))
        elif raw == "":
            parts.append("")
    out = "\n".join(parts)
    if text.endswith("\n") and (parts or text == "\n"):
        out += "\n"
    return out


def run_process_logging(
    cmd: list[str],
    log: Callable[..., None],
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    max_line: int = DEFAULT_MAX_LINE,
) -> int:
    """subprocess 出力をそのまま転送（\\r 進捗を改行にしない）。"""
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
    try:
        while True:
            chunk = proc.stdout.read(4096)
            if chunk:
                sys.stdout.write(chunk)
                sys.stdout.flush()
            elif proc.poll() is not None:
                break
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